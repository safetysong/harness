#!/usr/bin/env python3
"""끝 점검(Stop).

- 이번 답에서 파일을 바꿨는데 진행상황.md를 갱신하지 않았으면 갱신하라고 막는다.
- 진행상황.md '## 완료 조건'에서 '확인: `명령`'이 붙은 줄의 명령을 돌린다.
- 세트 검사기(set.json의 checks)도 돌린다.
- 하나라도 실패하면 끝내지 못하게 막는다. 단, 한 답에서 두 번까지만 막는다.
- 지난번 통과 뒤로 바뀐 것이 없으면 다시 돌리지 않는다.
- 프로젝트에 .claude/hooks/work_guard.py 가 있으면 답 하나 끝날 때 한 번 돌린다(끝 점검이 막은 뒤
  다시 멈출 때는 안 돌림). 지킴이가 막으려 하면(끝 번호 2, 또는 {"decision": "block"}) 여기서는
  막지 않고 경고만 남기며, 날짜와 내용을 .claude/harness-state/work-guard-warnings.jsonl 에 쌓는다(30일 보관).
"""
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_common as hc  # noqa: E402

CMD_RE = re.compile(r"확인\s*:\s*`([^`]+)`")
TIMEOUT = int(os.environ.get("HARNESS_CHECK_TIMEOUT", "180"))
BUDGET = int(os.environ.get("HARNESS_CHECK_BUDGET", "480"))  # 훅 자체 제한(600초)보다 짧게


def commands(md):
    out = []
    for line in hc.section(md, "완료 조건").splitlines():
        m = CMD_RE.search(line)
        if line.lstrip().startswith("- [") and m and "{{" not in m.group(1):
            label = re.sub(r"^\s*- \[[ xX]\]\s*", "", line[:m.start()]).strip(" ·:(")
            out.append((label or m.group(1), m.group(1)))
    return out


def set_checks(cfg):
    out = []
    for sd, info in hc.set_infos(cfg):
        for rel in info.get("checks", []):
            path = os.path.join(sd, rel)
            if os.path.isfile(path):
                out.append(("세트 검사: %s" % os.path.basename(rel), [sys.executable, path]))
    return out


