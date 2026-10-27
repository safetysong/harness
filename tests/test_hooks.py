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
PENDING = ".claude/harness-state/mistakes-pending.md"  # report 전 실수 기록이 모이는 곳
LIVE = ".claude/harness-state/harness"  # 세션 시작 때 받은 판이 풀리는 곳(git에 안 들어감)
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
        # 커밋 뒤 git이 뒤에서 하는 정리(maintenance)를 끈다. 켜 두면 .git을 지우는 시험과 부딪힌다.
        subprocess.run(["git", "-c", "maintenance.auto=false", "-c", "gc.auto=0", *args], cwd=self.dir,
                       check=True, capture_output=True)

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
        for k in ("HARNESS_UNATTENDED", "HARNESS_READ_ONLY", "HARNESS_ROOT"):  # 시험이 돌리는 환경에 흔들리지 않게
            env.pop(k, None)
        env.update(extra)
        return env

    def hook(self, name, data, *args):
        p = subprocess.run([sys.executable, os.path.join(HOOKS, name + ".py"), *args],
                           input=json.dumps(data, ensure_ascii=False), capture_output=True, text=True,
                           cwd=self.dir, env=self.env())
        out = json.loads(p.stdout) if p.stdout.strip().startswith("{") else p.stdout
        return p.returncode, out, p.stderr

    def report(self, **extra):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "core", "tools", "report.py"), self.dir],
                           capture_output=True, text=True, env=self.env(**extra), cwd=self.dir)
        return r.stdout

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

    def test_powershell_clone(self):
        p = self.project({"repo": "me/mine"})
        _, out, _ = p.hook("repo-guard", {"tool_name": "PowerShell",
                                          "tool_input": {"command": "Set-Location C:\\w; git clone https://github.com/other/x"}})
        self.assertEqual(decision(out), "ask")
        _, out, _ = p.hook("repo-guard", {"tool_name": "PowerShell",
                                          "tool_input": {"command": "git clone https://github.com/me/mine.git"}})
        self.assertEqual(out, "")
        for cmd in ("git.exe clone https://github.com/other/x", "Git clone https://github.com/other/x",
                    "gh.exe repo clone other/x"):
            _, out, _ = p.hook("repo-guard", {"tool_name": "PowerShell", "tool_input": {"command": cmd}})
            self.assertEqual(decision(out), "ask", cmd)

    def test_korean_input_in_cp949_locale(self):
        p = self.project({"repo": "me/mine"})
        data = json.dumps({"tool_name": "PowerShell", "tool_input": {
            "command": "git clone https://github.com/other/x  # 한글"}}, ensure_ascii=False).encode("utf-8")
        r = subprocess.run(["bash", SYNC, "hook", "repo-guard"], input=data, capture_output=True,
                           env=p.env(HARNESS_ROOT=ROOT, PYTHONIOENCODING="cp949"), cwd=p.dir)
        out = json.loads(r.stdout.decode("utf-8"))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertIn("저장소 지킴이", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_unattended_env_overrides(self):
        p = self.project({"unattended": True})
        d = json.dumps({"tool_name": "x__add_repo", "tool_input": {"repo": "b"}})
        for env, want in (("0", "ask"), ("off", "ask"), ("1", "deny"), ("", "deny")):
            r = subprocess.run([sys.executable, os.path.join(HOOKS, "repo-guard.py")], input=d, capture_output=True,
                               text=True, env=p.env(HARNESS_UNATTENDED=env), cwd=p.dir)
            self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], want, env)

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

    def test_post_reads_only_new_part(self):
        p = self.project({})
        p.set_transcript([user("시작"), say("한국어로 쓴 첫 알림이에요."), tool("Bash", command="ls")])
        data = {"transcript_path": p.transcript, "session_id": "k4"}
        self.assertEqual(p.hook("korean-guard", data, "post")[0], 0)
        with open(p.transcript, "a", encoding="utf-8") as f:
            f.write(json.dumps(say("Now checking every single file in the repository again."), ensure_ascii=False) + "\n")
        self.assertEqual(p.hook("korean-guard", data, "post")[0], 2)
        self.assertEqual(p.hook("korean-guard", data, "post")[0], 0)  # 이미 읽은 곳은 다시 안 봄

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

    def test_progress_check_off(self):
        p = self.project({"progress_check": False}, PROGRESS.format(cmd="true"))
        p.set_transcript([user("고쳐 줘"), tool("Edit", file_path=os.path.join(p.dir, "앱.py"))])
        self.assertEqual(p.hook("stop-check", {"session_id": "pc", "transcript_path": p.transcript})[1], "")

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
    def test_tool_failures_counted_apart(self):
        p = self.project({})
        data = {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
                "error": "cat: /tmp/a1/x.txt: No such file or directory"}
        self.assertEqual(p.hook("mistake-log", data)[1], "")
        data["error"] = "cat: /home/b2/y.txt: No such file or directory"
        _, out, _ = p.hook("mistake-log", data)
        self.assertIn("도구 고장", out["hookSpecificOutput"]["additionalContext"])
        # 도구 고장은 실수기록.md에 적지 않는다
        self.assertFalse(os.path.exists(os.path.join(p.dir, PENDING)))
        pending = json.loads(p.read(".claude/harness-state/stats-pending.json"))
        self.assertEqual(list(pending.values())[0]["도구실패"], 2)

    def test_claude_mistake_logged_on_second(self):
        p = self.project({"unattended": True})
        d = {"tool_name": "mcp__claude-code-remote__add_repo", "tool_input": {"owner": "a", "repo": "b"}}
        p.hook("repo-guard", d)
        self.assertFalse(os.path.exists(os.path.join(p.dir, PENDING)))
        p.hook("repo-guard", d)
        self.assertEqual(len([x for x in p.read(PENDING).splitlines() if x.startswith("- ")]), 1)

    def test_read_only_session_does_not_write(self):
        p = self.project({"unattended": True})
        p.set_transcript([user("safety-wiki 내용 확인해 줘. 읽기만 해")])
        d = {"tool_name": "x__add_repo", "tool_input": {"repo": "b"}, "transcript_path": p.transcript}
        p.hook("repo-guard", d)
        p.hook("repo-guard", d)
        self.assertFalse(os.path.exists(os.path.join(p.dir, PENDING)))
        p.hook("stop-check", {"session_id": "r1", "transcript_path": p.transcript})
        self.assertFalse(os.path.exists(os.path.join(p.dir, ".claude/harness-stats.jsonl")))
        self.assertIn("읽기 전용", p.report(HARNESS_READ_ONLY="1"))
        self.assertFalse(os.path.exists(os.path.join(p.dir, ".claude/harness-stats.jsonl")))
        p2 = self.project({"unattended": True, "read_only": True})
        d2 = {"tool_name": "x__add_repo", "tool_input": {"repo": "b"}}
        p2.hook("repo-guard", d2)
        p2.hook("repo-guard", d2)
        self.assertFalse(os.path.exists(os.path.join(p2.dir, PENDING)))

    def test_different_failures_not_logged(self):
        p = self.project({})
        p.hook("mistake-log", {"tool_name": "Bash", "error": "첫 번째 오류"})
        p.hook("mistake-log", {"tool_name": "Bash", "error": "전혀 다른 오류"})
        self.assertFalse(os.path.exists(os.path.join(p.dir, PENDING)))



