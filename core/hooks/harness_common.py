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


_INPUT = {}


def read_input():
    """훅 입력(JSON). 읽기 전용 판단 등에 쓰려고 기억해 둔다."""
    global _INPUT
    try:
        data = sys.stdin.read()
        _INPUT = json.loads(data) if data.strip() else {}
    except Exception:
        _INPUT = {}
    if not isinstance(_INPUT, dict):
        _INPUT = {}
    return _INPUT


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


def set_names(cfg):
    """harness.json의 set. 이름 하나, 이름 목록(두 세트), 또는 비움/"core"(바닥판만)."""
    raw = cfg.get("set")
    names = raw if isinstance(raw, list) else [raw] if raw else []
    return [n for n in names if isinstance(n, str) and re.fullmatch(r"[A-Za-z0-9_-]+", n) and n != "core"]


def set_dirs(cfg):
    out = []
    for n in set_names(cfg):
        d = os.path.join(HARNESS_ROOT, "sets", n)
        if os.path.isdir(d):
            out.append(d)
    return out


def set_infos(cfg):
    """[(세트 폴더, set.json 내용)]"""
    out = []
    for d in set_dirs(cfg):
        try:
            with open(os.path.join(d, "set.json"), encoding="utf-8") as f:
                out.append((d, json.load(f)))
        except Exception:
            out.append((d, {}))
    return out


def is_unattended(cfg=None):
    """아무도 없이 도는 세션인가.

    환경 변수 HARNESS_UNATTENDED(1/0)가 먼저. 그다음 harness.json 의 unattended:
    true(저장소 전체) 또는 세션 id 목록(["session_..."], 그 세션들만).
    """
    env = os.environ.get("HARNESS_UNATTENDED", "").strip().lower()
    if env in ("1", "true", "yes", "y", "on"):
        return True
    if env in ("0", "false", "no", "n", "off"):
        return False
    cfg = cfg if cfg is not None else load_config()
    val = cfg.get("unattended")
    if val is True:
        return True
    listed = {str(x) for x in val} if isinstance(val, list) else set()
    listed |= ids_from_file(cfg.get("unattended_from"))
    return bool(session_ids() & listed)


def ids_from_file(spec):
    """"CLAUDE.md:실행" 처럼 「파일:이름표」. 그 파일에서 「이름표:」가 든 줄의 session_... id들을 읽는다.

    세션 id를 harness.json 과 CLAUDE.md 두 곳에 따로 적다가 어긋나지 않게, 한 곳(표)에서만 읽는 길.
    """
    if not isinstance(spec, str) or ":" not in spec:
        return set()
    name, label = spec.split(":", 1)
    label = label.strip()
    if not label or os.path.isabs(name) or ".." in re.split(r"[/\\]", name):
        return set()
    out = set()
    pat = re.compile(r"(?:^|[\s|*`(])" + re.escape(label) + r"\s*[:：]\s*(.*)")
    for line in (read_text(os.path.join(project_dir(), name)) or "").splitlines():
        for cell in line.split("|"):  # 표 한 줄에 여러 칸이 있어도 그 이름표 칸의 id만
            m = pat.search(cell)
            if m:
                out.update(re.findall(r"session_[A-Za-z0-9]+", m.group(1)))
    return out


def session_ids():
    """이 세션을 가리키는 id들. 훅 입력의 session_id 와, 클라우드 세션 주소의 id(cse_X 와 같은 session_X)."""
    ids = {str(_INPUT.get("session_id") or ""), os.environ.get("CLAUDE_CODE_SESSION_ID", "")}
    remote = os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID", "")
    if remote:
        ids.add(remote)
        if remote.startswith("cse_"):
            ids.add("session_" + remote[4:])
    ids.discard("")
    return ids


