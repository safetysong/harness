#!/usr/bin/env bash
# 하네스 시험 한 번에 돌리기: 문법 검사 + 훅 시험 + 묶음 확인
set -eu
cd "$(dirname "$0")/.."
echo "== 셸 문법"
bash -n bootstrap/harness-sync.sh tests/run.sh
if command -v shellcheck >/dev/null 2>&1; then shellcheck bootstrap/harness-sync.sh tests/run.sh; fi
echo "== 파이썬 문법"
python3 - <<'PY'
import ast, glob
for f in glob.glob("core/**/*.py", recursive=True) + glob.glob("sets/**/*.py", recursive=True) + glob.glob("tools/*.py") + glob.glob("tests/*.py"):
    ast.parse(open(f, encoding="utf-8").read(), f)
PY
echo "== 묶음과 승인 목록"
python3 tools/build_bundles.py --check
echo "== 훅 시험"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py' "$@"
