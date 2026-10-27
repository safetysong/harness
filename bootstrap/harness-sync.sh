#!/usr/bin/env bash
# 하네스 받기 장치: 각 프로젝트의 .claude/harness-sync.sh 로 넣는 단 하나의 파일.
#
#   harness-sync.sh start           세션 시작 훅. 새 판을 받아 본 뒤(startup일 때만) 시작 안내를 보여 준다.
#   harness-sync.sh sync            새 판 받기만 한다.
#   harness-sync.sh upgrade         새 판을 받고, 이 받기 장치와 settings.json 자리까지 최신으로 맞춘다.
#   harness-sync.sh hook <이름> [모드]  .claude/harness/core/hooks/<이름>.py 를 돌린다.
#
# 안전 규칙
# - 받은 묶음의 sha256이 원본 main의 signed/approved.json 과 다르면 받지 않고 지금 판을 쓴다.
# - 네트워크가 안 되면 조용히 지금 판을 쓴다.
# - 파이썬이나 훅 파일이 없거나 훅이 고장 나면 일을 막지 않고 통과시킨다(fail open).
#   훅이 일부러 막을 때(끝 번호 2)만 막는다.
set -u

PROJECT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
ROOT="${HARNESS_ROOT:-$PROJECT/.claude/harness}"
STATE="$PROJECT/.claude/harness-state"

find_python() {
  for p in python3 python py; do  # py: 윈도 파이썬 실행기
    if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>/dev/null; then
      echo "$p"; return 0
    fi
  done
  return 1
}

PY="$(find_python || true)"
# 한국어 윈도는 기본 글자판이 cp949라 훅 입력·출력(UTF-8 JSON)이 깨진다. 파이썬을 늘 UTF-8로 돌린다.
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8

run_hook() {
  # $1 이름, $2 모드(없어도 됨), 입력은 $INPUT
  local script="$ROOT/core/hooks/$1.py"
  [ -n "$PY" ] && [ -f "$script" ] || return 0
  local code=0
  printf '%s' "$INPUT" | "$PY" "$script" ${2:+"$2"} || code=$?
  [ "$code" -eq 2 ] && return 2
  if [ "$1" = "korean-guard" ] && [ "${2:-}" = "pre" ] && [ -f "$PROJECT/.claude/hooks/work_guard.py" ]; then
    # 프로젝트에 자체 지킴이가 있으면 한국어 점검을 통과한 뒤 같이 돌린다
    code=0
    printf '%s' "$INPUT" | HARNESS_EVENT=PreToolUse "$PY" "$PROJECT/.claude/hooks/work_guard.py" || code=$?
    [ "$code" -eq 2 ] && return 2
  fi
  return 0
}

