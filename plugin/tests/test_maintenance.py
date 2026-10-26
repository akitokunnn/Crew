"""Department compaction and workspace cleanup through HTTP with fake Claude/Codex natives only."""
import tests  # Install the production-service connection guard.
import json
import threading
import time
import unittest
from contextlib import closing
from unittest import mock
from urllib.parse import urlencode

from crew import board, channel, chat, codex_runtime, documents, goal_context, goals, maintenance, messaging, runtimes, store, todo, web
import test_chat_http as chat_helpers
from test_codex_runtime import FakeClient
from test_shell import NOW, PLUGIN, ShellFixture


def wait_until(predicate, label, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.02)
    raise AssertionError('Timed out waiting for ' + label)


class MaintenanceFixture(ShellFixture):
    close_server = chat_helpers.ChatHttpTests.close_server
    request = chat_helpers.ChatHttpTests.request
    finish_turn = chat_helpers.ChatHttpTests.finish_turn

    def setUp(self):
        super().setUp()
        self.agents = []
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project, agents_provider=lambda: list(self.agents),
                                        claude_root=self.claude, codex_root=self.codex, runs_file=self.runs,
                                        now=lambda: store.parse_iso(NOW), process_factory=self.process_factory,
                                        vscode_state_db=self.vscode_state_db, lock_status=lambda: False)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url, self.service = self.server.url, self.server.service
        self.chat = self.service.chat
        grace = mock.patch.object(maintenance, 'CONFIRM_GRACE', .3)
        grace.start()
        self.addCleanup(grace.stop)

    def ok(self, path, data=None):
        code, result, _ = self.request(path, data)
        self.assertEqual(code, 200, result)
        return result

    def department(self):
        leader = self.ok('/api/leader?project=demo', {'name': 'boss', 'task': 'lead the department'})
        boss, boss_process = leader['session_id'], self.processes[-1]
        self.finish_turn(boss, boss_process)
        member = self.ok('/api/spawn?project=demo', {'name': 'worker', 'task': 'do the work', 'group': 'boss'})
        worker, worker_process = member['session_id'], self.processes[-1]
        self.finish_turn(worker, worker_process)
        return boss, boss_process, worker, worker_process

    def goal(self, sid, status, title, runtime='claude'):
        value = goals.create(self.conn, 'demo', sid, title, 'Deliver ' + title, ['Done'])
        goals.transition(self.conn, value['id'], 'running')
        execution = goals.create_execution(self.conn, value['id'], sid, runtime=runtime)
        goals.bind_native(self.conn, execution['id'], runtime, 'native-' + execution['id'])
        execution_status = {'completed': 'completed', 'paused': 'paused', 'running': 'idle'}[status]
        self.conn.execute('UPDATE goals SET status=?,manual_pause=? WHERE id=?', (status, int(status == 'paused'), value['id']))
        self.conn.execute('UPDATE goal_rounds SET status=? WHERE goal_id=?', (status, value['id']))
        self.conn.execute('UPDATE goal_executions SET status=? WHERE id=?', (execution_status, execution['id']))
        return goals.get(self.conn, value['id']), goals.get_execution(self.conn, execution['id'])

    def preview(self, leader):
        return self.ok('/api/maintenance/department/preview?project=demo', {'project': 'demo', 'leader_session_id': leader})

    def contexts(self, preview):
        return {entry['target']: entry for person in preview['people'] for entry in person['contexts']}

    def start(self, leader, preview, targets=None):
        targets = [target for target, entry in self.contexts(preview).items() if entry['available']] if targets is None else targets
        return self.ok('/api/maintenance/department/compact?project=demo', {
            'project': 'demo', 'leader_session_id': leader, 'preview_id': preview['preview_id'], 'targets': targets})

    def job(self, job_id, kind='department_compact', leader=None):
        query = {'project': 'demo', 'kind': kind}
        if leader is not None:
            query['leader_session_id'] = leader
        result = self.ok('/api/maintenance/jobs?' + urlencode(query))
        return next(item for item in result['jobs'] if item['id'] == job_id)

    def item(self, job_id, target, leader=None, kind='department_compact'):
        return next(item for item in self.job(job_id, kind, leader)['items'] if item['target'] == target)

    def settled(self, job_id, leader=None, kind='department_compact'):
        return wait_until(lambda: (lambda value: value if value['status'] != 'running' else None)(self.job(job_id, kind, leader)),
                          'job ' + job_id, timeout=10)

    @staticmethod
    def compact_line(process):
        for line in process.stdin.getvalue().splitlines():
            value = json.loads(line)
            if value.get('type') == 'user' and value['message']['content'] == '/compact':
                return value
        return None

    def complete_claude(self, process, native):
        process.emit({'type': 'system', 'subtype': 'compact_boundary', 'session_id': native,
                      'compact_metadata': {'pre_tokens': 64000, 'post_tokens': 5100}})
        process.emit({'type': 'result', 'is_error': False, 'session_id': native})

    def new_process(self, known, resume=None):
        return wait_until(lambda: next((process for process in self.processes[known:] if self.compact_line(process)
                                        and (resume is None or process.command[process.command.index('--resume') + 1] == resume)), None),
                          'maintenance native with /compact')


