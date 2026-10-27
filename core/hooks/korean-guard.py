#!/usr/bin/env python3
"""한국어 지킴이: 사용자에게 보이는 글이 영어이거나 「—」 대시를 쓰면 막거나 다시 쓰게 한다.

다짐이나 기억 저장으로는 안 고쳐져서 기계로 막는다. 훅 입력(JSON)을 stdin으로 받는다.
  pre  : 사용자에게 보이는 도구(질문·단추·파일 설명·세션 제목 등)의 글이 영어면 그 도구를 막음(exit 2)
  post : 도구가 끝날 때마다, 그사이 새로 쓴 중간 알림 글이 영어면 바로 알림(exit 2 → Claude가 한국어로 다시 씀).
         대화 기록은 지난번에 읽은 곳 뒤만 읽는다
  stop : 답을 마칠 때, 마지막 글이 영어거나 대시가 있으면 다시 쓰게 함(한 답에서 두 번까지)
판단: 코드 묶음·`코드`·주소·파일 경로를 빼고, 한글이 하나도 없는데 영어 낱말이 4개 이상이거나,
영어 낱말이 8개 이상이면서 한글 글자 수의 절반보다 많으면 「영어 글」.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_common as hc  # noqa: E402

HANGUL = re.compile(r"[가-힣ㄱ-ㆎ]")
WORD = re.compile(r"[A-Za-z][A-Za-z'’\-]{1,}")
STRIP = [re.compile(p, re.S) for p in (
    r"```.*?```", r"`[^`\n]*`", r"https?://\S+", r"\[[^\]]*\]\([^)]*\)",
    r"(?:[A-Za-z]:)?[\w.\-가-힣]*[/\\][\w./\\\-가-힣]+", r"<[^>]{1,80}>")]
DASH = "—"
SHOWN_TOOLS = {  # 사용자 화면에 글이 그대로 보이는 도구와 그 칸
    "mcp__ccd_turn__wrap_up": ("recap", "ask", "actions"),
    "AskUserQuestion": ("questions",),
    "SendUserFile": ("caption",),
    "mcp__ccd_session__start_session": ("title",),
    "mcp__ccd_session__hand_off_to_session": ("title",),
    "mcp__claude-code-remote__set_session_title": ("title",),
}
MSG = ("[자동 점검: 한국어 지킴이] 사용자에게 보이는 글은 쉬운 한국어로만 쓰고 「—」 대시는 쓰지 않습니다"
       "(진행 알림·설명·질문·요약·단추 모두). ")


def plain(text):
    for p in STRIP:
        text = p.sub(" ", text)
    return text


def is_english(text):
    t = plain(text)
    words = len(WORD.findall(t))
    han = len(HANGUL.findall(t))
    return (han == 0 and words >= 4) or (words >= 8 and words > han * 0.5)


def has_dash(text):
    return DASH in plain(text)


def problem(text):
    if is_english(text):
        return "영어 글"
    if has_dash(text):
        return "「—」 대시"
    return None


def strings(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for x in v.values():
            yield from strings(x)
    elif isinstance(v, list):
        for x in v:
            yield from strings(x)


def turn_texts(es):
    """이번 답(마지막 사용자 말 뒤)의 Claude 글 조각들."""
    start = hc.turn_start(es)
    res = []
    for i, d in enumerate(es[start:], start):
        if d.get("type") != "assistant" or d.get("isSidechain"):
            continue
        for b in (d.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip():
                res.append((i, b["text"]))
    return res


def short(t):
    t = " ".join(plain(t).split())
    return t[:90] + ("…" if len(t) > 90 else "")


def offset_path(sid):
    return os.path.join(hc.state_dir(), "korean-offset-" + re.sub(r"[^\w\-]", "_", sid or "x"))


def new_texts(path, sid):
    """지난번에 읽은 곳 뒤로 새로 붙은 Claude 글만 읽는다(긴 대화에서 도구마다 기록 전체를 읽지 않게).

    처음이거나 기록 파일이 짧아졌으면(새 파일) 이번 답 글만 본다.
    """
    op = offset_path(sid)
    try:
        start = int((hc.read_text(op) or "").strip() or -1)
    except ValueError:
        start = -1
    size = os.path.getsize(path)
    if start < 0 or start > size:
        texts = [t for _, t in turn_texts(hc.transcript(path))]
    else:
        texts = []
        with open(path, "rb") as f:
            f.seek(start)
            chunk = f.read()
        # 끝에 아직 덜 쓰인 줄이 있으면 다음번에 읽는다
        complete = chunk[:chunk.rfind(b"\n") + 1] if b"\n" in chunk else b""
        size = start + len(complete)
        for line in complete.decode("utf-8", "replace").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict) or d.get("type") != "assistant" or d.get("isSidechain"):
                continue
            for blk in (d.get("message") or {}).get("content") or []:
                if isinstance(blk, dict) and blk.get("type") == "text" and blk.get("text", "").strip():
                    texts.append(blk["text"])
    hc.write_atomic(op, str(size))
    return texts


def mode_pre(data):
    tool = data.get("tool_name", "")
    keys = SHOWN_TOOLS.get(tool)
    if not keys:
        return 0
    inp = data.get("tool_input") or {}
    texts = [s for k in keys for s in strings(inp.get(k))]
    bad = [(s, problem(s)) for s in texts if problem(s)]
    joined = " ".join(texts)
    if not bad and is_english(joined):
        bad = [(joined, "영어 글")]
    if bad:
        hc.bump_stat("막음:한국어지킴이")
        hc.record_mistake("한국어지킴이:" + bad[0][1], short(bad[0][0]))
        sys.stderr.write(MSG + "이 도구(%s)의 글에 %s가 있어요. 고쳐서 다시 보내세요. 문제 글: 「%s」\n"
                         % (tool, bad[0][1], short(bad[0][0])))
        return 2
    return 0


def mode_post(data, path):
    bad = [t for t in new_texts(path, data.get("session_id")) if is_english(t)]
    if bad:
        hc.bump_stat("막음:한국어지킴이")
        hc.record_mistake("한국어지킴이:영어 글", short(bad[-1]))
        sys.stderr.write(MSG + "방금 중간 알림을 영어로 썼어요: 「%s」 → 다음 글부터 한국어로, "
                               "필요하면 한 줄로 한국어로 다시 알리세요.\n" % short(bad[-1]))
        return 2
    return 0


def mode_stop(data, es):
    texts = turn_texts(es)
    if not texts:
        return 0
    last_tool = -1
    for i, d in enumerate(es):
        if d.get("type") == "assistant" and any(
                isinstance(b, dict) and b.get("type") == "tool_use"
                for b in (d.get("message") or {}).get("content") or []):
            last_tool = i
    final = "\n".join(t for i, t in texts if i > last_tool)
    what = problem(final) if final else None
    if not what:
        return 0
    sid = data.get("session_id") or "s"
    if hc.blocks_used("korean-guard", sid) >= hc.MAX_BLOCKS_PER_TURN:
        return 0
    hc.add_block("korean-guard", sid)
    hc.bump_stat("막음:한국어지킴이")
    hc.record_mistake("한국어지킴이:" + what, short(final))
    sys.stderr.write(MSG + "마지막 글에 %s가 있어요: 「%s」 → 같은 내용을 쉬운 한국어로, 대시 없이 다시 써서 "
                           "사용자에게 보내세요.\n" % (what, short(final)))
    return 2


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    data = hc.read_input()
    if mode == "pre":
        return mode_pre(data)
    path = data.get("transcript_path") or ""
    if not path or not os.path.isfile(path):
        return 0
    if mode == "post":
        return mode_post(data, path)
    if mode == "stop":
        return mode_stop(data, hc.transcript(path))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # 지킴이 고장으로 일을 막지 않는다
