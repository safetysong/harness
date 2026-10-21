"""훅마다 입력 JSON을 넣어 막기/통과를 확인하는 시험.

    python3 -m unittest discover -s tests -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(ROOT, "core", "hooks")
SYNC = os.path.join(ROOT, "bootstrap", "harness-sync.sh")


class Project:
    """시험용 빈 프로젝트(git 저장소)."""

    def __init__(self, cfg=None, progress=None):
        self.dir = tempfile.mkdtemp(prefix="harness-test-")
        os.makedirs(os.path.join(self.dir, ".claude"))
        if cfg is not None:
            self.write(".claude/harness.json", json.dumps(cfg, ensure_ascii=False))
        if progress is not None:
            self.write("진행상황.md", progress)
        self.git("init", "-q")
        self.git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "처음")
        self.transcript = os.path.join(self.dir, "transcript.jsonl")

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.dir, check=True, capture_output=True)

    def write(self, rel, text):
        path = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def read(self, rel):
        with open(os.path.join(self.dir, rel), encoding="utf-8") as f:
            return f.read()

    def set_transcript(self, events):
        with open(self.transcript, "w", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

    def env(self, **extra):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=self.dir)
        env.update(extra)
        return env

    def hook(self, name, data, *args):
        p = subprocess.run([sys.executable, os.path.join(HOOKS, name + ".py"), *args],
                           input=json.dumps(data, ensure_ascii=False), capture_output=True, text=True,
                           cwd=self.dir, env=self.env())
        out = json.loads(p.stdout) if p.stdout.strip().startswith("{") else p.stdout
        return p.returncode, out, p.stderr

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def say(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def tool(name, **inp):
    return {"type": "assistant", "message": {"role": "assistant",
                                             "content": [{"type": "tool_use", "name": name, "input": inp}]}}


def decision(out):
    if isinstance(out, dict):
        return (out.get("hookSpecificOutput") or {}).get("permissionDecision")
    return None


class Base(unittest.TestCase):
    def project(self, *a, **k):
        p = Project(*a, **k)
        self.addCleanup(p.cleanup)
        return p


class RepoGuard(Base):
    def test_add_repo_asks(self):
        p = self.project({})
        _, out, _ = p.hook("repo-guard", {"tool_name": "mcp__claude-code-remote__add_repo",
                                          "tool_input": {"owner": "a", "repo": "b"}})
        self.assertEqual(decision(out), "ask")

    def test_add_repo_denied_when_unattended(self):
        p = self.project({"unattended": True})
        _, out, _ = p.hook("repo-guard", {"tool_name": "mcp__claude-code-remote__add_repo",
                                          "tool_input": {"owner": "a", "repo": "b"}})
        self.assertEqual(decision(out), "deny")

    def test_github_other_repo(self):
        p = self.project({"repo": "me/mine"})
        _, out, _ = p.hook("repo-guard", {"tool_name": "mcp__github__get_file_contents",
                                          "tool_input": {"owner": "other", "repo": "x"}})
        self.assertEqual(decision(out), "ask")

    def test_github_same_repo_passes(self):
        p = self.project({"repo": "me/mine"})
        code, out, _ = p.hook("repo-guard", {"tool_name": "mcp__github__get_file_contents",
                                             "tool_input": {"owner": "Me", "repo": "Mine"}})
        self.assertEqual((code, out), (0, ""))

    def test_search_needs_repo_qualifier(self):
        p = self.project({"repo": "me/mine"})
        _, out, _ = p.hook("repo-guard", {"tool_name": "mcp__github__search_code",
                                          "tool_input": {"query": "hello"}})
        self.assertEqual(decision(out), "ask")
        code, out, _ = p.hook("repo-guard", {"tool_name": "mcp__github__search_code",
                                             "tool_input": {"query": "hello repo:me/mine"}})
        self.assertEqual(out, "")

    def test_git_clone(self):
        p = self.project({"repo": "me/mine"})
        _, out, _ = p.hook("repo-guard", {"tool_name": "Bash",
                                          "tool_input": {"command": "git clone https://github.com/other/x.git"}})
        self.assertEqual(decision(out), "ask")
        _, out, _ = p.hook("repo-guard", {"tool_name": "Bash",
                                          "tool_input": {"command": "cd /w && git clone git@github.com:me/mine.git"}})
        self.assertEqual(out, "")
        _, out, _ = p.hook("repo-guard", {"tool_name": "Bash", "tool_input": {"command": "ls -la"}})
        self.assertEqual(out, "")

    def test_no_repo_config_only_add_repo(self):
        p = self.project({})
        _, out, _ = p.hook("repo-guard", {"tool_name": "mcp__github__get_file_contents",
                                          "tool_input": {"owner": "other", "repo": "x"}})
        self.assertEqual(out, "")

    def test_broken_input_passes(self):
        p = self.project({"repo": "me/mine"})
        r = subprocess.run([sys.executable, os.path.join(HOOKS, "repo-guard.py")], input="not json",
                           capture_output=True, text=True, env=p.env())
        self.assertEqual((r.returncode, r.stdout), (0, ""))


class KoreanGuard(Base):
    def test_pre_blocks_english_question(self):
        p = self.project({})
        code, _, err = p.hook("korean-guard", {"tool_name": "AskUserQuestion", "tool_input": {
            "questions": [{"question": "Which option should we choose for the deployment target?"}]}}, "pre")
        self.assertEqual(code, 2)
        self.assertIn("한국어 지킴이", err)

    def test_pre_passes_korean(self):
        p = self.project({})
        code, _, _ = p.hook("korean-guard", {"tool_name": "AskUserQuestion", "tool_input": {
            "questions": [{"question": "어느 쪽으로 할까요? `main` 브랜치에 바로 넣을까요?"}]}}, "pre")
        self.assertEqual(code, 0)

    def test_pre_blocks_dash(self):
        p = self.project({})
        code, _, err = p.hook("korean-guard", {"tool_name": "SendUserFile", "tool_input": {
            "caption": "보고서입니다 — 확인해 주세요"}}, "pre")
        self.assertEqual(code, 2)
        self.assertIn("대시", err)

    def test_pre_ignores_other_tools(self):
        p = self.project({})
        code, _, _ = p.hook("korean-guard", {"tool_name": "Bash", "tool_input": {
            "command": "echo this is a long english command line here"}}, "pre")
        self.assertEqual(code, 0)

    def test_stop_english_blocked_twice_then_passes(self):
        p = self.project({})
        p.set_transcript([user("요약해 줘"), say("Here is the summary of everything we did today in detail.")])
        data = {"transcript_path": p.transcript, "session_id": "k1"}
        self.assertEqual(p.hook("korean-guard", data, "stop")[0], 2)
        self.assertEqual(p.hook("korean-guard", data, "stop")[0], 2)
        self.assertEqual(p.hook("korean-guard", data, "stop")[0], 0)
        # 사용자가 새로 말하면 다시 센다
        p.hook("turn-start", {"session_id": "k1"})
        self.assertEqual(p.hook("korean-guard", data, "stop")[0], 2)

    def test_stop_korean_passes(self):
        p = self.project({})
        p.set_transcript([user("요약해 줘"), say("오늘 한 일은 세 가지예요. `tests/run.sh` 를 돌렸어요.")])
        self.assertEqual(p.hook("korean-guard", {"transcript_path": p.transcript, "session_id": "k2"}, "stop")[0], 0)

    def test_post_warns_once(self):
        p = self.project({})
        p.set_transcript([user("시작"), say("Now I will check the files and run the tests quickly."),
                          tool("Bash", command="ls")])
        data = {"transcript_path": p.transcript, "session_id": "k3"}
        self.assertEqual(p.hook("korean-guard", data, "post")[0], 2)
        self.assertEqual(p.hook("korean-guard", data, "post")[0], 0)


PROGRESS = """# 진행상황