class DepartmentCompactTests(MaintenanceFixture, unittest.TestCase):
    def test_offline_finished_and_paused_goal_contexts_compact_natively_without_resuming_goal_work(self):
        boss, boss_process, worker, worker_process = self.department()
        self.chat.stop_managed(boss)
        finished, finished_execution = self.goal(boss, 'completed', 'Finished release')
        paused, paused_execution = self.goal(boss, 'paused', 'Paused audit')
        never = goals.create(self.conn, 'demo', boss, 'Never started', 'Nothing yet', ['Later'])
        before = {key: dict(self.conn.execute('SELECT * FROM goals WHERE id=?', (key,)).fetchone()) for key in (finished['id'], paused['id'], never['id'])}
        executions = {key: dict(self.conn.execute('SELECT * FROM goal_executions WHERE id=?', (key,)).fetchone())
                      for key in (finished_execution['id'], paused_execution['id'])}
        preview = self.preview(boss)
        contexts = self.contexts(preview)
        self.assertEqual(set(contexts), {boss, worker, finished_execution['id'], paused_execution['id']},
                         'Unused goals have no native context and are never invented')
        self.assertTrue(all(entry['available'] for entry in contexts.values()), contexts)
        self.assertTrue(contexts[worker]['running'])
        known = len(self.processes)
        job = self.start(boss, preview)
        self.assertEqual(self.start(boss, preview)['id'], job['id'], 'A repeated click returns the same job')
        self.assertEqual(job['status'], 'running')

        # The member's idle process compacts in place and keeps running.
        wait_until(lambda: self.compact_line(worker_process), 'in-place /compact')
        self.complete_claude(worker_process, worker)
        # The leader's three histories run one at a time under the same identity.
        general = self.new_process(known)
        self.assertEqual(general.command[general.command.index('--resume') + 1], boss)
        self.assertEqual(sum(process.poll() is None for process in self.processes[known:]), 1)
        self.complete_claude(general, boss)
        first = self.new_process(known + 1)
        self.assertIsNotNone(general.poll(), 'The temporary general native stops before the next context of the same person')
        self.assertEqual(first.options['env']['CREW_EXECUTION_ID'], finished_execution['id'])
        self.assertEqual(first.command[first.command.index('--resume') + 1], 'native-' + finished_execution['id'])
        self.complete_claude(first, 'native-' + finished_execution['id'])
        second = self.new_process(known + 2)
        self.assertEqual(second.options['env']['CREW_EXECUTION_ID'], paused_execution['id'])
        # The service's own goal sync neither interrupts nor resumes the paused goal during compaction.
        with closing(store.connect(self.home)) as conn, self.chat.lock:
            self.chat.goal_controller.sync(conn)
        self.assertFalse(any('"interrupt"' in line for line in second.stdin.getvalue().splitlines()))
        self.complete_claude(second, 'native-' + paused_execution['id'])
        result = self.settled(job['id'], boss)

        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual({item['status'] for item in result['items']}, {'completed'})
        by_target = {item['target']: item for item in result['items']}
        self.assertEqual(by_target[worker]['detail']['process'], 'existing')
        self.assertEqual(by_target[boss]['detail']['process'], 'temporary')
        self.assertEqual((by_target[boss]['detail']['pre_tokens'], by_target[boss]['detail']['post_tokens']), (64000, 5100))
        self.assertIsNone(worker_process.poll(), 'An already running process is left running')
        self.assertTrue(all(process.poll() is not None for process in self.processes[known:]))
        for key in (boss, finished_execution['id'], paused_execution['id']):
            self.assertNotIn(key, self.chat.processes, 'Only processes started by maintenance are stopped and released')
        for key, row in before.items():
            self.assertEqual(dict(self.conn.execute('SELECT * FROM goals WHERE id=?', (key,)).fetchone()), row)
        for key, row in executions.items():
            self.assertEqual(dict(self.conn.execute('SELECT * FROM goal_executions WHERE id=?', (key,)).fetchone()), row)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_executions').fetchone()[0], 2, 'No new execution is created')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_reports').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_context_manifests').fetchone()[0], 0,
                         'Compaction never sends a goal-context prompt')
        journal = self.chat.journal(paused_execution['id'])
        self.assertEqual([event['type'] for event in journal.events if event['type'] in ('message', 'compact_request', 'compact')],
                         ['compact_request', 'compact'])

    def test_ordinary_messages_never_enter_a_temporary_maintenance_native(self):
        boss, boss_process, worker, worker_process = self.department()
        self.chat.stop_managed(boss)
        self.chat.stop_managed(worker)
        running, execution = self.goal(boss, 'running', 'Live goal')
        self.conn.execute("UPDATE goal_executions SET status='offline' WHERE id=?", (execution['id'],))
        goal_row = dict(self.conn.execute('SELECT * FROM goals WHERE id=?', (running['id'],)).fetchone())
        execution_row = dict(self.conn.execute('SELECT * FROM goal_executions WHERE id=?', (execution['id'],)).fetchone())
        known = len(self.processes)
        job = self.start(boss, self.preview(boss), [worker, execution['id']])
        member = self.new_process(known, worker)
        native = self.new_process(known, 'native-' + execution['id'])
        code, refused, _ = self.request('/api/chat/send?project=demo', {'session_id': worker, 'text': 'hello during maintenance'})
        self.assertEqual((code, refused['error']), (409, chat.MAINTENANCE_HELD))
        self.assertFalse(any(event.get('text') == 'hello during maintenance' for event in self.chat.journal(worker).events))
        self.complete_claude(member, worker)
        code, refused, _ = self.request('/api/chat/send?project=demo', {'session_id': boss, 'goal_id': running['id'], 'text': 'continue the goal'})
        self.assertEqual((code, refused['error']), (409, chat.MAINTENANCE_HELD))
        self.assertEqual(dict(self.conn.execute('SELECT * FROM goals WHERE id=?', (running['id'],)).fetchone()), goal_row)
        self.assertEqual(dict(self.conn.execute('SELECT * FROM goal_executions WHERE id=?', (execution['id'],)).fetchone()), execution_row)
        self.complete_claude(native, 'native-' + execution['id'])
        self.assertEqual(self.settled(job['id'], boss)['status'], 'completed')
        self.assertEqual(dict(self.conn.execute('SELECT * FROM goal_executions WHERE id=?', (execution['id'],)).fetchone()), execution_row)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_context_manifests').fetchone()[0], 0)

    def test_busy_external_and_unconfirmed_contexts_fail_with_real_reasons_while_others_continue(self):
        boss, boss_process, worker, worker_process = self.department()
        self.ok('/api/chat/send?project=demo', {'session_id': worker, 'text': 'keep working'})
        preview = self.preview(boss)
        contexts = self.contexts(preview)
        self.assertEqual((contexts[worker]['available'], contexts[worker]['kind']), (False, 'busy'))
        job = self.start(boss, preview, [boss, worker])
        wait_until(lambda: self.compact_line(boss_process), 'leader /compact')
        boss_process.emit({'type': 'result', 'is_error': False, 'session_id': boss})
        result = self.settled(job['id'], boss)
        items = {item['target']: item for item in result['items']}
        self.assertEqual((items[worker]['status'], items[worker]['error_type']), ('failed', 'busy'))
        self.assertIn('正在工作', items[worker]['error'])
        self.assertEqual((items[boss]['status'], items[boss]['error_type']), ('failed', 'unconfirmed'),
                         'A finished request without the native compact event is not success')
        self.assertEqual(result['status'], 'failed')
        self.assertIsNone(self.compact_line(worker_process))
        self.assertFalse(any('"interrupt"' in line for line in worker_process.stdin.getvalue().splitlines()))
        self.assertIsNone(worker_process.poll())
        self.assertIsNone(boss_process.poll(), 'The existing leader process is not stopped')

        self.finish_turn(worker, worker_process)
        self.chat.stop_managed(worker)
        self.agents[:] = [{'sessionId': worker, 'name': 'worker', 'status': 'idle'}]
        self.service.refresh_agents()
        retry = self.preview(boss)
        self.assertEqual(self.contexts(retry)[worker]['kind'], 'external')
        job = self.start(boss, retry, [worker, boss])
        wait_until(lambda: len([line for line in boss_process.stdin.getvalue().splitlines() if '/compact' in line]) == 2, 'second leader /compact')
        boss_process.emit({'type': 'result', 'is_error': True, 'errors': ['Not enough messages to compact'], 'session_id': boss})
        result = self.settled(job['id'], boss)
        items = {item['target']: item for item in result['items']}
        self.assertEqual(items[worker]['error_type'], 'external')
        self.assertEqual((items[boss]['error_type'], items[boss]['error']), ('native_error', 'Not enough messages to compact'))

    def test_working_identity_is_not_interrupted_for_another_context(self):
        boss, boss_process, worker, worker_process = self.department()
        _, execution = self.goal(boss, 'completed', 'Done earlier')
        preview = self.preview(boss)
        contexts = self.contexts(preview)
        self.assertTrue(contexts[execution['id']]['available'], 'An idle conversation need not block maintenance')
        self.ok('/api/chat/send?project=demo', {'session_id': boss, 'text': 'Continue the current work'})
        known = len(self.processes)
        job = self.start(boss, preview, [execution['id']])
        result = self.settled(job['id'], boss)
        items = {item['target']: item for item in result['items']}
        self.assertEqual(items[execution['id']]['error_type'], 'identity_in_use')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(self.processes), known, 'No maintenance native starts while business work is active')
        self.assertIsNone(boss_process.poll())
        self.assertEqual(self.chat.journal(boss).status, 'working')

    def test_codex_offline_member_uses_native_thread_compact_and_unknown_size_is_reported(self):
        boss, boss_process, worker, worker_process = self.department()
        self.chat.stop_managed(worker)
        runtimes.set(self.conn, worker, 'codex', 'thread-worker')
        clients = []

        def client(**kwargs):
            value = FakeClient(**kwargs)
            clients.append(value)
            return value

        with mock.patch('crew.codex_runtime._ObservedClient', side_effect=client):
            preview = self.preview(boss)
            self.assertTrue(self.contexts(preview)[worker]['available'])
            job = self.start(boss, preview, [worker])
            fake = wait_until(lambda: clients and ('thread/compact/start', {}) in clients[-1].calls and clients[-1], 'codex compact')
            self.assertIn(('thread/resume', 'thread-worker'), [(name, params.get('threadId')) for name, params in fake.calls])
            fake.emit('item/completed', turnId=fake.turn_id, item={'id': 'compact', 'type': 'contextCompaction'})
            fake.emit('thread/tokenUsage/updated', turnId=fake.turn_id, tokenUsage={'last': {'totalTokens': 3000, 'outputTokens': 0}})
            fake.emit('turn/completed', turn={'id': fake.turn_id, 'status': 'completed', 'items': []})
            result = self.settled(job['id'], boss)
        item = result['items'][0]
        self.assertEqual(item['status'], 'completed', item)
        self.assertEqual((item['detail']['pre_tokens'], item['detail']['post_tokens']), (None, 3000))
        self.assertNotIn('turn/start', [name for name, _ in fake.calls], 'No ordinary model message is sent')
        self.assertTrue(fake.closed)
        self.assertNotIn(worker, self.chat.processes)

    def test_unconfirmed_stop_of_a_temporary_native_is_a_visible_failure_and_keeps_it_closed_to_messages(self):
        boss, boss_process, worker, worker_process = self.department()
        self.chat.stop_managed(worker)
        known = len(self.processes)
        with mock.patch.object(self.chat, 'stop_managed', side_effect=board.BoardError('托管会话终止后仍未退出')):
            job = self.start(boss, self.preview(boss), [worker])
            member = self.new_process(known, worker)
            self.complete_claude(member, worker)
            result = self.settled(job['id'], boss)
        item = result['items'][0]
        self.assertEqual((item['status'], item['error_type']), ('failed', 'stop_failed'))
        self.assertTrue(item['error'].startswith('压缩已原生确认，但本次临时启动的原生进程未能确认停止'), item['error'])
        self.assertEqual(item['detail']['post_tokens'], 5100)
        self.assertTrue(self.chat.maintenance_held(worker), 'An unconfirmed stop keeps ordinary input out')
        code, refused, _ = self.request('/api/chat/send?project=demo', {'session_id': worker, 'text': 'after maintenance'})
        self.assertEqual((code, refused['error']), (409, chat.MAINTENANCE_HELD))
        self.ok('/api/session/stop?project=demo', {'session_id': worker})
        self.assertIsNotNone(member.poll(), 'The explicit person stop still ends it')

    def test_restart_marks_unfinished_items_interrupted_without_resending(self):
        boss, boss_process, worker, worker_process = self.department()
        preview = self.preview(boss)
        job = self.start(boss, preview, [boss])
        wait_until(lambda: self.compact_line(boss_process), 'leader /compact')
        sent, known = len(boss_process.stdin.getvalue().splitlines()), len(self.processes)
        # A new service on the same HOME, as after a restart, marks the unfinished job interrupted.
        restarted = web.Service(self.home, PLUGIN, agents_provider=lambda: [], claude_root=self.claude, codex_root=self.codex,
                                runs_file=self.runs, process_factory=self.process_factory,
                                vscode_state_db=self.vscode_state_db, lock_status=lambda: False)
        self.addCleanup(restarted.chat.close)
        self.assertEqual(len(self.processes), known, 'Restart starts no native')
        restarted = self.job(job['id'], leader=boss)
        self.assertEqual((restarted['status'], restarted['items'][0]['status']), ('interrupted', 'interrupted'))
        self.assertEqual(restarted['items'][0]['error'], maintenance.INTERRUPTED)
        self.assertEqual(len(boss_process.stdin.getvalue().splitlines()), sent, 'Nothing is re-sent on restart')
        self.complete_claude(boss_process, boss)

    def test_previews_reject_other_workspaces_and_unknown_or_expired_selections(self):
        other = self.root / 'other'
        other.mkdir()
        board.init_project(self.conn, 'other', other, now=NOW)
        board.join(self.conn, 'x1', 'other', 'outsider', other, other, 'main', role='leader', now=NOW)
        code, result, _ = self.request('/api/maintenance/department/preview?project=demo', {'project': 'demo', 'leader_session_id': 'x1'})
        self.assertEqual(code, 400, result)
        code, result, _ = self.request('/api/maintenance/department/preview?project=other', {'project': 'demo', 'leader_session_id': 's3'})
        self.assertEqual(code, 400, result)
        preview = self.preview('s3')
        code, result, _ = self.request('/api/maintenance/department/compact?project=demo', {
            'project': 'demo', 'leader_session_id': 's3', 'preview_id': preview['preview_id'], 'targets': ['x1']})
        self.assertEqual(code, 400, result)
        self.service.maintenance.previews[preview['preview_id']]['deadline'] = 0
        code, result, _ = self.request('/api/maintenance/department/compact?project=demo', {
            'project': 'demo', 'leader_session_id': 's3', 'preview_id': preview['preview_id'], 'targets': ['s3']})
        self.assertEqual(code, 409, result)
        code, result, _ = self.request('/api/maintenance/jobs?' + urlencode({'project': 'other', 'kind': 'department_compact',
                                                                              'leader_session_id': 's3'}))
        self.assertEqual(code, 400, result)


