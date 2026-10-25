"""Git opt-in commands and HTTP routes use isolated boards and temporary repositories."""
import tests  # Install the production-service connection guard.
import json
import subprocess
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest import mock

from crew import board, git_admin, hooks, models, runtimes, settings, spawn, store, web
from test_core import CoreCase, NOW
from test_shell import PLUGIN, ShellFixture


class GitCommandTests(CoreCase):
    def setUp(self):
        super().setUp()
        carrier = mock.patch.object(runtimes, 'default_runtime', return_value='claude')
        carrier.start()
        self.addCleanup(carrier.stop)
        (self.root / ".gitignore").write_text("/Temp/wt/\n", encoding="ascii")
        self.git("add", ".gitignore")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "-c", "commit.gpgsign=false",
                 "commit", "--allow-empty", "-m", "fixture")
        for name in ("develop", "dev", "master", "topic"):
            self.git("branch", name)
        self.plans = []
        patcher = mock.patch("crew.spawn.request_start", side_effect=self.service_request)
        self.service = patcher.start()
        self.addCleanup(patcher.stop)
        self.transcripts = self.base / "claude-projects"
        self.transcripts.mkdir()
        patcher = mock.patch("crew.cli.CLAUDE_PROJECTS", self.transcripts)
        patcher.start()
        self.addCleanup(patcher.stop)

    def service_request(self, home, project_name, path, body):
        if path == "/api/git/enable":
            return git_admin.enable(self.conn, home, self.fake_plugin, project_name, self.launcher, **body, now=NOW)
        self.assertEqual(path, "/api/git/disable")
        return git_admin.disable(self.conn, project_name, now=NOW)

    def launcher(self, conn, project_name, name, task, **options):
        plan = spawn.prepare(conn, self.home, self.fake_plugin, project_name, name, task, **options)
        self.plans.append(plan)
        spec = plan["spec"]

        def register(_):
            worktree, branch = hooks.git_details(spec["cwd"])
            board.register_session(conn, plan["session_id"], project_name, spec["cwd"], worktree, branch,
                                   name=name, role=spec["role"], group_name=spec["group"], spawn_id=spec["id"], now=NOW)
            runtimes.set(conn, plan['session_id'], spec['runtime'], plan['session_id'] if spec['runtime'] == 'claude' else None)
            models.set(conn, plan["session_id"], spec["model"], spec["effort"], now=NOW)

        result = spawn._execute(conn, plan, register)
        return {**result, "session_id": plan["session_id"]}

    def enable(self):
        self.assert_cli(["git", "enable"])
        return board.git_config(self.conn, "demo")["admin_session_id"]

    def git(self, *arguments):
        return subprocess.run(["git", "-C", str(self.root), *arguments], check=True,
                              capture_output=True, text=True, encoding="utf-8").stdout

    def test_enable_starts_git_administrator_in_separate_worktree(self):
        text = self.assert_cli(["git", "enable"], sid=None)[1]
        self.assertIn("Git 管理已启用", text)
        self.assertIn("base: develop", text)
        self.assertIn("stable: master", text)
        config = board.git_config(self.conn, "demo")
        self.assertEqual((config["base"], config["stable"]), ("develop", "master"))
        administrator = board.session(self.conn, config["admin_session_id"])
        self.assertEqual((administrator["crew_name"], administrator["role"], administrator["group_name"]),
                         ("git管理员", "leader", "git"))
        plan = self.plans[0]
        self.assertEqual(administrator["session_id"], plan["session_id"])
        self.assertNotEqual(administrator["worktree"], store.norm_path(self.root))
        self.assertTrue(administrator["branch"].startswith("crew/git-admin-"))
        self.assertEqual(plan["worktree_command"][-1], "develop")
        self.assertEqual(models.get(self.conn, administrator["session_id"]), {"model": None, "effort": None})
        command = plan["command"]
        self.assertEqual(command[command.index("--agent") + 1], "crew:git")
        self.assertNotIn('--model', command)
        self.assertNotIn('--effort', command)
        self.assertIn("/crew:git", plan["files"][plan["spec"]["md"]])
        self.assertIsNone(config["branch_rules"])
        self.assertIsNotNone(config["enabled_at"])

    def test_repeated_enable_updates_config_without_starting_another_administrator(self):
        administrator = self.enable()
        self.assert_cli(["git", "enable", "--base", "topic", "--stable", "main"])
        config = board.git_config(self.conn, "demo")
        self.assertEqual(config["admin_session_id"], administrator)
        self.assertEqual(config["base"], "topic")
        self.assertEqual(len(self.plans), 1)

    def test_codex_only_git_administrator_reads_both_rules_without_claude_flags(self):
        settings.save(self.conn, {'default': {'runtime': 'codex', 'model': None, 'effort': None}})
        with mock.patch.object(spawn.codex_client, 'command', return_value=['codex.exe', 'app-server']):
            self.assert_cli(['git', 'enable'])
        plan = self.plans[0]
        administrator = board.git_config(self.conn, 'demo')['admin_session_id']
        self.assertEqual(runtimes.get(self.conn, administrator)['runtime'], 'codex')
        self.assertEqual(models.get(self.conn, administrator), {'model': None, 'effort': None})
        self.assertEqual(plan['command'], ['codex.exe', 'app-server'])
        brief = plan['files'][plan['spec']['md']]
        self.assertIn(str(self.fake_plugin / 'skills' / 'leader' / 'SKILL.md'), brief)
        self.assertIn(str(self.fake_plugin / 'skills' / 'git' / 'SKILL.md'), brief)
        self.assertNotIn('/crew:leader', brief)
        self.assertNotIn('/crew:git', brief)

    def test_explicit_codex_git_enable_reaches_launcher_and_reenable_keeps_identity(self):
        with mock.patch.object(spawn.codex_client, 'command', return_value=['codex.exe', 'app-server']):
            self.assert_cli(['git', 'enable', '--runtime', 'codex'])
        administrator = board.git_config(self.conn, 'demo')['admin_session_id']
        self.assert_cli(['git', 'enable', '--base', 'topic'])
        self.assertEqual(board.git_config(self.conn, 'demo')['admin_session_id'], administrator)
        self.assertEqual(len(self.plans), 1)
        self.assertIn('会话菜单', self.assert_cli(['git', 'enable', '--runtime', 'claude'], code=3)[2])

    def test_no_ai_carrier_does_not_enable_git_or_create_worktree(self):
        with mock.patch.object(settings, 'resolve', side_effect=board.BoardError('请先安装并登录 Codex CLI 或 Claude Code')):
            self.assertIn('安装并登录', self.assert_cli(['git', 'enable'], code=1)[2])
        self.assertIsNone(board.git_config(self.conn, 'demo'))
        self.assertFalse((self.root / 'Temp' / 'wt').exists())

    def test_git_brain_settings_supply_model_and_effort_without_changing_other_jobs(self):
        settings.save(self.conn, {'default': {'runtime': 'claude', 'model': None, 'effort': None},
                                 'git': {'runtime': 'codex', 'model': 'gpt-fixture-purpose', 'effort': 'high'}})
        with mock.patch.object(spawn.codex_client, 'command', return_value=['codex.exe', 'app-server']):
            self.assert_cli(['git', 'enable', '--runtime', 'codex'])
        self.assertEqual(self.plans[0]['spec']['model'], 'gpt-fixture-purpose')
        self.assertEqual(self.plans[0]['spec']['effort'], 'high')
        self.assertIsNone(settings.load(self.conn)['default']['model'])

    def test_start_failure_does_not_mark_git_enabled(self):
        with mock.patch.object(self, "launcher", side_effect=OSError("fixture launch failure")):
            self.assertIn("fixture launch failure", self.assert_cli(["git", "enable"], code=1)[2])
        self.assertIsNone(board.git_config(self.conn, "demo"))

    def test_enable_auto_priority_and_single_branch_stable(self):
        for removed, expected in (("develop", "dev"), ("dev", "main"), ("main", "master")):
            if removed == "main":
                self.git("symbolic-ref", "HEAD", "refs/heads/master")
            self.git("branch", "-D", removed)
            self.assert_cli(["git", "enable"], sid=None)
            config = board.git_config(self.conn, "demo")
            self.assertEqual(config["base"], expected)
        self.assertIsNone(config["stable"])

    def test_enable_explicit_branches_and_invalid_branch_leave_config_unchanged(self):
        self.assert_cli(["git", "enable", "--base", "topic", "--stable", "main"])
        previous = board.git_config(self.conn, "demo")
        for arguments in (("--base", "missing"), ("--stable", "missing")):
            error = self.assert_cli(["git", "enable", *arguments], code=1)[2]
            self.assertIn("分支不存在: missing", error)
            self.assertEqual(board.git_config(self.conn, "demo"), previous)

    def test_explicit_base_controls_stable_detection_and_empty_stable(self):
        self.assert_cli(["git", "enable", "--base", "master"])
        self.assertEqual(board.git_config(self.conn, "demo")["stable"], "main")
        board.enable_git(self.conn, "demo", "develop", "")
        self.assertIsNone(board.git_config(self.conn, "demo")["stable"])

    def test_status_disable_and_project_isolation(self):
        self.assertIn("未启用", self.assert_cli(["git", "status"], sid=None)[1])
        self.assert_cli(["git", "enable"])
        administrator = board.git_config(self.conn, "demo")["admin_session_id"]
        self.assertIn("base: develop", self.assert_cli(["git", "status"])[1])
        board.init_project(self.conn, "other", self.base / "other", now=NOW)
        self.assertIsNone(board.git_config(self.conn, "other"))
        self.assert_cli(["git", "disable"])
        self.assertIsNone(board.git_config(self.conn, "demo"))
        self.assertEqual(board.session(self.conn, administrator)["status"], "closed")
        group = next(g for g in board.groups(self.conn, "demo") if g["name"] == "git")
        self.assertEqual(group["closed_at"], NOW)
        self.assertIn("未启用", self.assert_cli(["git", "status"])[1])
        self.assert_cli(["git", "disable"])

    def test_enable_after_disable_gets_a_new_worktree(self):
        first = self.enable()
        self.assert_cli(["git", "disable"])
        second = self.enable()
        self.assertNotEqual(first, second)
        self.assertNotEqual(self.plans[0]["spec"]["cwd"], self.plans[1]["spec"]["cwd"])

    def test_no_recognised_base_requires_explicit_selection(self):
        self.git("symbolic-ref", "HEAD", "refs/heads/topic")
        for name in ("develop", "dev", "main", "master"):
            self.git("branch", "-D", name)
        self.assertIn("请指定 --base", self.assert_cli(["git", "enable"], code=1)[2])
        self.assert_cli(["git", "enable", "--base", "topic"])

    def test_policy_read_and_only_administrator_can_set(self):
        self.assertIn("未启用", self.assert_cli(["git", "policy"], code=1)[2])
        administrator = self.enable()
        self.assertIn("还没写分支规则", self.assert_cli(["git", "policy"], sid=None)[1])
        policy = "小改直接合 base\n大改走 PR\n命名 feature/<任务>\n提交 feat: 说明"
        self.assertIn("只有 git 管理员", self.assert_cli(["git", "policy", "--set", policy], code=1)[2])
        self.assert_cli(["git", "policy", "--set", policy], sid=administrator)
        self.assertEqual(self.assert_cli(["git", "policy"], sid=None)[1].strip(), policy)
        self.assertEqual(board.git_config(self.conn, "demo")["branch_rules"], policy)
        self.assert_cli(["git", "policy", "--set", " "], sid=administrator, code=1)

    def test_request_defaults_to_current_branch_and_queues_administrator_notice(self):
        administrator = self.enable()
        description = "增加按钮；单测通过；无需设备验收"
        self.assertIn("合并请求 #1", self.assert_cli(["git", "request", description])[1])
        request = board.git_requests(self.conn, "demo")[0]
        self.assertEqual((request["session_id"], request["branch"], request["description"], request["status"]),
                         ("s1", "main", description, "open"))
        notice = board.pending_notices(self.conn, administrator)[0]
        self.assertIn(description, notice["text"])
        self.assertIn("main", notice["text"])
        self.assert_cli(["git", "request", "指定分支", "--branch", "topic"])
        self.assertEqual(board.git_requests(self.conn, "demo")[1]["branch"], "topic")

    def test_request_requires_enabled_project_session_identity_and_text(self):
        self.assert_cli(["git", "request", "未启用"], code=1)
        self.enable()
        self.assert_cli(["git", "request", "无身份"], sid=None, code=1)
        self.assert_cli(["git", "request", " "], code=1)
        self.assert_cli(["git", "request", "空分支", "--branch", " "], code=1)
        board.init_project(self.conn, "other", self.base / "other", now=NOW)
        board.register_session(self.conn, "outsider", "other", self.base / "other", self.base / "other", "main", now=NOW)
        with self.assertRaisesRegex(board.BoardError, "不属于本项目"):
            board.request_git_merge(self.conn, "demo", "outsider", "main", "跨项目", title=None, now=NOW)
        self.assertEqual(board.git_requests(self.conn, "demo"), [])

    def test_unadopted_and_closed_sessions_can_request_and_receive_the_result(self):
        administrator = self.enable()
        board.register_session(self.conn, "unjoined-session", "demo", self.root, self.root, "main", now=NOW)
        self.assert_cli(["git", "request", "没有标题时用编号"], sid="unjoined-session")
        self.assertIn("unjoined（未收编） 提交分支 main", board.pending_notices(self.conn, administrator)[0]["text"])
        board.update_cc_name(self.conn, "unjoined-session", "cc-outsource", now=NOW)
        self.assertEqual(board.git_requests(self.conn, "demo")[0]["requester_name"], "cc-outsource")
        bucket = self.transcripts / "bucket"
        bucket.mkdir()
        (bucket / "unjoined-session.jsonl").write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in (
            {"type": "user", "cwd": str(self.root), "message": {"role": "user", "content": "修登录页"}},
            {"type": "ai-title", "aiTitle": "外包修复登录页"})), encoding="utf-8")
        self.assert_cli(["git", "request", "外包改动；测试通过"], sid="unjoined-session")
        self.assertIn("外包修复登录页（未收编，SendMessage 地址：cc-outsource） 提交分支 main",
                      board.pending_notices(self.conn, administrator)[1]["text"])
        self.assertIn("外包修复登录页", self.assert_cli(["git", "requests"])[1])
        (bucket / "unjoined-session").mkdir()
        (bucket / "unjoined-session" / "custom-title.json").write_text(json.dumps({"customTitle": "我改的名字"}), encoding="utf-8")
        self.assertIn("我改的名字", self.assert_cli(["git", "requests"])[1])
        request = board.git_requests(self.conn, "demo")[0]
        board.set_status(self.conn, "s2", "closed", now=NOW)
        self.assert_cli(["git", "request", "已关闭的会话也能交"], sid="s2")
        self.assert_cli(["git", "resolve", str(request["id"]), "merged", "--note", "已合入 base"], sid=administrator)
        delivered = board.deliver(self.conn, "unjoined-session", now=NOW)["handoffs"]
        self.assertEqual(len(delivered), 1)
        self.assertIn("已合入 base", delivered[0]["body"])
        self.assertEqual(board.handoffs(self.conn, "unjoined-session"), [])
        self.assertEqual(board.handoffs(self.conn, "s1"), [])

    def test_resolve_transitions_preserves_pr_and_member_delivery(self):
        administrator = self.enable()
        self.assert_cli(["git", "request", "变更；测试通过"])
        self.assertIn("只有 git 管理员", self.assert_cli(["git", "resolve", "1", "merged", "--note", "越权"], code=1)[2])
        self.assert_cli(["git", "resolve", "1", "merging", "--note", "检查 CI", "--pr", "42"], sid=administrator)
        self.assert_cli(["git", "resolve", "1", "waiting_user", "--note", "等待设备验收"], sid=administrator)
        request = board.git_requests(self.conn, "demo")[0]
        self.assertEqual((request["status"], request["pr_number"], request["note"]), ("waiting_user", 42, "等待设备验收"))
        self.assertEqual(len(board.handoffs(self.conn, "s1")), 2)
        delivered = board.deliver(self.conn, "s1", now=NOW)["handoffs"]
        self.assertTrue(all("合并请求 #1" in item["body"] for item in delivered))
        self.assertEqual(board.handoffs(self.conn, "s1"), [])
        self.assert_cli(["git", "resolve", "1", "merged", "--note", "已合入 base"], sid=administrator)
        self.assertEqual(board.git_requests(self.conn, "demo"), [])
        self.assertEqual(board.git_requests(self.conn, "demo", all=True)[0]["status"], "merged")
        self.assert_cli(["git", "resolve", "1", "rejected", "--note", "重复处理"], sid=administrator, code=1)

    def test_leader_result_notice_and_queue_order_and_project_isolation(self):
        administrator = self.enable()
        board.join(self.conn, "lead", "demo", "feature-lead", self.root, self.root, "topic", role="leader", now=NOW)
        self.assert_cli(["git", "request", "leader提交"], sid="lead")
        self.assert_cli(["git", "resolve", "1", "rejected", "--note", "范围不符"], sid=administrator)
        notice = board.pending_notices(self.conn, "lead")[0]
        self.assertIn("rejected", notice["text"])
        self.assert_cli(["git", "request", "新的请求"])
        self.assertEqual([item["id"] for item in board.git_requests(self.conn, "demo", all=True)], [2, 1])
        self.assertNotIn("leader提交", self.assert_cli(["git", "requests"])[1])
        self.assertIn("leader提交", self.assert_cli(["git", "requests", "--all"])[1])
        board.init_project(self.conn, "other", self.base / "other", now=NOW)
        self.assertEqual(board.git_requests(self.conn, "other", all=True), [])
        with self.assertRaises(board.BoardError):
            board.resolve_git_request(self.conn, "other", administrator, 2, "merged", "越项目", now=NOW)

    def test_resolve_validation_is_atomic(self):
        administrator = self.enable()
        self.assert_cli(["git", "request", "待处理"])
        for args in (("1", "merging", "--note", " "), ("1", "merging", "--note", "PR错", "--pr", "0"),
                     ("999", "merged", "--note", "不存在")):
            self.assert_cli(["git", "resolve", *args], sid=administrator, code=1)
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
        self.choices = {"branches": ["develop", "master", "topic"], "base": "develop", "stable": "master"}
        patcher = mock.patch("crew.git_view.branch_choices", return_value=self.choices)
        self.choice_provider = patcher.start()
        self.addCleanup(patcher.stop)
        self.server = web.create_server(
            self.home, PLUGIN, port=0, cwd=self.project, agents_provider=lambda: [],
            claude_root=self.claude, codex_root=self.codex, runs_file=self.runs,
            now=lambda: store.parse_iso(NOW), process_factory=self.process_factory,
            vscode_state_db=self.vscode_state_db, lock_status=lambda: False)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())

    def request(self, path, data=None, header=True):
        payload = None if data is None else json.dumps(data).encode("utf-8")
        headers = {"X-Crew": "1"} if header else {}
        try:
            response = urlopen(Request(self.server.url + path, payload, headers), timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_disabled_get_enable_and_disable(self):
        with mock.patch("crew.git_view.view") as view:
            code, result = self.request("/api/git?project=demo")
            self.assertEqual(code, 200)
            self.assertFalse(result["enabled"])
            self.assertEqual(result["choices"], self.choices)
            self.assertIsNone(result["config"])
            view.assert_not_called()
        code, config = self.request("/api/git/enable", {"project": "demo"})
        self.assertEqual(code, 200, config)
        self.assertEqual((config["base"], config["stable"]), ("develop", "master"))
        sid = config["admin_session_id"]
        admin = board.session(self.conn, sid)
        self.assertEqual((admin["crew_name"], admin["role"], admin["group_name"]), ("git管理员", "leader", "git"))
        self.assertNotEqual(admin["cwd"], str(self.project))
        self.assertTrue(self.server.service.chat.path(sid).exists())
        command = self.commands[-1]
        self.assertEqual(command[command.index("--agent") + 1], "crew:git")
        self.assertNotIn('--model', command)
        state = self.request("/api/state?project=demo")[1]
        admin_state = next(leader for leader in state["leaders"] if leader["session_id"] == sid)
        self.assertTrue(admin_state["is_git_admin"])
        self.assertEqual((admin_state["model"], admin_state["effort"]), (None, None))
        self.assertEqual(self.request("/api/session/options?project=demo", {"session_id": sid, "model": "claude-opus-5-5"})[0], 200)
        self.assertEqual(self.request("/api/git/disable?project=demo", {}), (200, {"ok": True}))
        self.assertIsNone(board.git_config(self.conn, "demo"))
        self.assertEqual(board.session(self.conn, sid)["status"], "closed")
        group = next(group for group in board.groups(self.conn, "demo") if group["name"] == "git")
        self.assertIsNotNone(group["closed_at"])

    def test_enabled_get_passes_project_sessions_and_pagination(self):
        other = self.root / "other-project"
        other.mkdir()
        board.init_project(self.conn, "other", other, now=NOW)
        board.join(self.conn, "other-session", "other", "other", other, self.project, "topic", now=NOW)
        self.request("/api/git/enable?project=demo", {"base": "topic", "stable": ""})
        payload = {"branches": [], "commits": [{"sha": "fixture"}], "next_offset": 600,
                   "github": None, "github_reason": "fixture stderr\n"}
        with mock.patch("crew.git_view.view", return_value=payload) as view, mock.patch("crew.git_view.fetch") as fetch:
            code, result = self.request("/api/git?project=demo&offset=300")
            fetch.assert_not_called()
        self.assertEqual(code, 200, result)
        self.assertTrue(result["enabled"])
        self.assertEqual(result["commits"], payload["commits"])
        self.assertEqual(result["github_reason"], "fixture stderr\n")
        self.assertIsNone(result["config"]["stable"])
        self.assertEqual(view.call_args.kwargs, {"offset": 300})
        root, config, sessions = view.call_args.args
        self.assertEqual(root, board.project(self.conn, "demo")["root"])
        self.assertEqual(config["base"], "topic")
        self.assertEqual({session["project"] for session in sessions}, {"demo"})
        self.assertNotIn("other-session", {session["session_id"] for session in sessions})

    def test_real_git_data_through_http(self):
        board.join(self.conn, "s1", "demo", "one", self.project, self.project, "develop", now=NOW)
        self.assertEqual(self.request("/api/git/enable?project=demo", {})[0], 200)
        with mock.patch("crew.git_view._github", return_value=(None, "fixture GitHub unavailable\n")), \
                mock.patch("crew.git_view.fetch") as fetch:
            code, result = self.request("/api/git?project=demo")
            fetch.assert_not_called()
        self.assertEqual(code, 200, result)
        self.assertEqual(result["base"]["name"], "develop")
        self.assertEqual(len(result["commits"]), 1)
        self.assertEqual(result["commits"][0]["parents"], [])
        branch = next(item for item in result["branches"] if item["name"] == "develop")
        self.assertEqual((branch["ahead"], branch["behind"], branch["conflicts"]), (0, 0, []))
        self.assertEqual(next(session for session in branch["sessions"] if session["session_id"] == "s1")["name"], "one")
        self.assertEqual(branch["worktrees"][0]["dirty"], False)
        self.assertEqual(result["github_reason"], "fixture GitHub unavailable\n")

    def test_enable_reuses_admin_and_git_view_lists_project_merge_requests(self):
        code, config = self.request("/api/git/enable?project=demo", {})
        self.assertEqual(code, 200, config)
        sid = config["admin_session_id"]
        self.assertEqual(self.request("/api/git/enable?project=demo", {"base": "topic"})[1]["admin_session_id"], sid)
        self.assertEqual(len(self.processes), 1)
        merged = board.request_git_merge(self.conn, "demo", "s1", "topic", "Implementation and tests", title=None, now=NOW)
        board.resolve_git_request(self.conn, "demo", sid, merged["id"], "merged", "CI passed", pr_number=17, now=NOW)
        opened = board.request_git_merge(self.conn, "demo", "s2", "master", "Needs review", title=None, now=NOW)
        with mock.patch("crew.git_view.view", return_value={}):
            code, result = self.request("/api/git?project=demo")
        self.assertEqual(code, 200, result)
        requests = {request["id"]: request for request in result["requests"]}
        self.assertEqual(set(requests), {merged["id"], opened["id"]})
        self.assertEqual((requests[merged["id"]]["requester_name"], requests[merged["id"]]["pr_number"],
                          requests[merged["id"]]["note"]), ("one", 17, "CI passed"))
        self.assertEqual(requests[opened["id"]]["status"], "open")

    def git_settings_fixture(self):
        board.join(self.conn, 'chief', 'demo', 'git manager', self.project, self.project,
                   'develop', role='leader', group_name='git manager', now=NOW)
        code, snapshot = self.request('/api/git/settings?project=demo')
        self.assertEqual(code, 200, snapshot)
        return {'project': 'demo', 'revision': snapshot['revision'], 'admin_session_id': 'chief',
                'base': 'develop', 'stable': '', 'branch_rules': 'Use PRs, tests and project rules.'}

    def test_settings_binds_existing_leader_and_preserves_its_brain_without_starting_ai(self):
        body = self.git_settings_fixture()
        from crew import rules
        for runtime in ('codex', 'claude'):
            with self.subTest(runtime=runtime):
                runtimes.set(self.conn, 'chief', runtime)
                models.set(self.conn, 'chief', 'native-fixture-model', 'high', now=NOW)
                code, saved = self.request('/api/git/settings?project=demo', body)
                self.assertEqual(code, 200, saved)
                self.assertEqual(saved['config']['admin_session_id'], 'chief')
                self.assertEqual(saved['config']['branch_rules'], body['branch_rules'])
                self.assertEqual(rules.skill_role(self.conn, board.session(self.conn, 'chief')), 'git')
                self.assertEqual(runtimes.get(self.conn, 'chief')['runtime'], runtime)
                self.assertEqual(models.get(self.conn, 'chief')['model'], 'native-fixture-model')
                body['revision'] = saved['revision']
        self.assertTrue(next(row for row in self.request('/api/state?project=demo')[1]['leaders'] if row['session_id'] == 'chief')['is_git_admin'])
        self.assertEqual(len(self.processes), 0)
        self.assertEqual(len([s for s in board.sessions(self.conn, 'demo') if s['role'] == 'leader']), 2)

    def test_settings_changes_branches_rules_and_admin_without_restarting_sessions(self):
        body = self.git_settings_fixture()
        first = self.request('/api/git/settings?project=demo', body)[1]
        board.join(self.conn, 'next-chief', 'demo', 'new manager', self.project, self.project,
                   'topic', role='leader', group_name='new manager', now=NOW)
        code, updated = self.request('/api/git/settings?project=demo',
                                    {**body, 'revision': first['revision'], 'base': 'topic', 'stable': 'master',
                                     'admin_session_id': 'next-chief', 'branch_rules': 'New policy'})
        self.assertEqual(code, 200, updated)
        self.assertEqual((updated['config']['base'], updated['config']['stable']), ('topic', 'master'))
        self.assertEqual(updated['config']['enabled_at'], first['config']['enabled_at'])
        self.assertEqual(updated['config']['admin_session_id'], 'next-chief')
        self.assertNotEqual(board.session(self.conn, 'chief')['status'], 'closed')
        self.assertEqual(len(self.processes), 0)

    def test_settings_rejects_invalid_identity_branch_and_cross_project_writes(self):
        body = self.git_settings_fixture()
        other = self.root / 'other-project'; other.mkdir()
        board.init_project(self.conn, 'other', other, now=NOW)
        board.join(self.conn, 'outsider', 'other', 'other chief', other, other,
                   'main', role='leader', group_name='other chief', now=NOW)
        for changes in ({'admin_session_id': 's1'}, {'admin_session_id': 'outsider'},
                        {'admin_session_id': ''}, {'base': 'missing'}, {'stable': 'develop'},
                        {'branch_rules': 42}, {'project': 'other'}):
            with self.subTest(changes=changes):
                code, result = self.request('/api/git/settings?project=demo', {**body, **changes})
                self.assertEqual(code, 400, result)
                self.assertIsNone(board.git_config(self.conn, 'demo'))
        self.assertEqual(self.request('/api/git/settings?project=demo', body, header=False)[0], 403)
        from crew import session_ops
        session_ops.archive(self.conn, 'chief', now=NOW)
        self.assertEqual(self.request('/api/git/settings?project=demo', body)[0], 400)
        self.assertEqual(len(self.processes), 0)

    def test_settings_concurrent_ai_policy_edit_is_not_overwritten(self):
        body = self.git_settings_fixture()
        saved = self.request('/api/git/settings?project=demo', body)[1]
        board.set_git_policy(self.conn, 'demo', 'chief', 'A newer AI policy', now=NOW)
        code, error = self.request('/api/git/settings?project=demo', {**body, 'revision': saved['revision']})
        self.assertEqual(code, 409, error)
        self.assertIn('重新读取', error['error'])
        self.assertEqual(board.git_policy(self.conn, 'demo'), 'A newer AI policy')

    def test_settings_disable_keeps_leader_group_and_merge_requests(self):
        body = self.git_settings_fixture()
        saved = self.request('/api/git/settings?project=demo', body)[1]
        request = board.request_git_merge(self.conn, 'demo', 's1', 'topic', 'Ready for merge', title=None, now=NOW)
        before = board.session(self.conn, 'chief')
        code, result = self.request('/api/git/settings?project=demo',
                                    {'project': 'demo', 'revision': saved['revision'], 'enabled': False})
        self.assertEqual(code, 200, result)
        self.assertIsNone(board.git_config(self.conn, 'demo'))
        self.assertEqual(board.session(self.conn, 'chief'), before)
        self.assertEqual(board.git_requests(self.conn, 'demo')[0]['id'], request['id'])
        self.assertIsNone(next(g for g in board.groups(self.conn, 'demo') if g['name'] == 'git manager')['closed_at'])
        self.assertEqual(len(self.processes), 0)

    def test_settings_is_independent_of_graph_github_and_keeps_config_on_repository_error(self):
        body = self.git_settings_fixture()
        self.request('/api/git/settings?project=demo', body)
        with mock.patch('crew.git_view.view', side_effect=AssertionError('No graph scan')), \
                mock.patch('crew.git_view._github', side_effect=AssertionError('No network')), \
                mock.patch('crew.git_view.branch_choices', side_effect=subprocess.TimeoutExpired(['fixture-git'], 10)):
            code, result = self.request('/api/git/settings?project=demo')
        self.assertEqual(code, 200, result)
        self.assertFalse(result['available'])
        self.assertIn('timed out', result['reason'])
        self.assertEqual(result['config']['admin_session_id'], 'chief')
        self.assertEqual({row['session_id'] for row in result['leaders']}, {'chief', 's3'})
        self.assertEqual(len(self.processes), 0)

    def test_fetch_calls_only_explicit_post_and_returns_raw_error(self):
        with mock.patch("crew.git_view.fetch") as fetch:
            self.assertEqual(self.request("/api/git/fetch?project=demo", {})[0], 400)
            fetch.assert_not_called()
        self.request("/api/git/enable?project=demo", {})
        for result in ({"ok": True, "output": "updated\n", "error": None, "last_fetch_at": NOW},
                       {"ok": False, "output": "", "error": "fatal: fixture remote rejected\n", "last_fetch_at": None}):
            with self.subTest(result=result), mock.patch("crew.git_view.fetch", return_value=result) as fetch:
                self.assertEqual(self.request("/api/git/fetch?project=demo", {}), (200, result))
                fetch.assert_called_once_with(board.project(self.conn, "demo")["root"])

    def test_commit_detail_returns_real_stats_and_invalid_sha_is_400(self):
        self.assertEqual(self.request("/api/git/commit?project=demo&sha=0000")[0], 400)
        self.assertEqual(self.request("/api/git/enable?project=demo", {})[0], 200)
        sha = self.git("rev-parse", "HEAD")
        code, result = self.request("/api/git/commit?project=demo&sha=" + sha)
        self.assertEqual(code, 200, result)
        self.assertEqual(result["sha"], sha)
        self.assertEqual(result["file_count"], 2)
        self.assertEqual(result["additions"], 2)
        self.assertEqual(result["deletions"], 0)
        for invalid in ("0" * 40, "--help", ""):
            code, result = self.request("/api/git/commit?project=demo&sha=" + invalid)
            self.assertEqual(code, 400, result)
            self.assertTrue(result["error"])
        self.assertEqual(self.request("/api/git/commit?project=unknown&sha=" + sha)[0], 400)

    def test_commit_detail_selects_only_requested_project(self):
        other = self.root / "other-project"
        other.mkdir()
        board.init_project(self.conn, "other", other, now=NOW)
        self.request("/api/git/enable?project=demo", {})
        board.enable_git(self.conn, "other", "develop", None, now=NOW)
        payload = {"sha": "a" * 40, "files": [], "file_count": 0, "additions": 0, "deletions": 0}
        with mock.patch("crew.git_view.commit_detail", return_value=payload) as detail:
            self.assertEqual(self.request("/api/git/commit?project=other&sha=" + payload["sha"]), (200, payload))
            detail.assert_called_once_with(board.project(self.conn, "other")["root"], payload["sha"])

    def test_invalid_project_branch_and_offset_and_csrf(self):
        self.assertEqual(self.request("/api/git?project=unknown")[0], 400)
        self.assertEqual(self.request("/api/git?project=demo&offset=-1")[0], 400)
        self.assertEqual(self.request("/api/git?project=demo&offset=invalid")[0], 400)
        self.assertEqual(self.request("/api/git/enable?project=demo", {"base": "missing"})[0], 400)
        self.assertEqual(self.request("/api/git/enable?project=demo", {"stable": 42})[0], 400)
        self.assertEqual(self.request("/api/git/enable?project=demo", {}, header=False)[0], 403)
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
        request = board.request_git_merge(self.conn, "demo", "s1", "topic", "tests passed", title=None, now=NOW)
        board.resolve_git_request(self.conn, "demo", "s3", request["id"], "merged", "CI passed", now=NOW)
        return board.handoffs(self.conn, "s1")[0]

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
            self.assertEqual(self.processes[0].command[-2:], ["--resume", "s1"])
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
