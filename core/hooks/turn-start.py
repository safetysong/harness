#!/usr/bin/env python3
"""사용자가 말을 보낼 때(UserPromptSubmit): 한 답의 막은 횟수를 비우고 짧게 상기시킨다."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_common as hc  # noqa: E402


def main():
    data = hc.read_input()
    hc.reset_blocks(data.get("session_id") or "s")
    cfg = hc.load_config()
    name = cfg.get("progress_file") or "진행상황.md"
    sys.stdout.write("[자동] 사용자에게 쓰는 모든 글은 쉬운 한국어로만, 「—」 대시 없이. "
                     "파일을 바꾸면 %s도 같이 갱신(한 일·결정·사용자 지시 원문·다음 할 일).\n" % name)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
