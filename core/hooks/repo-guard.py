#!/usr/bin/env python3
"""저장소 지킴이(PreToolUse): 한 세션에는 저장소 하나.

저장소가 여러 개 붙은 클라우드 세션은 다시 뜰 때 어느 저장소의 훅도 읽지 않는다(공식 문서).
그래서 add_repo는 언제나 사용자에게 허락 창을 띄운다. 아무도 없는 예약 실행 세션은 허락 창에서
멈추므로 harness.json에 "unattended": true 를 두면 허락 창 대신 막는다. 세션마다 다르게 하려면
환경 변수 HARNESS_UNATTENDED=1(막기) 또는 0(허락 창)이 harness.json 보다 앞선다.

harness.json에 repo(예 "owner/name")를 정하면 아래도 같은 방식으로 다룬다.
- 다른 저장소를 가리키는 GitHub 도구(owner/repo가 다름)
- repo: 조건 없이 GitHub 전체를 뒤지는 검색
- 다른 저장소 git clone, git remote add / set-url, gh repo clone (Bash와 PowerShell 도구 모두)
"""
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_common as hc  # noqa: E402

SEARCH_TOOLS = ("search_code", "search_issues", "search_pull_requests", "search_commits")


MODE = "ask"


def stop(reason, rule):
    if MODE == "deny":
        hc.deny(reason, rule)
    hc.bump_stat("물음:저장소지킴이")
    hc.emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                    "permissionDecisionReason": reason}})
    sys.exit(0)


def same(repo, owner, name):
    want = repo.lower().split("/")
    return [owner.lower(), name.lower()] == want


def commands(cmd):
    """셸 명령 글을 낱말 목록들로 나눈다. 따옴표 안 글과 heredoc 본문은 명령으로 보지 않는다."""
    lines, out, skip_until = cmd.splitlines(), [], None
    for line in lines:
        if skip_until is not None:
            if line.strip() == skip_until:
                skip_until = None
            continue
        h = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", line)
        if h:
            skip_until = h.group(1)
            line = line[:h.start()]
        try:
            lex = shlex.shlex(line, posix=True, punctuation_chars=";&|()")
            lex.whitespace_split = True
            toks = list(lex)
        except ValueError:
            toks = line.split()
        cur = []
        for t in toks:
            if t and set(t) <= set(";&|()"):
                if cur:
                    out.append(cur)
                cur = []
            else:
                cur.append(t)
        if cur:
            out.append(cur)
    return out


def repo_of(arg):
    """주소나 owner/name 글에서 (owner, name). 깃허브가 아닌 주소(프록시 등)도 경로 끝 두 칸을 본다."""
    a = re.sub(r"\.git/?$", "", arg.strip().rstrip("/"))
    m = re.search(r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$", a.replace(":", "/"))
    return (m.group(1), m.group(2)) if m else None


def base(word):
    w = word.replace("\\", "/").split("/")[-1].lower()
    return w[:-4] if w.endswith(".exe") else w


def check_bash(cmd, repo):
    """명령의 첫 낱말이 git/gh 일 때만 본다(echo·grep·커밋 메시지 안의 글은 무시)."""
    for words in commands(cmd):
        while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):
            words = words[1:]  # 앞의 환경 변수 설정(FOO=1 git ...)
        if not words:
            continue
        prog, args = base(words[0]), words[1:]
        # git -C 폴더, -c 키=값 같은 앞 옵션 건너뛰기
        while prog == "git" and args and args[0].startswith("-"):
            args = args[2:] if args[0] in ("-C", "-c") else args[1:]
        target, what = None, None
        if prog == "git" and args[:1] == ["clone"]:
            rest = [x for x in args[1:] if not x.startswith("-")]
            target, what = (rest[0] if rest else None), "git clone"
        elif prog == "git" and args[:1] == ["remote"] and len(args) > 1 and args[1] in ("add", "set-url"):
            rest = [x for x in args[2:] if not x.startswith("-")]
            target, what = (rest[1] if len(rest) > 1 else None), "git remote " + args[1]
        elif prog == "gh" and args[:2] == ["repo", "clone"]:
            target, what = (args[2] if len(args) > 2 else None), "gh repo clone"
        else:
            continue
        found = repo_of(target) if target else None
        if not found:
            stop("저장소 지킴이: '%s' 명령이 어느 저장소를 가리키는지 알 수 없어요. 이 프로젝트는 %s 하나만 다룹니다."
                 % (what, repo), "저장소-알수없음")
        if not same(repo, *found):
            stop("저장소 지킴이: %s/%s 는 이 프로젝트 저장소(%s)가 아니에요. 이 프로젝트는 저장소 하나만 다뤄요."
                 % (found[0], found[1], repo), "다른저장소")


def main():
    global MODE
    data = hc.read_input()
    cfg = hc.load_config()
    MODE = "deny" if hc.is_unattended(cfg) else "ask"
    tool = data.get("tool_name") or ""
    ti = data.get("tool_input") or {}
    if tool.endswith("__add_repo"):
        stop("이 세션에 저장소(%s)를 더 붙이려 합니다. 붙이면 이 세션이 다시 뜰 때 자동 장치가 모두 꺼집니다. "
             "보통은 그 저장소 세션에 맡기거나 그 저장소 하나로 새 세션을 만듭니다. "
             "꼭 이 세션에 붙여야 할 때만 허락하세요." % (ti.get("repo") or "이름 모름"), "저장소붙이기")
    repo = (cfg.get("repo") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        return  # 저장소를 정하지 않은 프로젝트는 여기까지만
    if tool.startswith("mcp__github__"):
        short = tool[len("mcp__github__"):]
        if short in SEARCH_TOOLS:
            q = str(ti.get("query", ""))
            found = re.findall(r"repo:([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", q)
            if not found or any(not same(repo, o, n) for o, n in found):
                stop("저장소 지킴이: 검색어에 repo:%s 를 넣어 이 저장소 안에서만 찾아 주세요." % repo, "전체검색")
            return
        owner, name = ti.get("owner"), ti.get("repo")
        if owner and name and not same(repo, str(owner), str(name)):
            stop("저장소 지킴이: %s/%s 는 이 프로젝트 저장소(%s)가 아니에요. 이 프로젝트는 저장소 하나만 다뤄요." % (owner, name, repo),
                    "다른저장소")
        return
    if tool in ("Bash", "PowerShell"):  # 윈도는 PowerShell 도구로 같은 명령을 쓸 수 있다
        check_bash(str(ti.get("command", "")), repo)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        pass