do_sync() {
  [ -n "$PY" ] || { mkdir -p "$STATE"; echo "하네스 받기 꺼짐(파이썬 없음)" > "$STATE/sync-status.txt"; return 0; }
  [ -n "${HARNESS_ROOT:-}" ] && return 0   # 플러그인으로 쓸 때는 플러그인이 판을 관리
  mkdir -p "$STATE"
  PROJECT="$PROJECT" ROOT="$ROOT" STATE="$STATE" "$PY" - <<'PYEOF' || true
import hashlib, json, os, re, shutil, sys, tarfile, tempfile, urllib.request

project, root, state = os.environ["PROJECT"], os.environ["ROOT"], os.environ["STATE"]
status_file = os.path.join(state, "sync-status.txt")


def status(msg):
    with open(status_file, "w", encoding="utf-8") as f:
        f.write(msg + "\n")
    sys.exit(0)


try:
    with open(os.path.join(project, ".claude", "harness.json"), encoding="utf-8") as f:
        cfg = json.load(f)
except Exception:
    status("하네스 받기 꺼짐(.claude/harness.json 없음)")
raw = cfg.get("set")
names = raw if isinstance(raw, list) else [raw] if raw else []
source = cfg.get("source", "safetysong/harness")
if (len(names) > 3 or not all(isinstance(n, str) and re.fullmatch(r"[A-Za-z0-9_-]+", n) for n in names)
        or not isinstance(source, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source)):
    status("하네스 받기 꺼짐(harness.json의 set 또는 source가 이상함)")
names = [n for n in names if n != "core"] or ["core"]  # 세트가 없으면 바닥판만
set_name = " ".join(names)
base = os.environ.get("HARNESS_SOURCE_URL") or "https://raw.githubusercontent.com/%s/main" % source


def local_version():
    try:
        with open(os.path.join(root, "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return None


def fetch(url, limit=5_000_000):
    with urllib.request.urlopen(url, timeout=6) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("너무 큼")
    return data


now = local_version()
try:
    approved = json.loads(fetch(base + "/signed/approved.json"))
    new = approved["version"]
    entries = [approved["bundles"][n] for n in names]
except KeyError:
    status("경고: 승인 목록에 세트 %s 가 없어요. 지금 판 %s 유지" % (set_name, now or "없음"))
except Exception:
    status("새 판 확인 못 함(네트워크). 지금 판 %s 유지" % (now or "없음"))

active_set = None
try:
    with open(os.path.join(root, "SET"), encoding="utf-8") as f:
        active_set = f.read().strip()
except Exception:
    pass
if now == new and active_set == set_name:
    status("최신 판 %s" % now)
blobs = []
for entry in entries:
    file_name = str(entry.get("file", ""))
    if not re.fullmatch(r"dist/[A-Za-z0-9_.-]+\.tar", file_name):
        status("경고: 승인 목록의 묶음 이름이 이상해요. 지금 판 %s 유지" % (now or "없음"))
    try:
        blob = fetch(base + "/" + file_name)
    except Exception:
        status("새 판 %s 받기 실패(네트워크). 지금 판 %s 유지" % (new, now or "없음"))
    if hashlib.sha256(blob).hexdigest() != str(entry.get("sha256", "")).lower():
        status("경고: 받은 묶음이 승인 목록(해시)과 달라 받지 않았어요. 지금 판 %s 유지" % (now or "없음"))
    blobs.append(blob)

parent = os.path.dirname(root)
os.makedirs(parent, exist_ok=True)
tmp = tempfile.mkdtemp(prefix=".harness-new-", dir=parent)
try:
    out = os.path.join(tmp, "x")
    for i, blob in enumerate(blobs):  # 세트가 둘이면 묶음 둘을 한 곳에 푼다(바닥판 파일은 같음)
        path = os.path.join(tmp, "b%d.tar" % i)
        with open(path, "wb") as f:
            f.write(blob)
        with tarfile.open(path) as t:
            for m in t.getmembers():
                n = m.name
                if not (m.isfile() or m.isdir()) or n.startswith(("/", "\\")) or ".." in re.split(r"[/\\]", n):
                    raise ValueError("이상한 파일 " + n)
            if hasattr(tarfile, "data_filter"):
                t.extractall(out, filter="data")
            else:
                t.extractall(out)
    # 묶음 안 파일 시각은 1970-01-01로 고정돼 있다. 그대로 두면 크기가 같은 파일(VERSION 등)이 바뀌어도
    # git이 못 알아챌 수 있어서, 푼 파일 시각을 지금으로 맞춘다.
    for d, _, files in os.walk(out):
        for name in files:
            os.utime(os.path.join(d, name), None)
    with open(os.path.join(out, "VERSION"), encoding="utf-8") as f:
        if f.read().strip() != new:
            raise ValueError("판 번호가 다름")
    with open(os.path.join(out, "SET"), "w", encoding="utf-8") as f:
        f.write(set_name + "\n")
    old = None
    if os.path.exists(root):
        old = os.path.join(tmp, "old")
        os.rename(root, old)
    try:
        os.rename(out, root)
    except Exception:
        if old:
            os.rename(old, root)  # 바꾸다 실패하면 옛 판을 되돌려 놓는다
        raise
    for n in names:
        agents = os.path.join(root, "sets", n, "agents")
        if not os.path.isdir(agents):
            continue
        dest = os.path.join(project, ".claude", "agents")
        os.makedirs(dest, exist_ok=True)
        for name in os.listdir(agents):
            try:
                shutil.copyfile(os.path.join(agents, name), os.path.join(dest, name))
            except OSError:
                pass  # 에이전트 복사 실패는 하네스를 멈추지 않는다
except Exception as e:
    status("경고: 새 판 %s 풀기 실패(%s). 지금 판 %s 유지" % (new, e, now or "없음"))
finally:
    shutil.rmtree(tmp, ignore_errors=True)
status("새 판 %s 받음(전 판 %s). 훅은 이번 시작부터, settings 바뀐 것은 다시 설치한 뒤 다음 시작부터 켜짐. "
       "바뀐 .claude/harness 는 커밋에 같이 넣기" % (new, now or "없음"))
PYEOF
}

cmd="${1:-}"
case "$cmd" in
  start)
    INPUT="$(cat 2>/dev/null || true)"
    case "$INPUT" in
      *'"source"'*'"startup"'*|'') do_sync ;;
    esac
    if [ -z "$PY" ]; then
      echo "[장치 상태] 파이썬(3.8 이상)을 찾지 못해 하네스 장치가 모두 꺼져 있어요. 파이썬을 깔면 다음 시작부터 켜져요."
      exit 0
    fi
    run_hook session-start || true
    exit 0
    ;;
  sync)
    do_sync
    cat "$STATE/sync-status.txt" 2>/dev/null
    exit 0
    ;;
  upgrade)
    if [ -n "${HARNESS_ROOT:-}" ]; then
      echo "플러그인으로 쓰는 중이라 upgrade는 필요 없어요(플러그인 업데이트로 바뀜)."
      exit 0
    fi
    [ -n "$PY" ] || { echo "파이썬(3.8 이상)을 찾지 못해 upgrade를 못 해요."; exit 1; }
    rm -f "$STATE/sync-status.txt"  # 지난 결과를 이번 결과로 잘못 읽지 않게
    do_sync
    cat "$STATE/sync-status.txt" 2>/dev/null
    case "$(cat "$STATE/sync-status.txt" 2>/dev/null)" in
      *"받음("*|*"최신 판"*) ;;
      *) echo "새 판을 받지 못해 받기 장치와 settings는 그대로 둡니다."; exit 1 ;;
    esac
    new="$ROOT/bootstrap/harness-sync.sh"
    self="$PROJECT/.claude/harness-sync.sh"
    if [ -f "$new" ] && ! cmp -s "$new" "$self" 2>/dev/null; then
      # 돌고 있는 자기 자신을 덮어쓰지 않게, 새 파일을 만든 뒤 이름만 바꾼다
      cp "$new" "$self.new" && mv "$self.new" "$self" && echo "받기 장치(.claude/harness-sync.sh)를 새 판으로 바꿨어요."
    fi
    "$PY" "$ROOT/core/tools/merge_settings.py" "$PROJECT" || exit 1
    echo "다 됐어요. 바뀐 settings는 다음 세션 시작부터 켜져요. 바뀐 파일은 커밋에 같이 넣어 주세요."
    exit 0
    ;;
  hook)
    INPUT="$(cat 2>/dev/null || true)"
    [ -n "${2:-}" ] || exit 0
    case "$2" in *[!A-Za-z0-9_-]*) exit 0 ;; esac
    run_hook "$2" "${3:-}"
    exit $?
    ;;
  *)
    echo "쓰는 법: harness-sync.sh start | sync | upgrade | hook <이름> [모드]" >&2
    exit 0
    ;;
esac
