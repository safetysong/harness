#!/usr/bin/env python3
"""이 하네스 판을 저장소 자리(.claude/harness)에 싣는다(upgrade가 부름).

    python3 <하네스 판>/core/tools/promote.py [프로젝트 폴더]

- 받은 판(.claude/harness-state/harness)에서 돌리면 그것을 .claude/harness 로 옮기고 받은 판 자리는 비운다.
- 이미 .claude/harness 에서 돌리면 판은 그대로 두고 에이전트만 맞춘다.
- 세트 에이전트(sets/<세트>/agents/*.md)를 .claude/agents 에 복사한다.
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.dirname(os.path.dirname(HERE))  # 이 파일이 든 하네스 판


def main():
    project = sys.argv[1] if len(sys.argv) > 1 else (os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    tracked = os.path.join(project, ".claude", "harness")
    if os.path.realpath(SOURCE) != os.path.realpath(tracked):
        parent = os.path.dirname(tracked)
        os.makedirs(parent, exist_ok=True)
        tmp = tempfile.mkdtemp(prefix=".harness-promote-", dir=parent)
        try:
            staged = os.path.join(tmp, "new")
            shutil.copytree(SOURCE, staged, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            old = None
            if os.path.exists(tracked):
                old = os.path.join(tmp, "old")
                os.rename(tracked, old)
            try:
                os.rename(staged, tracked)
            except Exception:
                if old:
                    os.rename(old, tracked)
                raise
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(SOURCE, ignore_errors=True)  # 받은 판은 이제 저장소 자리에 있음
        with open(os.path.join(tracked, "VERSION"), encoding="utf-8") as f:
            print("하네스 판 %s 을 .claude/harness 에 실었어요." % f.read().strip())
    sets_dir = os.path.join(tracked, "sets")
    dest = os.path.join(project, ".claude", "agents")
    for name in sorted(os.listdir(sets_dir)) if os.path.isdir(sets_dir) else []:
        agents = os.path.join(sets_dir, name, "agents")
        if not os.path.isdir(agents):
            continue
        os.makedirs(dest, exist_ok=True)
        for f in os.listdir(agents):
            try:
                shutil.copyfile(os.path.join(agents, f), os.path.join(dest, f))
            except OSError:
                pass


if __name__ == "__main__":
    main()