def is_read_only():
    """읽기만 하는 세션이면 프로젝트 파일(실수기록.md, 장치 기록)을 고치지 않는다.

    harness.json "read_only": true, 환경 변수 HARNESS_READ_ONLY=1, 또는 이번 세션의 첫 사용자 말에
    「읽기만」이 들어 있으면 읽기 전용으로 본다.
    """
    if os.environ.get("HARNESS_READ_ONLY", "").lower() in ("1", "true", "yes"):
        return True
    if load_config().get("read_only") is True:
        return True
    path = _INPUT.get("transcript_path") or ""
    try:
        f = open(path, encoding="utf-8")
    except Exception:
        return False
    with f:
        for line in f:  # 첫 사용자 말까지만 읽는다(긴 기록 전체를 읽지 않게)
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict) and is_real_prompt(d):
                c = (d.get("message") or {}).get("content")
                text = c if isinstance(c, str) else " ".join(
                    str(b.get("text", "")) for b in c or [] if isinstance(b, dict))
                # 「읽기만 하지 말고」처럼 반대 뜻은 빼고
                return bool(re.search(r"읽기만(?!\s*하지\s*말|\s*하지마|\s*가능)", text))
    return False


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
    bump_stat("막음:저장소지킴이")
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
MISTAKES_PENDING = "mistakes-pending.md"   # 실수기록.md 로 옮길 줄(state_dir 안)
MISTAKES_LOG = "실수기록.md"                # git에 남는 실수 기록(report 가 씀)


class _Lock:
    """여러 훅이 동시에 같은 파일을 고치지 않게 잠근다(잠글 수 없는 곳에서는 그냥 진행).

    잠금 이름마다 따로 잠근다. 같은 이름을 잠근 채로 또 잠그면 멈추니, 실수 기록과 숫자 기록은 이름이 다르다.
    """

    def __init__(self, name="mistakes"):
        self.name = name

    def __enter__(self):
        self.f = open(os.path.join(state_dir(), self.name + ".lock"), "w")
        self.win = False
        try:
            import fcntl
            fcntl.flock(self.f, fcntl.LOCK_EX)
        except ImportError:
            try:  # 윈도
                import msvcrt
                for _ in range(100):
                    try:
                        msvcrt.locking(self.f.fileno(), msvcrt.LK_LOCK, 1)
                        self.win = True
                        break
                    except OSError:
                        time.sleep(0.05)
            except Exception:
                pass
        except Exception:
            pass
        return self

    def __exit__(self, *a):
        if self.win:
            try:
                import msvcrt
                msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
        self.f.close()


def record_mistake(kind, detail, tool_failure=False):
    """같은 종류가 2번째면 True를 돌려준다.

    Claude의 실수(지킴이가 막음, 완료 조건 실패)는 2번째에 실수기록.md에 적는다.
    도구 고장(시간 초과, 인터넷 끊김, 명령 실패 등)은 실수기록.md에 적지 않고 따로 센다.
    읽기 전용 세션에서는 실수기록.md를 고치지 않는다.
    """
    try:
        with _Lock():
            return _record_mistake(kind, detail, tool_failure)
    except Exception:
        return False


def _record_mistake(kind, detail, tool_failure):
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
    if second and not tool_failure and is_read_only():
        second = False  # 읽기 전용이면 적지 않고, 다음 보통 세션에서 적을 수 있게 남겨 둔다
    if second:
        entry["logged"] = True
        if not tool_failure:
            # git에 들어가는 실수기록.md 는 직접 고치지 않고 모아 둔다(report 가 옮김)
            with open(os.path.join(state_dir(), MISTAKES_PENDING), "a", encoding="utf-8") as f:
                f.write("- %s · %s · %s\n" % (time.strftime("%Y-%m-%d"), kind, detail[:200].replace("\n", " ")))
            bump_stat("실수기록")
    write_atomic(path, json.dumps(data, ensure_ascii=False, indent=1))
    return second


# ---- 장치 숫자 기록 -----------------------------------------------------------
# 막은 횟수·경고·실수·도구 고장을 git에 안 들어가는 state_dir 에 모은다. git에 남는
# .claude/harness-stats.jsonl(하루 한 줄)로 옮기는 것은 report(PR 올릴 때)만 한다.

