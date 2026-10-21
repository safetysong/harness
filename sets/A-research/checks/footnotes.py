#!/usr/bin/env python3
"""세트 A 검사기: 바뀐 글(.md)에서 각주 짝이 맞는지 본다.

- 본문에 [^이름]이 있는데 [^이름]: 설명이 없음 → 실패
- [^이름]: 설명이 있는데 본문에서 한 번도 안 씀 → 실패
harness.json의 research.files(예 ["원고/*.md"])가 있으면 그 파일들을, 없으면 바뀐 .md 파일을 본다.
"""
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "core", "hooks"))
import harness_common as hc  # noqa: E402

SKIP = {"진행상황.md", "CLAUDE.md", "실수기록.md", "README.md"}


def targets(cfg):
    pats = (cfg.get("research") or {}).get("files")
    root = hc.project_dir()
    if pats:
        files = []
        for p in pats:
            files += [os.path.relpath(f, root) for f in glob.glob(os.path.join(root, p), recursive=True)]
        return sorted(set(files))
    return [f for f in hc.changed_files({".md"}) if os.path.basename(f) not in SKIP]


def check(text):
    body = re.sub(r"```.*?```", "", text, flags=re.S)
    defs = set(re.findall(r"^\[\^([^\]]+)\]:", body, flags=re.M))
    refs = set(re.findall(r"\[\^([^\]]+)\](?!:)", body))
    return sorted(refs - defs), sorted(defs - refs)


def main():
    cfg = hc.load_config()
    problems = []
    for f in targets(cfg):
        text = hc.read_text(os.path.join(hc.project_dir(), f)) or ""
        no_def, unused = check(text)
        if no_def:
            problems.append("%s: 설명 없는 각주 %s" % (f, ", ".join(no_def)))
        if unused:
            problems.append("%s: 본문에서 안 쓴 각주 %s" % (f, ", ".join(unused)))
    if problems:
        print("각주 짝이 안 맞아요.\n" + "\n".join(problems))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