class ReviewFixes(Base):
    def test_read_only_negation_and_later_logging(self):
        p = self.project({"unattended": True})
        p.set_transcript([user("읽기만 하지 말고 고쳐 줘")])
        d = {"tool_name": "x__add_repo", "tool_input": {"repo": "b"}, "transcript_path": p.transcript}
        p.hook("repo-guard", d)
        p.hook("repo-guard", d)
        self.assertTrue(os.path.exists(os.path.join(p.dir, PENDING)))
        q = self.project({"unattended": True})
        q.set_transcript([user("읽기만 해")])
        d["transcript_path"] = q.transcript
        q.hook("repo-guard", d)
        q.hook("repo-guard", d)
        self.assertFalse(os.path.exists(os.path.join(q.dir, PENDING)))
        # 같은 프로젝트의 다음 보통 세션에서는 적힌다
        q.set_transcript([user("고쳐 줘")])
        q.hook("repo-guard", d)
        self.assertTrue(os.path.exists(os.path.join(q.dir, PENDING)))

    def test_edit_failures_are_mistakes(self):
        p = self.project({})
        data = {"tool_name": "Edit", "error": "String to replace not found in file."}
        p.hook("mistake-log", data)
        _, out, _ = p.hook("mistake-log", data)
        self.assertIn("실수 기록", out["hookSpecificOutput"]["additionalContext"])
        self.assertIn("String to replace", p.read(PENDING))

    def test_missing_set_folder_named(self):
        p = self.project({"set": "B-ap"})
        _, out, _ = p.hook("session-start", {"source": "startup"})
        self.assertIn("B-ap(없음", out)

    def test_bad_stats_file_does_not_block_flush(self):
        p = self.project({"unattended": True})
        p.write(".claude/harness-stats.jsonl", '{"date": "2026-01-01", "counts": [1]}\nnot json\n')
        p.hook("repo-guard", {"tool_name": "x__add_repo", "tool_input": {"repo": "b"}})
        p.hook("stop-check", {"session_id": "b1"})
        p.report()
        self.assertIn("막음:저장소지킴이", p.read(".claude/harness-stats.jsonl"))