## 지금 상태
시험 중

## 완료 조건
- [ ] 통과하는 검사 · 확인: `true`
- [ ] 실패하는 검사 · 확인: `{cmd}`
- [ ] 틀 그대로 · 확인: `{{{{명령}}}}`
- [ ] 사람이 볼 것

## 다음 할 일
1. 끝내기

## 꼭 기억
- 쉬운 한국어
"""


class StopCheck(Base):
    def test_failing_command_blocks_twice(self):
        p = self.project({}, PROGRESS.format(cmd="echo 망가짐; exit 3"))
        data = {"session_id": "s1", "transcript_path": p.transcript}
        _, out, _ = p.hook("stop-check", data)
        self.assertEqual(out["decision"], "block")
        self.assertIn("실패하는 검사", out["reason"])
        self.assertIn("망가짐", out["reason"])
        self.assertNotIn("통과하는 검사", out["reason"])
        _, out, _ = p.hook("stop-check", data)
        self.assertEqual(out["decision"], "block")
        _, out, _ = p.hook("stop-check", data)
        self.assertNotIn("decision", out)
        self.assertIn("systemMessage", out)

    def test_passing_commands_and_cache(self):
        p = self.project({}, PROGRESS.format(cmd="echo 돌았음 >> ran.txt"))
        p.write(".gitignore", "ran.txt\n.claude/harness-state/\n")
        data = {"session_id": "s2", "transcript_path": p.transcript}
        self.assertEqual(p.hook("stop-check", data)[1], "")
        self.assertEqual(p.hook("stop-check", data)[1], "")
        # 바뀐 것이 없으면 두 번째는 돌리지 않는다
        self.assertEqual(p.read("ran.txt").count("돌았음"), 1)
        p.write("새파일.txt", "바뀜")
        p.hook("stop-check", data)
        self.assertEqual(p.read("ran.txt").count("돌았음"), 2)

    def test_edit_without_progress_update_blocks(self):
        p = self.project({}, PROGRESS.format(cmd="true"))
        p.set_transcript([user("고쳐 줘"), tool("Edit", file_path=os.path.join(p.dir, "앱.py"))])
        _, out, _ = p.hook("stop-check", {"session_id": "s3", "transcript_path": p.transcript})
        self.assertEqual(out["decision"], "block")
        self.assertIn("진행상황.md", out["reason"])
        p.set_transcript([user("고쳐 줘"), tool("Edit", file_path=os.path.join(p.dir, "앱.py")),
                          tool("Edit", file_path=os.path.join(p.dir, "진행상황.md"))])
        self.assertEqual(p.hook("stop-check", {"session_id": "s4", "transcript_path": p.transcript})[1], "")

    def test_no_progress_file_passes(self):
        p = self.project({})
        self.assertEqual(p.hook("stop-check", {"session_id": "s5"})[1], "")

    def test_set_check_runs(self):
        p = self.project({"set": "B-app"}, PROGRESS.format(cmd="true"))
        p.write("깨진.json", "{ 이상함")
        _, out, _ = p.hook("stop-check", {"session_id": "s6", "transcript_path": p.transcript})
        self.assertEqual(out["decision"], "block")
        self.assertIn("깨진.json", out["reason"])


    def test_not_git_repo_always_runs(self):
        p = self.project({}, PROGRESS.format(cmd="echo 돌았음 >> ran.txt"))
        shutil.rmtree(os.path.join(p.dir, ".git"))
        data = {"session_id": "s7", "transcript_path": p.transcript}
        p.hook("stop-check", data)
        p.hook("stop-check", data)
        self.assertEqual(p.read("ran.txt").count("돌았음"), 2)

    def test_budget(self):
        p = self.project({}, PROGRESS.format(cmd="sleep 3"))
        r = subprocess.run([sys.executable, os.path.join(HOOKS, "stop-check.py")],
                           input=json.dumps({"session_id": "s8"}), capture_output=True, text=True,
                           env=p.env(HARNESS_CHECK_BUDGET="6"), cwd=p.dir)
        self.assertIn("시간이 모자라", json.loads(r.stdout)["reason"])

    def test_stop_feedback_is_not_new_turn(self):
        p = self.project({}, PROGRESS.format(cmd="true"))
        p.set_transcript([user("고쳐 줘"), tool("Edit", file_path=os.path.join(p.dir, "앱.py")),
                          user("Stop hook feedback:\n아직 끝낼 수 없어요"), say("알겠어요")])
        _, out, _ = p.hook("stop-check", {"session_id": "s9", "transcript_path": p.transcript})
        self.assertEqual(out["decision"], "block")


    def test_work_guard_warns_only(self):
        p = self.project({}, PROGRESS.format(cmd="true"))
        p.write(".claude/hooks/work_guard.py",
                "import json, os, sys\nd = json.load(sys.stdin)\n"
                "print('업무 단어가 보여요 ' + os.environ.get('HARNESS_EVENT', ''), file=sys.stderr)\nsys.exit(2)\n")
        _, out, _ = p.hook("stop-check", {"session_id": "w1", "transcript_path": p.transcript})
        self.assertNotIn("decision", out)
        self.assertIn("업무 지킴이 경고", out["systemMessage"])
        self.assertIn("Stop", out["systemMessage"])
        p.hook("stop-check", {"session_id": "w1", "transcript_path": p.transcript})
        log = p.read(".claude/harness-state/work-guard-warnings.jsonl").splitlines()
        self.assertEqual(len(log), 2)
        self.assertEqual(json.loads(log[0])["date"], __import__("datetime").date.today().isoformat())
        _, out, _ = p.hook("session-start", {"source": "startup"})
        self.assertIn("업무 지킴이 경고 최근 7일 2건", out)

    def test_work_guard_rides_on_block(self):
        p = self.project({}, PROGRESS.format(cmd="exit 1"))
        p.write(".claude/hooks/work_guard.py", "import sys\nsys.stdin.read()\nprint('조심', file=sys.stderr)\nsys.exit(2)\n")
        _, out, _ = p.hook("stop-check", {"session_id": "w2", "transcript_path": p.transcript})
        self.assertEqual(out["decision"], "block")
        self.assertIn("[업무 지킴이 경고] 조심", out["reason"])
        self.assertIn("조심", out["systemMessage"])
        # 끝 점검이 막은 뒤 다시 멈출 때는 지킴이를 또 돌리지 않는다
        p.hook("stop-check", {"session_id": "w2", "stop_hook_active": True, "transcript_path": p.transcript})
        self.assertEqual(len(p.read(".claude/harness-state/work-guard-warnings.jsonl").splitlines()), 1)

    def test_work_guard_json_block_and_bad_log(self):
        p = self.project({}, PROGRESS.format(cmd="true"))
        p.write(".claude/hooks/work_guard.py", "import sys, json\nsys.stdin.read()\n"
                "print(json.dumps({'decision': 'block', 'reason': '제이슨 경고'}))\n")
        p.write(".claude/harness-state/work-guard-warnings.jsonl", '[]\n{"date": null}\n{"date": "2000-01-01"}\n')
        _, out, _ = p.hook("stop-check", {"session_id": "w4", "transcript_path": p.transcript})
        self.assertIn("제이슨 경고", out["systemMessage"])
        # 30일 넘은 줄과 이상한 줄은 정리된다
        self.assertEqual(len(p.read(".claude/harness-state/work-guard-warnings.jsonl").splitlines()), 1)
        p.write(".claude/harness-state/work-guard-warnings.jsonl", '[]\n{"date": null}\n')
        _, out, _ = p.hook("session-start", {"source": "startup"})
        self.assertIn("업무 지킴이 경고 최근 7일 0건", out)
        self.assertIn("## 완료 조건", out)

    def test_work_guard_quiet_pass(self):
        p = self.project({}, PROGRESS.format(cmd="true"))
        p.write(".claude/hooks/work_guard.py", "import sys\nsys.stdin.read()\n")
        self.assertEqual(p.hook("stop-check", {"session_id": "w3", "transcript_path": p.transcript})[1], "")
        self.assertFalse(os.path.exists(os.path.join(p.dir, ".claude/harness-state/work-guard-warnings.jsonl")))
        _, out, _ = p.hook("session-start", {"source": "startup"})
        self.assertIn("업무 지킴이 경고 최근 7일 0건", out)


class MistakeLog(Base):
    def test_second_same_failure_logged(self):
        p = self.project({})
        data = {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
                "error": "cat: /tmp/a1/x.txt: No such file or directory"}
        self.assertEqual(p.hook("mistake-log", data)[1], "")
        data["error"] = "cat: /home/b2/y.txt: No such file or directory"
        _, out, _ = p.hook("mistake-log", data)
        self.assertIn("실수 기록", out["hookSpecificOutput"]["additionalContext"])
        self.assertIn("No such file", p.read("실수기록.md"))
        # 세 번째는 다시 적지 않는다
        self.assertEqual(p.hook("mistake-log", data)[1], "")
        self.assertEqual(len([x for x in p.read("실수기록.md").splitlines() if x.startswith("- ")]), 1)

    def test_different_failures_not_logged(self):
        p = self.project({})
        p.hook("mistake-log", {"tool_name": "Bash", "error": "첫 번째 오류"})
        p.hook("mistake-log", {"tool_name": "Bash", "error": "전혀 다른 오류"})
        self.assertFalse(os.path.exists(os.path.join(p.dir, "실수기록.md")))

    def test_guard_blocks_count(self):
        p = self.project({"unattended": True})
        d = {"tool_name": "mcp__claude-code-remote__add_repo", "tool_input": {"owner": "a", "repo": "b"}}
        p.hook("repo-guard", d)
        p.hook("repo-guard", d)
        self.assertIn("저장소붙이기", p.read("실수기록.md"))


class SessionStart(Base):
    def test_shows_progress_and_status(self):
        p = self.project({"set": "C-submission", "repo": "me/mine",
                          "submission": {"deadline": "2099-01-01"}}, PROGRESS.format(cmd="true"))
        p.write("CLAUDE.md", "규칙")
        _, out, _ = p.hook("session-start", {"source": "startup"})
        for word in ("## 지금 상태", "## 완료 조건", "## 다음 할 일", "## 꼭 기억", "세트 C 제출물 안내",
                     "[마감] 2099-01-01", "[장치 상태]", "지침 2자", "저장소(me/mine)", "Read로 먼저"):
            self.assertIn(word, out)

    def test_missing_progress(self):
        p = self.project({})
        _, out, _ = p.hook("session-start", {"source": "startup"})
        self.assertIn("진행상황 파일(진행상황.md)이 없어요", out)
        self.assertIn("[장치 상태]", out)

    def test_long_front_counted(self):
        p = self.project({}, "## 지금 상태\n" + "줄\n" * 45)
        _, out, _ = p.hook("session-start", {"source": "resume"})
        self.assertIn("앞 칸 40줄 넘는 진행상황 1개", out)


class ChangedFiles(Base):
    def test_subfolder_project_and_no_commit(self):
        p = self.project({})
        shutil.rmtree(os.path.join(p.dir, ".git"))
        p.git("init", "-q")
        p.write("app/x.py", "1")
        p.write("app/.claude/harness.json", "{}")
        p.write("other/y.py", "1")
        code = ("import sys; sys.path.insert(0, %r); import harness_common as hc; print(hc.changed_files({'.py'}))"
                % HOOKS)
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env=dict(os.environ, CLAUDE_PROJECT_DIR=os.path.join(p.dir, "app")))
        self.assertEqual(r.stdout.strip(), "['x.py']")
        p.git("add", "-A")
        p.git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "a")
        p.write("app/x.py", "2")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env=dict(os.environ, CLAUDE_PROJECT_DIR=os.path.join(p.dir, "app")))
        self.assertEqual(r.stdout.strip(), "['x.py']")


class SetChecks(Base):
    def run_check(self, p, rel):
        r = subprocess.run([sys.executable, os.path.join(ROOT, rel)], capture_output=True, text=True, env=p.env(),
                           cwd=p.dir)
        return r.returncode, r.stdout

    def test_footnotes(self):
        p = self.project({"set": "A-research"})
        p.write("원고.md", "주장[^1] 그리고 또 주장[^2]\n\n[^1]: 출처 하나\n[^3]: 안 쓴 출처\n")
        code, out = self.run_check(p, "sets/A-research/checks/footnotes.py")
        self.assertEqual(code, 1)
        self.assertIn("설명 없는 각주 2", out)
        self.assertIn("안 쓴 각주 3", out)
        p.write("원고.md", "주장[^1]\n\n[^1]: 출처\n")
        self.assertEqual(self.run_check(p, "sets/A-research/checks/footnotes.py")[0], 0)

    def test_syntax(self):
        p = self.project({"set": "B-app"})
        p.write("a.py", "def f(:\n")
        p.write("b.html", "<div><span>글</div>")
        p.write("ok.json", "{}")
        code, out = self.run_check(p, "sets/B-app/checks/syntax.py")
        self.assertEqual(code, 1)
        self.assertIn("a.py", out)
        self.assertIn("b.html", out)
        self.assertNotIn("ok.json", out)

    def test_requirements_md_and_hwpx(self):
        sub = {"file": "초안.md", "required_sections": ["추진 배경", "기대 효과"], "max_chars": 20}
        p = self.project({"set": "C-submission", "submission": sub})
        p.write("초안.md", "# 추진 배경\n짧은 글\n")
        code, out = self.run_check(p, "sets/C-submission/checks/requirements.py")
        self.assertEqual(code, 1)
        self.assertIn("기대 효과", out)
        p.write("초안.md", "# 추진 배경\n짧다\n# 기대 효과\n좋다\n")
        self.assertEqual(self.run_check(p, "sets/C-submission/checks/requirements.py")[0], 0)
        with zipfile.ZipFile(os.path.join(p.dir, "초안.hwpx"), "w") as z:
            z.writestr("Contents/section0.xml", "<hp:p><hp:t>추진 배경</hp:t></hp:p><hp:p><hp:t>"
                       + "가" * 30 + "</hp:t></hp:p>")
        sub.update(file="초안.hwpx")
        p.write(".claude/harness.json", json.dumps({"set": "C-submission", "submission": sub}, ensure_ascii=False))
        code, out = self.run_check(p, "sets/C-submission/checks/requirements.py")
        self.assertIn("기대 효과", out)
        self.assertIn("분량이 넘쳐요", out)


class Sync(Base):
    """harness-sync.sh 를 이 저장소(file://)를 원본으로 삼아 시험한다."""

    def setUp(self):
        self.src = tempfile.mkdtemp(prefix="harness-src-")
        self.addCleanup(shutil.rmtree, self.src, True)
        for d in ("signed", "dist"):
            shutil.copytree(os.path.join(ROOT, d), os.path.join(self.src, d))

    def sync(self, p, *args, data="", **env):
        e = p.env(HARNESS_SOURCE_URL="file://" + self.src, **env)
        e.pop("HARNESS_ROOT", None)
        return subprocess.run(["bash", SYNC, *args], input=data, capture_output=True, text=True, env=e, cwd=p.dir)

    def version(self):
        with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
            return f.read().strip()

    def test_first_install_then_latest(self):
        p = self.project({"set": "C-submission"})
        r = self.sync(p, "sync")
        self.assertIn("새 판 %s 받음" % self.version(), r.stdout)
        self.assertTrue(os.path.isfile(os.path.join(p.dir, ".claude/harness/core/hooks/stop-check.py")))
        self.assertTrue(os.path.isfile(os.path.join(p.dir, ".claude/agents/심사위원.md")))
        self.assertFalse(os.path.exists(os.path.join(p.dir, ".claude/harness/sets/A-research")))
        self.assertIn("최신 판", self.sync(p, "sync").stdout)

    def test_hash_mismatch_keeps_old(self):
        p = self.project({"set": "B-app"})
        self.sync(p, "sync")
        with open(os.path.join(self.src, "dist", "B-app.tar"), "ab") as f:
            f.write(b"tampered")
        ap = os.path.join(self.src, "signed", "approved.json")
        with open(ap, encoding="utf-8") as f:
            a = json.load(f)
        a["version"] = "9.9.9"
        with open(ap, "w", encoding="utf-8") as f:
            json.dump(a, f)
        r = self.sync(p, "sync")
        self.assertIn("승인 목록(해시)과 달라", r.stdout)
        self.assertEqual(p.read(".claude/harness/VERSION").strip(), self.version())

    def test_network_failure_keeps_old(self):
        p = self.project({"set": "B-app"})
        self.sync(p, "sync")
        shutil.rmtree(os.path.join(self.src, "signed"))
        r = self.sync(p, "sync")
        self.assertIn("네트워크", r.stdout)
        self.assertTrue(os.path.isfile(os.path.join(p.dir, ".claude/harness/VERSION")))

    def test_start_shows_status(self):
        p = self.project({"set": "A-research"}, PROGRESS.format(cmd="true"))
        r = self.sync(p, "start", data=json.dumps({"source": "startup", "session_id": "x"}))
        self.assertEqual(r.returncode, 0)
        self.assertIn("[장치 상태]", r.stdout)
        self.assertIn("받음", r.stdout)
        self.assertIn("## 완료 조건", r.stdout)

    def test_hook_dispatch(self):
        p = self.project({"set": "B-app", "unattended": True})
        add = json.dumps({"tool_name": "x__add_repo", "tool_input": {"repo": "b"}})
        # 판이 아직 없으면 통과
        r = self.sync(p, "hook", "repo-guard", data=add)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.sync(p, "sync")
        r = self.sync(p, "hook", "repo-guard", data=add)
        self.assertIn('"deny"', r.stdout)
        # 이상한 이름은 무시
        self.assertEqual(self.sync(p, "hook", "../../x", data=add).returncode, 0)
        # 영어 질문은 끝 번호 2
        q = json.dumps({"tool_name": "AskUserQuestion", "tool_input": {"questions": [
            {"question": "Should we go ahead and merge this pull request now?"}]}})
        self.assertEqual(self.sync(p, "hook", "korean-guard", "pre", data=q).returncode, 2)

    def test_project_work_guard(self):
        p = self.project({"set": "B-app"})
        self.sync(p, "sync")
        p.write(".claude/hooks/work_guard.py", "import sys\nsys.stdin.read()\nprint('업무 지킴이', file=sys.stderr)\nsys.exit(2)\n")
        q = json.dumps({"tool_name": "AskUserQuestion", "tool_input": {"questions": [{"question": "할까요?"}]}})
        r = self.sync(p, "hook", "korean-guard", "pre", data=q)
        self.assertEqual(r.returncode, 2)
        self.assertIn("업무 지킴이", r.stderr)


class Bundles(Base):
    def test_bundles_match_source(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_bundles.py"), "--check"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_settings_valid(self):
        for rel in ("core/settings.core.json", "core/plugin-hooks.json", ".claude-plugin/plugin.json",
                    ".claude-plugin/marketplace.json", "signed/approved.json"):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                json.load(f)

    def test_claude_template_short(self):
        with open(os.path.join(ROOT, "core", "CLAUDE.template.md"), encoding="utf-8") as f:
            self.assertLess(len(f.read().splitlines()), 200)


if __name__ == "__main__":
    unittest.main()
