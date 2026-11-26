"""Hook and guard membership follows registered sessions even after cwd changes."""
import tests  # Install the production-service connection guard.
import contextlib
import io
from pathlib import Path
import subprocess
import unittest

from crew import board, goals, guard, hooks
from test_core import CoreCase, NOW


class RegisteredProjectTests(CoreCase):
    def setUp(self):
        super().setUp()
        self.outside = self.base / "outside"
        self.outside.mkdir()

    def test_registered_stop_outside_project_sets_idle(self):
        board.set_status(self.conn, "s1", "working", now=NOW)
        self.assertEqual(self.hook("stop", cwd=str(self.outside), last_assistant_message="done"), (0, None))
        session = board.session(self.conn, "s1")
        self.assertEqual((session["project"], session["status"], session["last_message"]), ("demo", "idle", "done"))
        self.assertEqual(board.events(self.conn, "demo", limit=1)[0]["kind"], "hook:stop")

    def test_registered_prompt_outside_project_records_event(self):
        code, _ = self.hook("user-prompt-submit", cwd=str(self.outside), prompt="continue")
        self.assertEqual(code, 0)
        self.assertEqual(board.session(self.conn, "s1")["status"], "working")
        event = board.events(self.conn, "demo", limit=1)[0]
        self.assertEqual((event["kind"], event["detail"]["prompt"]), ("hook:user-prompt-submit", "continue"))

    def test_registered_compact_outside_project_keeps_identity_and_status(self):
        board.set_status(self.conn, "s1", "working", "current run", now=NOW)
        before = board.session(self.conn, "s1")
        code, output = self.hook("session-start", cwd=str(self.outside), source="compact")
        self.assertEqual(code, 0)
        self.assertIsNotNone(output)
        self.assertIn("Crew 项目：demo", output["hookSpecificOutput"]["additionalContext"])
        after = board.session(self.conn, "s1")
        for field in ("project", "crew_name", "cwd", "worktree", "branch", "status", "status_note"):
            self.assertEqual(after[field], before[field], field)
        self.assertEqual(board.events(self.conn, "demo", limit=1)[0]["kind"], "hook:session-start")

    def test_unregistered_outside_project_does_not_write_database(self):
        before = self.conn.serialize()
        for event, extra in (
            ("stop", {"last_assistant_message": "done"}),
            ("user-prompt-submit", {"prompt": "continue"}),
            ("session-start", {"source": "compact"}),
            ("guard", {"tool_name": "Skill", "tool_input": {"skill": "crew:member"}}),
        ):
            with self.subTest(event=event):
                self.assertEqual(self.hook(event, sid="unknown", cwd=str(self.outside), **extra), (0, None))
                self.assertEqual(self.conn.serialize(), before)


    def test_registered_project_wins_over_another_project_cwd(self):
        board.init_project(self.conn, "other", self.outside, now=NOW)
        code, _ = self.hook("user-prompt-submit", cwd=str(self.outside), prompt="continue")
        self.assertEqual(code, 0)
        self.assertEqual(board.events(self.conn, "demo", limit=1)[0]["kind"], "hook:user-prompt-submit")
        self.assertEqual(board.session(self.conn, "s1")["project"], "demo")

    def test_registered_member_push_outside_project_is_blocked(self):
        self.conn.execute("INSERT INTO git_management(project,enabled_at,base,admin_session_id) VALUES(?,?,?,?)",
                          ("demo", NOW, "main", "s3"))
        for command in ("git push", "gh pr merge 12", "gh pr create"):
            with self.subTest(command=command):
                reason = guard.check(self.home, self.data(cwd=str(self.outside), tool_name="Bash",
                                                         tool_input={"command": command}), now=NOW)
                self.assertIsNotNone(reason)
                self.assertIn("git_request_add", reason)

    def test_registered_member_device_outside_project_is_blocked(self):
        board.add_resource(self.conn, "demo", "phone", guard_regex=r"\badb\b", now=NOW)
        board.take(self.conn, "s2", "phone", "testing", now=NOW)
        reason = guard.check(self.home, self.data(cwd=str(self.outside), tool_name="Bash",
                                                 tool_input={"command": "adb devices"}), now=NOW)
        self.assertIsNotNone(reason)
        self.assertIn("设备正在被借用", reason)

    def test_registered_member_claim_outside_project_is_blocked(self):
        board.claim(self.conn, "s2", "src/", "editing", now=NOW)
        reason = guard.check(self.home, self.data(cwd=str(self.outside), tool_name="Edit",
                                                 tool_input={"file_path": str(self.root / "src" / "a.py")}), now=NOW)
        self.assertIsNotNone(reason)
        self.assertIn("文件归属", reason)


def init_repo(root):
    hooks = Path(root).parent / 'empty-git-hooks'
    hooks.mkdir(exist_ok=True)
    for args in (('init',), ('config', 'core.hooksPath', str(hooks)), ('config', 'user.name', 'Task Branch Fixture'),
                 ('config', 'user.email', 'task-branch@example.invalid'), ('config', 'commit.gpgsign', 'false'),
                 ('commit', '--allow-empty', '-m', 'initial')):
        subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, timeout=20)