class Unattended(Base):
    def test_ignored_files_and_unattended_warn_only(self):
        p = self.project({}, PROGRESS.format(cmd="true"))
        p.write(".gitignore", "work/\n")
        p.set_transcript([user("돌려 줘"), tool("Write", file_path=os.path.join(p.dir, "work", "결과.md"))])
        # git이 무시하는 파일만 바꿨으면 진행상황을 안 적어도 됨
        self.assertEqual(p.hook("stop-check", {"session_id": "u1", "transcript_path": p.transcript})[1], "")
        p.set_transcript([user("돌려 줘"), tool("Write", file_path=os.path.join(p.dir, "소식.md"))])
        _, out, _ = p.hook("stop-check", {"session_id": "u2", "transcript_path": p.transcript})
        self.assertEqual(out["decision"], "block")
        q = self.project({"unattended": ["session_run"]}, PROGRESS.format(cmd="true"))
        q.set_transcript([user("돌려 줘"), tool("Write", file_path=os.path.join(q.dir, "소식.md"))])
        _, out, _ = q.hook("stop-check", {"session_id": "session_run", "transcript_path": q.transcript})
        self.assertNotIn("decision", out)  # 무인 실행은 막지 않고
        self.assertIn("무인 실행이라 막지 않고", out["systemMessage"])  # 알리기만
        _, out, _ = q.hook("stop-check", {"session_id": "session_human", "transcript_path": q.transcript})
        self.assertEqual(out["decision"], "block")  # 목록에 없는 세션(사람)은 그대로

    def test_unattended_session_list_for_repo_guard(self):
        p = self.project({"unattended": ["session_run"]})
        d = {"tool_name": "x__add_repo", "tool_input": {"repo": "b"}}
        self.assertEqual(decision(p.hook("repo-guard", dict(d, session_id="session_run"))[1]), "deny")
        self.assertEqual(decision(p.hook("repo-guard", dict(d, session_id="session_me"))[1]), "ask")


class ReviewFixes030(Base):
    def test_check_ignore_with_outside_path(self):
        p = self.project({}, PROGRESS.format(cmd="true"))
        p.write(".gitignore", "work/\n")
        p.set_transcript([user("돌려 줘"), tool("Write", file_path=os.path.join(p.dir, "work", "결과.md")),
                          tool("Write", file_path="/tmp/밖.md")])
        _, out, _ = p.hook("stop-check", {"session_id": "o1", "transcript_path": p.transcript})
        self.assertEqual(out["decision"], "block")  # 밖 파일 때문에 막히는 것은 맞음
        self.assertNotIn("결과.md", out["reason"])  # 무시된 파일은 이유에서 빠짐

    def test_cloud_session_id_forms(self):
        p = self.project({"unattended": ["session_ABC"]})
        d = json.dumps({"tool_name": "x__add_repo", "tool_input": {"repo": "b"}, "session_id": "uuid-1"})
        r = subprocess.run([sys.executable, os.path.join(HOOKS, "repo-guard.py")], input=d, capture_output=True,
                           text=True, env=p.env(CLAUDE_CODE_REMOTE_SESSION_ID="cse_ABC"), cwd=p.dir)
        self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_previous_turn_final_not_rechecked(self):
        p = self.project({})
        p.set_transcript([user("요약"), say("Here is the summary of everything we did today in detail.")])
        data = {"transcript_path": p.transcript, "session_id": "k9"}
        p.hook("korean-guard", data, "stop")  # 끝 점검이 기록 끝을 읽은 곳으로 둠
        with open(p.transcript, "a", encoding="utf-8") as f:
            for e in (user("다음 일"), tool("Bash", command="ls")):
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        self.assertEqual(p.hook("korean-guard", data, "post")[0], 0)

    def test_older_live_copy_is_not_used(self):
        p = self.project({"set": "B-app"}, PROGRESS.format(cmd="true"))
        os.makedirs(os.path.join(p.dir, ".claude/harness-state/harness/core/hooks"))
        p.write(".claude/harness-state/harness/VERSION", "0.0.1\n")
        os.makedirs(os.path.join(p.dir, ".claude/harness"))
        shutil.copytree(os.path.join(ROOT, "core"), os.path.join(p.dir, ".claude/harness/core"))
        p.write(".claude/harness/VERSION", "9.0.0\n")
        e = p.env(HARNESS_SOURCE_URL="file:///없는곳")
        r = subprocess.run(["bash", SYNC, "start"], input=json.dumps({"source": "startup"}), capture_output=True,
                           text=True, env=e, cwd=p.dir)
        self.assertIn("판 9.0.0", r.stdout)  # 더 새것인 저장소 판으로 돎

    def test_hooks_leave_no_pycache(self):
        p = self.project({"set": "B-app"}, PROGRESS.format(cmd="true"))
        e = p.env(HARNESS_SOURCE_URL="file://" + ROOT)
        e.pop("PYTHONDONTWRITEBYTECODE", None)
        subprocess.run(["bash", SYNC, "upgrade"], capture_output=True, text=True, env=e, cwd=p.dir)
        subprocess.run(["bash", SYNC, "hook", "repo-guard"], input="{}", capture_output=True, text=True, env=e, cwd=p.dir)
        found = [d for d, _, _ in os.walk(os.path.join(p.dir, ".claude")) if d.endswith("__pycache__")]
        self.assertEqual(found, [])

    def test_old_sync_script_noticed(self):
        p = self.project({}, PROGRESS.format(cmd="true"))
        p.write(".claude/harness-sync.sh", "# 옛 받기 장치\n")
        _, out, _ = p.hook("session-start", {"source": "startup"})
        self.assertIn("받기 장치(.claude/harness-sync.sh)가 옛 판", out)


