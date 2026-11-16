"""Codex hook contracts and Crew policies, using only temporary boards."""
import tests  # Install the production-service connection guard.
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from crew import board, branch_claims, codex_guard, codex_runtime, goals, runtimes, store


PLUGIN = Path(__file__).resolve().parents[1]


def init_repo(root):
    hooks = Path(root).parent / 'empty-git-hooks'
    hooks.mkdir(exist_ok=True)
    for args in (('init',), ('config', 'core.hooksPath', str(hooks)), ('config', 'user.name', 'Task Branch Fixture'),
                 ('config', 'user.email', 'task-branch@example.invalid'), ('config', 'commit.gpgsign', 'false'),
                 ('commit', '--allow-empty', '-m', 'initial')):
        subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, timeout=20)


class CodexGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="crew-codex-guard-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.home, self.root = self.base / "home", self.base / "repo"
        self.root.mkdir()
        self.conn = store.connect(self.home)
        self.addCleanup(self.conn.close)
        (self.home / "blacklist.txt").write_text("", encoding="utf-8")
        board.init_project(self.conn, "demo", self.root)
        for identity in ("member", "other", "admin"):
            board.register_session(self.conn, identity, "demo", self.root, None, "main", name=identity)
            runtimes.set(self.conn, identity, 'codex')

    def payload(self, command="echo ok", tool="Bash", **extra):
        return {"hook_event_name": "PreToolUse", "session_id": "native-codex-thread", "turn_id": "turn-1",
                "tool_use_id": "call-1", "cwd": str(self.root), "tool_name": tool,
                "tool_input": {"command": command}, **extra}

    def check(self, command="echo ok", tool="Bash", session="member", **extra):
        return codex_guard.check(self.home, session, self.payload(command, tool, **extra))

    @staticmethod
    def patch(*operations):
        return "*** Begin Patch\n" + "\n".join(operations) + "\n*** End Patch\n"

    def test_native_shell_passes_existing_blacklist_and_lease_guard(self):
        (self.home / "blacklist.txt").write_text("Bash :: forbidden :: policy\n", encoding="utf-8")
        self.assertIn("policy", self.check("echo forbidden"))
        self.assertIsNone(self.check("echo allowed"))
        board.add_resource(self.conn, "demo", "phone", guard_regex=r"\badb\b")
        board.take(self.conn, "other", "phone", "test")
        self.assertIn("设备正在被借用", self.check("adb devices"))
        self.assertIsNone(self.check("adb devices", session="other"))

    def test_old_codex_carrier_is_denied_after_switch_back_without_writes_or_migration(self):
        self.assertIsNone(self.check())
        runtimes.set(self.conn, 'member', 'claude')
        before = self.conn.total_changes
        with mock.patch('crew.store.connect', side_effect=AssertionError('guard must stay read-only')), \
             mock.patch('crew.guard.check', side_effect=AssertionError('inactive carrier cannot reach policy execution')):
            self.assertIn('inactive', self.check())
        self.assertEqual(self.conn.total_changes, before)

    def test_git_admin_guard_uses_bound_crew_identity(self):
        self.conn.execute("INSERT INTO git_management(project,enabled_at,base,admin_session_id) VALUES(?,?,?,?)",
                          ("demo", store.now_iso(), "main", "admin"))
        # Native ids (including a spoofed admin id) never choose the actor.
        self.assertIn("crew git request", self.check("git push origin main", session_id="admin"))
        self.assertIsNone(self.check("git push origin main", session="admin"))
        self.assertIn("crew git request", self.check("gh pr merge 12 --squash"))
        self.assertIsNone(self.check("git status"))

    def test_patch_claims_cover_add_update_delete_and_both_move_paths(self):
        board.claim(self.conn, "other", "taken.txt")
        patches = [
            self.patch("*** Add File: taken.txt\n+new"),
            self.patch("*** Update File: taken.txt\n@@\n-old\n+new"),
            self.patch("*** Delete File: taken.txt"),
            self.patch("*** Update File: taken.txt\n*** Move to: free.txt\n@@\n-old\n+new"),
            self.patch("*** Update File: free.txt\n*** Move to: taken.txt\n@@\n-old\n+new"),
        ]
        for patch in patches:
            with self.subTest(patch=patch):
                self.assertIn("文件归属", self.check(patch, "apply_patch"))
                self.assertIsNone(self.check(patch, "apply_patch", session="other"))

    def test_later_file_in_multifile_patch_rejects_entire_call(self):
        board.claim(self.conn, "other", "last.txt")
        patch = self.patch("*** Add File: first.txt\n+safe", "*** Delete File: last.txt")
        self.assertIn("文件归属", self.check(patch, "apply_patch"))
        self.assertFalse((self.root / "first.txt").exists())

    def test_absolute_and_parent_relative_paths_are_checked(self):
        board.claim(self.conn, "other", "taken.txt")
        for path in (str(self.root / "taken.txt"), "sub/../taken.txt"):
            with self.subTest(path=path):
                self.assertIn("文件归属", self.check(self.patch("*** Delete File: " + path), "apply_patch"))

    def test_unclaimed_patch_and_own_claim_are_allowed(self):
        board.claim(self.conn, "member", "mine.txt")
        patch = self.patch("*** Update File: mine.txt\n@@\n-old\n+new", "*** Add File: new.txt\n+hello")
        self.assertIsNone(self.check(patch, "apply_patch"))

    def test_patch_blacklist_keeps_canonical_tool_and_original_content(self):
        (self.home / "blacklist.txt").write_text("apply_patch :: forbidden_content :: content policy\n", encoding="utf-8")
        patch = self.patch("*** Add File: ordinary.txt\n+forbidden_content")
        self.assertIn("content policy", self.check(patch, "apply_patch"))

    def test_alias_and_canonical_blacklists_check_each_absolute_path(self):
        for tool in ("Edit", "Write", "apply_patch"):
            with self.subTest(tool=tool):
                # Relative patch name alone cannot match this absolute suffix.
                escaped = str(self.root).replace("\\", "/").replace(".", r"\.") + "/secret.txt"
                (self.home / "blacklist.txt").write_text(tool + " :: " + escaped + " :: secret path\n", encoding="utf-8")
                patch = self.patch("*** Update File: safe.txt\n*** Move to: secret.txt\n@@\n-x\n+y")
                self.assertIn("secret path", self.check(patch, "apply_patch"))

    def test_patch_like_text_in_added_content_is_not_a_header(self):
        board.claim(self.conn, "other", "taken.txt")
        patch = self.patch("*** Add File: safe.txt\n+*** Delete File: taken.txt")
        self.assertIsNone(self.check(patch, "apply_patch"))

    def test_crlf_and_unicode_separators_follow_native_line_boundaries(self):
        board.claim(self.conn, "other", "taken.txt")
        patch = self.patch("*** Add File: safe.txt\n+text\u2028*** Delete File: taken.txt")
        self.assertEqual(codex_guard.patch_paths(patch), ["safe.txt"])
        self.assertIsNone(self.check(patch.replace("\n", "\r\n"), "apply_patch"))

    def test_malformed_or_unknown_hook_data_is_denied(self):
        cases = [None, [], {}, self.payload(hook_event_name="PostToolUse"),
                 self.payload(tool_name="unknown"), self.payload(tool_input=[]),
                 self.payload(tool_input={"command": 3}), self.payload(cwd="relative"),
                 self.payload(session_id=""), self.payload(tool_input={"command": ""})]
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertIn("BLOCKED", codex_guard.check(self.home, "member", payload))

    def test_malformed_patch_never_falls_through(self):
        cases = ["echo not a patch", self.patch(), self.patch("*** Unknown: bad"),
                 self.patch("*** Add File: \n+body"), self.patch("*** Update File: x"),
                 self.patch("*** Update File: x\n@@\nnot-a-hunk"),
                 self.patch("*** Move to: x"), self.patch("*** Add File: C:relative.txt\n+x"),
                 self.patch("*** Add File: x\n+body") + "ignored trailer\n"]
        for patch in cases:
            with self.subTest(patch=patch):
                self.assertIn("BLOCKED", self.check(patch, "apply_patch"))

    def test_missing_board_unknown_identity_and_closed_identity_are_denied(self):
        absent = self.base / "absent"
        self.assertIn("BLOCKED", codex_guard.check(absent, "member", self.payload()))
        self.assertFalse(absent.exists())
        self.assertIn("BLOCKED", self.check(session="missing"))
        board.set_status(self.conn, "member", "closed")
        self.assertIn("BLOCKED", self.check())

    def test_payload_project_does_not_replace_bound_membership(self):
        board.claim(self.conn, "other", "taken.txt")
        self.assertIn("文件归属", self.check(self.patch("*** Delete File: taken.txt"), "apply_patch", project="other"))

    def test_broken_blacklist_board_or_internal_exception_are_denied(self):
        (self.home / "blacklist.txt").write_text("malformed", encoding="utf-8")
        self.assertIn("BLOCKED", self.check("crew doctor"))
        (self.home / "blacklist.txt").write_text("", encoding="utf-8")
        with mock.patch("crew.codex_guard.guard.check", side_effect=RuntimeError("failure")):
            self.assertIn("BLOCKED", self.check())
        self.conn.execute("DROP TABLE resources")
        self.assertIn("BLOCKED", self.check("crew doctor"))

    def test_cli_emits_supported_deny_and_success_contracts(self):
        args = ["--home", str(self.home), "--session", "member"]
        for payload, denied in ((self.payload(), False), (self.payload(tool_name="unknown"), True)):
            out, err = io.StringIO(), io.StringIO()
            result = codex_guard.main(args, stdin=io.StringIO(json.dumps(payload)), stdout=out, stderr=err)
            self.assertEqual(result, 0)
            if denied:
                self.assertEqual(json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertIn("BLOCKED", err.getvalue())
            else:
                self.assertEqual(out.getvalue(), "")
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(codex_guard.main(args, stdin=io.StringIO("invalid json"), stdout=out, stderr=err), 0)
        self.assertEqual(json.loads(out.getvalue())["hookSpecificOutput"]["hookEventName"], "PreToolUse")

    def test_real_entrypoint_uses_supplied_temporary_identity(self):
        board.claim(self.conn, "other", "taken.txt")
        payload = self.payload(self.patch("*** Delete File: taken.txt"), "apply_patch")
        result = subprocess.run([sys.executable, str(PLUGIN / "crew_codex_hook.py"), "--home", str(self.home),
                                 "--session", "member"], input=json.dumps(payload), capture_output=True,
                                text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("文件归属", result.stderr)
        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def run_claim_hook(self, payload, session='member', execution=None):
        args = ['--home', str(self.home), '--session', session]
        if execution is not None:
            args += ['--execution', execution['id']]
            payload = {**payload, 'session_id': 'native-' + execution['id']}
        out, err = io.StringIO(), io.StringIO()
        code = codex_guard.main(args, stdin=io.StringIO(json.dumps(payload)), stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def branch_claims(self):
        return [dict(row) for row in self.conn.execute(
            'SELECT project,branch,session_id,goal_id FROM branch_claims ORDER BY branch')]

    def goal_execution(self, session):
        board.join(self.conn, 'admin', 'demo', 'Chief', self.root, None, 'main', role='leader')
        board.join(self.conn, 'member', 'demo', 'Worker', self.root, None, 'main', group_name='Chief')
        goal = goals.create(self.conn, 'demo', 'admin', 'Claim branch', 'Create task branch', ['Branch has owner'])
        if session != 'admin':
            goals.assign(self.conn, goal['id'], session, 'Create branch')
        execution = goals.create_execution(self.conn, goal['id'], session, runtime='codex')
        goals.bind_native(self.conn, execution['id'], 'codex', 'native-' + execution['id'])
        return goal, execution

    def test_main_allowed_shell_records_bound_session(self):
        self.assertEqual(self.run_claim_hook(self.payload('git switch -c ordinary')), (0, '', ''))
        self.assertEqual(self.branch_claims(), [
            {'project': 'demo', 'branch': 'ordinary', 'session_id': 'member', 'goal_id': None}])

    def test_main_goal_shell_records_bound_identity_and_goal_for_both_roles(self):
        expected = []
        for session in ('member', 'admin'):
            with self.subTest(session=session):
                goal, execution = self.goal_execution(session)
                payload = self.payload('git worktree add -b ' + session + '-branch ../tree')
                self.assertEqual(self.run_claim_hook(payload, session, execution), (0, '', ''))
                expected.append({'project': 'demo', 'branch': session + '-branch',
                                 'session_id': session, 'goal_id': goal['id']})
        self.assertEqual(self.branch_claims(), sorted(expected, key=lambda row: row['branch']))

    def test_goal_execution_cannot_open_a_second_task_branch(self):
        init_repo(self.root)
        _, execution = self.goal_execution('member')
        self.assertEqual(self.run_claim_hook(self.payload('git branch feature/task'), execution=execution), (0, '', ''))
        subprocess.run(['git', '-C', str(self.root), 'branch', 'feature/task'], check=True, capture_output=True, timeout=20)
        code, out, err = self.run_claim_hook(self.payload('git switch -c feature/task-fix'), execution=execution)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertIn('本任务已有分支 feature/task', err)
        self.assertEqual([row['branch'] for row in self.branch_claims()], ['feature/task'])
        self.assertEqual(self.run_claim_hook(self.payload('git switch -c ordinary')), (0, '', ''))

    def test_main_denied_shell_does_not_record_for_ordinary_or_goal_session(self):
        self.conn.execute('INSERT INTO git_management(project,enabled_at,base,admin_session_id) VALUES(?,?,?,?)',
                          ('demo', store.now_iso(), 'main', 'admin'))
        _, execution = self.goal_execution('member')
        for scoped in (None, execution):
            with self.subTest(goal=scoped is not None):
                code, out, err = self.run_claim_hook(self.payload('git push; git switch -c blocked'), execution=scoped)
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(out)['hookSpecificOutput']['permissionDecision'], 'deny')
                self.assertIn('crew git request', err)
                self.assertEqual(self.branch_claims(), [])

    def test_main_apply_patch_does_not_enter_branch_recorder(self):
        patch = self.patch('*** Add File: notes.txt\n+git switch -c not-a-shell')
        _, execution = self.goal_execution('member')
        for scoped in (None, execution):
            with self.subTest(goal=scoped is not None):
                with mock.patch.object(branch_claims, 'record_from_hook',
                                       side_effect=AssertionError('apply_patch cannot register shell branch claims')):
                    self.assertEqual(self.run_claim_hook(self.payload(patch, 'apply_patch'), execution=scoped), (0, '', ''))
                self.assertEqual(self.branch_claims(), [])

    def test_main_recording_failure_keeps_allowed_tool_and_reports_reason(self):
        with mock.patch.object(branch_claims, 'record', side_effect=OSError('fixture disk unavailable')):
            code, out, err = self.run_claim_hook(self.payload('git branch topic'))
        self.assertEqual(code, 0)
        self.assertEqual(out, '')
        self.assertEqual(err.splitlines(), ['Crew 未能记录分支归属：OSError: fixture disk unavailable'])
        self.assertEqual(self.branch_claims(), [])

    @unittest.skipUnless(os.name == 'nt', 'Windows native hook shell wrapper')
    def test_powershell_wrapper_preserves_explicit_deny(self):
        board.claim(self.conn, 'other', 'taken.txt')
        handler = codex_runtime.hook_config(self.home, PLUGIN, 'member')['hooks']['PreToolUse'][0]['hooks'][0]
        payload = self.payload(self.patch('*** Delete File: taken.txt'), 'apply_patch')
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', handler['commandWindows']],
                                input=json.dumps(payload), capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output['hookSpecificOutput']['permissionDecision'], 'deny')
        self.assertIn('文件归属', output['hookSpecificOutput']['permissionDecisionReason'])


if __name__ == "__main__":
    unittest.main()
