"""Git opt-in commands and HTTP routes use isolated boards and temporary repositories."""
import tests  # Install the production-service connection guard.
import uuid
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest import mock

from tests.ops_helpers import ai_call
from crew.ops.errors import OpError
from crew import board, git_admin, git_overview, git_view, hooks, messaging, models, runtimes, settings, spawn, store, web
from crew.ops import tokens
from test_core import CoreCase, NOW
from test_shell import PLUGIN, ShellFixture
from tests.fixture_carriers import install_lookup


class GitStorageMigrationTests(unittest.TestCase):
    """Databases from before the integration branch keep their data; the release branch is not carried over."""

    def test_old_stable_column_is_left_alone_and_integration_starts_empty_under_concurrent_opens(self):
        with tempfile.TemporaryDirectory(dir=PLUGIN, prefix="git-migration-") as folder:
            home = Path(folder) / "home"
            home.mkdir()
            old = store.connect(home)
            board.init_project(old, "demo", Path(folder), now=NOW)
            # The table exactly as databases created before 2026-11-10 have it.
            old.executescript(
                "DROP TABLE git_management;"
                "CREATE TABLE git_management (project TEXT PRIMARY KEY REFERENCES projects(name), enabled_at TEXT NOT NULL,"
                " base TEXT NOT NULL, stable TEXT, admin_session_id TEXT REFERENCES sessions(session_id), branch_rules TEXT);"
                "INSERT INTO git_management VALUES('demo','%s','develop','master',NULL,'keep develop green');" % NOW)
            old.close()
            errors = []

            def open_once():
                try:
                    store.connect(home).close()
                except Exception as exc:  # Surface the real failure in the assertion below.
                    errors.append(exc)
            threads = [threading.Thread(target=open_once) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(20)
            self.assertEqual(errors, [])
            conn = store.connect(home)
            try:
                columns = [row["name"] for row in conn.execute("PRAGMA table_info(git_management)")]
                self.assertEqual(columns.count("integration"), 1)
                self.assertEqual(conn.execute("SELECT stable FROM git_management").fetchone()[0], "master")
                config = board.git_config(conn, "demo")
                self.assertEqual({key: config[key] for key in ("base", "integration", "branch_rules")},
                                 {"base": "develop", "integration": None, "branch_rules": "keep develop green"})
                self.assertNotIn("stable", config)
            finally:
                conn.close()


class GitOperationTests(CoreCase):
    def setUp(self):
        super().setUp()
        install_lookup(self, self.base)
        carrier = mock.patch.object(runtimes, 'default_runtime', return_value='claude')
        carrier.start()
        self.addCleanup(carrier.stop)
        (self.root / ".gitignore").write_text("/Temp/wt/\n", encoding="ascii")
        self.git("add", ".gitignore")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "-c", "commit.gpgsign=false",
                 "commit", "--allow-empty", "-m", "fixture")
        for name in ("develop", "dev", "master", "topic"):
            self.git("branch", name)
        self.plans, self.launch_roles, self.native_failure = [], [], None
        self.transcripts = self.base / 'claude-projects'
        self.transcripts.mkdir()
        self.service = web.Service(self.home, PLUGIN, cwd=self.root, agents_provider=lambda: [],
                                   claude_root=self.transcripts, isolated_sources=True, now=lambda: store.parse_iso(NOW))
        self.addCleanup(self.service.close)
        self.service.chat.start = lambda *args, **kwargs: self.launcher(*args, **kwargs)

    def configure(self, **options):
        return self.service.op('git.enable', options, project='demo')

    def agent(self, operation, args=None, sid='s1'):
        return ai_call(self.service, self.conn, sid, operation, args)

    def launcher(self, conn, project_name, name, task, _registered=None, **options):
        plan = spawn.prepare(conn, self.home, self.fake_plugin, project_name, name, task, **options)
        self.plans.append(plan)
        spec = plan["spec"]

        def register(_):
            worktree, branch = hooks.git_details(spec["cwd"])
            board.register_session(conn, plan["session_id"], project_name, spec["cwd"], worktree, branch,
                                   name=name, role=spec["role"], group_name=spec["group"], spawn_id=spec["id"], now=NOW)
            runtimes.set(conn, plan['session_id'], spec['runtime'], plan['session_id'] if spec['runtime'] == 'claude' else None)
            models.set(conn, plan["session_id"], spec["model"], spec["effort"], now=NOW, speed=spec["speed"])
            if _registered is not None:
                _registered(plan["session_id"])
            # The native starts here: its MCP token takes the role the board shows at this moment.
            self.launch_roles.append(tokens.role_of(conn, plan["session_id"]))
            if self.native_failure is not None:
                raise self.native_failure

        result = spawn._execute(conn, plan, register)
        return {**result, "session_id": plan["session_id"]}

    def enable(self):
        self.configure()
        return board.git_config(self.conn, "demo")["admin_session_id"]

    def git(self, *arguments):
        return subprocess.run(["git", "-C", str(self.root), *arguments], check=True,
                              capture_output=True, text=True, encoding="utf-8").stdout

    def test_enable_starts_git_administrator_in_separate_worktree(self):
        self.configure()
        config = board.git_config(self.conn, "demo")
        # No integration branch is guessed; master is not special any more.
        self.assertEqual((config["base"], config["integration"]), ("develop", None))
        self.assertNotIn("stable", config)
        administrator = board.session(self.conn, config["admin_session_id"])
        self.assertEqual((administrator["crew_name"], administrator["role"], administrator["group_name"]),
                         ("Git 助手", "leader", "git"))
        plan = self.plans[0]
        self.assertEqual(administrator["session_id"], plan["session_id"])
        self.assertNotEqual(administrator["worktree"], store.norm_path(self.root))
        self.assertTrue(administrator["branch"].startswith("crew/git-admin-"))
        self.assertEqual(plan["worktree_command"][-1], "develop")
        self.assertEqual(models.get(self.conn, administrator["session_id"]), {"model": None, "effort": None, "speed": None})
        command = plan["command"]
        self.assertEqual(command[command.index("--agent") + 1], "crew:git")
        self.assertNotIn('--model', command)
        self.assertNotIn('--effort', command)
        self.assertIn("[Crew git]", plan["files"][plan["spec"]["md"]])
        self.assertIsNone(config["branch_rules"])
        self.assertIsNotNone(config["enabled_at"])
        self.assertNotIn("/crew:git", plan["files"][plan["spec"]["md"]])

    def test_repeated_enable_updates_config_without_starting_another_administrator(self):
        administrator = self.enable()
        self.configure(base='topic', integration='main')
        config = board.git_config(self.conn, "demo")
        self.assertEqual(config["admin_session_id"], administrator)
        self.assertEqual((config["base"], config["integration"]), ("topic", "main"))
        self.assertEqual(len(self.plans), 1)

    def test_git_assistant_retains_leader_identity(self):
        sid = self.enable()
        value = self.agent('session.whoami', sid=sid)
        self.assertEqual(value['role'], 'leader')
        self.assertEqual(value['session_id'], sid)
        self.assertNotIn('builtin', value)

    def test_global_assistant_whoami_has_no_project_git_identity(self):
        from crew import personal
        self.enable()
        assistant = personal.ensure_assistant(self.conn, self.home)
        identity = self.agent('session.whoami', sid=assistant['session_id'])
        self.assertEqual(identity['session_id'], assistant['session_id'])
        self.assertFalse(identity.get('project'))

    def test_reenable_preserves_existing_assistant_name(self):
        sid = self.enable()
        self.conn.execute("UPDATE sessions SET crew_name='git管理员' WHERE session_id=?", (sid,))
        self.configure()
        self.assertEqual(board.session(self.conn, sid)['crew_name'], 'git管理员')
        self.assertEqual(len(self.plans), 1)

    def test_request_can_explicitly_wait_for_merge_before_follow_up_work(self):
        admin = self.enable()
        self.agent('git.request.add', {'description': 'After merge, verify the integration', 'blocking': True})
        item = board.git_requests(self.conn, 'demo')[0]
        self.assertEqual(item['blocking'], 1)
        self.agent('git.request.resolve', {'id': item['id'], 'status': 'merged', 'note': 'Ready for verification'}, sid=admin)
        self.assertEqual(self.conn.execute('SELECT request_id FROM git_result_handoffs').fetchone()[0], item['id'])

    def test_codex_only_git_administrator_reads_both_rules_without_claude_flags(self):
        settings.save(self.conn, {'default': {'runtime': 'codex', 'model': None, 'effort': None, 'speed': 'priority'}})
        with mock.patch.object(spawn.codex_client, 'command', return_value=['codex.exe', 'app-server']):
            self.configure()
        plan = self.plans[0]
        administrator = board.git_config(self.conn, 'demo')['admin_session_id']
        self.assertEqual(runtimes.get(self.conn, administrator)['runtime'], 'codex')
        self.assertEqual(models.get(self.conn, administrator), {'model': None, 'effort': None, 'speed': 'priority'})
        self.assertEqual(plan['command'], ['codex.exe', 'app-server'])
        brief = plan['files'][plan['spec']['md']]
        # Rules arrive through the editable injected configuration, not built-in file paths.
        self.assertNotIn('SKILL.md', brief)
        self.assertIn('git_policy_set', brief)
        self.assertNotIn('/crew:leader', brief)
        self.assertNotIn('/crew:git', brief)

    def test_explicit_codex_git_enable_reaches_launcher_and_reenable_keeps_identity(self):
        with mock.patch.object(spawn.codex_client, 'command', return_value=['codex.exe', 'app-server']):
            self.configure(runtime='codex')
        administrator = board.git_config(self.conn, 'demo')['admin_session_id']
        self.configure(base='topic')
        self.assertEqual(board.git_config(self.conn, 'demo')['admin_session_id'], administrator)
        self.assertEqual(len(self.plans), 1)
        with self.assertRaisesRegex((OpError, board.BoardError, ValueError), '会话菜单'):
            self.configure(runtime='claude')

    def test_no_ai_carrier_does_not_enable_git_or_create_worktree(self):
        with mock.patch.object(settings, 'resolve', side_effect=board.BoardError('请先安装并登录 Codex CLI 或 Claude Code')):
            with self.assertRaisesRegex((OpError, board.BoardError, ValueError), '安装并登录'):
                self.configure()
        self.assertIsNone(board.git_config(self.conn, 'demo'))
        self.assertFalse((self.root / 'Temp' / 'wt').exists())

    def test_git_brain_settings_supply_model_and_effort_without_changing_other_jobs(self):
        settings.save(self.conn, {'default': {'runtime': 'claude', 'model': None, 'effort': None},
                                 'git': {'runtime': 'codex', 'model': 'gpt-fixture-purpose', 'effort': 'high'}})
        with mock.patch.object(spawn.codex_client, 'command', return_value=['codex.exe', 'app-server']):
            self.configure(runtime='codex')
        self.assertEqual(self.plans[0]['spec']['model'], 'gpt-fixture-purpose')
        self.assertEqual(self.plans[0]['spec']['effort'], 'high')
        self.assertIsNone(settings.load(self.conn)['default']['model'])

    def test_administrator_starts_with_its_git_role(self):
        # Real Haiku run (2026-11-27): the first Git assistant could not find git_request_resolve or
        # git_policy_set, because its token was issued as a leader before the board named it administrator.
        administrator = self.enable()
        self.assertEqual(self.launch_roles, ['git_admin'])
        self.assertEqual(tokens.role_of(self.conn, administrator), 'git_admin')

    def test_native_start_failure_after_registration_does_not_mark_git_enabled(self):
        self.native_failure = OSError('fixture native failure')
        with self.assertRaisesRegex((OpError, board.BoardError, ValueError, OSError), 'fixture native failure'):
            self.configure()
        self.assertIsNone(board.git_config(self.conn, "demo"))

    def test_start_failure_does_not_mark_git_enabled(self):
        with mock.patch.object(self, "launcher", side_effect=OSError("fixture launch failure")):
            with self.assertRaisesRegex((OpError, board.BoardError, ValueError, OSError), 'fixture launch failure'):
                self.configure()
        self.assertIsNone(board.git_config(self.conn, "demo"))

    def test_enable_auto_priority_never_picks_an_integration_branch(self):
        for removed, expected in (("develop", "dev"), ("dev", "main"), ("main", "master")):
            if removed == "main":
                self.git("symbolic-ref", "HEAD", "refs/heads/master")
            self.git("branch", "-D", removed)
            self.configure()
            config = board.git_config(self.conn, "demo")
            self.assertEqual(config["base"], expected)
        self.assertIsNone(config["integration"])

    def test_enable_explicit_branches_and_invalid_branch_leave_config_unchanged(self):
        self.configure(base='topic', integration='main')
        previous = board.git_config(self.conn, "demo")
        for field in ('base', 'integration'):
            with self.assertRaisesRegex(board.BoardError, '分支不存在: missing'):
                self.configure(**{field: 'missing'})
            self.assertEqual(board.git_config(self.conn, "demo"), previous)

    def test_integration_branch_is_optional_and_must_differ_from_base(self):
        self.configure(base='master', integration='main')
        self.assertEqual(board.git_config(self.conn, "demo")["integration"], "main")
        with self.assertRaisesRegex(board.BoardError, '本地集成分支必须与主干分支不同'):
            self.configure(base='main', integration='main')
        board.enable_git(self.conn, "demo", "develop", "")
        self.assertIsNone(board.git_config(self.conn, "demo")["integration"])
        self.configure(base='develop')
        self.assertIsNone(board.git_config(self.conn, "demo")["integration"])

    def test_status_disable_and_project_isolation(self):
        self.assertIsNone(self.service.op("git.settings.get", project="demo")["config"])
        self.configure()
        administrator = board.git_config(self.conn, "demo")["admin_session_id"]
        self.assertEqual(self.service.op("git.settings.get", project="demo")["config"]["base"], "develop")
        board.init_project(self.conn, "other", self.base / "other", now=NOW)
        self.assertIsNone(board.git_config(self.conn, "other"))
        self.service.op('git.disable', project='demo')
        self.assertIsNone(board.git_config(self.conn, "demo"))
        self.assertEqual(board.session(self.conn, administrator)["status"], "closed")
        group = next(g for g in board.groups(self.conn, "demo") if g["name"] == "git")
        self.assertEqual(group["closed_at"], NOW)
        self.assertIsNone(self.service.op("git.settings.get", project="demo")["config"])
        self.service.op('git.disable', project='demo')

    def test_enable_after_disable_gets_a_new_worktree(self):
        first = self.enable()
        self.service.op('git.disable', project='demo')
        second = self.enable()
        self.assertNotEqual(first, second)
        self.assertNotEqual(self.plans[0]["spec"]["cwd"], self.plans[1]["spec"]["cwd"])

    def test_no_recognised_base_requires_explicit_selection(self):
        self.git("symbolic-ref", "HEAD", "refs/heads/topic")
        for name in ("develop", "dev", "main", "master"):
            self.git("branch", "-D", name)
        with self.assertRaisesRegex((OpError, board.BoardError, ValueError), '请指定 --base'):
            self.configure()
        self.configure(base='topic')

    def test_policy_read_and_only_administrator_can_set(self):
        with self.assertRaisesRegex((OpError, board.BoardError, ValueError), '未启用'):
            self.agent('git.policy.get')
        administrator = self.enable()
        self.assertFalse(self.agent("git.policy.get")["policy"])
        policy = "小改直接合 base\n大改走 PR\n命名 feature/<任务>\n提交 feat: 说明"
        with self.assertRaisesRegex((OpError, board.BoardError, ValueError), '不能调用'):
            self.agent('git.policy.set', {'text': policy})
        self.agent('git.policy.set', {'text': policy}, sid=administrator)
        self.assertEqual(self.agent("git.policy.get")["policy"], policy)
        self.assertEqual(board.git_config(self.conn, "demo")["branch_rules"], policy)
        with self.assertRaises((OpError, board.BoardError, ValueError)):
            self.agent('git.policy.set', {'text': ' '}, sid=administrator)

    def test_request_defaults_to_current_branch_and_queues_administrator_notice(self):
        administrator = self.enable()
        description = "增加按钮；单测通过；无需设备验收"
        self.agent("git.request.add", {"description": description})
        request = board.git_requests(self.conn, "demo")[0]
        self.assertEqual((request["session_id"], request["branch"], request["description"], request["status"]),
                         ("s1", "main", description, "open"))
        notice = board.pending_notices(self.conn, administrator)[0]
        self.assertIn(description, notice["text"])
        self.assertIn("main", notice["text"])
        self.agent('git.request.add', {'description': '指定分支', 'branch': 'topic'})
        self.assertEqual(board.git_requests(self.conn, "demo")[1]["branch"], "topic")

    def test_request_requires_enabled_project_session_identity_and_text(self):
        with self.assertRaises((OpError, board.BoardError, ValueError)):
            self.agent('git.request.add', {'description': '未启用'})
        self.enable()
        with self.assertRaises(OpError):
            self.service.op('git.request.add', {'description': '无身份'}, project='demo')
        with self.assertRaises((OpError, board.BoardError, ValueError)):
            self.agent('git.request.add', {'description': ' '})
        with self.assertRaises((OpError, board.BoardError, ValueError)):
            self.agent('git.request.add', {'description': '空分支', 'branch': ' '})
        board.init_project(self.conn, "other", self.base / "other", now=NOW)
        board.register_session(self.conn, "outsider", "other", self.base / "other", self.base / "other", "main", now=NOW)
        with self.assertRaisesRegex(board.BoardError, "不属于本项目"):
            board.request_git_merge(self.conn, "demo", "outsider", "main", "跨项目", title=None, now=NOW)
        self.assertEqual(board.git_requests(self.conn, "demo"), [])

    def test_resolve_transitions_preserves_pr_and_member_delivery(self):
        administrator = self.enable()
        self.agent('git.request.add', {'description': '变更；测试通过'})
        with self.assertRaisesRegex((OpError, board.BoardError, ValueError), '不能调用'):
            self.agent('git.request.resolve', {'id': 1, 'status': 'merged', 'note': '越权'})
        self.agent('git.request.resolve', {'id': 1, 'status': 'merging', 'note': '检查 CI', 'pr_number': 42}, sid=administrator)
        self.agent('git.request.resolve', {'id': 1, 'status': 'waiting_user', 'note': '等待设备验收'}, sid=administrator)
        request = board.git_requests(self.conn, "demo")[0]
        self.assertEqual((request["status"], request["pr_number"], request["note"]), ("waiting_user", 42, "等待设备验收"))
        self.assertEqual(len(board.handoffs(self.conn, "s1")), 0)
        delivered = board.deliver(self.conn, "s1", now=NOW)["handoffs"]
        self.assertTrue(all("合并请求 #1" in item["body"] for item in delivered))
        self.assertEqual(board.handoffs(self.conn, "s1"), [])
        self.agent('git.request.resolve', {'id': 1, 'status': 'merged', 'note': '已合入 base'}, sid=administrator)
        self.assertEqual(board.git_requests(self.conn, "demo"), [])
        self.assertEqual(board.git_requests(self.conn, "demo", all=True)[0]["status"], "merged")
        with self.assertRaises((OpError, board.BoardError, ValueError)):
            self.agent('git.request.resolve', {'id': 1, 'status': 'rejected', 'note': '重复处理'}, sid=administrator)

    def test_leader_result_notice_and_queue_order_and_project_isolation(self):
        administrator = self.enable()
        board.join(self.conn, "lead", "demo", "feature-lead", self.root, self.root, "topic", role="leader", now=NOW)
        self.agent('git.request.add', {'description': 'leader提交'}, sid='lead')
        self.agent('git.request.resolve', {'id': 1, 'status': 'rejected', 'note': '范围不符'}, sid=administrator)
        self.assertEqual(board.pending_notices(self.conn, "lead"), [])
        self.assertIn("rejected", board.handoffs(self.conn, "lead")[0]["body"])
        self.assertEqual(self.conn.execute("SELECT session_id FROM git_result_handoffs").fetchone()[0], "lead")
        self.agent('git.request.add', {'description': '新的请求'})
        self.assertEqual([item["id"] for item in board.git_requests(self.conn, "demo", all=True)], [2, 1])
        self.assertNotIn('leader提交', [item['description'] for item in self.agent('git.requests.list')['requests']])
        self.assertIn('leader提交', [item['description'] for item in self.agent('git.requests.list', {'all': True})['requests']])
        board.init_project(self.conn, "other", self.base / "other", now=NOW)
        self.assertEqual(board.git_requests(self.conn, "other", all=True), [])
        with self.assertRaises(board.BoardError):
            board.resolve_git_request(self.conn, "other", administrator, 2, "merged", "越项目", now=NOW)

    def test_resolve_validation_is_atomic(self):
        administrator = self.enable()
        self.agent('git.request.add', {'description': '待处理'})
        for args in ({'id': 1, 'status': 'merging', 'note': ' '},
                     {'id': 1, 'status': 'merging', 'note': 'PR错', 'pr_number': 0},
                     {'id': 999, 'status': 'merged', 'note': '不存在'}):
            with self.assertRaises((OpError, board.BoardError, ValueError)):
                self.agent('git.request.resolve', args, sid=administrator)
        self.assertEqual(board.git_requests(self.conn, "demo")[0]["status"], "open")
        self.assertEqual(board.handoffs(self.conn, "s1"), [])


class GitHttpTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.git_repo()
        self.git("branch", "-M", "develop")
        for branch in ("master", "topic"):
            self.git("branch", branch)
        executable = mock.patch("crew.chat.shutil.which", return_value="C:/fixture/claude.exe")
        executable.start()
        self.addCleanup(executable.stop)
        self.choices = {"branches": ["develop", "master", "topic"], "base": "develop"}
        patcher = mock.patch("crew.git_view.branch_choices", return_value=self.choices)
        self.choice_provider = patcher.start()
        self.addCleanup(patcher.stop)
        self.server = web.create_server(
            self.home, PLUGIN, port=0, cwd=self.project, agents_provider=lambda: [],
            claude_root=self.claude, codex_root=self.codex, runs_file=self.runs,
            now=lambda: store.parse_iso(NOW), process_factory=self.process_factory,
            vscode_state_db=self.vscode_state_db, idle_status=lambda: 0)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        # The startup warm-up reads the workspace once; let it finish before a test enables Git or mocks a scan.
        self.server.service.warm_worker.join(10)
        self.assertFalse(self.server.service.warm_worker.is_alive())

    def settle_git(self):
        """Wait for the background branch scans that state reads queued (web_service/git.py)."""
        for worker in self.server.service._git_scan_workers():
            worker.join(10)
            self.assertFalse(worker.is_alive())

    def state(self):
        code, result = self.request("/api/op/workspace.state?project=demo")
        self.assertEqual(code, 200, result)
        return result

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())

    def request(self, path, data=None, header=True):
        payload = None if data is None else json.dumps(data).encode("utf-8")
        headers = {"X-Crew": "1", 'X-Crew-Request-Id': uuid.uuid4().hex} if header else {}
        try:
            response = urlopen(Request(self.server.url + path, payload, headers), timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_disabled_get_enable_and_disable(self):
        with mock.patch("crew.git_view.view") as view:
            code, result = self.request("/api/op/git.overview?project=demo")
            self.assertEqual(code, 200)
            self.assertFalse(result["enabled"])
            self.assertEqual(result["choices"], self.choices)
            self.assertIsNone(result["config"])
            view.assert_not_called()
        code, config = self.request("/api/op/git.enable", {"project": "demo"})
        self.assertEqual(code, 200, config)
        self.assertEqual((config["base"], config["integration"]), ("develop", None))
        sid = config["admin_session_id"]
        admin = board.session(self.conn, sid)
        self.assertEqual((admin["crew_name"], admin["role"], admin["group_name"]), ("Git 助手", "leader", "git"))
        self.assertNotEqual(admin["cwd"], str(self.project))
        self.assertTrue(self.server.service.chat.path(sid).exists())
        command = self.commands[-1]
        self.assertEqual(command[command.index("--agent") + 1], "crew:git")
        self.assertNotIn('--model', command)
        state = self.request("/api/op/workspace.state?project=demo")[1]
        admin_state = state['git_assistant']
        self.assertEqual(admin_state['session_id'], sid)
        self.assertTrue(admin_state['builtin'])
        self.assertNotIn(sid, {leader['session_id'] for leader in state['leaders']})
        self.assertEqual((admin_state["model"], admin_state["effort"]), (None, None))
        self.assertEqual(self.request("/api/op/session.options.set?project=demo", {"session_id": sid, "model": "claude-opus-5-5"})[0], 200)
        self.assertEqual(self.request("/api/op/git.disable?project=demo", {}), (200, {"ok": True}))
        self.assertIsNone(board.git_config(self.conn, "demo"))
        self.assertEqual(board.session(self.conn, sid)["status"], "closed")
        group = next(group for group in board.groups(self.conn, "demo") if group["name"] == "git")
        self.assertIsNotNone(group["closed_at"])

    def test_enabled_get_passes_config_excluded_assistant_branch_and_force(self):
        code, config = self.request("/api/op/git.enable?project=demo", {"base": "topic", "integration": "master"})
        self.assertEqual(code, 200, config)
        payload = {"base": {"name": "topic", "origin": False, "unpushed": None, "behind_origin": None},
                   "integration": None, "branches": [], "merged_hidden": 2,
                   "repository": self.project.name, "last_fetch_at": None}
        with mock.patch("crew.git_view.view", return_value=payload) as view, mock.patch("crew.git_view.fetch") as fetch:
            code, result = self.request("/api/op/git.overview?project=demo")
            fetch.assert_not_called()
            self.settle_git()
        self.assertEqual(code, 200, result)
        self.assertTrue(result["enabled"])
        self.assertEqual({key: result[key] for key in payload}, payload)
        self.assertEqual(result["config"]["integration"], "master")
        self.assertNotIn("stable", result["config"])
        self.assertNotIn("stable", result["choices"])
        root = board.project(self.conn, "demo")["root"]
        assistant = board.session(self.conn, config["admin_session_id"])
        # The page reads the repository itself; the leader cards built beside it only queue a background scan,
        # which the mocked view() leaves without a cached result.
        self.assertEqual(view.call_args_list, [
            mock.call(root, config, exclude=(assistant["branch"],), force=True),
            mock.call(root, config, exclude=(assistant["branch"],))])

    def topic_owner_fixture(self):
        self.git("switch", "topic")
        self.git("-c", "user.name=Crew Test", "-c", "user.email=crew@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "topic work")
        self.git("switch", "develop")
        board.join(self.conn, "s1", "demo", "one", self.project, None, "topic", group_name="lead", now=NOW)
        code, config = self.request("/api/op/git.enable?project=demo", {})
        self.assertEqual(code, 200, config)

    def test_real_git_data_through_http(self):
        self.topic_owner_fixture()
        with mock.patch("crew.git_view.fetch") as fetch:
            code, result = self.request("/api/op/git.overview?project=demo")
            fetch.assert_not_called()
        self.assertEqual(code, 200, result)
        self.assertEqual({key: result["base"][key] for key in ("name", "origin", "unpushed", "behind_origin")},
                         {"name": "develop", "origin": False, "unpushed": None, "behind_origin": None})
        self.assertIsNone(result["integration"])
        self.assertNotIn("stable", result)
        # master no longer has a release role: it holds nothing develop lacks, so it is merged and hidden.
        self.assertEqual(result["merged_hidden"], 1)
        self.assertEqual(len(result["branches"]), 1)
        branch = result["branches"][0]
        self.assertEqual((branch["name"], branch["state"], branch["commits"], branch["behind"]),
                         ("topic", "unmerged", 1, 0))
        self.assertEqual(branch["latest"]["sha"], self.git("rev-parse", "topic"))
        self.assertEqual(branch["latest"]["subject"], "topic work")
        self.assertIsNone(branch["worktree"])
        self.assertEqual(branch["owners"], [{"session_id": "s3", "name": "lead"}])

    def test_state_includes_git_branches_with_owners_when_enabled(self):
        self.topic_owner_fixture()
        with mock.patch("crew.git_view.view", wraps=git_view.view) as view:
            self.assertEqual(self.state()["git_branches"], {"pending": True})
            self.settle_git()
            data = self.state()["git_branches"]
        self.assertEqual(data["base"]["name"], "develop")
        self.assertEqual([branch["name"] for branch in data["branches"]], ["topic"])
        self.assertEqual(data["branches"][0]["owners"], [{"session_id": "s3", "name": "lead"}])
        self.assertEqual(data["merged_hidden"], 1)
        # One unforced background scan; the second read is served from its result.
        view.assert_called_once()
        self.assertNotIn("force", view.call_args.kwargs)

    def test_first_state_does_not_wait_for_the_branch_scan_and_gets_the_direct_result_after_it(self):
        self.topic_owner_fixture()
        started, release = threading.Event(), threading.Event()
        refs = git_view._refs

        def held(root):
            started.set()
            release.wait(10)
            return refs(root)

        before = self.conn.execute("SELECT COALESCE(MAX(seq),0) FROM change_log").fetchone()[0]
        with mock.patch("crew.git_view._refs", side_effect=held):
            began = time.monotonic()
            self.assertEqual(self.state()["git_branches"], {"pending": True})
            self.assertTrue(started.wait(5))
            # The scan is still held inside Git, and another read does not wait for it either.
            self.assertEqual(self.state()["git_branches"], {"pending": True})
            self.assertLess(time.monotonic() - began, 5)
            release.set()
            self.settle_git()
        result = self.state()["git_branches"]
        expected = git_overview.read(self.conn, board.project(self.conn, "demo"), board.git_config(self.conn, "demo"), force=True)
        self.assertEqual(result, expected)
        self.assertEqual([branch["owners"] for branch in result["branches"]], [[{"session_id": "s3", "name": "lead"}]])
        # The finished scan reached the event stream as the workspace's conversations invalidation.
        query = ("SELECT entity FROM change_log WHERE seq>? AND scope='workspace:demo' "
                 "AND type='conversations.invalidated'")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self.conn.execute(query, (before,)).fetchone() is None:
            time.sleep(.05)
        self.assertEqual(self.conn.execute(query, (before,)).fetchone()[0], "demo")

    def test_failed_branch_scan_shows_its_reason_until_a_scan_succeeds(self):
        self.topic_owner_fixture()
        reason = "fatal: detected dubious ownership in repository at 'C:/fixture'"
        with mock.patch("crew.git_view._refs", side_effect=git_view.GitError(reason)):
            self.assertEqual(self.state()["git_branches"], {"pending": True})
            self.settle_git()
            self.assertEqual(self.state()["git_branches"], {"error": reason})
            self.settle_git()
        # Every read retries a failed scan; the reason stays until one succeeds.
        self.assertEqual(self.state()["git_branches"], {"error": reason})
        self.settle_git()
        self.assertEqual([branch["name"] for branch in self.state()["git_branches"]["branches"]], ["topic"])

    def test_state_git_branches_is_none_when_disabled(self):
        with mock.patch("crew.git_overview.read") as read:
            code, result = self.request("/api/op/workspace.state?project=demo")
        self.assertEqual(code, 200, result)
        self.assertIsNone(result["git_branches"])
        read.assert_not_called()

    def test_state_git_branches_is_none_for_non_repository(self):
        temporary = tempfile.TemporaryDirectory(prefix="crew-git-state-ordinary-")
        self.addCleanup(temporary.cleanup)
        ordinary = temporary.name
        self.assertIsNotNone(git_view.unavailable_reason(ordinary))
        board.init_project(self.conn, "ordinary", ordinary, now=NOW)
        self.conn.execute("INSERT INTO git_management(project,enabled_at,base) VALUES(?,?,?)",
                          ("ordinary", NOW, "main"))
        with mock.patch("crew.git_overview.read") as read:
            code, result = self.request("/api/op/workspace.state?project=ordinary")
        self.assertEqual(code, 200, result)
        self.assertIsNone(result["git_branches"])
        read.assert_not_called()

    def test_state_preserves_git_read_errors_without_failing_request(self):
        self.topic_owner_fixture()
        failures = (git_view.GitError("boom"), OSError("fixture read denied"),
                    subprocess.TimeoutExpired(["fixture-git"], 10))
        for failure in failures:
            with self.subTest(error_type=type(failure).__name__), \
                    mock.patch("crew.git_view.view", side_effect=failure):
                self.state()
                self.settle_git()
                result = self.state()
                self.settle_git()
            self.assertEqual(result["git_branches"], {"error": str(failure)})

    def test_enabled_get_propagates_git_read_error(self):
        self.topic_owner_fixture()
        with mock.patch("crew.git_view.view", side_effect=git_view.GitError("boom")):
            code, result = self.request("/api/op/git.overview?project=demo")
        self.assertEqual(code, 400, result)
        self.assertEqual(result["error"], "boom")

    def test_enable_reuses_admin_and_git_view_lists_project_merge_requests(self):
        code, config = self.request("/api/op/git.enable?project=demo", {})
        self.assertEqual(code, 200, config)
        sid = config["admin_session_id"]
        self.assertEqual(self.request("/api/op/git.enable?project=demo", {"base": "topic"})[1]["admin_session_id"], sid)
        self.assertEqual(len(self.processes), 1)
        merged = board.request_git_merge(self.conn, "demo", "s1", "topic", "Implementation and tests", title=None, now=NOW)
        board.resolve_git_request(self.conn, "demo", sid, merged["id"], "merged", "CI passed", pr_number=17, now=NOW)
        opened = board.request_git_merge(self.conn, "demo", "s2", "master", "Needs review", title=None, now=NOW)
        with mock.patch("crew.git_overview.read", return_value={"branches": []}):
            code, result = self.request("/api/op/git.overview?project=demo")
        self.assertEqual(code, 200, result)
        requests = {request["id"]: request for request in result["requests"]}
        self.assertEqual(set(requests), {merged["id"], opened["id"]})
        self.assertEqual((requests[merged["id"]]["requester_name"], requests[merged["id"]]["pr_number"],
                          requests[merged["id"]]["note"]), ("one", 17, "CI passed"))
        self.assertEqual(requests[opened["id"]]["status"], "open")

    def git_settings_fixture(self):
        board.join(self.conn, 'chief', 'demo', 'Git 助手', self.project, self.project,
                   'develop', role='leader', group_name='git', now=NOW)
        board.enable_git(self.conn, 'demo', 'develop', '', admin_session_id='chief', now=NOW)
        code, snapshot = self.request('/api/op/git.settings.get?project=demo')
        self.assertEqual(code, 200, snapshot)
        return {'project': 'demo', 'revision': snapshot['revision'],
                'base': 'develop', 'integration': '', 'branch_rules': 'Use PRs, tests and project rules.'}

    def test_settings_rejects_any_admin_session_id_without_changing_binding(self):
        body = self.git_settings_fixture()
        before = board.git_config(self.conn, 'demo')
        for sid in ('chief', 's3', 's1', 'outsider', '', None):
            for enabled in (True, False):
                with self.subTest(sid=sid, enabled=enabled):
                    code, error = self.request('/api/op/git.settings.set?project=demo',
                                               {**body, 'admin_session_id': sid, 'enabled': enabled})
                    self.assertEqual(code, 400, error)
                    self.assertEqual(error['error'], 'Git 助手由 Crew 内置，不能指定其他 leader')
                    self.assertEqual(board.git_config(self.conn, 'demo'), before)
        self.assertEqual(len(self.processes), 0)

    def test_settings_requires_enable_to_create_the_assistant(self):
        body = {'project': 'demo', 'revision': git_admin.revision(None), 'base': 'develop', 'integration': '', 'branch_rules': ''}
        code, error = self.request('/api/op/git.settings.set?project=demo', body)
        self.assertEqual(code, 400, error)
        self.assertEqual(error['error'], '请先启用 Git 助手')
        self.assertIsNone(board.git_config(self.conn, 'demo'))

    def test_builtin_assistant_state_has_leader_fields_and_scoped_attention_counts(self):
        self.git_settings_fixture()
        journal = self.server.service.chat.journal('chief')
        journal.emit('assistant', id='read-reply', text='Read reply')
        board.mark_read(self.conn, 'chief', journal.events[-1]['seq'])
        journal.emit('assistant', id='unread-one', text='First unread reply')
        journal.emit('assistant', id='unread-two', text='Second unread reply')
        board.ask(self.conn, 'chief', 'Choose a branch', ['A', 'B'], now=NOW)
        answered = board.ask(self.conn, 'chief', 'Answered choice', ['A', 'B'], now=NOW)
        board.answer(self.conn, answered, 'A', now=NOW)
        board.ask(self.conn, 's3', 'Another leader choice', ['A', 'B'], now=NOW)
        board.ask(self.conn, 's1', 'Member choice', ['A', 'B'], now=NOW)
        board.join(self.conn, 'git-member', 'demo', 'Git reviewer', self.project, self.project, 'topic', group_name='git', now=NOW)
        board.ask(self.conn, 'git-member', 'Git member choice', ['A', 'B'], now=NOW)
        for status in ('open', 'waiting_user', 'waiting_user', 'merging', 'merged', 'rejected'):
            request = board.request_git_merge(self.conn, 'demo', 's1', 'topic', status, title=None, now=NOW)
            if status != 'open':
                board.resolve_git_request(self.conn, 'demo', 'chief', request['id'], status, status, now=NOW)
        other = self.root / 'other-project'; other.mkdir()
        board.init_project(self.conn, 'other', other, now=NOW)
        board.join(self.conn, 'other-admin', 'other', 'Git 助手', other, other, 'main', role='leader', group_name='git', now=NOW)
        self.conn.execute("INSERT INTO git_management(project,enabled_at,base,admin_session_id) VALUES('other',?,'main','other-admin')", (NOW,))
        request = board.request_git_merge(self.conn, 'other', 'other-admin', 'topic', 'Other request', title=None, now=NOW)
        board.resolve_git_request(self.conn, 'other', 'other-admin', request['id'], 'waiting_user', 'Other wait', now=NOW)
        board.ask(self.conn, 'other-admin', 'Other choice', ['A', 'B'], now=NOW)
        self.server.service.chat.journal('other-admin').emit('assistant', id='other-unread', text='Other reply')
        for runtime in ('claude', 'codex'):
            with self.subTest(runtime=runtime):
                runtimes.set(self.conn, 'chief', runtime)
                state = self.request('/api/op/workspace.state?project=demo')[1]
                assistant = state['git_assistant']
                self.assertTrue(assistant['builtin'])
                self.assertEqual(assistant['runtime'], runtime)
                self.assertEqual((assistant['session_id'], assistant['name']), ('chief', 'Git 助手'))
                self.assertEqual((assistant['base'], assistant['integration']), ('develop', None))
                self.assertEqual(set(assistant) - {'builtin', 'base', 'integration', 'requests', 'attention'}, set(state['leaders'][0]))
                self.assertNotIn('chief', {leader['session_id'] for leader in state['leaders']})
                self.assertTrue(all('is_git_admin' not in leader for leader in state['leaders']))
                self.assertEqual(assistant['requests'], {'active': 4, 'waiting_user': 2, 'merging': 1, 'queued': 1})
                self.assertEqual(assistant['unread'], 2)
                self.assertEqual(assistant['pending'], 2)  # Shared leader field also includes its member's choice.
                self.assertEqual(assistant['attention'], 5)  # 2 waiting requests + 2 replies + 1 own open choice.
                with mock.patch('crew.git_overview.read', return_value={'branches': []}):
                    code, git = self.request('/api/op/git.overview?project=demo')
                self.assertEqual(code, 200, git)
                self.assertEqual(git['assistant'], assistant)
        other_state = self.request('/api/op/workspace.state?project=other')[1]
        self.assertEqual(other_state['leaders'], [])
        self.assertEqual(other_state['git_assistant']['attention'], 3)
        self.assertEqual(self.request('/api/op/leaders.order.set?project=demo', {'project': 'demo', 'order': ['s3']})[0], 200)
        self.assertEqual(self.request('/api/op/leaders.order.set?project=demo', {'project': 'demo', 'order': ['s3'], 'pinned': ['s3']})[0], 200)
        self.assertEqual(self.request('/api/op/leaders.order.set?project=demo', {'project': 'demo', 'order': ['s3'], 'pinned': ['chief']})[0], 400)

    def test_disabled_assistant_is_null_and_legacy_ordinary_leader_stays_visible(self):
        self.assertIsNone(self.request('/api/op/workspace.state?project=demo')[1]['git_assistant'])
        with mock.patch('crew.git_overview.read', return_value={'branches': []}):
            self.assertIsNone(self.request('/api/op/git.overview?project=demo')[1]['assistant'])
        board.enable_git(self.conn, 'demo', 'develop', '', admin_session_id='s3', now=NOW)
        board.join(self.conn, 'unbound-git', 'demo', 'git管理员', self.project, self.project,
                   'topic', role='leader', group_name='git', now=NOW)
        before = board.session(self.conn, 's3')
        config = board.git_config(self.conn, 'demo')
        state = self.request('/api/op/workspace.state?project=demo')[1]
        self.assertFalse(state['git_assistant']['builtin'])
        self.assertIn('unbound-git', {item['session_id'] for item in state['leaders']})
        self.assertEqual(state['git_assistant']['session_id'], 's3')
        leader = next(item for item in state['leaders'] if item['session_id'] == 's3')
        self.assertEqual({key: value for key, value in state['git_assistant'].items()
                          if key not in {'builtin', 'base', 'integration', 'requests', 'attention'}}, leader)
        with mock.patch('crew.git_overview.read', return_value={'branches': []}):
            self.assertFalse(self.request('/api/op/git.overview?project=demo')[1]['assistant']['builtin'])
        self.assertEqual(board.session(self.conn, 's3'), before)
        self.assertEqual(board.git_config(self.conn, 'demo'), config)

    def test_settings_changes_branches_rules_without_restarting_sessions(self):
        body = self.git_settings_fixture()
        first = self.request('/api/op/git.settings.set?project=demo', body)[1]
        code, updated = self.request('/api/op/git.settings.set?project=demo',
                                    {**body, 'revision': first['revision'], 'base': 'topic', 'integration': 'master',
                                     'branch_rules': 'New policy'})
        self.assertEqual(code, 200, updated)
        self.assertEqual((updated['config']['base'], updated['config']['integration']), ('topic', 'master'))
        self.assertEqual(updated['config']['enabled_at'], first['config']['enabled_at'])
        self.assertEqual(updated['config']['admin_session_id'], 'chief')
        self.assertEqual(updated['config']['branch_rules'], 'New policy')
        self.assertNotEqual(board.session(self.conn, 'chief')['status'], 'closed')
        self.assertEqual(len(self.processes), 0)

    def test_settings_rejects_invalid_branch_rules_and_cross_project_writes(self):
        body = self.git_settings_fixture()
        other = self.root / 'other-project'; other.mkdir()
        board.init_project(self.conn, 'other', other, now=NOW)
        before = board.git_config(self.conn, 'demo')
        for changes in ({'base': 'missing'}, {'integration': 'develop'}, {'integration': 'missing'}, {'integration': None},
                        {'branch_rules': 42}, {'project': 'other'}):
            with self.subTest(changes=changes):
                code, result = self.request('/api/op/git.settings.set?project=demo', {**body, **changes})
                self.assertEqual(code, 400, result)
                self.assertEqual(board.git_config(self.conn, 'demo'), before)
        self.assertEqual(self.request('/api/op/git.settings.set?project=demo', body, header=False)[0], 403)
        self.assertEqual(len(self.processes), 0)

    def test_settings_concurrent_ai_policy_edit_is_not_overwritten(self):
        body = self.git_settings_fixture()
        saved = self.request('/api/op/git.settings.set?project=demo', body)[1]
        board.set_git_policy(self.conn, 'demo', 'chief', 'A newer AI policy', now=NOW)
        code, error = self.request('/api/op/git.settings.set?project=demo', {**body, 'revision': saved['revision']})
        self.assertEqual(code, 409, error)
        self.assertIn('重新读取', error['error'])
        self.assertEqual(board.git_policy(self.conn, 'demo'), 'A newer AI policy')

    def test_settings_disable_keeps_leader_group_and_merge_requests(self):
        body = self.git_settings_fixture()
        saved = self.request('/api/op/git.settings.set?project=demo', body)[1]
        request = board.request_git_merge(self.conn, 'demo', 's1', 'topic', 'Ready for merge', title=None, now=NOW)
        before = board.session(self.conn, 'chief')
        code, result = self.request('/api/op/git.settings.set?project=demo',
                                    {'project': 'demo', 'revision': saved['revision'], 'enabled': False})
        self.assertEqual(code, 200, result)
        self.assertIsNone(board.git_config(self.conn, 'demo'))
        self.assertEqual(board.session(self.conn, 'chief'), before)
        self.assertEqual(board.git_requests(self.conn, 'demo')[0]['id'], request['id'])
        self.assertIsNone(next(g for g in board.groups(self.conn, 'demo') if g['name'] == 'git')['closed_at'])
        self.assertEqual(len(self.processes), 0)

    def test_settings_is_independent_of_graph_github_and_keeps_config_on_repository_error(self):
        body = self.git_settings_fixture()
        self.request('/api/op/git.settings.set?project=demo', body)
        with mock.patch('crew.git_view.view', side_effect=AssertionError('No graph scan')), \
                mock.patch('crew.git_view._github', side_effect=AssertionError('No network')), \
                mock.patch('crew.git_view.branch_choices', side_effect=subprocess.TimeoutExpired(['fixture-git'], 10)):
            code, result = self.request('/api/op/git.settings.get?project=demo')
        self.assertEqual(code, 200, result)
        self.assertFalse(result['available'])
        self.assertIn('timed out', result['reason'])
        self.assertEqual(result['config']['admin_session_id'], 'chief')
        self.assertEqual({row['session_id'] for row in result['leaders']}, {'s3'})
        self.assertEqual(len(self.processes), 0)

    def test_fetch_calls_only_explicit_post_and_returns_raw_error(self):
        with mock.patch("crew.git_view.fetch") as fetch:
            self.assertEqual(self.request("/api/op/git.fetch?project=demo", {})[0], 400)
            fetch.assert_not_called()
        self.request("/api/op/git.enable?project=demo", {})
        for result in ({"ok": True, "output": "updated\n", "error": None, "last_fetch_at": NOW},
                       {"ok": False, "output": "", "error": "fatal: fixture remote rejected\n", "last_fetch_at": None}):
            with self.subTest(result=result), mock.patch("crew.git_view.fetch", return_value=result) as fetch:
                self.assertEqual(self.request("/api/op/git.fetch?project=demo", {}), (200, result))
                fetch.assert_called_once_with(board.project(self.conn, "demo")["root"])

    def test_adopt_hands_unowned_branch_to_chosen_leader(self):
        body = {"project": "demo", "branch": "topic", "leader_session_id": "heir"}
        self.assertEqual(self.request("/api/op/git.adopt", body)[0], 400)
        self.assertEqual(self.request("/api/op/git.enable?project=demo", {})[0], 200)
        self.git("switch", "topic")
        self.git("-c", "user.name=Crew Test", "-c", "user.email=crew@example.invalid", "-c", "commit.gpgsign=false",
                 "commit", "--allow-empty", "-m", "orphan work")
        self.git("switch", "develop")
        board.join(self.conn, "heir", "demo", "Heir", self.project, None, "develop", role="leader", now=NOW)
        self.assertEqual(self.request("/api/op/git.adopt", body, header=False)[0], 403)
        self.assertEqual(self.request("/api/op/git.adopt", {**body, "branch": " "})[0], 400)
        code, result = self.request("/api/op/git.adopt", body)
        self.assertEqual(code, 200, result)
        self.assertEqual(result, {"branch": "topic", "leader": {"session_id": "heir", "name": "Heir"}, "already": False})
        branch = next(item for item in self.request("/api/op/git.overview?project=demo")[1]["branches"] if item["name"] == "topic")
        self.assertEqual(branch["owners"], [{"session_id": "heir", "name": "Heir"}])

    def test_commit_detail_returns_real_stats_and_invalid_sha_is_400(self):
        self.assertEqual(self.request("/api/op/git.commit.get?project=demo&sha=0000")[0], 400)
        self.assertEqual(self.request("/api/op/git.enable?project=demo", {})[0], 200)
        sha = self.git("rev-parse", "HEAD")
        code, result = self.request("/api/op/git.commit.get?project=demo&sha=" + sha)
        self.assertEqual(code, 200, result)
        self.assertEqual(result["sha"], sha)
        self.assertEqual(result["file_count"], 2)
        self.assertEqual(result["additions"], 2)
        self.assertEqual(result["deletions"], 0)
        for invalid in ("0" * 40, "--help", ""):
            code, result = self.request("/api/op/git.commit.get?project=demo&sha=" + invalid)
            self.assertEqual(code, 400, result)
            self.assertTrue(result["error"])
        self.assertEqual(self.request("/api/op/git.commit.get?project=unknown&sha=" + sha)[0], 400)

    def test_commit_detail_selects_only_requested_project(self):
        other = self.root / "other-project"
        other.mkdir()
        board.init_project(self.conn, "other", other, now=NOW)
        self.request("/api/op/git.enable?project=demo", {})
        board.enable_git(self.conn, "other", "develop", None, now=NOW)
        payload = {"sha": "a" * 40, "files": [], "file_count": 0, "additions": 0, "deletions": 0}
        with mock.patch("crew.git_view.commit_detail", return_value=payload) as detail:
            self.assertEqual(self.request("/api/op/git.commit.get?project=other&sha=" + payload["sha"]), (200, payload))
            detail.assert_called_once_with(board.project(self.conn, "other")["root"], payload["sha"])

    def test_invalid_project_branch_and_csrf(self):
        self.assertEqual(self.request("/api/op/git.overview?project=unknown")[0], 400)
        self.assertEqual(self.request("/api/op/git.enable?project=demo", {"base": "missing"})[0], 400)
        self.assertEqual(self.request("/api/op/git.enable?project=demo", {"integration": 42})[0], 400)
        self.assertEqual(self.request("/api/op/git.enable?project=demo", {"integration": "develop"})[0], 400)
        self.assertEqual(self.request("/api/op/git.enable?project=demo", {}, header=False)[0], 403)
        self.assertIsNone(board.git_config(self.conn, "demo"))

    def test_static_git_assets(self):
        for asset, expected in (("git.js", "text/javascript"), ("git.css", "text/css")):
            with urlopen(self.server.url + "/" + asset, timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertIn(expected, response.headers["Content-Type"])
                self.assertTrue(response.read())

    def result_handoff(self):
        board.update_cc_name(self.conn, "s3", "cc-lead", now=NOW)
        self.conn.execute("INSERT INTO git_management(project,enabled_at,base,admin_session_id) VALUES(?,?,?,?)",
                          ("demo", NOW, "develop", "s3"))
        request = board.request_git_merge(self.conn, "demo", "s1", "topic", "tests passed", title=None, blocking=True, now=NOW)
        board.resolve_git_request(self.conn, "demo", "s3", request["id"], "merged", "CI passed", now=NOW)
        return board.handoffs(self.conn, "s1")[0]

    def test_merge_request_after_stop_wakes_git_assistant_but_earlier_request_does_not(self):
        service = self.server.service
        self.conn.execute("INSERT INTO git_management(project,enabled_at,base,admin_session_id) VALUES(?,?,?,?)",
                          ("demo", NOW, "develop", "s3"))
        board.request_git_merge(self.conn, "demo", "s1", "topic", "filed before the stop", title=None, now=NOW)
        after = "2027-01-01T00:00:00.000Z"
        self.conn.execute("INSERT INTO session_stop_gates VALUES(?,?,?)", ("s3", after, board.HUMAN))
        with mock.patch.object(service.chat, "running", return_value=True), \
                mock.patch.object(service.chat, "busy", return_value=False), \
                mock.patch.object(service.chat, "send", return_value={"accepted": True}) as send:
            service.deliver_notices()
            send.assert_not_called()
            self.assertTrue(messaging.stopped(self.conn, "s3"))
            board.request_git_merge(self.conn, "demo", "s1", "topic", "filed after the stop", title=None,
                                    now="2027-01-02T00:00:00.000Z")
            service.deliver_notices()
            send.assert_called_once()
            self.assertIn("filed after the stop", send.call_args.args[1])
        self.assertFalse(messaging.stopped(self.conn, "s3"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events WHERE kind='git:admin_woken' AND session_id='s3'").fetchone()[0], 1)
        self.assertEqual(board.pending_notices(self.conn, "s3"), [])

    def test_actionable_results_share_delivery_for_leaders_members_and_both_carriers(self):
        service = self.server.service
        self.conn.execute("INSERT INTO git_management(project,enabled_at,base,admin_session_id) VALUES(?,?,?,?)", ('demo', NOW, 'develop', 's3'))
        board.join(self.conn, 'result-lead', 'demo', 'Result chief', self.project, self.project, 'topic', role='leader', now=NOW)
        for sid in ('s1', 'result-lead'):
            for runtime in ('claude', 'codex'):
                with self.subTest(sid=sid, runtime=runtime), service.write_lock:
                    runtimes.set(self.conn, sid, runtime)
                    request = board.request_git_merge(self.conn, 'demo', sid, 'topic', 'Continue after merge', title=None, blocking=True, now=NOW)
                    board.resolve_git_request(self.conn, 'demo', 's3', request['id'], 'merged', 'Continue verification', now=NOW)
                    with mock.patch.object(service.chat, 'running', return_value=True), mock.patch.object(service.chat, 'busy', return_value=False), \
                            mock.patch.object(service.chat, 'send') as send, mock.patch.object(service.chat, 'wake') as wake:
                        service.deliver_git_results(self.conn); service.deliver_git_results(self.conn)
                        send.assert_called_once(); wake.assert_not_called()
                        self.assertEqual(send.call_args.args[0]['session_id'], sid)

    def test_idle_background_result_is_sent_once(self):
        service = self.server.service
        with service.write_lock:
            self.result_handoff()
            with mock.patch.object(service.chat, "running", return_value=True), \
                    mock.patch.object(service.chat, "busy", return_value=False), \
                    mock.patch.object(service.chat, "send") as send, mock.patch.object(service.chat, "wake") as wake:
                service.deliver_git_results(self.conn)
                service.deliver_git_results(self.conn)
                send.assert_called_once()
                self.assertIn("CI passed", send.call_args.args[1])
                wake.assert_not_called()
            self.assertEqual(board.handoffs(self.conn, "s1"), [])

    def test_offline_result_uses_existing_wake_entry(self):
        service = self.server.service
        with service.write_lock:
            self.result_handoff()
            with mock.patch.object(service.chat, "running", return_value=False), \
                    mock.patch.object(service.chat, "wake", wraps=service.chat.wake) as wake, \
                    mock.patch("crew.chat.shutil.which", return_value="C:/fixture/claude.exe"):
                service.deliver_git_results(self.conn)
                wake.assert_called_once()
                self.assertEqual(wake.call_args.kwargs["caller_session_id"], "s3")
            self.assertEqual(len(self.processes), 1)
            command = self.processes[0].command
            self.assertEqual(command[command.index('--resume') + 1], 's1')
            self.assertIn("CI passed", self.processes[0].stdin.getvalue())
            service.deliver_git_results(self.conn)
            self.assertEqual(len(self.processes), 1)
            self.assertEqual(board.handoffs(self.conn, "s1"), [])

    def test_vscode_online_result_remains_in_handoff(self):
        service = self.server.service
        with service.write_lock:
            self.result_handoff()
            with service.agents_lock:
                service.agents = [{"sessionId": "s1"}]
            with mock.patch.object(service.chat, "send") as send, mock.patch.object(service.chat, "wake") as wake:
                service.deliver_git_results(self.conn)
                send.assert_not_called()
                wake.assert_not_called()
            self.assertEqual(len(board.handoffs(self.conn, "s1")), 1)
            self.assertEqual(len(board.deliver(self.conn, "s1", now=NOW)["handoffs"]), 1)
            self.assertEqual(board.handoffs(self.conn, "s1"), [])

    def test_busy_and_unknown_process_state_defer_result(self):
        service = self.server.service
        with service.write_lock:
            self.result_handoff()
            with mock.patch.object(service.chat, "running", return_value=True), \
                    mock.patch.object(service.chat, "busy", return_value=True), \
                    mock.patch.object(service.chat, "send") as send, mock.patch.object(service.chat, "wake") as wake:
                service.deliver_git_results(self.conn)
                service.agents_error = "fixture process scan error"
                service.deliver_git_results(self.conn)
                send.assert_not_called()
                wake.assert_not_called()
            self.assertEqual(len(board.handoffs(self.conn, "s1")), 1)

    def test_failed_wake_keeps_result_pending_for_retry(self):
        service = self.server.service
        with service.write_lock:
            self.result_handoff()
            with mock.patch.object(service.chat, "running", return_value=False), \
                    mock.patch.object(service.chat, "wake", side_effect=OSError("fixture launch error")):
                service.deliver_git_results(self.conn)
            self.assertEqual(len(board.handoffs(self.conn, "s1")), 1)
            event = self.conn.execute("SELECT detail FROM events WHERE kind='git:result_error'").fetchone()
            self.assertIn("fixture launch error", event["detail"])
            with mock.patch.object(service.chat, "running", return_value=False), \
                    mock.patch.object(service.chat, "wake") as wake:
                service.deliver_git_results(self.conn)
                wake.assert_called_once()
            self.assertEqual(board.handoffs(self.conn, "s1"), [])