class ClaudeBranchClaimTests(CoreCase):
    def setUp(self):
        super().setUp()
        board.join(self.conn, 's3', 'demo', 'Chief', self.root, self.root, 'main', role='leader', now=NOW)
        board.join(self.conn, 's1', 'demo', 'Worker', self.root, self.root, 'main', group_name='Chief', now=NOW)

    def claims(self):
        return [dict(row) for row in self.conn.execute(
            'SELECT project,branch,session_id,goal_id FROM branch_claims ORDER BY branch')]

    def execution(self, sid):
        goal = goals.create(self.conn, 'demo', 's3', 'Claim branch', 'Create a branch', ['Branch has owner'])
        if sid != 's3':
            goals.assign(self.conn, goal['id'], sid, 'Create branch')
        execution = goals.create_execution(self.conn, goal['id'], sid, runtime='claude')
        native = 'native-' + execution['id']
        goals.bind_native(self.conn, execution['id'], 'claude', native)
        return goal, execution, native

    def goal_hook(self, execution, native, tool, command):
        return hooks.handle('guard', self.data(native, tool_name=tool, tool_input={'command': command}),
                            self.home, self.fake_plugin, now=NOW)

    def test_allowed_shells_record_leaders_and_members(self):
        expected = []
        for sid in ('s1', 's3'):
            for tool in ('Bash', 'PowerShell'):
                name = sid + '-' + tool
                with self.subTest(sid=sid, tool=tool):
                    self.assertEqual(self.hook('guard', sid=sid, tool_name=tool,
                                               tool_input={'command': 'git switch -c ' + name}), (0, None))
                    expected.append({'project': 'demo', 'branch': name, 'session_id': sid, 'goal_id': None})
        self.assertEqual(self.claims(), sorted(expected, key=lambda row: row['branch']))

    def test_rejected_shell_command_does_not_record_any_branch(self):
        self.conn.execute('INSERT INTO git_management(project,enabled_at,base,admin_session_id) VALUES(?,?,?,?)',
                          ('demo', NOW, 'main', 's3'))
        for tool in ('Bash', 'PowerShell'):
            with self.subTest(tool=tool):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(self.hook('guard', tool_name=tool,
                                               tool_input={'command': 'git push; git switch -c blocked'}), (2, None))
                self.assertIn('git_request_add', err.getvalue())
                self.assertEqual(self.claims(), [])

    def test_non_shell_tool_does_not_record_command_like_content(self):
        self.assertEqual(self.hook('guard', tool_name='Read',
                                   tool_input={'file_path': str(self.root / 'notes.txt'),
                                               'command': 'git switch -c not-a-shell'}), (0, None))
        self.assertEqual(self.claims(), [])

    def test_goal_shells_record_logical_identity_and_goal_for_both_roles(self):
        expected = []
        for sid in ('s1', 's3'):
            for tool in ('Bash', 'PowerShell'):
                with self.subTest(sid=sid, tool=tool):
                    goal, execution, native = self.execution(sid)
                    name = sid + '-' + tool
                    self.assertEqual(self.goal_hook(execution, native, tool, 'git branch ' + name), (0, None))
                    self.assertIsNone(self.conn.execute('SELECT 1 FROM sessions WHERE session_id=?', (native,)).fetchone())
                    expected.append({'project': 'demo', 'branch': name, 'session_id': sid, 'goal_id': goal['id']})
        self.assertEqual(self.claims(), sorted(expected, key=lambda row: row['branch']))

    def test_goal_execution_cannot_open_a_second_task_branch(self):
        init_repo(self.root)
        _, execution, native = self.execution('s1')
        self.assertEqual(self.goal_hook(execution, native, 'Bash', 'git branch feature/task'), (0, None))
        subprocess.run(['git', '-C', str(self.root), 'branch', 'feature/task'], check=True, capture_output=True, timeout=20)
        for tool in ('Bash', 'PowerShell'):
            with self.subTest(tool=tool):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(self.goal_hook(execution, native, tool, 'git worktree add -b feature/task-voice ../voice'),
                                     (2, None))
                self.assertIn('本任务已有分支 feature/task', err.getvalue())
        self.assertEqual([row['branch'] for row in self.claims()], ['feature/task'])

    def test_goal_rejected_shell_and_non_shell_do_not_record(self):
        self.conn.execute('INSERT INTO git_management(project,enabled_at,base,admin_session_id) VALUES(?,?,?,?)',
                          ('demo', NOW, 'main', 's3'))
        _, execution, native = self.execution('s1')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self.goal_hook(execution, native, 'Bash', 'git push; git switch -c blocked'), (2, None))
        self.assertIn('git_request_add', err.getvalue())
        self.assertEqual(self.goal_hook(execution, native, 'Read', 'git branch non-shell'), (0, None))
        self.assertEqual(self.claims(), [])


if __name__ == "__main__":
    unittest.main()
