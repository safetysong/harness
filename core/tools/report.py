#!/usr/bin/env python3
"""쌓아 둔 장치 숫자와 실수 기록을 git에 남는 파일로 옮긴다(PR 올릴 때 Claude가 돌림).

    bash .claude/harness-sync.sh report

- .claude/harness-state/ (git에 안 들어감)에 모인 것을
  .claude/harness-stats.jsonl (하루 한 줄)과 실수기록.md 로 옮기고, 모아 둔 것은 비운다.
- 훅은 이 두 파일을 스스로 고치지 않는다. 그래서 예약 실행의 git pull 이 깨지지 않는다.
- 읽기 전용 세션이면 아무것도 하지 않는다.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "hooks"))
import harness_common as hc  # noqa: E402


def main():
    if len(sys.argv) > 1:
        os.environ["CLAUDE_PROJECT_DIR"] = sys.argv[1]
    if hc.is_read_only():
        print("읽기 전용이라 기록을 옮기지 않았어요.")
        return 0
    days = hc.flush_stats()
    try:
        lines = hc.flush_mistakes()
    except OSError as e:
        print("실수기록.md 를 쓰지 못했어요(%s). 숫자 기록은 옮겼어요. 모아 둔 실수는 그대로 남겨 둡니다." % e)
        return 1
    if not days and not lines:
        print("옮길 기록이 없어요.")
    else:
        print("기록을 옮겼어요: 장치 숫자 %d일치(.claude/harness-stats.jsonl), 실수 %d줄(실수기록.md). "
              "PR에 같이 넣어 주세요." % (days, lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
