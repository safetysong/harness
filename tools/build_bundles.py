#!/usr/bin/env python3
"""세트마다 묶음(dist/<세트>.tar)과 바닥판만 든 묶음(dist/core.tar)을 만들고 승인 목록(signed/approved.json)을 쓴다.

  python3 tools/build_bundles.py          묶음과 승인 목록을 새로 씀(판을 올릴 때, PR에 같이 넣음)
  python3 tools/build_bundles.py --check  지금 원본으로 만든 묶음이 dist/·승인 목록과 같은지 확인(Actions)

묶음은 언제 어디서 만들어도 같은 바이트가 나오게 만든다(파일 순서·시간·주인 고정).
그래야 승인 목록의 sha256으로 「main에서 승인한 그 묶음」인지 확인할 수 있다.
"""
import hashlib
import io
import json
import os
import sys
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_PARTS = {"__pycache__", ".DS_Store"}


def files_for(set_name):
    """묶음에 넣을 파일. set_name이 None이면 바닥판만(core 묶음)."""
    out = ["VERSION", "bootstrap/harness-sync.sh"]  # 받기 장치도 넣어 upgrade가 승인된 판으로 바꾸게
    tops = ["core"] + ([os.path.join("sets", set_name)] if set_name else [])
    for top in tops:
        for d, dirs, files in os.walk(os.path.join(ROOT, top)):
            dirs[:] = [x for x in dirs if x not in SKIP_PARTS]
            for f in files:
                if f in SKIP_PARTS or f.endswith(".pyc"):
                    continue
                out.append(os.path.relpath(os.path.join(d, f), ROOT).replace(os.sep, "/"))
    return sorted(set(out))


def build(set_name):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as t:
        for rel in files_for(set_name):
            with open(os.path.join(ROOT, rel), "rb") as f:
                data = f.read()
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o755 if rel.endswith((".py", ".sh")) else 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def sets():
    return sorted(d for d in os.listdir(os.path.join(ROOT, "sets"))
                  if os.path.isfile(os.path.join(ROOT, "sets", d, "set.json")))


def main():
    check = "--check" in sys.argv
    with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
        version = f.read().strip()
    approved = {"version": version, "bundles": {}}
    blobs = {}
    for s in ["core"] + sets():
        blob = build(None if s == "core" else s)
        blobs[s] = blob
        approved["bundles"][s] = {"file": "dist/%s.tar" % s, "sha256": hashlib.sha256(blob).hexdigest()}
    text = json.dumps(approved, ensure_ascii=False, indent=2) + "\n"
    ap = os.path.join(ROOT, "signed", "approved.json")
    if check:
        bad = []
        try:
            with open(ap, encoding="utf-8") as f:
                if f.read() != text:
                    bad.append("signed/approved.json 이 원본과 달라요")
        except OSError:
            bad.append("signed/approved.json 이 없어요")
        for s, blob in blobs.items():
            try:
                with open(os.path.join(ROOT, "dist", s + ".tar"), "rb") as f:
                    if f.read() != blob:
                        bad.append("dist/%s.tar 가 원본과 달라요" % s)
            except OSError:
                bad.append("dist/%s.tar 가 없어요" % s)
        if bad:
            print("\n".join(bad) + "\n→ python3 tools/build_bundles.py 를 돌려 다시 만든 뒤 커밋하세요.")
            return 1
        print("묶음 %d개와 승인 목록이 원본과 같아요(판 %s)." % (len(blobs), version))
        return 0
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    os.makedirs(os.path.dirname(ap), exist_ok=True)
    for s, blob in blobs.items():
        with open(os.path.join(ROOT, "dist", s + ".tar"), "wb") as f:
            f.write(blob)
    with open(ap, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
