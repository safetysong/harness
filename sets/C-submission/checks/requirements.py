#!/usr/bin/env python3
"""세트 C 검사기: 제출물이 요강(harness.json의 submission)에 맞는지 본다.

submission 칸 예
  {"file": "제출/초안.md", "format": ".hwpx", "required_sections": ["추진 배경", "기대 효과"],
   "min_chars": 1000, "max_chars": 6000, "deadline": "2026-11-30", "final_file": "제출/최종.hwpx"}
- file: 검사할 초안(.md .txt .hwpx .docx)
- format: 마지막에 낼 파일 형식. final_file(마지막에 낼 파일)이 있으면 그 형식이 맞는지 본다(초안은 .md여도 됨)
- 마감은 시작 안내에서 남은 날로 보여 준다(여기서 막지 않음)
"""
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "core", "hooks"))
import harness_common as hc  # noqa: E402


def text_of(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".hwpx", ".docx"):
        prefix = "Contents/section" if ext == ".hwpx" else "word/document"
        out = []
        with zipfile.ZipFile(path) as z:
            for n in sorted(z.namelist()):
                if n.startswith(prefix) and n.endswith(".xml"):
                    xml = z.read(n).decode("utf-8", "replace")
                    xml = re.sub(r"</(?:hp:p|w:p)>", "\n", xml)
                    out.append(re.sub(r"<[^>]+>", "", xml))
        return "\n".join(out)
    return hc.read_text(path) or ""


def count_chars(text):
    """공백과 마크다운 기호를 빼고 센 글자 수."""
    t = re.sub(r"[#*_>`|\-]", "", text)
    return len(re.sub(r"\s+", "", t))


def main():
    sub = hc.load_config().get("submission") or {}
    rel = sub.get("file")
    if not rel:
        return 0
    path = os.path.join(hc.project_dir(), rel)
    if not os.path.isfile(path):
        print("제출물 파일 %s 이 없어요." % rel)
        return 1
    try:
        text = text_of(path)
    except Exception as e:
        print("제출물 %s 을 읽지 못했어요(%s)." % (rel, e))
        return 1
    problems = []
    flat = re.sub(r"\s+", "", text)
    missing = [s for s in sub.get("required_sections", []) if re.sub(r"\s+", "", s) not in flat]
    if missing:
        problems.append("필수 항목이 빠졌어요: " + ", ".join(missing))
    n = count_chars(text)
    if sub.get("max_chars") and n > int(sub["max_chars"]):
        problems.append("분량이 넘쳐요: %d자(최대 %s자)" % (n, sub["max_chars"]))
    if sub.get("min_chars") and n < int(sub["min_chars"]):
        problems.append("분량이 모자라요: %d자(최소 %s자)" % (n, sub["min_chars"]))
    fmt = (sub.get("format") or "").lower()
    if fmt and not fmt.startswith("."):
        fmt = "." + fmt
    final = sub.get("final_file")
    if fmt and final and os.path.splitext(final)[1].lower() != fmt:
        problems.append("최종 파일 형식이 달라요: %s(요강은 %s)" % (final, fmt))
    if problems:
        print("요강에 안 맞아요(%s).\n%s" % (rel, "\n".join(problems)))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