class ReviewFixes032(Base):
    def test_unattended_never_blocks_stop(self):
        p = self.project({"unattended": True, "set": "B-app"}, PROGRESS.format(cmd="exit 1"))
        p.write("깨진.json", "{")
        _, out, _ = p.hook("stop-check", {"session_id": "n1", "transcript_path": p.transcript})
        self.assertNotIn("decision", out)
        self.assertIn("무인 실행이라 막지 않고", out["systemMessage"])
        self.assertIn("깨진.json", p.read(".claude/harness-state/unattended-stop.log"))

    def test_unattended_from_claude_md(self):
        p = self.project({"unattended_from": "CLAUDE.md:실행"})
        p.write("CLAUDE.md", "## 세션 주소\n| 메인: session_MAIN |\n| 실행: session_RUN |\n")
        d = json.dumps({"tool_name": "x__add_repo", "tool_input": {"repo": "b"}, "session_id": "u"})
        for remote, want in (("cse_RUN", "deny"), ("cse_MAIN", "ask")):
            r = subprocess.run([sys.executable, os.path.join(HOOKS, "repo-guard.py")], input=d, capture_output=True,
                               text=True, env=p.env(CLAUDE_CODE_REMOTE_SESSION_ID=remote), cwd=p.dir)
            self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], want, remote)

    def test_repo_guard_false_positives_gone(self):
        p = self.project({"repo": "me/mine", "unattended": True})
        quiet = [
            'echo "git clone https://github.com/other/x"',
            "grep -rn 'git remote add' .",
            "git commit -m \"문서: git clone https://github.com/other/x 예시\"",
            "cat > a.md <<'EOF'\ngit clone https://github.com/other/x\nEOF",
            "git clone http://127.0.0.1:44327/git/me/mine",
            "git -C /w remote set-url origin https://github.com/me/mine.git",
            "git clone --depth 1 https://github.com/me/mine",
            "git clone -b dev https://github.com/me/mine/tree/main",
            "git remote add -t main origin https://github.com/me/mine",
            "git commit -m \"첫 줄\ngit clone https://github.com/other/x\"",
        ]
        for c in quiet:
            _, out, _ = p.hook("repo-guard", {"tool_name": "Bash", "tool_input": {"command": c}})
            self.assertEqual(out, "", c)
        loud = [
            "cd /w && git clone https://github.com/other/x",
            "FOO=1 git clone --depth 1 git@github.com:other/x.git",
            "/usr/bin/git remote add up https://github.com/other/x",
            "gh repo clone other/x",
            "git clone",
            "sudo git clone https://github.com/other/x",
            "env FOO=1 git clone https://github.com/other/x",
            "bash -c \"git clone https://github.com/other/x\"",
            "cat <<< foo\ngit clone https://github.com/other/x",
            "echo \"<<EOF\"\ngit clone https://github.com/other/x",
            "echo it's; git clone https://github.com/other/x",
            "git clone https://gitlab.com/me/mine",
        ]
        for c in loud:
            _, out, _ = p.hook("repo-guard", {"tool_name": "Bash", "tool_input": {"command": c}})
            self.assertEqual(decision(out), "deny", c)

    def test_windows_full_path_git(self):
        p = self.project({"repo": "me/mine", "unattended": True})
        _, out, _ = p.hook("repo-guard", {"tool_name": "PowerShell", "tool_input": {
            "command": "C:\\Git\\bin\\git.exe clone https://github.com/other/x"}})
        self.assertEqual(decision(out), "deny")

    def test_one_line_table(self):
        p = self.project({"unattended_from": "CLAUDE.md:실행"})
        p.write("CLAUDE.md", "| 메인: session_MAIN | 실행: session_RUN |\n")
        r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import harness_common as hc; "
                            "print(sorted(hc.ids_from_file('CLAUDE.md:실행')))" % HOOKS],
                           capture_output=True, text=True, env=p.env(), cwd=p.dir)
        self.assertEqual(r.stdout.strip(), "['session_RUN']")
        # 이름표 칸과 id 칸이 따로인 표(ai-digest 모양)
        p.write("CLAUDE.md", "| 메인: ★ AI 비서 | `session_MAIN` | ai-digest |\n"
                             "| 실행: ⚙️ 자동 실행 (안 여셔도 됨) | `session_RUN2` | ai-digest | 매일 9시 |\n"
                             "| 메인: 다른 | `session_X` | 실행: 둘째 | `session_RUN3` |\n")
        r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import harness_common as hc; "
                            "print(sorted(hc.ids_from_file('CLAUDE.md:실행')))" % HOOKS],
                           capture_output=True, text=True, env=p.env(), cwd=p.dir)
        self.assertEqual(r.stdout.strip(), "['session_RUN2', 'session_RUN3']")
        p.write("CLAUDE.md", "| 실행: | https://claude.ai/code/session_LINK | 비고 | session_NOTE 참고 |\n")
        r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import harness_common as hc; "
                            "print(sorted(hc.ids_from_file('CLAUDE.md:실행')))" % HOOKS],
                           capture_output=True, text=True, env=p.env(), cwd=p.dir)
        self.assertEqual(r.stdout.strip(), "['session_LINK']")
        p.write("CLAUDE.md", "| 실행: 이름만 | | 메인: 비서 | session_MAIN |\n")
        r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import harness_common as hc; "
                            "print(sorted(hc.ids_from_file('CLAUDE.md:실행')))" % HOOKS],
                           capture_output=True, text=True, env=p.env(), cwd=p.dir)
        self.assertEqual(r.stdout.strip(), "[]")

    def test_stop_sees_interim_english_post_missed(self):
        p = self.project({})
        p.set_transcript([user("고쳐 줘"), say("Let me read the file and then edit the configuration."),
                          tool("Read", file_path="a"), say("다 고쳤어요.")])
        self.assertEqual(p.hook("korean-guard", {"transcript_path": p.transcript, "session_id": "k10"}, "stop")[0], 2)

    def test_post_matcher_is_narrow(self):
        for rel in ("core/settings.core.json", "core/plugin-hooks.json"):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                post = json.load(f)["hooks"]["PostToolUse"]
            self.assertNotIn("*", [g.get("matcher") for g in post], rel)


