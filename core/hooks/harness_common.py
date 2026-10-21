"""하네스 훅들이 같이 쓰는 도구.

원칙
- 훅이 스스로 고장 나면 조용히 통과시킨다(fail open). 일부러 막을 때만 막는다.
- 바깥 패키지 없이 파이썬 기본 기능만 쓴다.
"""
import hashlib
import json
import os
import re
import sys
import time

WORK_GUARD = os.path.join(".claude", "hooks", "work_guard.py")  # 프로젝트 자체 지킴이(있을 때만)
WORK_GUARD_LOG = "work-guard-warnings.jsonl"  # 답 끝 경고를 날짜별로 쌓는 곳(state_dir 안)
WORK_GUARD_KEEP_DAYS = 30
HARNESS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def project_dir():
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def state_dir():
    d = os.path.join(project_dir(), ".claude", "harness-state")
    os.makedirs(d, exist_ok=True)
    return d


def read_input():
    try:
        data = sys.stdin.read()
        return json.loads(data) if data.strip() else {}
    except Exception:
        return {}


def load_config():
    """프로젝트의 .claude/harness.json. 없으면 빈 설정."""
    path = os.path.join(project_dir(), ".claude", "harness.json")
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def harness_version():
    try:
        with open(os.path.join(HARNESS_ROOT, "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return "?"


def set_dir(cfg):
    name = cfg.get("set") or ""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        return None
    d = os.path.join(HARNESS_ROOT, "sets", name)
    return d if os.path.isdir(d) else None


def load_set_info(cfg):
    d = set_dir(cfg)
    if not d:
        return {}
    try:
        with open(os.path.join(d, "set.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def progress_path(cfg):
    return os.path.join(project_dir(), cfg.get("progress_file") or "진행상황.md")


def read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def section(md, title):
    """'## 제목' 아래부터 다음 '## ' 전까지의 글."""
    if not md:
        return ""
    lines = md.splitlines()
    out, inside = [], False
    for line in lines:
        if line.startswith("## "):
            if inside:
                break
            inside = line[3:].strip().startswith(title)
            continue
        if inside:
            out.append(line)
    return "\n".join(out).strip()


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.flush()


def deny(reason, rule):
    """PreToolUse에서 도구 사용을 막는다. 막은 일은 실수 기록에도 남긴다."""
    record_mistake("막힘:" + rule, reason)
    emit({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    })
    sys.exit(0)


# ---- 실수 기록 -------------------------------------------------------------

MISTAKE_FILE = "mistakes.json"


class _Lock:
    """여러 훅이 동시에 실수 기록을 고치지 않게 잠근다(잠글 수 없는 곳에서는 그냥 진행)."""

    def __enter__(self):
        self.f = open(os.path.join(state_dir(), "mistakes.lock"), "w")
        try:
            import fcntl
            fcntl.flock(self.f, fcntl.LOCK_EX)
        except Exception:
            pass
        return self

    def __exit__(self, *a):
        self.f.close()


def record_mistake(kind, detail):
    """같은 종류가 2번째면 실수기록.md에 적고 True를 돌려준다."""
    try:
        with _Lock():
            return _record_mistake(kind, detail)
    except Exception:
        return False


def _record_mistake(kind, detail):
    path = os.path.join(state_dir(), MISTAKE_FILE)
    data = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    entry = data.get(kind) or {"count": 0, "logged": False}
    entry["count"] += 1
    entry["last"] = detail[:300]
    data[kind] = entry
    second = entry["count"] >= 2 and not entry["logged"]
    if second:
        entry["logged"] = True
        log = os.path.join(project_dir(), "실수기록.md")
        new = not os.path.exists(log)
        with open(log, "a", encoding="utf-8") as f:
            if new:
                f.write("# 실수 기록\n\n같은 종류 실수가 2번 나면 하네스가 여기에 적습니다. "
                        "적힌 것은 장치(훅·검사)로 바꿀 후보입니다.\n\n")
            f.write("- %s · %s · %s\n" % (time.strftime("%Y-%m-%d"), kind, detail[:200].replace("\n", " ")))
    write_atomic(path, json.dumps(data, ensure_ascii=False, indent=1))
    return second


def kind_of_error(tool_name, error):
    """오류 글에서 숫자·경로를 지워 '종류'를 만든다."""
    first = (error or "").strip().splitlines()[0] if (error or "").strip() else ""
    first = re.sub(r"(/[^\s:'\"]+)+", "<경로>", first)
    first = re.sub(r"\d+", "N", first)[:80]
    h = hashlib.sha1(first.encode("utf-8")).hexdigest()[:8]
    return "%s:%s:%s" % (tool_name or "도구", h, first)


# ---- 한 답에서 막은 횟수 ------------------------------------------------------
# 훅들이 동시에 돌 수 있어서, 세션·훅마다 따로 파일 하나를 쓴다(서로 덮어쓰지 않게).

MAX_BLOCKS_PER_TURN = 2


def _safe(name):
    return re.sub(r"[^\w\-]", "_", name or "x")


def _blocks_path(name, session):
    return os.path.join(state_dir(), "blocks-%s-%s.txt" % (_safe(session), _safe(name)))


def blocks_used(name, session):
    try:
        with open(_blocks_path(name, session), encoding="utf-8") as f:
            return int(f.read().strip() or 0)
    except Exception:
        return 0


def add_block(name, session):
    write_atomic(_blocks_path(name, session), str(blocks_used(name, session) + 1))


def reset_blocks(session):
    """사용자가 새로 말하면(UserPromptSubmit) 그 세션의 횟수를 비운다."""
    prefix = "blocks-%s-" % _safe(session)
    for f in os.listdir(state_dir()):
        if f.startswith(prefix):
            try:
                os.remove(os.path.join(state_dir(), f))
            except OSError:
                pass


def write_atomic(path, text):
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def transcript(path):
    """대화 기록(JSONL)을 줄마다 읽는다. 못 읽으면 빈 목록."""
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        pass
    return out


def is_real_prompt(d):
    """사용자가 직접 보낸 말(도구 결과가 아닌 user 기록)."""
    if d.get("type") != "user" or d.get("isSidechain") or d.get("isMeta") or d.get("isCompactSummary"):
        return False
    c = (d.get("message") or {}).get("content")
    if isinstance(c, str):
        return _human_text(c)
    if isinstance(c, list):
        return any(isinstance(b, dict) and b.get("type") == "text" and _human_text(str(b.get("text", "")))
                   for b in c)
    return False


NOT_HUMAN = ("<", "Stop hook feedback", "This session is being continued", "[Request interrupted")


def _human_text(t):
    return not t.lstrip().startswith(NOT_HUMAN)


def turn_start(es):
    """이번 답이 시작된 자리(마지막 사용자 말)."""
    start = 0
    for i, d in enumerate(es):
        if is_real_prompt(d):
            start = i
    return start


def turn_edited_files(es):
    """이번 답에서 Write·Edit 도구로 바꾼 파일 경로들."""
    out = []
    for d in es[turn_start(es):]:
        if d.get("type") != "assistant" or d.get("isSidechain"):
            continue
        for b in (d.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use" and \
                    b.get("name") in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
                inp = b.get("input") or {}
                p = inp.get("file_path") or inp.get("notebook_path")
                if p:
                    out.append(p)
    return out


def front_lines(md):
    """진행상황의 앞 칸(지금 상태·꼭 기억·다음 할 일 등) 줄 수."""
    n, inside = 0, False
    for line in (md or "").splitlines():
        if line.startswith("## "):
            inside = bool(re.search(r"지금 상태|꼭 기억|남은 과제|다음 할 일|바로 할 일|완료 조건", line))
        if inside:
            n += 1
    return n


def changed_files(exts=None):
    """마지막 커밋 뒤로 바뀌었거나 새로 생긴 파일(프로젝트 기준 경로). git이 없으면 빈 목록."""
    import subprocess

    def git(*args):
        try:
            p = subprocess.run(["git", *args], cwd=project_dir(), capture_output=True, text=True, timeout=20)
        except Exception:
            return None
        return p.stdout if p.returncode == 0 else None

    top = (git("rev-parse", "--show-toplevel") or "").strip()
    if not top:
        return []
    out = set()
    # 첫 커밋 전이면 diff는 실패하지만 새 파일 목록은 쓴다. 두 목록 모두 저장소 뿌리 기준 경로.
    for listing in (git("diff", "--name-only", "-z", "HEAD"),
                    git("ls-files", "--others", "--exclude-standard", "--full-name", "-z", ":/")):
        out.update(x for x in (listing or "").split("\0") if x)
    proj = os.path.realpath(project_dir())
    res = []
    for f in sorted(out):
        full = os.path.realpath(os.path.join(top, f))
        if not full.startswith(proj + os.sep) or not os.path.isfile(full):
            continue
        rel = os.path.relpath(full, proj).replace(os.sep, "/")
        if rel.startswith(".claude/"):
            continue
        if exts is None or os.path.splitext(rel)[1].lower() in exts:
            res.append(rel)
    return res