def fingerprint():
    def git(*args):
        try:
            p = subprocess.run(["git", *args], cwd=hc.project_dir(), capture_output=True, timeout=20)
        except Exception:
            return None
        return p.stdout if p.returncode == 0 else None
    head = git("rev-parse", "HEAD")
    if head is None:
        return None
    # 하네스가 스스로 쓰는 파일(장치 숫자 기록, 실수기록.md)이 바뀐 것만으로는 다시 검사하지 않는다
    own = [hc.STATS_FILE.replace(os.sep, "/"), "실수기록.md"]
    skip = [":(exclude)" + f for f in own]
    parts = [head, git("status", "--porcelain", "--", ".", *skip), git("diff", "HEAD", "--", ".", *skip)]
    if any(p is None for p in parts):
        return None
    for name in sorted(git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0")):
        if not name or name.startswith(b".claude/harness-state/") or name.decode("utf-8", "replace") in own:
            continue
        try:
            with open(os.path.join(hc.project_dir(), name.decode("utf-8", "replace")), "rb") as f:
                parts.append(name + f.read(2_000_000))
        except OSError:
            pass
    return hashlib.sha256(b"\0".join(parts)).hexdigest()


def run(label, cmd, deadline):
    left = int(deadline - time.time())
    if left <= 5:
        return "- %s (시간이 모자라 돌리지 못함. 확인 명령을 더 짧게 해 주세요)" % label
    limit = min(TIMEOUT, left)
    try:
        p = subprocess.run(cmd, shell=isinstance(cmd, str), cwd=hc.project_dir(), capture_output=True,
                           text=True, timeout=limit)
        if p.returncode == 0:
            return None
        tail = "\n".join((p.stdout + p.stderr).strip().splitlines()[-15:])
        return "- %s (끝 번호 %d)\n%s" % (label, p.returncode, tail)
    except subprocess.TimeoutExpired:
        return "- %s (%d초 넘게 걸려 멈춤)" % (label, limit)


def progress_missing(data, cfg):
    """이번 답에서 파일을 바꿨는데 진행상황 파일은 안 바꿨으면 그 파일 목록을 돌려준다."""
    es = hc.transcript(data.get("transcript_path") or "")
    edited = hc.turn_edited_files(es)
    progress = os.path.basename(hc.progress_path(cfg))
    skip = (progress, "실수기록.md")
    others = [p for p in edited if os.path.basename(p) not in skip and "harness-state" not in p]
    if not others or any(os.path.basename(p) == progress for p in edited):
        return []
    return others


def guard_said(p):
    """지킴이가 막으려 했으면 그 글, 아니면 빈 글."""
    if p.returncode == 2:
        return p.stderr.strip() or p.stdout.strip() or "이유 없이 막으려 함"
    if p.returncode == 0 and p.stdout.strip().startswith("{"):
        try:
            out = json.loads(p.stdout)
        except ValueError:
            return ""
        if isinstance(out, dict) and out.get("decision") == "block":
            return str(out.get("reason") or "이유 없이 막으려 함")
    return ""


def save_warning(data, text):
    path = os.path.join(hc.state_dir(), hc.WORK_GUARD_LOG)
    since = (datetime.date.today() - datetime.timedelta(days=hc.WORK_GUARD_KEEP_DAYS)).isoformat()
    keep = []
    for line in (hc.read_text(path) or "").splitlines():
        try:
            d = json.loads(line).get("date")
            if isinstance(d, str) and d >= since:
                keep.append(line)
        except Exception:
            pass
    keep.append(json.dumps({"date": datetime.date.today().isoformat(),
                            "session": data.get("session_id") or "s", "message": text}, ensure_ascii=False))
    hc.write_atomic(path, "\n".join(keep) + "\n")


def work_guard_warning(data):
    """프로젝트 자체 지킴이를 돌려, 막으려 했으면 경고 한 줄을 돌려준다(여기서는 막지 않음)."""
    path = os.path.join(hc.project_dir(), hc.WORK_GUARD)
    if data.get("stop_hook_active") or not os.path.isfile(path):
        return None
    try:
        p = subprocess.run([sys.executable, path], input=json.dumps(data, ensure_ascii=False),
                           capture_output=True, text=True, cwd=hc.project_dir(), timeout=30,
                           env=dict(os.environ, HARNESS_EVENT="Stop"))
    except Exception:
        return None
    text = " ".join(guard_said(p).split())[:300]
    if not text:
        return None
    try:
        save_warning(data, text)
    except OSError:
        pass
    hc.bump_stat("경고:업무지킴이")
    return "[업무 지킴이 경고] " + text


def check(data):
    deadline = time.time() + BUDGET
    warning = work_guard_warning(data)
    session = data.get("session_id") or "s"
    cfg = hc.load_config()
    failures = []
    # 프로젝트에 옛 「진행상황 안 적음」 검사 장치가 있으면 harness.json "progress_check": false 로 끈다
    missing = progress_missing(data, cfg) if cfg.get("progress_check", True) is not False else []
    if missing:
        failures.append("- 파일(%s)을 바꿨는데 %s를 갱신하지 않았어요. 한 일·결정·사용자 지시 원문·다음 할 일을 적어 주세요."
                        % (", ".join(os.path.basename(p) for p in missing[:3]),
                           os.path.basename(hc.progress_path(cfg))))
    md = hc.read_text(hc.progress_path(cfg))
    checks = (commands(md) if md else []) + set_checks(cfg)
    fp = fingerprint() if checks else None
    fp_file = os.path.join(hc.state_dir(), "last-pass.txt")
    if checks and not (fp and hc.read_text(fp_file) == fp):
        failed = [f for f in (run(label, cmd, deadline) for label, cmd in checks) if f]
        if not failed and fp:
            hc.write_atomic(fp_file, fp)
        failures += failed
    note = "\n" + warning if warning else ""
    if not failures:
        if warning:
            hc.emit({"systemMessage": warning})
        return
    used = hc.blocks_used("stop-check", session)
    if used >= hc.MAX_BLOCKS_PER_TURN:
        hc.emit({"systemMessage": "끝 점검이 이번 답에서 %d번 막았는데도 남은 것이 있어요. 더 막지 않고 끝냅니다. "
                                  "사용자 확인이 필요해요.\n%s%s" % (used, "\n".join(failures), note)})
        return
    hc.add_block("stop-check", session)
    hc.bump_stat("막음:끝점검")
    hc.record_mistake("완료조건실패" if not missing else "진행상황안적음", failures[0])
    hc.emit({"decision": "block", **({"systemMessage": warning} if warning else {}),
             "reason": "아직 끝낼 수 없어요(끝 점검 %d/%d번째 막음). 고친 뒤 끝내 주세요.\n%s%s"
                       % (used + 1, hc.MAX_BLOCKS_PER_TURN, "\n".join(failures), note)})


def main():
    try:
        check(hc.read_input())
    finally:
        hc.flush_stats()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