class Stats(Base):
    def test_hooks_never_touch_tracked_logs_and_report_moves_them(self):
        p = self.project({"unattended": True}, PROGRESS.format(cmd="echo 돌았음 >> ran.txt"))
        p.write(".gitignore", "ran.txt\n.claude/harness-state/\n")
        p.git("add", "-A")
        p.git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "준비")
        d = {"tool_name": "x__add_repo", "tool_input": {"repo": "b"}}
        for _ in range(2):
            p.hook("repo-guard", d)
            p.hook("stop-check", {"session_id": "t1", "transcript_path": p.transcript})
        # 훅만 돌았을 때는 git 작업 트리가 깨끗하다(예약 실행의 git pull 이 안 깨짐)
        st = subprocess.run(["git", "status", "--porcelain"], cwd=p.dir, capture_output=True, text=True).stdout
        self.assertEqual(st, "")
        _, out, _ = p.hook("session-start", {"source": "startup"})
        self.assertIn("기록 대기(숫자 1일, 실수 1줄)", out)
        self.assertIn("옮겼어요", p.report())
        rows = [json.loads(x) for x in p.read(".claude/harness-stats.jsonl").splitlines()]
        self.assertEqual(len(rows), 1)  # 하루 한 줄
        self.assertEqual(rows[0]["counts"]["막음:저장소지킴이"], 2)
        self.assertEqual(rows[0]["counts"]["실수기록"], 1)
        self.assertEqual(len([x for x in p.read("실수기록.md").splitlines() if x.startswith("- ")]), 1)
        self.assertIn("옮길 기록이 없어요", p.report())
        # 장치 기록이 바뀐 것만으로는 완료 조건을 다시 돌리지 않는다
        p.hook("stop-check", {"session_id": "t1", "transcript_path": p.transcript})
        self.assertEqual(p.read("ran.txt").count("돌았음"), 1)


