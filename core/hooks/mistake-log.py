#!/usr/bin/env python3
"""실수 기록(PostToolUseFailure): 같은 종류 실패가 2번이면 실수기록.md에 적고 Claude에게 알린다.

지킴이가 막은 일과 완료 조건 실패는 각 훅이 직접 기록한다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_common as hc  # noqa: E402


def main():
    data = hc.read_input()
    if data.get("is_interrupt"):
        return
    tool = data.get("tool_name") or "도구"
    err = data.get("error") or ""
    if not err:
        resp = data.get("tool_response")
        err = resp if isinstance(resp, str) else ""
    if not err:
        return
    kind = hc.kind_of_error(tool, err)
    if hc.record_mistake(kind, err):
        hc.emit({"hookSpecificOutput": {
            "hookEventName": data.get("hook_event_name") or "PostToolUseFailure",
            "additionalContext": "[실수 기록] 같은 종류 실패가 2번 났어요(%s). 실수기록.md에 적었어요. "
                                 "또 나지 않게 막는 장치(훅·검사·진행상황 '꼭 기억')를 사용자에게 제안해 주세요." % kind,
        }})


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