STATS_PENDING = "stats-pending.json"
STATS_FILE = os.path.join(".claude", "harness-stats.jsonl")
STATS_KEEP_DAYS = 120


def bump_stat(name, n=1):
    try:
        with _Lock("stats"):
            path = os.path.join(state_dir(), STATS_PENDING)
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
            day = data.setdefault(time.strftime("%Y-%m-%d"), {})
            day[name] = int(day.get(name, 0)) + n
            write_atomic(path, json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


def pending_summary():
    """(모아 둔 숫자 날 수, 모아 둔 실수 줄 수)"""
    try:
        with open(os.path.join(state_dir(), STATS_PENDING), encoding="utf-8") as f:
            days = len(json.load(f) or {})
    except Exception:
        days = 0
    lines = len([x for x in (read_text(os.path.join(state_dir(), MISTAKES_PENDING)) or "").splitlines() if x.strip()])
    return days, lines


def flush_mistakes():
    """모아 둔 실수 줄을 git에 남는 실수기록.md 에 옮긴다. 옮긴 줄 수를 돌려준다."""
    with _Lock():
        pending = os.path.join(state_dir(), MISTAKES_PENDING)
        lines = [x for x in (read_text(pending) or "").splitlines() if x.strip()]
        if not lines:
            return 0
        log = os.path.join(project_dir(), MISTAKES_LOG)
        have = set((read_text(log) or "").splitlines())
        new = not os.path.exists(log)
        written = 0
        with open(log, "a", encoding="utf-8") as f:
            if new:
                f.write("# 실수 기록\n\n같은 종류 실수가 2번 나면 하네스가 모아 두었다가 여기에 옮깁니다. "
                        "적힌 것은 장치(훅·검사)로 바꿀 후보입니다.\n\n")
            for x in lines:
                if x not in have:
                    f.write(x + "\n")
                    have.add(x)
                    written += 1
        os.remove(pending)
        return written


def flush_stats():
    """모아 둔 숫자를 git에 남는 파일에 더한다. 옮긴 날 수를 돌려준다(report 만 부름)."""
    if is_read_only():
        return 0
    days = 0
    try:
        with _Lock("stats"):
            pending_path = os.path.join(state_dir(), STATS_PENDING)
            try:
                with open(pending_path, encoding="utf-8") as f:
                    pending = json.load(f)
            except Exception:
                return 0
            if not pending:
                return 0
            days = len(pending) if isinstance(pending, dict) else 0
            path = os.path.join(project_dir(), STATS_FILE)
            rows = {}
            for line in (read_text(path) or "").splitlines():
                try:
                    r = json.loads(line)
                    if isinstance(r, dict) and isinstance(r.get("date"), str) and isinstance(r.get("counts"), dict):
                        rows[r["date"]] = {k: v for k, v in r["counts"].items() if isinstance(v, int)}
                except ValueError:
                    pass
            for date, counts in pending.items() if isinstance(pending, dict) else []:
                if not isinstance(counts, dict):
                    continue
                cur = rows.setdefault(str(date), {})
                for k, v in counts.items():
                    if isinstance(v, int):
                        cur[k] = int(cur.get(k, 0)) + v
            keep = sorted(rows)[-STATS_KEEP_DAYS:]
            text = "".join(json.dumps({"date": d, "counts": dict(sorted(rows[d].items()))}, ensure_ascii=False) + "\n"
                           for d in keep)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            write_atomic(path, text)
            os.remove(pending_path)
    except Exception:
        return 0
    return days


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


def log_state(name, text):
    """git에 안 들어가는 state_dir 의 기록 파일에 날짜와 함께 덧붙인다."""
    try:
        with open(os.path.join(state_dir(), name), "a", encoding="utf-8") as f:
            f.write("## %s\n%s\n" % (time.strftime("%Y-%m-%d %H:%M"), text))
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