class WorkspaceCleanupTests(MaintenanceFixture, unittest.TestCase):
    def populate(self):
        boss, boss_process, worker, worker_process = self.department()
        channel.post(self.conn, 'demo', boss, 'old leader post', source='crew')
        channel.post(self.conn, 'demo', board.HUMAN, 'old user post', source='web')
        upto = self.conn.execute("SELECT MAX(id) FROM channel_messages WHERE project='demo'").fetchone()[0]
        self.conn.execute("INSERT INTO channel_digests(project,upto_message_id,body,message_count,trigger,model,created_at) "
                          "VALUES('demo',?,'## 已定结论\nold card',2,'manual','test',?)", (upto, NOW))
        board.handoff(self.conn, boss, '*', 'old broadcast')
        board.handoff(self.conn, worker, 'boss', 'old handoff to boss')
        running, _ = self.goal(boss, 'running', 'Unfinished feature')
        done, _ = self.goal(boss, 'completed', 'Shipped feature')
        rule = goal_context.put(self.conn, None, 'rule', 'project rule', {'type': 'human', 'id': 'r1'}, scope='project', project='demo', confirmed=True)
        leader_rule = goal_context.put(self.conn, None, 'rule', 'leader rule', {'type': 'human', 'id': 'r2'}, scope='leader',
                                       project='demo', leader_session_id=boss, confirmed=True)
        goal_fact = goal_context.put(self.conn, running['id'], 'fact', 'goal fact', {'type': 'human', 'id': 'r3'})
        crew_rule = goal_context.put(self.conn, None, 'rule', 'global rule', {'type': 'human', 'id': 'r4'}, scope='crew', confirmed=True)
        todo.add(self.conn, boss, 'Plug the device', 'Plug it in', 'Light is on')
        board.ask(self.conn, boss, 'Pick A or B?')
        board.claim(self.conn, worker, 'src/')
        board.add_resource(self.conn, 'demo', 'phone')
        board.take(self.conn, worker, 'phone', 'testing')
        messaging.queue(self.conn, worker, boss, 'old mail for the leader')
        board.queue_leader_notice(self.conn, boss, 'old queued notice', 'crew')
        board.join(self.conn, 'git-admin', 'demo', 'git管理员', self.project, self.project, 'main', role='leader', group_name='git', now=NOW)
        self.conn.execute("INSERT INTO git_management(project,enabled_at,base,stable,admin_session_id,branch_rules) "
                          "VALUES('demo',?,'main',NULL,'git-admin','keep main green')", (NOW,))
        (self.project / 'deliverable.md').write_text('# kept\n', encoding='utf-8')
        documents.register(self.conn, boss, 'deliverable.md', title='Deliverable', summary='kept', project='demo')
        other = self.root / 'other'
        other.mkdir()
        board.init_project(self.conn, 'other', other, now=NOW)
        board.join(self.conn, 'x1', 'other', 'boss', other, other, 'main', role='leader', now=NOW)
        channel.post(self.conn, 'other', 'x1', 'other workspace post', source='crew')
        board.handoff(self.conn, 'x1', '*', 'other workspace broadcast')
        return {'boss': boss, 'worker': worker, 'boss_process': boss_process, 'worker_process': worker_process,
                'running': running, 'done': done, 'rule': rule, 'leader_rule': leader_rule, 'goal_fact': goal_fact,
                'crew_rule': crew_rule}

    def cleanup_preview(self):
        return self.ok('/api/maintenance/cleanup/preview?project=demo', {'project': 'demo'})

    def cleanup(self, preview):
        return self.ok('/api/maintenance/cleanup?project=demo', {'project': 'demo', 'preview_id': preview['preview_id'], 'confirm': True})

    def test_cleanup_archives_old_work_and_a_new_same_name_leader_inherits_nothing(self):
        data = self.populate()
        preview = self.cleanup_preview()
        self.assertFalse(preview['empty'])
        self.assertEqual([item['name'] for item in preview['departments']], ['boss', 'git', 'lead'])
        self.assertTrue(preview['identities']['git_admin'])
        self.assertEqual((preview['channel']['messages'], preview['channel']['digests'], preview['handoffs']), (2, 1, 2))
        self.assertEqual((preview['todos'], preview['decisions'], preview['claims'], preview['leases'], preview['context']), (1, 1, 1, 1, 3))
        self.assertEqual([goal['title'] for goal in preview['goals']['unfinished']], ['Unfinished feature'])
        self.assertEqual((preview['kept']['documents'], preview['kept']['crew_rules']), (1, 1))
        self.assertEqual(preview['running']['processes'], 2)
        code, refused, _ = self.request('/api/maintenance/cleanup?project=demo', {'project': 'demo', 'preview_id': preview['preview_id']})
        self.assertEqual(code, 400, refused)
        job = self.cleanup(preview)
        self.assertEqual(self.cleanup(preview)['id'], job['id'], 'A repeated confirmation never runs twice')
        result = self.settled(job['id'], kind='workspace_cleanup')
        self.assertEqual(result['status'], 'completed', result)
        self.assertIsNotNone(data['boss_process'].poll())
        self.assertIsNotNone(data['worker_process'].poll())
        conn = self.conn
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM sessions WHERE project='demo' AND status<>'closed'").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM groups WHERE project='demo' AND closed_at IS NULL").fetchone()[0], 0)
        self.assertIsNone(board.git_config(conn, 'demo'), 'The administrator is closed through the Git lifecycle')
        self.assertTrue(board.is_archived(conn, 'git-admin'))
        self.assertFalse((self.project / '.git').exists(), 'No Git command or branch change is made')
        self.assertEqual(goals.get(conn, data['running']['id'])['status'], 'cancelled')
        self.assertEqual(goals.get(conn, data['done']['id'])['status'], 'completed', 'Finished goals and their history stay')
        self.assertEqual(conn.execute("SELECT status FROM todos").fetchone()[0], 'cancelled')
        self.assertEqual(conn.execute("SELECT status FROM decisions").fetchone()[0], 'withdrawn')
        self.assertIsNotNone(conn.execute('SELECT released_at FROM claims').fetchone()[0])
        self.assertIsNotNone(conn.execute('SELECT released_at FROM leases').fetchone()[0])
        for key in ('rule', 'leader_rule', 'goal_fact'):
            self.assertEqual(goal_context.get(conn, data[key]['id'])['state'], 'obsolete')
        self.assertEqual(goal_context.get(conn, data['crew_rule']['id'])['state'], 'active', 'Global crew rules are kept')
        self.assertEqual(len(documents.list(conn, 'demo')), 1)
        self.assertEqual((self.project / 'deliverable.md').read_text(encoding='utf-8'), '# kept\n')
        self.assertTrue(self.chat.path(data['boss']).exists(), 'Original conversation files are kept')
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM channel_messages WHERE project='demo'").fetchone()[0], 2)

        self.assertEqual(channel.messages(conn, 'demo')['messages'], [])
        self.assertIsNone(channel.digest(conn, 'demo'))
        self.assertEqual(self.ok('/api/state?project=demo')['channel']['unread'], 0)
        fresh = self.ok('/api/leader?project=demo', {'name': 'boss', 'task': 'start over'})
        new = fresh['session_id']
        self.finish_turn(new, self.processes[-1])
        self.assertNotEqual(new, data['boss'])
        self.assertEqual(board.handoffs(conn, new), [], 'Old * and same-name handoffs are never delivered')
        self.assertIsNone(channel.summary(conn, new), 'No old channel card or posts are injected')
        self.assertEqual(board.pending_notices(conn, new), [])
        self.service.deliver_notices()
        self.chat.deliver_messages()
        self.assertIsNone(conn.execute("SELECT sent_at FROM leader_notices WHERE text='old queued notice'").fetchone()[0])
        self.assertIsNone(conn.execute("SELECT delivered_at FROM peer_messages WHERE body='old mail for the leader'").fetchone()[0])
        self.assertFalse(any('old mail' in json.dumps(event, ensure_ascii=False) for event in self.chat.journal(new).events))
        new_goal = goals.create(conn, 'demo', new, 'Fresh goal', 'New work', ['Works'])
        visible = [item['text'] for item in goal_context.inspect(conn, new_goal['id'])['items'] if item['state'] == 'active']
        self.assertEqual(visible, ['global rule'])
        channel.post(conn, 'demo', new, 'new post', source='crew')
        self.assertEqual([m['body'] for m in channel.messages(conn, 'demo')['messages']], ['new post'])
        self.assertEqual([m['body'] for m in channel.messages(conn, 'other')['messages']], ['other workspace post'])
        self.assertEqual([h['body'] for h in board.handoffs(conn, 'x1')], ['other workspace broadcast'])

        history = self.ok('/api/maintenance/jobs?project=demo&kind=workspace_cleanup')
        self.assertEqual(history['cleanups'], 1)
        archived = self.ok('/api/maintenance/archive?' + urlencode({'project': 'demo', 'job_id': job['id'], 'kind': 'channel'}))
        self.assertEqual([m['body'] for m in archived['messages']], ['old leader post', 'old user post'])
        self.assertIn('old card', archived['digest']['body'])
        handoffs = self.ok('/api/maintenance/archive?' + urlencode({'project': 'demo', 'job_id': job['id'], 'kind': 'handoffs'}))
        self.assertEqual([h['body'] for h in handoffs['handoffs']], ['old broadcast', 'old handoff to boss'])
        code, result, _ = self.request('/api/maintenance/archive?' + urlencode({'project': 'other', 'job_id': job['id'], 'kind': 'channel'}))
        self.assertEqual(code, 400, result)

    def test_external_identity_keeps_real_partial_state_and_no_boundary(self):
        data = self.populate()
        self.agents[:] = [{'sessionId': 's1', 'name': 'one', 'status': 'busy'}]
        self.service.refresh_agents()
        preview = self.cleanup_preview()
        self.assertEqual(preview['running']['external'], 1)
        result = self.settled(self.cleanup(preview)['id'], kind='workspace_cleanup')
        items = {item['target']: item for item in result['items']}
        self.assertEqual((items['session:s1']['status'], items['session:s1']['error_type']), ('failed', 'external'))
        self.assertEqual(items['session:' + data['boss']]['status'], 'completed')
        self.assertEqual((items['archive']['status'], items['archive']['error_type']), ('failed', 'blocked'))
        self.assertEqual(result['status'], 'partial')
        self.assertIsNotNone(data['boss_process'].poll(), 'Real partial stops remain')
        self.assertEqual(goals.get(self.conn, data['running']['id'])['status'], 'cancelled')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM workspace_cleanup_boundaries").fetchone()[0], 0)
        self.assertIsNone(board.session_control(self.conn, data['boss']), 'Nothing is archived while a stop is unconfirmed')
        self.assertEqual([m['body'] for m in channel.messages(self.conn, 'demo')['messages']], ['old leader post', 'old user post'])

    def test_preview_expiry_change_and_empty_workspace_are_refused(self):
        self.populate()
        preview = self.cleanup_preview()
        self.service.maintenance.previews[preview['preview_id']]['deadline'] = 0
        code, result, _ = self.request('/api/maintenance/cleanup?project=demo', {'project': 'demo', 'preview_id': preview['preview_id'], 'confirm': True})
        self.assertEqual(code, 409, result)
        self.assertIn('请重新预览', result['error'])
        preview = self.cleanup_preview()
        self.ok('/api/spawn?project=demo', {'name': 'late', 'task': 'joined after preview', 'group': 'boss'})
        code, result, _ = self.request('/api/maintenance/cleanup?project=demo', {'project': 'demo', 'preview_id': preview['preview_id'], 'confirm': True})
        self.assertEqual((code, result['error']), (409, '工作区在预览后发生了变化，请重新预览'))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM maintenance_jobs').fetchone()[0], 0)
        empty = self.root / 'empty'
        empty.mkdir()
        board.init_project(self.conn, 'empty', empty, now=NOW)
        preview = self.ok('/api/maintenance/cleanup/preview?project=empty', {'project': 'empty'})
        self.assertTrue(preview['empty'])
        code, result, _ = self.request('/api/maintenance/cleanup?project=empty', {'project': 'empty', 'preview_id': preview['preview_id'], 'confirm': True})
        self.assertEqual(code, 400, result)

    def test_digest_generated_before_cleanup_is_discarded_instead_of_returning(self):
        self.populate()
        release, started = threading.Event(), threading.Event()

        def slow(home, text, process_factory, cancelled=None):
            started.set()
            release.wait(5)
            return '## 已定结论\nstale card\n## 未定的事 · 在等谁\n暂无\n## 各组跨组相关进展\n暂无', 0.0, 'test', 'claude'

        channel.post(self.conn, 'demo', board.HUMAN, 'post after the old card', source='web')
        with mock.patch('crew.channel_digest.generate', side_effect=slow):
            self.ok('/api/channel/digest?project=demo', {'project': 'demo'})
            self.assertTrue(started.wait(5))
            result = self.settled(self.cleanup(self.cleanup_preview())['id'], kind='workspace_cleanup')
            self.assertEqual(result['status'], 'completed', result)
            release.set()
            wait_until(lambda: not self.service.channel_digest.state('demo')['digesting'], 'digest worker')
        self.assertIn('作废', self.service.channel_digest.state('demo')['digest_error'])
        self.assertIsNone(channel.digest(self.conn, 'demo'))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM channel_digests WHERE body LIKE '%stale card%'").fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
