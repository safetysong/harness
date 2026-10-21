#!/usr/bin/env python3
"""세트 B 검사기: 바뀐 파일의 문법을 본다.

.py 파이썬 컴파일 / .js .mjs node --check (node가 있을 때) / .json 읽기 / .sh bash -n /
.html 여는 태그와 닫는 태그(script·style·div 등) 짝
"""
import json
import os
import shutil
import subprocess
import sys
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "core", "hooks"))
import harness_common as hc  # noqa: E402

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr",
        "p", "li", "td", "th", "tr", "option", "dt", "dd", "thead", "tbody", "tfoot", "colgroup"}


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append((tag, self.getpos()[0]))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if self.stack and self.stack[-1][0] == tag:
            self.stack.pop()
        elif any(t == tag for t, _ in self.stack):
            while self.stack and self.stack[-1][0] != tag:
                t, line = self.stack.pop()
                self.errors.append("%d줄 <%s> 닫는 태그 없음" % (line, t))
            self.stack.pop()
        else:
            self.errors.append("%d줄 </%s> 여는 태그 없음" % (self.getpos()[0], tag))


def check(path):
    ext = os.path.splitext(path)[1].lower()
    full = os.path.join(hc.project_dir(), path)
    if ext == ".py":
        p = subprocess.run([sys.executable, "-c", "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())", full],
                           capture_output=True, text=True)
        return p.stderr.strip().splitlines()[-1] if p.returncode else None
    if ext in (".js", ".mjs", ".cjs") and shutil.which("node"):
        p = subprocess.run(["node", "--check", full], capture_output=True, text=True)
        return (p.stderr.strip().splitlines() or ["문법 오류"])[0] if p.returncode else None
    if ext == ".json":
        try:
            with open(full, encoding="utf-8") as f:
                json.load(f)
        except Exception as e:
            return str(e)
        return None
    if ext == ".sh" and shutil.which("bash"):
        p = subprocess.run(["bash", "-n", full], capture_output=True, text=True)
        return p.stderr.strip() if p.returncode else None
    if ext in (".html", ".htm"):
        t = Tags()
        t.feed(hc.read_text(full) or "")
        t.close()
        errs = t.errors + ["%d줄 <%s> 닫는 태그 없음" % (line, tag) for tag, line in t.stack if tag not in ("html", "body", "head")]
        return "; ".join(errs[:3]) or None
    return None


def main():
    problems = []
    for f in hc.changed_files({".py", ".js", ".mjs", ".cjs", ".json", ".sh", ".html", ".htm"}):
        err = check(f)
        if err:
            problems.append("%s: %s" % (f, err))
    if problems:
        print("문법 오류가 있어요.\n" + "\n".join(problems))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
