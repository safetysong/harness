#!/usr/bin/env python3
"""도구 고장 세기(PostToolUseFailure).

시간 초과, 인터넷 끊김, 명령 실패 같은 도구 고장은 Claude의 실수와 섞이지 않게 실수기록.md에
적지 않고 「도구실패」로 따로 센다. 같은 종류가 2번이면 Claude에게만 알린다.
지킴이가 막은 일과 완료 조건 실패(Claude의 실수)는 각 훅이 직접 실수기록.md에 적는다.
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
    hc.bump_stat("도구실패")
    if hc.record_mistake("도구:" + kind, err, tool_failure=True):
        hc.emit({"hookSpecificOutput": {
            "hookEventName": data.get("hook_event_name") or "PostToolUseFailure",
            "additionalContext": "[도구 고장] 같은 종류 실패가 2번 났어요(%s). 같은 방법을 되풀이하지 말고 "
                                 "다른 방법을 찾거나, 계속되면 사용자에게 알려 주세요." % kind,
        }})


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
