"""Room checkout lifecycle in real temporary Git repositories."""
import tests
from pathlib import Path
import subprocess
from unittest import mock

from crew import board, branch_claims, chat, codex_runtime, goals, runtimes, store, war_room, war_room_plan
from crew import war_room_worktree as wt
from test_chat import FakeProcess
from test_codex_runtime import FakeClient
from test_message_delivery import DeliveryFixture, NOW, PLUGIN
import test_native_approvals
from test_war_room_plan import approve_assignments


class RoomCheckoutTests(DeliveryFixture):
    def setUp(self):
        super().setUp()
        self.repo = self.root
        self.manager = chat.Manager(self.home, PLUGIN, lambda: [], FakeProcess)
        self.addCleanup(self.manager.close)
        self.manager.goal_controller.live_native = set()
        board.join(self.conn, 'c', 'demo', 'gamma', self.root, None, None, role='leader', now=NOW)

    def git(self, *args, cwd=None):
        return subprocess.run(['git', '-C', str(cwd or self.repo), *map(str, args)], check=True,
                              capture_output=True, text=True, encoding='utf-8').stdout.strip()

    def init(self):
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('commit', '--allow-empty', '-m', 'base')

    def open(self, names=('beta',), **kwargs):
        room = war_room.start(self.conn, 'demo', 'a', '测试', '校验目录', **kwargs)
        self.room = room['id']
        return approve_assignments(self.conn, room, names)

    def row(self, sid='b'):
        return dict(self.conn.execute('SELECT * FROM war_room_worktrees WHERE room_id=? AND session_id=?',
                                      (self.room, sid)).fetchone())

    def tick(self):
        wt.sweep(self.manager, self.conn, NOW)

    def test_root_check_ignore_and_host_restart(self):
        self.init()
        room = self.open()
        row = self.row()
        self.assertEqual((row['state'], row['branch']), ('pending', 'crew/room-1-beta-b'))
        self.assertEqual(Path(row['path']), self.root / '.crew/worktrees/room-1-beta-b')
        self.assertIn(wt.NO_ADMIN, self.notices('a')[-1]['text'])
        self.assertEqual(room['isolation'], 'worktree')
        self.git('worktree', 'add', '-b', row['branch'], row['path'])
        self.tick()
        self.assertEqual(self.row()['state'], 'active')
        self.conn.execute("UPDATE war_room_worktrees SET state='pending'")
        self.tick()
        self.check_exclude('/.crew/worktrees/')

    def check_exclude(self, rule):
        target = Path(self.row()['path'])/'proof.txt'
        target.write_text('test', encoding='utf-8')
        self.git('check-ignore', '--', str(target.relative_to(self.repo)))
        self.assertEqual(self.git('status', '--porcelain'), '')
        exclude = self.repo / self.git('rev-parse', '--git-path', 'info/exclude')
        self.assertEqual(exclude.read_text(encoding='utf-8').splitlines().count(rule), 1)

    def test_subdirectory_exclude(self):
        self.init()
        self.root = self.repo / 'src'
        self.root.mkdir()
        self.conn.execute('UPDATE projects SET root=?', (str(self.root),))
        self.open()
        self.tick()
        self.assertEqual(self.row()['state'], 'active', self.row()['error'])
        self.check_exclude('/src/.crew/worktrees/')

    def test_gate_path_and_foreign_directory(self):
        self.init()
        room = self.open()
        goal = room['board'][0]['goal_id']
        execution = {'goal_id': goal, 'session_id': 'b'}
        self.assertIsNone(wt.path_for(self.conn, {**execution, 'goal_id': 'unrelated'}))
        control = self.manager.goal_controller
        self.assertEqual(wt.path_for(self.conn, execution), {'pending': True})
        for action in (lambda: control.ensure(self.conn, goal, 'b'),
                       lambda: control.send(goal, 'b', '开工'),
                       lambda: goals.create_execution(self.conn, goal, 'b', runtime='codex')):
            with self.assertRaisesRegex(board.Conflict, wt.WAIT):
                action()
        self.assertEqual(goals.get(self.conn, goal)['status'], 'queued')
        path = Path(self.row()['path'])
        path.mkdir(parents=True)
        self.tick()
        self.assertEqual(self.row()['state'], 'failed')
        self.assertIn('占用', self.row()['error'])
        self.assertTrue(path.is_dir())
        self.assertEqual(wt.path_for(self.conn, execution), {'pending': True})

    def test_one_member_two_assignments_and_authorized_details(self):
        self.init()
        room = self.open(('beta', 'beta', 'gamma'))
        self.tick()
        self.assertEqual(len(room['worktrees']), 2)
        self.assertEqual([r['member'] for r in war_room.get(self.conn, self.room, 'b')['worktrees']], ['b'])
        self.assertEqual(war_room.get(self.conn, self.room, 'm')['worktrees'], [])
        self.assertNotIn('worktrees', war_room.change_value(self.conn, 'demo', self.room))
        row = self.row()
        ids = [i['goal_id'] for i in room['board'] if i['owner_session_id'] == 'b']
        executions = [self.manager.goal_controller.ensure(self.conn, goal, 'b') for goal in ids]
        for execution in [*executions, executions[0]]:
            self.assertEqual(wt.path_for(self.conn, execution), {'path': row['path']})
            for command in ('git commit -m test', 'git branch '+row['branch']):
                self.assertIsNone(branch_claims.task_branch_reason(self.home, row['path'], command, execution['id']))
            self.assertIsNotNone(branch_claims.task_branch_reason(self.home, row['path'], 'git branch other', execution['id']))
            branch_claims.record(self.home, 'b', row['path'], 'git commit -m test', execution['id'])
            self.git('commit', '--allow-empty', '-m', 'assignment', cwd=row['path'])
        self.assertIsNone(self.conn.execute('SELECT goal_id FROM branch_claims').fetchone()[0])

    def test_non_git_and_pre_upgrade(self):
        room = self.open()
        self.assertEqual((room['worktrees'], room['isolation_reason']), ([], 'not_git'))
        self.assertIn(wt.NOT_GIT, self.notices('a')[-1]['text'])
        war_room.close(self.conn, self.room, 'user', reason='user_stop')
        ordinary = war_room.start(self.conn, 'demo', 'user', '普通', '开房', lead='a')
        self.assertIn(wt.NOT_GIT, self.notices('a')[-1]['text'])
        war_room.close(self.conn, ordinary['id'], 'user', reason='cancelled')
        self.init()
        room = self.open()
        self.conn.execute('DELETE FROM war_room_worktrees')
        self.conn.execute("DELETE FROM war_room_events WHERE kind='worktree_repository'")
        self.tick()
        self.assertEqual(war_room.get(self.conn, self.room)['isolation_reason'], 'pre_upgrade')
        self.assertFalse(war_room.get(self.conn, self.room)['worktrees'])

    def test_claude_launch_receives_checkout(self):
        self.init()
        room = self.open()
        self.tick()
        control = self.manager.goal_controller
        goal = room['board'][0]['goal_id']
        runtimes.set(self.conn, 'b', 'claude')
        with mock.patch('crew.chat.claude_executable', return_value='fixture-claude'):
            control.send(goal, 'b', 'do work')
        execution = control.current(self.conn, goal, 'b')
        self.assertEqual(Path(self.manager.processes[execution['id']].process.options['cwd']), Path(self.row()['path']))
        self.assertEqual(Path(board.session(self.conn, 'b')['cwd']), self.root)

    def test_codex_native_approval_in_checkout(self):
        self.init()
        room = self.open()
        self.tick()
        goal, path = room['board'][0]['goal_id'], self.row()['path']
        runtimes.set(self.conn, 'b', 'codex')
        with mock.patch.object(codex_runtime, '_ObservedClient', side_effect=FakeClient):
            self.manager.goal_controller.send(goal, 'b', 'do work')
        execution = self.manager.goal_controller.current(self.conn, goal, 'b')
        running = self.manager.processes[execution['id']]
        client = running.client
        self.assertEqual(Path(dict(client.calls)['thread/start']['cwd']), Path(path))
        result, done = test_native_approvals.NativeApprovalTests.pending(self, running, client, command='git status', cwd=path)
        running.resolve_approval(running.pending_approvals()[0]['id'], True)
        self.assertTrue(done.wait(3))
        self.assertEqual(result, {'decision': 'accept'})

    def active(self, names=('beta',)):
        self.init()
        room = self.open(names)
        self.tick()
        self.assertEqual(self.row()['state'], 'active', self.row()['error'])
        return room, Path(self.row()['path'])

    def request(self):
        return self.conn.execute("INSERT INTO git_merge_requests(project,session_id,branch,description,created_at,updated_at) "
                                 "VALUES('demo','b',?,'请合并',?,?)", (self.row()['branch'], NOW, NOW)).lastrowid

    def stop(self):
        war_room.close(self.conn, self.room, 'user', reason='user_stop', now=NOW)

    def test_managed_base_and_current_integration_ancestry(self):
        self.init()
        self.git('switch', '-c', 'integration')
        self.git('commit', '--allow-empty', '-m', 'integration base')
        base = self.git('rev-parse', 'HEAD')
        self.git('switch', 'main')
        board.enable_git(self.conn, 'demo', 'main', 'integration', admin_session_id='a')
        self.open()
        self.tick()
        row = self.row()
        self.assertEqual((row['base_ref'], row['base_commit']), ('integration', base))
        self.git('commit', '--allow-empty', '-m', 'task', cwd=row['path'])
        self.git('switch', 'integration')
        self.git('merge', '--ff-only', row['branch'])
        self.git('switch', 'main')
        self.stop()
        self.tick()
        row = self.row()
        self.assertEqual((row['state'], row['outcome']), ('removed', 'merged'), row['error'])
        self.assertFalse(Path(row['path']).exists())
        self.assertEqual(self.git('branch', '--list', row['branch']), '')

    def test_abandon_snapshot_and_restore_command_after_room_closed(self):
        room, path = self.active()
        (path/'important.txt').write_text('untracked work', encoding='utf-8')
        (path/'.gitignore').write_text('ignored.txt\n', encoding='utf-8')
        (path/'ignored.txt').write_text('local only', encoding='utf-8')
        war_room.close(self.conn, self.room, 'a', '计划停止', reason='abandoned', now=NOW)
        before = self.conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0]
        self.tick()
        self.tick()
        row = self.row()
        self.assertEqual((row['state'], row['outcome']), ('removed', 'backed_up'), row['error'])
        self.assertFalse(path.exists())
        posts = war_room.get(self.conn, self.room)['messages']
        [post] = [p for p in posts if '恢复命令：' in p['body']]
        self.assertEqual((post['speaker_session_id'], post['mentions']), ('a', []))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0], before)
        command = post['body'].splitlines()[-1]
        self.assertRegex(command, r'^git branch \S+ refs/crew-backup/room-1/beta$')
        self.git(*command.split()[1:])
        self.assertEqual(self.git('show', row['branch']+':important.txt'), 'untracked work')
        self.assertNotIn('ignored.txt', self.git('ls-tree', '--name-only', row['branch']).splitlines())
        for actor in ('a', board.HUMAN):
            with self.assertRaisesRegex(board.BoardError, '只读'):
                war_room.say(self.conn, room['id'], actor, '普通发言')

    def test_request_then_merge_keeps_later_untracked_files_through_gc(self):
        room, path = self.active()
        self.git('commit', '--allow-empty', '-m', 'submitted', cwd=path)
        branch = self.row()['branch']
        request = self.request()
        (path/'later.txt').write_text('after submission', encoding='utf-8')
        self.stop()
        self.tick()
        row = self.row()
        self.assertEqual(row['state'], 'awaiting_merge', row['error'])
        self.assertFalse(path.exists())
        self.git('gc', '--prune=now')
        self.assertEqual(self.git('show', row['snapshot']+':later.txt'), 'after submission')
        self.git('merge', '--ff-only', branch)
        self.conn.execute("UPDATE git_merge_requests SET status='merged' WHERE id=?", (request,))
        self.tick()
        row = self.row()
        self.assertEqual(row['outcome'], 'backed_up', row['error'])
        self.assertEqual(self.git('show', row['backup_ref']+':later.txt'), 'after submission')

    def test_request_merged_then_delete_branch(self):
        _, path = self.active()
        self.git('commit', '--allow-empty', '-m', 'submitted', cwd=path)
        request = self.request()
        self.stop()
        self.tick()
        self.assertEqual(self.row()['state'], 'awaiting_merge')
        self.git('merge', '--ff-only', self.row()['branch'])
        self.conn.execute("UPDATE git_merge_requests SET status='merged' WHERE id=?", (request,))
        self.tick()
        self.assertEqual((self.row()['state'], self.row()['outcome']), ('removed', 'merged'))

    def test_rejected_request_then_item_return_rebuilds_from_backup(self):
        room, path = self.active()
        item = room['board'][0]
        war_room.set_item(self.conn, self.room, 'b', status='done', item_id=item['id'], note='交付')
        (path/'restore.txt').write_text('my changes', encoding='utf-8')
        request = self.request()
        goals.request_cancel(self.conn, item['goal_id'])
        goals.finalize_cancel(self.conn, item['goal_id'])
        self.assertEqual(self.row()['state'], 'releasing')
        self.tick()
        self.assertEqual(self.row()['state'], 'awaiting_merge', self.row()['error'])
        self.conn.execute("UPDATE git_merge_requests SET status='rejected' WHERE id=?", (request,))
        self.tick()
        backup = self.row()['backup_ref']
        self.assertEqual(self.row()['outcome'], 'backed_up')
        war_room.set_item(self.conn, self.room, 'a', item_id=item['id'], status='todo')
        self.tick()
        self.assertEqual(self.row()['state'], 'active', self.row()['error'])
        self.assertEqual((path/'restore.txt').read_text(encoding='utf-8'), 'my changes')
        self.assertEqual(self.git('rev-parse', 'HEAD', cwd=path), self.git('rev-parse', backup))

    def test_worktree_removal_replay_and_no_duplicate_notice(self):
        _, path = self.active()
        (path/'work').write_text('survives crash', encoding='utf-8')
        self.stop()
        with mock.patch.object(war_room, 'cleanup_notice', side_effect=OSError('公告写入中断')):
            self.tick()
        self.assertFalse(path.exists())
        self.assertEqual((self.row()['state'], self.row()['error']), ('releasing', '公告写入中断'))
        self.tick()
        self.assertEqual(self.row()['outcome'], 'backed_up', self.row()['error'])
        self.assertEqual(self.git('show', self.row()['backup_ref']+':work'), 'survives crash')
        self.tick()
        self.assertEqual(sum('恢复命令' in p['body'] for p in war_room.get(self.conn, self.room)['messages']), 1)

    def test_mismatch_or_external_claude_blocks_removal(self):
        room, path = self.active()
        execution = self.manager.goal_controller.ensure(self.conn, room['board'][0]['goal_id'], 'b')
        goals.bind_native(self.conn, execution['id'], 'claude', 'external')
        self.stop()
        for live in (None, {'external'}):
            self.manager.goal_controller.live_native = live
            self.tick()
            self.assertEqual(self.row()['state'], 'releasing')
            self.assertTrue(path.exists())
        self.manager.goal_controller.live_native = set()
        self.git('switch', '-c', 'someone-else', cwd=path)
        self.tick()
        self.assertEqual(self.row()['state'], 'releasing')
        self.assertIn('占用', self.row()['error'])
        self.assertTrue(path.exists())

    def test_transfer_and_multiple_goals_release_only_last(self):
        room, path = self.active(('beta', 'beta'))
        items = [i for i in room['board'] if i['goal_id']]
        old = self.manager.goal_controller.ensure(self.conn, items[0]['goal_id'], 'b')
        body = room['plans'][0]['body'].replace('beta', 'gamma', 1)
        war_room_plan.update(self.conn, self.room, 'a', 1, body, '换人', None)
        war_room_plan.approve(self.conn, self.room, 'user', 2, {}, None)
        self.assertEqual((self.row()['state'], self.row('c')['state']), ('active', 'pending'))
        self.assertIsNotNone(branch_claims.task_branch_reason(self.home, str(path), 'git commit -m old', old['id']))
        goals.finalize_transfer(self.conn, items[0]['goal_id'])
        self.tick()
        target = self.manager.goal_controller.ensure(self.conn, items[0]['goal_id'], 'c')
        self.assertEqual(wt.path_for(self.conn, target), {'path': self.row('c')['path']})
        for item in items[1:]:
            goals.request_cancel(self.conn, item['goal_id'])
            goals.finalize_cancel(self.conn, item['goal_id'])
        self.assertEqual(self.row()['state'], 'releasing')
        war_room.change_members(self.conn, self.room, 'a', remove=['gamma'])
        self.assertEqual(self.row('c')['state'], 'releasing')


    def test_reassign_last_task_and_previous_owner_stops(self):
        room, path = self.active()
        old = self.manager.goal_controller.ensure(self.conn, room['board'][0]['goal_id'], 'b')
        body = room['plans'][0]['body'].replace('beta', 'gamma')
        war_room_plan.update(self.conn, self.room, 'a', 1, body, '转交', None)
        war_room_plan.approve(self.conn, self.room, 'user', 2, {}, None)
        self.assertEqual(self.row()['state'], 'releasing')
        self.assertEqual(self.row('c')['state'], 'pending')
        self.assertIsNone(wt.path_for(self.conn, old))
        self.tick()
        self.assertFalse(path.exists())
        self.assertEqual(self.row('c')['state'], 'active')

    def test_goal_release_waits_for_all_goals(self):
        room, path = self.active(('beta', 'beta'))
        first, second = [i['goal_id'] for i in room['board'] if i['goal_id']]
        goals.request_cancel(self.conn, first)
        goals.finalize_cancel(self.conn, first)
        self.assertEqual(self.row()['state'], 'active')
        goals.request_cancel(self.conn, second)
        goals.finalize_cancel(self.conn, second)
        self.assertEqual(self.row()['state'], 'releasing')
        self.tick()
        self.assertFalse(path.exists())

    def test_backup_write_failure_preserves_tree_and_retries(self):
        _, path = self.active()
        (path/'original').write_text('keep', encoding='utf-8')
        # Git forbids making a child ref underneath an existing reference.
        self.git('update-ref', 'refs/crew-backup/room-1', 'HEAD')
        self.stop()
        self.tick()
        self.assertTrue(self.row()['error'])
        self.assertEqual((path/'original').read_text(encoding='utf-8'), 'keep')
        self.git('update-ref', '-d', 'refs/crew-backup/room-1')
        self.tick()
        self.assertEqual(self.row()['outcome'], 'backed_up')
        self.assertIsNone(self.row()['error'])

    def test_waiting_branch_moved_by_another_checkout_survives(self):
        _, path = self.active()
        self.git('commit', '--allow-empty', '-m', 'submitted', cwd=path)
        self.request()
        self.stop()
        self.tick()
        branch = self.row()['branch']
        foreign = self.base/'elsewhere'
        self.git('worktree', 'add', foreign, branch)
        self.git('commit', '--allow-empty', '-m', 'external', cwd=foreign)
        self.tick()
        self.assertEqual(self.row()['state'], 'awaiting_merge')
        self.assertIn('占用', self.row()['error'])
        self.git('worktree', 'remove', foreign)
        self.tick()
        self.assertIn('已改变', self.row()['error'])
        self.assertEqual(self.git('log', '-1', '--format=%s', branch), 'external')

    def test_linked_workspace_uses_shared_exclude_and_configured_base(self):
        self.init()
        linked = self.base/'linked'
        self.git('worktree', 'add', '-b', 'workspace', linked)
        self.git('commit', '--allow-empty', '-m', 'workspace-only', cwd=linked)
        self.conn.execute('UPDATE projects SET root=?', (str(linked),))
        board.enable_git(self.conn, 'demo', 'main', admin_session_id='a')
        self.open()
        self.tick()
        row = self.row()
        self.assertEqual((row['state'], row['base_ref'], row['base_commit']), ('active', 'main', self.git('rev-parse', 'main')))
        self.assertEqual(self.git('rev-parse', 'HEAD', cwd=row['path']), row['base_commit'])
        self.git('check-ignore', '--', '.crew/worktrees/probe', cwd=linked)
        self.assertEqual(self.git('status', '--porcelain', cwd=linked), '')
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_broken_repository_cannot_be_approved_as_plain_folder(self):
        room = war_room.start(self.conn, 'demo', 'a', '筹备', '验证')
        self.assertIn(wt.NOT_GIT, room['brief'])
        (self.root/'.git').write_text('gitdir: missing\n', encoding='utf-8')
        with self.assertRaisesRegex(board.BoardError, 'git|repository'):
            approve_assignments(self.conn, room, ['beta'])
        self.assertIsNone(war_room.get(self.conn, room['id'])['approved_version'])
        self.assertFalse(self.conn.execute('SELECT 1 FROM war_room_worktrees').fetchone())

    def test_dirty_checkout_is_rebuilt_from_still_existing_branch(self):
        room, path = self.active()
        item = room['board'][0]
        war_room.set_item(self.conn, self.room, 'b', item_id=item['id'], status='done', note='交付')
        branch = self.row()['branch']
        self.git('commit', '--allow-empty', '-m', 'submitted', cwd=path)
        original = self.git('rev-parse', branch)
        self.request()
        (path/'extra').write_text('pending review', encoding='utf-8')
        goals.request_cancel(self.conn, item['goal_id'])
        goals.finalize_cancel(self.conn, item['goal_id'])
        self.tick()
        self.assertEqual(self.row()['state'], 'awaiting_merge')
        war_room.set_item(self.conn, self.room, 'a', item_id=item['id'], status='doing')
        self.tick()
        self.assertEqual(self.row()['state'], 'active', self.row()['error'])
        self.assertEqual(self.git('rev-parse', 'HEAD', cwd=path), original)
        self.assertEqual((path/'extra').read_text(encoding='utf-8'), 'pending review')


    def test_repository_probe_and_worker_release_locks_before_real_git(self):
        from crew import git_process
        from crew.ops import executor
        from test_core import operation_service
        self.init()
        service = operation_service(self, self.root)
        def call(name, **args):
            return executor.human_call(service, 'war_room.'+name, {'project': 'demo', **args})

        real_run, calls = git_process.run, []
        def observe(args, *options, **kwargs):
            calls.append(args)
            self.assertFalse(service.write_lock.locked(), args)
            self.assertFalse(service.chat.lock._is_owned(), args)
            return real_run(args, *options, **kwargs)

        with (mock.patch.object(git_process, 'run', new=observe),
              mock.patch.object(service.chat, 'deliver_leader_notices'),
              mock.patch('crew.war_room_work.deliver')):
            room = call('open', title='隔离', goal='校验', lead='alpha')
            self.room = room['id']
            body = '## 分工\n- [A] beta | 测试\n## 等你定\n## 完成标准\n- [C] 验证通过'
            war_room_plan.update(self.conn, self.room, 'a', 0, body, '首稿', None)
            call('plan.approve', room=self.room, version=1, recommendations={}, budget_tokens=None)
            service.deliver_notices()
            self.stop()
            service.deliver_notices()
        self.assertEqual(self.row()['state'], 'removed', self.row()['error'])
        self.assertTrue(any('add' in args and 'worktree' in args for args in calls))
        self.assertTrue(any('remove' in args and 'worktree' in args for args in calls))

    def test_unrelated_goal_cannot_claim_a_room_checkout(self):
        _, path = self.active()
        other = goals.create(self.conn, 'demo', 'c', '其他项目内任务', '核对', ['完成'])
        execution = self.manager.goal_controller.ensure(self.conn, other['id'], 'c')
        self.assertIsNotNone(branch_claims.task_branch_reason(self.home, str(path), 'git commit -m other', execution['id']))
        self.assertEqual(branch_claims.record(self.home, 'c', str(path), 'git commit -m other', execution['id']), [])


    def test_embedded_repository_cannot_be_force_removed_as_a_gitlink(self):
        _, path = self.active()
        nested = path/'nested'
        nested.mkdir()
        self.git('init', '-b', 'main', cwd=nested)
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 'commit', '--allow-empty', '-m', 'nested', cwd=nested)
        (nested/'only-copy').write_text('untracked inside nested repo', encoding='utf-8')
        self.stop()
        self.tick()
        self.assertEqual(self.row()['state'], 'releasing')
        self.assertIn('嵌套仓库', self.row()['error'])
        self.assertEqual((nested/'only-copy').read_text(encoding='utf-8'), 'untracked inside nested repo')

    def test_snapshot_cannot_discard_an_index_only_version(self):
        _, path = self.active()
        data = path/'partial.txt'
        data.write_text('staged version', encoding='utf-8')
        self.git('add', '--', data.name, cwd=path)
        data.write_text('working version', encoding='utf-8')
        self.stop()
        self.tick()
        self.assertEqual(self.row()['state'], 'releasing')
        self.assertIn('暂存', self.row()['error'])
        self.assertEqual(self.git('show', ':partial.txt', cwd=path), 'staged version')
        self.assertEqual(data.read_text(encoding='utf-8'), 'working version')
        self.git('add', '--', data.name, cwd=path)
        self.tick()
        self.assertEqual(self.row()['outcome'], 'backed_up', self.row()['error'])

