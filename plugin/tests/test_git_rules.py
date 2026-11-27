"""Git management context, central-operation guard and injected roles."""
import tests  # Install the production-service connection guard.
from crew import board, store
from crew.ops import tokens
from test_core import CoreCase, NOW


class GitRulesTests(CoreCase):
    def setUp(self):
        super().setUp()
        for sid in ('s1', 's2', 's3'):
            tokens.issue(self.conn, session_id=sid, runtime='claude', now=NOW)

    def enable(self, policy=None):
        board.join(self.conn, "s3", "demo", "git管理员", self.root, store.norm_path(self.root), "main",
                   role="leader", now=NOW)
        self.conn.execute("INSERT INTO git_management(project,enabled_at,base,admin_session_id,branch_rules) "
                          "VALUES(?,?,?,?,?)", ("demo", NOW, "main", "s3", policy))

    def context(self, sid="s1", **extra):
        code, output = self.hook("session-start", sid, **extra)
        self.assertEqual(code, 0)
        return output["hookSpecificOutput"]["additionalContext"]

    def test_session_start_shows_first_six_policy_lines(self):
        self.enable("\n".join("规则%d" % i for i in range(1, 9)))
        context = self.context()
        self.assertIn("本项目的推送、开 PR、合并交给 Git 助手「git管理员」：做完用 Crew 工具 git_request_add 交给它", context)
        self.assertIn('message_send', context)
        self.assertIn("发版分支由用户自己处理", context)
        self.assertIn("规则1\n规则2\n规则3\n规则4\n规则5\n规则6", context)
        self.assertNotIn("规则7", context)
        self.assertIn("全文用 git_policy_get 看", context)

    def test_session_start_without_policy_tells_sessions_to_ask_admin(self):
        self.enable()
        self.assertIn("Git 助手还没写分支规则，开工前先问它", self.context())

    def test_selected_administrators_actual_name_and_duties_are_used(self):
        self.enable('Project policy')
        self.conn.execute("UPDATE sessions SET crew_name='Repository lead' WHERE session_id='s3'")
        self.assertIn('「Repository lead」', self.context())
        own = self.context('s3')
        self.assertIn('你是本项目当前绑定的 Git 助手', own)
        self.assertNotIn('自己不 push', own)
        self.assertIn('发版分支由用户自己处理，本地集成分支不推送', own)

    def test_unenrolled_and_archived_sessions_still_receive_git_context(self):
        self.enable("规则正文")
        self.assertIn("交给 Git 助手", self.context("plain"))
        board.set_status(self.conn, "s1", "closed", now=NOW)
        self.conn.execute("INSERT INTO session_controls(session_id,archived_status) VALUES(?,?)", ("s1", "idle"))
        self.assertIn("交给 Git 助手", self.context())

    def test_disabled_git_does_not_add_context(self):
        self.assertNotIn("Git 助手", self.context())

    def test_enrolled_members_and_leaders_cannot_push_create_or_merge(self):
        self.enable()
        board.join(self.conn, "s2", "demo", "lead", self.root, store.norm_path(self.root), "main", role="leader", now=NOW)
        for sid in ("s1", "s2"):
            for command in ("git push origin feature/x", "gh pr merge 12 --squash", "gh pr create --title test", "cd repo && GIT PUSH origin main"):
                with self.subTest(sid=sid, command=command):
                    self.assertIn("本项目的推送、开 PR、合并交给 Git 助手：用 Crew 工具 git_request_add 交给它；发版分支由用户自己处理。", self.guard(sid, command=command))
        event = self.conn.execute("SELECT detail FROM events WHERE kind='hook:guard' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIn("git_request_add", event["detail"])

    def test_git_admin_can_push_create_and_merge(self):
        self.enable()
        for command in ("git push origin feature/x", "gh pr merge 12", "gh pr create"):
            self.assertIsNone(self.guard("s3", command=command))

    def test_global_options_paths_and_shell_chains_are_blocked(self):
        self.enable()
        for tool in ("Bash", "PowerShell"):
            for command in (
                'git -C "C:/some repo" push', 'git -c k=v push',
                'git --git-dir=C:/repo/.git push', 'git --git-dir "C:/repo/.git" push',
                'git -C repo -c k=v --no-pager push origin main',
                r'C:\Git\bin\git.exe push', '"C:/Program Files/Git/bin/git.exe" -C repo push',
                'git log && git -C repo push', 'echo ok; git -c k=v push',
                'git push | echo done', 'echo ok | git push', '& git push',
                '& "C:/Program Files/Git/bin/git.exe" push', 'git log\ngit push',
                'echo "safe; text"; gh pr merge 12',
            ):
                with self.subTest(tool=tool, command=command):
                    self.assertIn("git_request_add", self.guard(tool=tool, command=command))

    def test_help_read_commands_and_quoted_strings_are_allowed(self):
        self.enable()
        for tool in ("Bash", "PowerShell"):
            for command in (
                'git push --help', 'git -C repo push --help', 'git push -h',
                'git --help push', 'git log', 'git -c k=v log',
                'echo "git push"', "echo 'git push'", 'echo "git push; gh pr merge"',
                'echo "git push" && git log', 'Write-Output "git push"',
                'gh pr create --help', 'git config alias.message "git push"',
            ):
                with self.subTest(tool=tool, command=command):
                    self.assertIsNone(self.guard(tool=tool, command=command))

    def test_unenrolled_closed_and_foreign_project_sessions_are_not_blocked(self):
        self.enable()
        board.register_session(self.conn, "plain", "demo", self.root, None, None, now=NOW)
        self.assertIsNone(self.guard("plain", command="git push"))
        self.assertIsNone(self.guard("unknown", command="git push"))
        board.set_status(self.conn, "s1", "closed", now=NOW)
        self.assertIsNone(self.guard(command="gh pr merge 12"))
        other = self.base / "other"
        other.mkdir()
        board.init_project(self.conn, "other", other, now=NOW)
        board.join(self.conn, "foreign", "other", "foreign", other, None, None, now=NOW)
        self.assertIsNone(self.guard("foreign", command="git push"))

    def test_disabled_git_and_read_only_git_commands_are_not_blocked(self):
        self.assertIsNone(self.guard(command="git push origin feature/x"))
        self.enable()
        for command in ("git status", "git log --all", "gh pr view 12", "gh pr checks 12"):
            self.assertIsNone(self.guard(command=command))

    def test_blacklist_still_applies_to_git_admin(self):
        self.enable()
        self.assertIn("blacklist.txt", self.guard("s3", command="git push --force origin feature/x"))

    def test_git_admin_startup_identifies_the_injected_role(self):
        self.enable()
        self.assertIn('[Crew git]', self.context('s3'))

    def test_ordinary_leader_receives_the_leader_role(self):
        self.enable()
        board.join(self.conn, 's2', 'demo', 'lead', self.root, store.norm_path(self.root), 'main', role='leader', now=NOW)
        context = self.context('s2')
        self.assertIn('[Crew leader]', context)
        self.assertNotIn('[Crew git]', context)
