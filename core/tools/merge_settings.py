#!/usr/bin/env python3
"""하네스 settings를 프로젝트 .claude/settings.json 에 합친다(설치 4번, upgrade가 부름).

    python3 .claude/harness/core/tools/merge_settings.py [프로젝트 폴더]

- 기존 내용은 지우지 않는다. 금지 목록은 합집합.
- 훅은 예전 하네스 훅(harness-sync.sh 를 부르는 것)만 빼고 새 판 훅을 붙인다(다시 해도 두 번 안 들어감).
- 프로젝트의 다른 Stop 훅이 「진행상황」을 검사하면 harness.json 에 "progress_check": false 를 넣어
  두 장치가 같은 일로 두 번 막지 않게 한다. 옛 장치 등록을 빼는 것은 사용자에게 물어본 뒤 사람이 한다.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.dirname(HERE)


def is_harness(group):
    return any("harness-sync.sh" in str(h.get("command", "")) for h in group.get("hooks", []))


def scripts_in(command, project):
    """훅 명령에서 프로젝트 안 스크립트 파일 경로를 뽑는다.

    "$CLAUDE_PROJECT_DIR"/.claude/hooks/x.py 처럼 따옴표가 섞이거나, 프로젝트 경로에 빈칸이 있어도 찾는다.
    """
    mark = "\0P"
    cmd = command
    for p in ("${CLAUDE_PROJECT_DIR}", "$CLAUDE_PROJECT_DIR", "$env:CLAUDE_PROJECT_DIR", project):
        cmd = cmd.replace(p, mark)
    cmd = cmd.replace('"', "").replace("'", "")
    out = []
    for tok in re.findall(r"[\0\w./\\:~-]+\.(?:sh|py|js|ps1)", cmd):
        rel = tok.replace(mark, "").lstrip("/\\") if tok.startswith(mark) else None
        candidates = [os.path.join(project, rel)] if rel is not None else [tok, os.path.join(project, tok)]
        for path in candidates:
            if os.path.isfile(path):
                out.append(path)
                break
    return out


def old_progress_checkers(hooks, project):
    found = []
    for event in ("Stop", "SubagentStop"):
        for g in hooks.get(event, []):
            if is_harness(g):
                continue
            for h in g.get("hooks", []):
                for path in scripts_in(str(h.get("command", "")), project):
                    try:
                        with open(path, encoding="utf-8", errors="replace") as f:
                            if "진행상황" in f.read():
                                found.append(os.path.relpath(path, project))
                    except OSError:
                        pass
    return sorted(set(found))


def main():
    project = sys.argv[1] if len(sys.argv) > 1 else (os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    with open(os.path.join(CORE, "settings.core.json"), encoding="utf-8") as f:
        core = json.load(f)
    path = os.path.join(project, ".claude", "settings.json")
    cur = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cur = json.load(f)
    cur.setdefault("language", core.get("language", "korean"))
    deny = cur.setdefault("permissions", {}).setdefault("deny", [])
    deny += [d for d in core["permissions"]["deny"] if d not in deny]
    hooks = cur.setdefault("hooks", {})
    for event in list(hooks):
        hooks[event] = [g for g in hooks[event] if not is_harness(g)]
        if not hooks[event]:
            del hooks[event]
    for event, groups in core["hooks"].items():
        hooks[event] = hooks.get(event, []) + groups
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("settings.json 합침: 하네스 훅 %d개 이벤트, 금지 목록 %d개" % (len(core["hooks"]), len(deny)))

    old = old_progress_checkers(hooks, project)
    if old:
        hp = os.path.join(project, ".claude", "harness.json")
        try:
            with open(hp, encoding="utf-8-sig") as f:
                cfg = json.load(f)
        except Exception:
            cfg = None
        if not isinstance(cfg, dict):
            # 못 읽는 harness.json 을 덮어쓰면 set·repo 같은 설정이 사라지니 그대로 둔다
            print("옛 진행상황 검사 장치(%s)가 있는데 harness.json 을 읽지 못했어요. "
                  "harness.json 에 \"progress_check\": false 를 직접 넣어 주세요." % ", ".join(old))
        elif "progress_check" not in cfg:
            cfg["progress_check"] = False
            with open(hp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
                f.write("\n")
            print("옛 진행상황 검사 장치(%s)가 있어 하네스 진행상황 점검을 껐어요(harness.json progress_check: false). "
                  "옛 장치 등록을 빼고 하네스로 맡기려면 사용자에게 먼저 여쭤 주세요." % ", ".join(old))
        else:
            print("옛 진행상황 검사 장치: %s (harness.json progress_check: %s 그대로)" % (", ".join(old), cfg["progress_check"]))


if __name__ == "__main__":
    main()
