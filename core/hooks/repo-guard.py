#!/usr/bin/env python3
"""저장소 지킴이(PreToolUse): 한 세션에는 저장소 하나.

저장소가 여러 개 붙은 클라우드 세션은 다시 뜰 때 어느 저장소의 훅도 읽지 않는다(공식 문서).
그래서 add_repo는 언제나 사용자에게 허락 창을 띄운다. 아무도 없는 예약 실행 세션은 허락 창에서
멈추므로 harness.json에 "unattended": true 를 두면 허락 창 대신 막는다.

harness.json에 repo(예 "owner/name")를 정하면 아래도 같은 방식으로 다룬다.
- 다른 저장소를 가리키는 GitHub 도구(owner/repo가 다름)
- 다른 저장소를 가리키는 GitHub 도구(owner/repo가 다름)
- repo: 조건 없이 GitHub 전체를 뒤지는 검색
- 다른 저장소 git clone, git remote add / set-url, gh repo clone (Bash와 PowerShell 도구 모두)
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_common as hc  # noqa: E402

SEARCH_TOOLS = ("search_code", "search_issues", "search_pull_requests", "search_commits")
URL_RE = re.compile(r"(?:github\.com[:/])([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:[/\s\"']|$)")


MODE = "ask"


def stop(reason, rule):
    if MODE == "deny":
        hc.deny(reason, rule)
    hc.bump_stat("물음:저장소지킴이")
    hc.emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                    "permissionDecisionReason": reason}})
    sys.exit(0)


def same(repo, owner, name):
    want = repo.lower().split("/")
    return [owner.lower(), name.lower()] == want


def check_bash(cmd, repo):
    # PowerShell은 대소문자를 안 가리고 git.exe 처럼 .exe를 붙여도 된다
    for m in re.finditer(r"\b(git(?:\.exe)?\s+clone|git(?:\.exe)?\s+remote\s+(?:add|set-url)|gh(?:\.exe)?\s+repo\s+clone)\b"
                         r"([^;&|\n]*)", cmd, re.I):
        rest = m.group(2)
        urls = URL_RE.findall(rest + " ")
        if m.group(1).lower().startswith("gh"):
            t = re.search(r"\s([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", rest)
            urls = urls or ([t.groups()] if t else [])
        if not urls:
            stop("저장소 지킴이: '%s' 명령이 어느 저장소를 가리키는지 알 수 없어요. 이 프로젝트는 %s 하나만 다룹니다."
                    % (m.group(1), repo), "저장소-알수없음")
        for owner, name in urls:
            if not same(repo, owner, name):
                stop("저장소 지킴이: %s/%s 는 이 프로젝트 저장소(%s)가 아니에요. 이 프로젝트는 저장소 하나만 다뤄요."
                        % (owner, name, repo), "다른저장소")


def main():
    global MODE
    data = hc.read_input()
    cfg = hc.load_config()
    MODE = "deny" if cfg.get("unattended") else "ask"
    tool = data.get("tool_name") or ""
    ti = data.get("tool_input") or {}
    if tool.endswith("__add_repo"):
        stop("이 세션에 저장소(%s)를 더 붙이려 합니다. 붙이면 이 세션이 다시 뜰 때 자동 장치가 모두 꺼집니다. "
             "보통은 그 저장소 세션에 맡기거나 그 저장소 하나로 새 세션을 만듭니다. "
             "꼭 이 세션에 붙여야 할 때만 허락하세요." % (ti.get("repo") or "이름 모름"), "저장소붙이기")
    repo = (cfg.get("repo") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        return  # 저장소를 정하지 않은 프로젝트는 여기까지만
    if tool.startswith("mcp__github__"):
        short = tool[len("mcp__github__"):]
        if short in SEARCH_TOOLS:
            q = str(ti.get("query", ""))
            found = re.findall(r"repo:([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", q)
            if not found or any(not same(repo, o, n) for o, n in found):
                stop("저장소 지킴이: 검색어에 repo:%s 를 넣어 이 저장소 안에서만 찾아 주세요." % repo, "전체검색")
            return
        owner, name = ti.get("owner"), ti.get("repo")
        if owner and name and not same(repo, str(owner), str(name)):
            stop("저장소 지킴이: %s/%s 는 이 프로젝트 저장소(%s)가 아니에요. 이 프로젝트는 저장소 하나만 다뤄요." % (owner, name, repo),
                    "다른저장소")
        return
    if tool in ("Bash", "PowerShell"):  # 윈도는 PowerShell 도구로 같은 명령을 쓸 수 있다
        check_bash(str(ti.get("command", "")), repo)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        pass
