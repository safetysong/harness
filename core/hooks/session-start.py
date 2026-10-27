#!/usr/bin/env python3
"""세션 시작 안내: 진행상황·완료 조건을 먼저 보여 주고 [장치 상태] 한 줄을 붙인다."""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_common as hc  # noqa: E402


SKIP_DIRS = {"백업", "out", "node_modules", ".git", ".claude"}


def progress_files(cfg):
    """뿌리와 작업 폴더(세 단계까지)의 진행상황 파일들."""
    name = os.path.basename(hc.progress_path(cfg))
    root = hc.project_dir()
    found = []
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith("."))
        if d[len(root):].count(os.sep) >= 3:
            dirs[:] = []
        if name in files:
            found.append(os.path.join(d, name))
    return found


def work_guard_week():
    """업무 지킴이 경고가 최근 7일에 몇 번 났는지. 프로젝트 자체 지킴이가 없으면 None."""
    if not os.path.isfile(os.path.join(hc.project_dir(), hc.WORK_GUARD)):
        return None
    since = (datetime.date.today() - datetime.timedelta(days=6)).isoformat()
    n = 0
    for line in (hc.read_text(os.path.join(hc.state_dir(), hc.WORK_GUARD_LOG)) or "").splitlines():
        try:
            d = json.loads(line).get("date")
            if isinstance(d, str) and d >= since:
                n += 1
        except Exception:
            pass
    return n


def device_line(cfg, infos, files):
    parts = ["판 %s" % hc.harness_version()]
    claude_md = hc.read_text(os.path.join(hc.project_dir(), "CLAUDE.md"))
    if claude_md is not None:
        parts.append("지침 %d자" % len(claude_md))
    long_ = [f for f in files if hc.front_lines(hc.read_text(f)) > 40]
    parts.append("앞 칸 40줄 넘는 진행상황 %d개" % len(long_))
    titles = [info.get("title") or os.path.basename(d) for d, info in infos]
    found = {os.path.basename(d) for d, _ in infos}
    missing = [n for n in hc.set_names(cfg) if n not in found]
    titles += ["%s(없음: 받기 전이거나 이름이 틀림)" % n for n in missing]
    parts.append("세트 " + " + ".join(titles) if titles else "바닥판만(세트 없음)")
    if cfg.get("progress_check") is False:
        parts.append("진행상황 점검은 프로젝트 옛 장치가 맡음")
    if hc.is_read_only():
        parts.append("읽기 전용(실수기록·장치 기록 안 고침)")
    guards = ["한국어", "완료 조건", "실수 기록"]
    if cfg.get("repo"):
        guards.insert(0, "저장소(%s)" % cfg["repo"])
    parts.append("켜진 지킴이: " + ", ".join(guards))
    n = work_guard_week()
    if n is not None:
        parts.append("업무 지킴이 경고 최근 7일 %d건" % n)
    tracked = hc.read_text(os.path.join(hc.project_dir(), ".claude", "harness", "VERSION"))
    if tracked and tracked.strip() != hc.harness_version():
        parts.append("저장소에 실린 판 %s, 돌고 있는 판 %s(받은 판) → upgrade PR 필요" % (tracked.strip(), hc.harness_version()))
    days, lines = hc.pending_summary()
    if days or lines:
        parts.append("기록 대기(숫자 %d일, 실수 %d줄) → PR 올릴 때 `bash .claude/harness-sync.sh report`" % (days, lines))
    sync = hc.read_text(os.path.join(hc.state_dir(), "sync-status.txt"))
    if sync:
        parts.append(sync.strip())
    return "[장치 상태] " + " · ".join(parts)


def deadline_line(cfg):
    sub = cfg.get("submission") or {}
    dl = sub.get("deadline")
    if not dl:
        return ""
    try:
        d = datetime.date.fromisoformat(dl)
    except ValueError:
        return ""
    left = (d - datetime.date.today()).days
    if left < 0:
        return "[마감] %s 마감이 %d일 지났어요." % (dl, -left)
    return "[마감] %s 마감까지 %d일 남았어요." % (dl, left)


def main():
    data = hc.read_input()
    cfg = hc.load_config()
    infos = hc.set_infos(cfg)
    out = []
    files = progress_files(cfg)
    md = hc.read_text(hc.progress_path(cfg))
    if md is None:
        out.append("진행상황 파일(%s)이 없어요. 하네스 틀(core/진행상황.template.md)로 먼저 만들어 주세요."
                   % (cfg.get("progress_file") or "진행상황.md"))
    else:
        rel = [os.path.relpath(f, hc.project_dir()) for f in files]
        out.append("작업 전에 진행상황 파일을 Read로 먼저 읽으세요: " + ", ".join(rel))
        for title in ("지금 상태", "완료 조건", "다음 할 일", "꼭 기억"):
            body = hc.section(md, title)
            if body:
                out.append("## %s\n%s" % (title, body))
    dl = deadline_line(cfg)
    if dl:
        out.append(dl)
    if data.get("source", "startup") == "startup":
        for sd, _ in infos:
            guide = hc.read_text(os.path.join(sd, "guide.md"))
            if guide:
                out.append(guide.strip())
    out.append(device_line(cfg, infos, files))
    sys.stdout.write("\n\n".join(out) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # 고장 나도 세션은 시작되게
        sys.stdout.write("[장치 상태] 시작 안내 훅 오류: %s\n" % e)