class SessionStart(Base):
    def test_shows_progress_and_status(self):
        p = self.project({"set": "C-submission", "repo": "me/mine",
                          "submission": {"deadline": "2099-01-01"}}, PROGRESS.format(cmd="true"))
        p.write("CLAUDE.md", "규칙")
        _, out, _ = p.hook("session-start", {"source": "startup"})
        for word in ("## 지금 상태", "## 완료 조건", "## 다음 할 일", "## 꼭 기억", "세트 C 제출물 안내",
                     "[마감] 2099-01-01", "[장치 상태]", "지침 2자", "저장소(me/mine)", "Read로 먼저"):
            self.assertIn(word, out)

    def test_two_sets_and_core_only(self):
        p = self.project({"set": ["A-research", "B-app"]}, PROGRESS.format(cmd="true"))
        _, out, _ = p.hook("session-start", {"source": "startup"})
        self.assertIn("세트 A 글·조사 + B 앱·코드", out)
        self.assertIn("세트 A 글·조사 안내", out)
        self.assertIn("세트 B 앱·코드 안내", out)
        p.write("깨진.json", "{")
        p.write("원고.md", "주장[^1]\n")
        _, out, _ = p.hook("stop-check", {"session_id": "two", "transcript_path": p.transcript})
        self.assertIn("깨진.json", out["reason"])
        self.assertIn("각주", out["reason"])
        p2 = self.project({"set": "core"})
        _, out, _ = p2.hook("session-start", {"source": "startup"})
        self.assertIn("바닥판만(세트 없음)", out)

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
        # 받은 판은 git에 안 들어가는 곳에 풀리고, 저장소 자리와 에이전트는 그대로
        self.assertTrue(os.path.isfile(os.path.join(p.dir, LIVE, "core/hooks/stop-check.py")))
        self.assertFalse(os.path.exists(os.path.join(p.dir, ".claude/harness")))
        self.assertFalse(os.path.exists(os.path.join(p.dir, ".claude/agents")))
        self.assertFalse(os.path.exists(os.path.join(p.dir, LIVE, "sets/A-research")))
        self.assertIn("최신 판", self.sync(p, "sync").stdout)
        # upgrade 가 저장소 자리로 싣고 에이전트를 맞춘다
        self.assertEqual(self.sync(p, "upgrade").returncode, 0)
        self.assertTrue(os.path.isfile(os.path.join(p.dir, ".claude/harness/core/hooks/stop-check.py")))
        self.assertFalse(os.path.exists(os.path.join(p.dir, LIVE)))
        self.assertTrue(os.path.isfile(os.path.join(p.dir, ".claude/agents/심사위원.md")))
        self.assertIn("최신 판", self.sync(p, "sync").stdout)

    def test_resume_checks_but_throttled(self):
        p = self.project({"set": "B-app"})
        r = self.sync(p, "start", data=json.dumps({"source": "resume"}))
        self.assertIn("받음", r.stdout)  # 처음이면 이어 하기에서도 받는다
        shutil.rmtree(os.path.join(p.dir, LIVE))
        r = self.sync(p, "start", data=json.dumps({"source": "resume"}))
        self.assertFalse(os.path.exists(os.path.join(p.dir, LIVE)))  # 30분 안에는 다시 안 받음

    def test_tracked_differs_from_live_shows_upgrade_needed(self):
        p = self.project({"set": "B-app"}, PROGRESS.format(cmd="true"))
        self.sync(p, "upgrade")
        p.write(".claude/harness/VERSION", "0.0.1\n")
        r = self.sync(p, "start", data=json.dumps({"source": "startup"}))
        self.assertIn("upgrade PR 필요", r.stdout)

    def test_two_sets_and_core(self):
        p = self.project({"set": ["A-research", "C-submission"]})
        r = self.sync(p, "sync")
        self.assertIn("받음", r.stdout)
        for rel in ("sets/A-research/set.json", "sets/C-submission/set.json", "core/hooks/stop-check.py"):
            self.assertTrue(os.path.isfile(os.path.join(p.dir, LIVE, rel)), rel)
        self.assertFalse(os.path.exists(os.path.join(p.dir, LIVE, "sets/B-app")))
        self.sync(p, "upgrade")
        self.assertTrue(os.path.isfile(os.path.join(p.dir, ".claude/agents/근거확인자.md")))
        self.assertTrue(os.path.isfile(os.path.join(p.dir, ".claude/agents/심사위원.md")))
        self.assertIn("최신 판", self.sync(p, "sync").stdout)
        p2 = self.project({"set": "core"})
        self.assertIn("받음", self.sync(p2, "sync").stdout)
        self.assertFalse(os.path.exists(os.path.join(p2.dir, LIVE, "sets")))

    def test_upgrade(self):
        p = self.project({"set": "B-app"})
        old_hook = {"type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/harness-sync.sh\" hook stop-check"}
        mine = {"type": "command", "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/stop-check.sh\""}
        p.write(".claude/hooks/stop-check.sh", "#!/bin/bash\n# 진행상황.md 갱신 안 했으면 막기\nexit 0\n")
        p.write(".claude/settings.json", json.dumps({"hooks": {"Stop": [{"hooks": [old_hook]}, {"hooks": [mine]}],
                                                              "OldEvent": [{"hooks": [old_hook]}]},
                                                    "permissions": {"deny": ["Bash(mine:*)"]}}))
        p.write(".claude/harness-sync.sh", "# 옛 받기 장치\n")
        r = self.sync(p, "upgrade")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(SYNC, encoding="utf-8") as f:
            self.assertEqual(p.read(".claude/harness-sync.sh"), f.read())
        st = json.loads(p.read(".claude/settings.json"))
        self.assertNotIn("OldEvent", st["hooks"])
        stop_cmds = [h["command"] for g in st["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(sum("hook stop-check" in c for c in stop_cmds), 1)
        self.assertIn(mine["command"], stop_cmds)
        self.assertIn("Bash(mine:*)", st["permissions"]["deny"])
        self.assertTrue(any("PowerShell" in g.get("matcher", "") for g in st["hooks"]["PreToolUse"]))
        self.assertIs(json.loads(p.read(".claude/harness.json"))["progress_check"], False)
        self.assertIn("옛 진행상황 검사 장치", r.stdout)
        self.assertIn(".claude/harness-state/", p.read(".gitignore"))
        # 다시 해도 같은 결과
        self.sync(p, "upgrade")
        self.assertEqual(json.loads(p.read(".claude/settings.json")), st)

    def test_upgrade_finds_quoted_script_and_keeps_bad_json(self):
        p = self.project({"set": "B-app"})
        mine = {"type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/stop_check.py"}
        p.write(".claude/hooks/stop_check.py", "# 진행상황 검사\n")
        p.write(".claude/settings.json", json.dumps({"hooks": {"Stop": [{"hooks": [mine]}]}}))
        r = self.sync(p, "upgrade")
        self.assertIn("stop_check.py", r.stdout)
        self.assertIs(json.loads(p.read(".claude/harness.json"))["progress_check"], False)
        q = self.project({"set": "B-app"})
        q.write(".claude/hooks/stop_check.py", "# 진행상황 검사\n")
        q.write(".claude/settings.json", json.dumps({"hooks": {"Stop": [{"hooks": [mine]}]}}))
        self.sync(q, "sync")
        q.write(".claude/harness.json", '{"set": "B-app", "repo": "a/b",}')
        r = subprocess.run([sys.executable, os.path.join(q.dir, LIVE, "core/tools/merge_settings.py"), q.dir],
                           capture_output=True, text=True)
        self.assertIn("읽지 못했어요", r.stdout)
        self.assertEqual(q.read(".claude/harness.json"), '{"set": "B-app", "repo": "a/b",}')

    def test_upgrade_bad_set_type(self):
        p = self.project({"set": ["A-research", 1]})
        p.write(".claude/harness-sync.sh", "# 옛 받기 장치\n")
        r = self.sync(p, "upgrade")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(p.read(".claude/harness-sync.sh"), "# 옛 받기 장치\n")

    def test_upgrade_stops_on_bad_hash(self):
        p = self.project({"set": "B-app"})
        with open(os.path.join(self.src, "dist", "B-app.tar"), "ab") as f:
            f.write(b"x")
        p.write(".claude/harness-sync.sh", "# 옛 받기 장치\n")
        r = self.sync(p, "upgrade")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(p.read(".claude/harness-sync.sh"), "# 옛 받기 장치\n")
        self.assertFalse(os.path.exists(os.path.join(p.dir, ".claude/settings.json")))

    def test_extracted_files_get_current_time(self):
        p = self.project({"set": "B-app"})
        self.sync(p, "sync")
        import time as _t
        for rel in ("VERSION", "core/hooks/stop-check.py"):
            self.assertGreater(os.path.getmtime(os.path.join(p.dir, LIVE, rel)), _t.time() - 600, rel)

    def test_hash_mismatch_keeps_old(self):
        p = self.project({"set": "B-app"})
        self.sync(p, "sync")
        p.write(LIVE + "/VERSION", "0.0.1\n")  # 지금 판이 옛 판이라 새로 받으려 함
        with open(os.path.join(self.src, "dist", "B-app.tar"), "ab") as f:
            f.write(b"tampered")  # 판 번호는 같은데 내용이 바뀜 = 바꿔치기
        r = self.sync(p, "sync")
        self.assertIn("승인 목록(해시)과 달라", r.stdout)
        self.assertEqual(p.read(LIVE + "/VERSION").strip(), "0.0.1")

    def test_stale_cache_still_warns(self):
        p = self.project({"set": "B-app"})
        self.sync(p, "sync")
        ap = os.path.join(self.src, "signed", "approved.json")
        with open(ap, encoding="utf-8") as f:
            a = json.load(f)
        a["version"] = "9.9.9"  # 목록은 새 판인데 묶음은 아직 옛 판(캐시)
        a["bundles"]["B-app"]["sha256"] = "0" * 64
        with open(ap, "w", encoding="utf-8") as f:
            json.dump(a, f)
        r = self.sync(p, "sync")
        self.assertIn("경고", r.stdout)  # 받지 않은 묶음은 믿지 않으므로 늘 경고
        self.assertIn("몇 분 뒤 저절로 풀릴 수", r.stdout)

    def test_network_failure_keeps_old(self):
        p = self.project({"set": "B-app"})
        self.sync(p, "sync")
        shutil.rmtree(os.path.join(self.src, "signed"))
        r = self.sync(p, "sync")
        self.assertIn("네트워크", r.stdout)
        self.assertTrue(os.path.isfile(os.path.join(p.dir, LIVE, "VERSION")))

    def test_start_shows_status(self):
        p = self.project({"set": "A-research"}, PROGRESS.format(cmd="true"))
        r = self.sync(p, "start", data=json.dumps({"source": "startup", "session_id": "x"}))
        self.assertEqual(r.returncode, 0)
        self.assertIn("[장치 상태]", r.stdout)
        self.assertIn("받음", r.stdout)
        self.assertIn("## 완료 조건", r.stdout)

    def test_start_without_python_says_so(self):
        p = self.project({"set": "B-app"})
        bindir = os.path.join(p.dir, "onlybash")
        os.makedirs(bindir)
        for tool in ("bash", "cat"):
            os.symlink(shutil.which(tool), os.path.join(bindir, tool))
        e = p.env(PATH=bindir)
        r = subprocess.run([shutil.which("bash"), SYNC, "start"], input="{}", capture_output=True, text=True,
                           env=e, cwd=p.dir)
        self.assertEqual(r.returncode, 0)
        self.assertIn("파이썬(3.8 이상)을 찾지 못해", r.stdout)

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

    def test_hooks_fail_open_and_cover_powershell(self):
        for rel in ("core/settings.core.json", "core/plugin-hooks.json"):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                hooks = json.load(f)["hooks"]
            for event, groups in hooks.items():
                for g in groups:
                    for h in g["hooks"]:
                        self.assertEqual(h.get("onFailure"), "continue", (rel, event))
            matchers = [g.get("matcher", "") for g in hooks["PreToolUse"]]
            self.assertTrue(any("PowerShell" in m and "Bash" in m for m in matchers), rel)

    def test_pre_guard_has_room_for_work_guard(self):
        # 한국어 pre 훅은 프로젝트 work_guard.py 까지 같이 돌리므로 시간을 넉넉히(옛 설치는 20초)
        for rel in ("core/settings.core.json", "core/plugin-hooks.json"):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                hooks = json.load(f)["hooks"]["PreToolUse"]
            pre = [h for g in hooks for h in g["hooks"] if "korean-guard pre" in h["command"]]
            self.assertTrue(pre and all(h["timeout"] >= 20 for h in pre), rel)

    def test_claude_template_short(self):
        with open(os.path.join(ROOT, "core", "CLAUDE.template.md"), encoding="utf-8") as f:
            self.assertLess(len(f.read().splitlines()), 200)


if __name__ == "__main__":
    unittest.main()
