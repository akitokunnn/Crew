"""Branch ownership uses an isolated board and a real temporary Git worktree."""
import tests  # Install the production-service connection guard.
import contextlib
import io
import json
import subprocess
from unittest import mock

from crew import board, cli, codex_tools, git_overview, git_view, runtimes, session_ops, store
from test_core import CoreCase, NOW


class GitOverviewTests(CoreCase):
    def setUp(self):
        super().setUp()
        hooks = self.base / "empty-hooks"
        hooks.mkdir()
        self.git("init")
        self.git("config", "core.hooksPath", str(hooks))
        self.git("config", "user.name", "Overview Fixture")
        self.git("config", "user.email", "overview@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("commit", "--allow-empty", "-m", "initial")
        self.git("switch", "-c", "topic")
        self.git("commit", "--allow-empty", "-m", "topic work")
        self.topic = self.git("rev-parse", "HEAD")
        self.git("switch", "main")
        self.worktree = self.base / "topic-worktree"
        self.git("worktree", "add", str(self.worktree), "topic")
        self.git("branch", "crew/git-fixture")
        board.join(self.conn, "git-admin", "demo", "Git 助手", self.root, self.root,
                   "crew/git-fixture", role="leader", group_name="git", now=NOW)
        board.enable_git(self.conn, "demo", "main", None, admin_session_id="git-admin", now=NOW)
        self.config = board.git_config(self.conn, "demo")
        self.project = board.project(self.conn, "demo")
        git_view._overview_cache.clear()
        self.addCleanup(git_view._overview_cache.clear)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              capture_output=True, text=True, encoding="utf-8", timeout=20).stdout.strip()

    def leader(self, sid, name, branch="main", worktree=None):
        return board.join(self.conn, sid, "demo", name, self.root, worktree, branch,
                          role="leader", now=NOW)

    def member(self, sid, name, leader_name, branch="main", worktree=None):
        return board.join(self.conn, sid, "demo", name, self.root, worktree, branch,
                          group_name=leader_name, now=NOW)

    def dispatch(self, run_id, crew_sid, claude_sid, started_at):
        self.conn.execute(
            "INSERT INTO codex_runs(run_id,crew_session_id,claude_session_id,task_file,workdir,started_at) "
            "VALUES(?,?,?,?,?,?)", (run_id, crew_sid, claude_sid, str(self.base / "task.md"),
                                    str(self.worktree) + "/.", started_at))

    def read(self, force=False):
        return git_overview.read(self.conn, self.project, self.config, force=force)

    def owners(self):
        branch = next(item for item in self.read()["branches"] if item["name"] == "topic")
        return branch["owners"]

    def test_member_registered_by_branch_belongs_to_group_leader(self):
        self.leader("chief", "Chief")
        self.member("worker", "Worker", "Chief", branch="topic")
        self.assertEqual(self.owners(), [{"session_id": "chief", "name": "Chief"}])

    def test_leader_registered_by_normalized_worktree_owns_branch(self):
        self.leader("chief", "Chief", branch="old-name", worktree=str(self.worktree) + "/.")
        self.assertEqual(self.owners(), [{"session_id": "chief", "name": "Chief"}])

    def test_dispatch_only_member_belongs_to_leader_for_both_identity_fields(self):
        self.leader("chief", "Chief")
        self.member("worker", "Worker", "Chief")
        for run_id, crew_sid, claude_sid in (("crew-run", "worker", None), ("legacy-run", None, "worker")):
            with self.subTest(run_id=run_id):
                self.conn.execute("DELETE FROM codex_runs")
                self.dispatch(run_id, crew_sid, claude_sid, NOW)
                self.assertEqual(self.owners(), [{"session_id": "chief", "name": "Chief"}])

    def test_dispatch_crew_identity_takes_precedence_over_legacy_identity(self):
        self.leader("crew-chief", "Crew chief")
        self.leader("legacy-chief", "Legacy chief")
        self.dispatch("both-identities", "crew-chief", "legacy-chief", NOW)
        self.assertEqual(self.owners(), [{"session_id": "crew-chief", "name": "Crew chief"}])

    def test_closed_sessions_are_not_owners_from_registration_or_dispatch(self):
        self.leader("closed-chief", "Closed chief", branch="topic")
        self.leader("open-chief", "Open chief")
        self.member("closed-worker", "Closed worker", "Open chief", branch="topic")
        for sid in ("closed-chief", "closed-worker"):
            board.set_status(self.conn, sid, "closed", now=NOW)
            self.dispatch(sid + "-run", sid, None, NOW)
        self.assertEqual(self.owners(), [])

    def test_git_assistant_and_its_group_are_not_owners(self):
        self.conn.execute("UPDATE sessions SET worktree=? WHERE session_id='git-admin'", (str(self.worktree),))
        self.member("git-worker", "Git worker", "git", branch="topic")
        self.dispatch("admin-run", "git-admin", None, NOW)
        self.assertEqual(self.owners(), [])

    def test_git_assistant_branch_is_neither_listed_nor_counted(self):
        data = self.read()
        self.assertEqual([item["name"] for item in data["branches"]], ["topic"])
        self.assertEqual(data["merged_hidden"], 0)
        self.git("update-ref", "refs/heads/crew/git-fixture", self.topic)
        changed = self.read(force=True)
        self.assertEqual([item["name"] for item in changed["branches"]], ["topic"])
        self.assertEqual(changed["merged_hidden"], 0)

    def test_direct_owners_precede_dispatches_with_latest_first_and_no_duplicates(self):
        self.leader("direct", "Direct", branch="topic")
        self.member("same-owner", "Same owner", "Direct", worktree=self.worktree)
        self.leader("older", "Older")
        self.leader("newer", "Newer")
        self.dispatch("older-run", "older", None, "2026-11-09T01:00:00Z")
        self.dispatch("newer-run", "newer", None, "2026-11-09T02:00:00Z")
        self.dispatch("duplicate-run", "same-owner", None, "2026-11-09T03:00:00Z")
        self.assertEqual(self.owners(), [
            {"session_id": "direct", "name": "Direct"},
            {"session_id": "newer", "name": "Newer"},
            {"session_id": "older", "name": "Older"}])

    def test_other_project_registration_and_dispatch_are_not_owners(self):
        other = self.base / "other-project"
        other.mkdir()
        board.init_project(self.conn, "other", other, now=NOW)
        board.join(self.conn, "outsider", "other", "Other chief", other, self.worktree, "topic",
                   role="leader", now=NOW)
        self.dispatch("outside-run", "outsider", None, NOW)
        self.assertEqual(self.owners(), [])

    def test_unclaimed_branch_has_empty_owners(self):
        self.assertEqual(self.owners(), [])

    def test_owner_changes_are_visible_while_git_scan_is_cached(self):
        self.assertEqual(self.owners(), [])
        self.leader("chief", "Chief", branch="topic")
        self.assertEqual(self.owners(), [{"session_id": "chief", "name": "Chief"}])
        self.leader("chief", "Renamed chief", branch="topic")
        self.assertEqual(self.owners(), [{"session_id": "chief", "name": "Renamed chief"}])

    def claim(self, sid, branch='topic', at=NOW, project='demo'):
        self.conn.execute('INSERT INTO branch_claims(project,branch,session_id,claimed_at) VALUES(?,?,?,?)',
                          (project, branch, sid, at))

    def test_member_claim_maps_to_leader_and_leader_claim_maps_to_self(self):
        self.leader('chief', 'Chief')
        self.member('worker', 'Worker', 'Chief')
        for sid in ('worker', 'chief'):
            with self.subTest(sid=sid):
                self.conn.execute('DELETE FROM branch_claims')
                self.claim(sid)
                self.assertEqual(self.owners(), [{'session_id': 'chief', 'name': 'Chief'}])

    def test_direct_then_newest_claims_then_dispatch_with_leader_deduplication(self):
        self.leader('direct', 'Direct', branch='topic')
        self.member('direct-worker', 'Direct worker', 'Direct')
        self.leader('older', 'Older claim')
        self.leader('newer', 'Newer claim')
        self.member('newer-worker', 'Newer worker', 'Newer claim')
        self.leader('dispatched', 'Dispatched')
        self.claim('older', at='2026-11-09T01:00:00Z')
        self.claim('newer', at='2026-11-09T02:00:00Z')
        self.claim('newer-worker', at='2026-11-09T03:00:00Z')
        self.claim('direct-worker', at='2026-11-09T04:00:00Z')
        self.dispatch('dispatch-run', 'dispatched', None, '2026-11-09T06:00:00Z')
        self.dispatch('duplicate-run', 'newer-worker', None, '2026-11-09T07:00:00Z')
        self.assertEqual(self.owners(), [
            {'session_id': 'direct', 'name': 'Direct'},
            {'session_id': 'newer', 'name': 'Newer claim'},
            {'session_id': 'older', 'name': 'Older claim'},
            {'session_id': 'dispatched', 'name': 'Dispatched'}])

    def test_other_project_closed_sessions_and_git_assistant_claims_are_ignored(self):
        other = self.base / 'other-project'
        other.mkdir()
        board.init_project(self.conn, 'other', other, now=NOW)
        board.join(self.conn, 'outsider', 'other', 'Other chief', other, None, 'main', role='leader', now=NOW)
        self.leader('closed-chief', 'Closed chief')
        self.leader('open-chief', 'Open chief')
        self.member('closed-worker', 'Closed worker', 'Open chief')
        self.member('git-worker', 'Git worker', 'git')
        for sid in ('closed-chief', 'closed-worker'):
            board.set_status(self.conn, sid, 'closed', now=NOW)
        for sid in ('closed-chief', 'closed-worker', 'git-admin', 'git-worker', 'outsider'):
            self.claim(sid)
        self.claim('outsider', project='other')
        self.claim('open-chief', project='other')
        self.assertEqual(self.owners(), [])

    def test_claims_for_unlisted_branches_do_not_change_branches_or_owners(self):
        self.leader('chief', 'Chief')
        before = self.read()
        for branch in ('main', 'crew/git-fixture', 'does-not-exist'):
            self.claim('chief', branch=branch)
        self.assertEqual(self.read(), before)
        self.assertEqual(self.owners(), [])

    def test_new_claims_and_updated_claim_times_are_visible_with_cached_git_scan(self):
        self.leader('first', 'First')
        self.leader('second', 'Second')
        self.assertEqual(self.owners(), [])
        self.claim('first', at='2026-11-09T01:00:00Z')
        self.claim('second', at='2026-11-09T02:00:00Z')
        self.assertEqual(self.owners(), [{'session_id': 'second', 'name': 'Second'},
                                         {'session_id': 'first', 'name': 'First'}])
        self.conn.execute('UPDATE branch_claims SET claimed_at=? WHERE session_id=?',
                          ('2026-11-09T03:00:00Z', 'first'))
        self.assertEqual(self.owners(), [{'session_id': 'first', 'name': 'First'},
                                         {'session_id': 'second', 'name': 'Second'}])

    def adopt(self, sid, branch='topic'):
        return git_overview.adopt(self.conn, self.project, self.config, branch, sid, now=NOW)

    def test_leader_adopts_branch_left_by_archived_leader(self):
        self.leader('gone', 'Gone', branch='topic')
        self.leader('heir', 'Heir')
        self.assertEqual(self.owners(), [{'session_id': 'gone', 'name': 'Gone'}])
        session_ops.archive(self.conn, 'gone', now=NOW)
        self.assertEqual(self.owners(), [])
        self.assertEqual(self.adopt('heir'), {'branch': 'topic', 'leader': {'session_id': 'heir', 'name': 'Heir'},
                                              'already': False})
        self.assertEqual(self.owners(), [{'session_id': 'heir', 'name': 'Heir'}])
        self.assertEqual(self.adopt('heir')['already'], True)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM branch_claims').fetchone()[0], 1)

    def test_branch_owned_by_another_current_leader_is_not_taken(self):
        self.leader('owner', 'Owner', branch='topic')
        self.leader('rival', 'Rival')
        with self.assertRaisesRegex(board.Conflict, 'Owner'):
            self.adopt('rival')
        self.assertEqual(self.owners(), [{'session_id': 'owner', 'name': 'Owner'}])

    def test_adoption_rechecks_owner_after_another_connection_claims_during_scan(self):
        self.leader('heir', 'Heir')
        self.leader('rival', 'Rival')
        read = git_overview.read

        def scanned(*args, **kwargs):
            result = read(*args, **kwargs)
            if kwargs.get('force'):
                # A second UI / carrier finishes its claim after our repository
                # scan, before this caller reserves the board write transaction.
                with contextlib.closing(store.connect(self.home)) as other:
                    other.execute('INSERT INTO branch_claims VALUES(?,?,?,?,?)',
                                  ('demo', 'topic', 'rival', None, NOW))
            return result

        with mock.patch.object(git_overview, 'read', side_effect=scanned):
            with self.assertRaisesRegex(board.Conflict, 'Rival'):
                self.adopt('heir')
        self.assertEqual(self.owners(), [{'session_id': 'rival', 'name': 'Rival'}])

    def test_only_current_leaders_of_the_project_adopt_listed_branches(self):
        self.leader('chief', 'Chief')
        self.member('worker', 'Worker', 'Chief')
        self.leader('archived', 'Archived')
        session_ops.archive(self.conn, 'archived', now=NOW)
        other = self.base / 'other-project'
        other.mkdir()
        board.init_project(self.conn, 'other', other, now=NOW)
        board.join(self.conn, 'outsider', 'other', 'Outsider', other, None, 'main', role='leader', now=NOW)
        for sid in ('worker', 'archived', 'outsider', 'git-admin'):
            with self.subTest(sid=sid), self.assertRaisesRegex(board.BoardError, '在岗的 leader'):
                self.adopt(sid)
        for branch in ('main', 'crew/git-fixture', 'missing'):
            with self.subTest(branch=branch), self.assertRaisesRegex(board.BoardError, '不在待处理列表'):
                self.adopt('chief', branch)
        with self.assertRaisesRegex(board.BoardError, '启用 Git'):
            git_overview.adopt(self.conn, self.project, None, 'topic', 'chief')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM branch_claims').fetchone()[0], 0)

    def test_cli_lists_unowned_branches_and_adopts_for_caller(self):
        self.leader('heir', 'Heir')

        def run(*argv, sid='heir'):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(['--home', str(self.home), '--session', sid, *argv], cwd=self.root)
            return code, out.getvalue(), err.getvalue()

        code, out, _ = run('git', 'branches')
        self.assertEqual(code, 0)
        self.assertIn('topic', out)
        self.assertIn('没人认领', out)
        listed = json.loads(run('git', 'branches', '--json')[1])
        self.assertEqual([(item['name'], item['owners']) for item in listed['branches']], [('topic', [])])
        self.assertEqual(run('git', 'adopt', 'topic')[:2], (0, '已认领 topic，归 Heir 管\n'))
        self.assertIn('Heir', run('git', 'branches')[1])
        self.member('worker', 'Worker', 'Heir')
        code, _, err = run('git', 'adopt', 'topic', sid='worker')
        self.assertEqual(code, 1)
        self.assertIn('在岗的 leader', err)

    def test_cli_and_codex_branch_lists_carry_integration_remote_pr_and_uncommitted_work(self):
        self.leader('heir', 'Heir')
        runtimes.set(self.conn, 'heir', 'codex')
        # A merged branch whose worktree still has source edits is listed and adoptable, like the MIB proactive-* ones.
        self.git('branch', 'drafts', 'main')
        drafts = self.base / 'drafts-worktree'
        self.git('worktree', 'add', str(drafts), 'drafts')
        (drafts / 'draft.cpp').write_text('work\n', encoding='utf-8')
        self.git('branch', 'trial', 'main')
        board.enable_git(self.conn, 'demo', 'main', 'trial', admin_session_id='git-admin', now=NOW)
        self.config = board.git_config(self.conn, 'demo')
        git_view._pull_requests[git_view._root(self.root)] = {'at': NOW, 'open': {'topic': {
            'number': 9, 'url': 'https://github.com/example/demo/pull/9', 'draft': False, 'base': 'main', 'ci': 'success'}}}
        self.addCleanup(git_view._pull_requests.clear)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(['--home', str(self.home), '--session', 'heir', 'git', 'branches'], cwd=self.root), 0)
        text = out.getvalue()
        self.assertIn('topic · 1 个提交未合入 main，其中 1 个未进 trial · 只在本地 · PR #9', text)
        self.assertIn('drafts · 已合入 main · 只在本地 · 1 个未提交改动', text)
        self.assertFalse([line for line in text.splitlines() if line.startswith('trial ')])
        result = codex_tools.CodexTools(self.home, 'heir')({'tool': 'crew_git', 'arguments': {'action': 'branches', 'data': {}}})
        listed = json.loads(result['contentItems'][0]['text'])
        self.assertEqual(listed['integration'], 'trial')
        items = {item['name']: item for item in listed['branches']}
        self.assertEqual(set(items), {'topic', 'drafts'})
        self.assertEqual((items['topic']['state'], items['topic']['outside_integration'], items['topic']['pr']['number']),
                         ('unmerged', 1, 9))
        self.assertEqual((items['drafts']['state'], items['drafts']['dirty']['files'], items['drafts']['remote']),
                         ('merged', 1, None))
        self.assertEqual(self.adopt('heir', 'drafts')['leader'], {'session_id': 'heir', 'name': 'Heir'})

    def test_codex_tool_lists_and_adopts_with_host_identity(self):
        self.leader('heir', 'Heir')
        self.member('worker', 'Worker', 'Heir')
        for sid in ('heir', 'worker'):
            runtimes.set(self.conn, sid, 'codex')

        def call(sid, action, data=None):
            result = codex_tools.CodexTools(self.home, sid)({'tool': 'crew_git', 'arguments': {
                'action': action, 'data': {} if data is None else data}})
            return result['success'], json.loads(result['contentItems'][0]['text'])

        ok, listed = call('worker', 'branches')
        self.assertTrue(ok)
        self.assertEqual([(item['name'], item['owners']) for item in listed['branches']], [('topic', [])])
        ok, error = call('worker', 'adopt', {'branch': 'topic'})
        self.assertFalse(ok)
        self.assertIn('在岗的 leader', error['error']['message'])
        ok, adopted = call('heir', 'adopt', {'branch': 'topic'})
        self.assertTrue(ok, adopted)
        self.assertEqual(adopted['adopted']['leader'], {'session_id': 'heir', 'name': 'Heir'})
        self.assertEqual(self.owners(), [{'session_id': 'heir', 'name': 'Heir'}])
        self.assertFalse(call('heir', 'adopt', {})[0])
