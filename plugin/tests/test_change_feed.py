"""Snapshot + sequence change feed: same-transaction records, resume, invalidation and the SSE handler."""
import tests
import io
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from crew import board, change_feed, goal_links, goals, store, todo
from crew.web_service import events
from tests.test_runtimes import RuntimeFixture


def changes(conn, after=0, project='demo'):
    return change_feed.read(conn, change_feed.scopes(project), after)


def record_todo(conn, project='demo', entity=1, now=None):
    with board.transaction(conn):
        return change_feed.updated(conn, change_feed.workspace(project), 'todo', entity, {'id': entity}, now)


def clear_feed(conn):
    """Start a test from an empty feed: creating its workspaces already recorded their own changes."""
    conn.execute('DELETE FROM change_log')
    conn.execute("DELETE FROM sqlite_sequence WHERE name='change_log'")


class FeedFixture(RuntimeFixture):
    def setUp(self):
        super().setUp()
        clear_feed(self.conn)


class RecordTests(FeedFixture):
    def test_a_change_must_ride_on_the_business_transaction(self):
        with self.assertRaisesRegex(RuntimeError, '同一事务'):
            change_feed.updated(self.conn, change_feed.workspace('demo'), 'todo', 1, {'id': 1})
        self.assertEqual(change_feed.latest_seq(self.conn), 0)

    def test_unknown_kinds_scopes_and_empty_values_are_refused(self):
        with board.transaction(self.conn):
            with self.assertRaisesRegex(ValueError, '未登记'):
                change_feed.updated(self.conn, change_feed.GLOBAL, 'todos', 1, {})
            with self.assertRaisesRegex(ValueError, '范围'):
                change_feed.updated(self.conn, 'demo', 'todo', 1, {})
            with self.assertRaisesRegex(ValueError, '新值'):
                change_feed.updated(self.conn, change_feed.GLOBAL, 'todo', 1, None)
        with self.assertRaises(ValueError):
            change_feed.workspace('')

    def test_rolled_back_business_write_leaves_no_change_and_no_used_sequence(self):
        with self.assertRaises(board.BoardError):
            with board.transaction(self.conn):
                change_feed.updated(self.conn, change_feed.workspace('demo'), 'todo', 1, {'id': 1})
                raise board.BoardError('业务校验失败')
        self.assertEqual(change_feed.latest_seq(self.conn), 0)
        self.assertEqual(record_todo(self.conn), 1)

    def test_a_failed_todo_write_records_nothing(self):
        added = todo.add(self.conn, 'member-id', '连接设备', '打开设置连接设备', '设备出现在列表里')
        seq = change_feed.latest_seq(self.conn)
        with self.assertRaises(board.Conflict):
            todo.resolve(self.conn, 'demo', added['id'], 'done', expected_version=7)
        self.assertEqual(change_feed.latest_seq(self.conn), seq)
        self.assertEqual(changes(self.conn, seq), [])


class TodoPilotTests(FeedFixture):
    def add(self, title='连接设备', **options):
        return todo.add(self.conn, 'member-id', title, '打开设置连接设备', '设备出现在列表里', **options)

    def test_every_todo_write_carries_the_snapshot_value(self):
        added = self.add(blocking=True)
        [created] = changes(self.conn)
        self.assertEqual((created['type'], created['entity']), ('todo.updated', str(added['id'])))
        self.assertEqual(created['data'], todo.list(self.conn, 'demo')[0])

        resolved = todo.resolve(self.conn, 'demo', added['id'], 'done')
        [gone] = changes(self.conn, created['seq'])
        self.assertEqual((gone['type'], gone['entity'], gone['data']), ('todo.removed', str(added['id']), None))
        self.assertEqual(todo.list(self.conn, 'demo'), [])

        todo.undo(self.conn, 'demo', added['id'], resolved['before'], resolved['version'])
        [back] = changes(self.conn, gone['seq'])
        self.assertEqual(back['type'], 'todo.updated')
        self.assertEqual(back['data'], todo.list(self.conn, 'demo')[0])

        todo.withdraw(self.conn, 'member-id', added['id'], '已不需要')
        self.assertEqual([item['type'] for item in changes(self.conn, back['seq'])], ['todo.removed'])

    def test_a_duplicate_add_changes_nothing(self):
        self.add()
        seq = change_feed.latest_seq(self.conn)
        self.assertTrue(self.add()['duplicate'])
        self.assertEqual(change_feed.latest_seq(self.conn), seq)

    def test_goal_links_republish_the_open_action(self):
        goal = goals.create(self.conn, 'demo', 'chief-id', '原目标', '原始需求', ['可复核的 example'])
        goals.assign(self.conn, goal['id'], 'member-id', '处理这个目标')
        execution = goals.create_execution(self.conn, goal['id'], 'member-id', runtime='claude')
        goals.transition(self.conn, goal['id'], 'running')
        item = todo.add_native(self.conn, 'member-id', '连接设备', '打开设置连接设备', '设备出现在列表里',
                               execution_id=execution['id'])
        latest = changes(self.conn)[-1]
        self.assertEqual(latest['data'], todo.list(self.conn, 'demo')[0])
        self.assertEqual(latest['data']['goal_ids'], [goal['id']])

        todo.withdraw(self.conn, 'member-id', item['id'], '自己解决了')
        seq = change_feed.latest_seq(self.conn)
        goal_links._mark_delivered(self.conn, goal_links.get(self.conn, 'todo', item['id']), 2)
        self.assertEqual([change['type'] for change in changes(self.conn, seq)], ['todo.removed'])

    def test_workspace_cleanup_removes_every_open_action(self):
        first, second = self.add('第一项'), self.add('第二项')
        self.conn.execute("INSERT INTO maintenance_jobs(id,project,kind,request_id,status,detail_json,created_at,updated_at) "
                          "VALUES('job-1','demo','workspace_cleanup','request-1','running','{}','2026-11-27T00:00:00.000Z',"
                          "'2026-11-27T00:00:00.000Z')")
        seq = change_feed.latest_seq(self.conn)
        manager = SimpleNamespace(chat=SimpleNamespace(_identity_keys=lambda conn, sid: [], running=lambda key: False))
        with mock.patch('crew.maintenance.job', return_value={'detail': {}}), \
                mock.patch('crew.maintenance.session_ops._archive'), mock.patch('crew.maintenance.session_ops._close_group'):
            from crew import maintenance
            with board.transaction(self.conn):
                maintenance.Manager._archive(manager, self.conn, 'demo', 'job-1')
        removed = changes(self.conn, seq)
        # The cleanup boundary also resets the channel's unread count.
        self.assertEqual(sorted((change['type'], change['entity']) for change in removed),
                         sorted([('todo.removed', str(first['id'])), ('todo.removed', str(second['id'])),
                                 ('channel.updated', 'demo')]))


class ReadTests(FeedFixture):
    def test_resume_returns_exactly_the_changes_after_the_cursor_in_visible_scopes(self):
        (self.root / 'other').mkdir()
        board.init_project(self.conn, 'other', self.root / 'other')
        clear_feed(self.conn)
        first = record_todo(self.conn, entity=1)
        record_todo(self.conn, 'other', entity=2)
        with board.transaction(self.conn):
            change_feed.updated(self.conn, change_feed.GLOBAL, 'todo', 3, {'id': 3})
        record_todo(self.conn, entity=4)
        self.assertEqual([c['entity'] for c in changes(self.conn)], ['1', '3', '4'])
        self.assertEqual([c['entity'] for c in changes(self.conn, first)], ['3', '4'])
        self.assertEqual([c['entity'] for c in change_feed.read(self.conn, change_feed.scopes(None), 0)], ['3'])
        self.assertEqual(changes(self.conn, change_feed.latest_seq(self.conn)), [])

    def test_bad_cursors_are_errors_and_future_cursors_need_a_snapshot(self):
        record_todo(self.conn)
        for value in (-1, '1', 1.0, None):
            with self.assertRaises(ValueError):
                changes(self.conn, value)
        with self.assertRaises(change_feed.SnapshotRequired) as caught:
            changes(self.conn, 2)
        self.assertEqual(caught.exception.reason, 'ahead')

    def test_pruned_history_requires_a_snapshot_but_the_watermark_itself_resumes(self):
        for entity in range(1, 6):
            record_todo(self.conn, entity=entity)
        with mock.patch.object(change_feed, 'RETAIN_ROWS', 2), board.transaction(self.conn):
            self.assertEqual(change_feed.prune(self.conn), 3)
        self.assertEqual(change_feed.pruned_through(self.conn), 3)
        with self.assertRaises(change_feed.SnapshotRequired) as caught:
            changes(self.conn, 2)
        self.assertEqual(caught.exception.reason, 'pruned')
        self.assertEqual([c['seq'] for c in changes(self.conn, 3)], [4, 5])

    def test_age_pruning_keeps_one_contiguous_prefix_and_survives_an_empty_table(self):
        record_todo(self.conn, entity=1, now='2026-11-25T00:00:00.000Z')
        record_todo(self.conn, entity=2, now='2026-11-27T00:00:00.000Z')
        # A clock step back must not punch a hole that the watermark cannot describe.
        record_todo(self.conn, entity=3, now='2026-11-24T00:00:00.000Z')
        record_todo(self.conn, entity=4, now='2026-11-27T00:00:01.000Z')
        with board.transaction(self.conn):
            self.assertEqual(change_feed.prune(self.conn, '2026-11-27T00:00:02.000Z'), 3)
        self.assertEqual([c['seq'] for c in changes(self.conn, 3)], [4])
        with board.transaction(self.conn):
            change_feed.prune(self.conn, '2026-11-29T00:00:00.000Z')
        self.assertEqual((change_feed.pruned_through(self.conn), change_feed.latest_seq(self.conn)), (4, 4))
        self.assertEqual(changes(self.conn, 4), [])
        self.assertEqual(record_todo(self.conn, entity=5), 5)

    def test_recording_prunes_on_its_own_every_few_hundred_changes(self):
        with mock.patch.object(change_feed, 'RETAIN_ROWS', 10), mock.patch.object(change_feed, 'PRUNE_EVERY', 16):
            for entity in range(1, 33):
                record_todo(self.conn, entity=entity)
        self.assertEqual(change_feed.pruned_through(self.conn), 22)

    def test_a_backlog_beyond_the_resume_limit_requires_a_snapshot(self):
        for entity in range(1, 5):
            record_todo(self.conn, entity=entity)
        self.assertEqual(len(change_feed.read(self.conn, change_feed.scopes('demo'), 0, limit=4)), 4)
        with self.assertRaises(change_feed.SnapshotRequired) as caught:
            change_feed.read(self.conn, change_feed.scopes('demo'), 0, limit=3)
        self.assertEqual(caught.exception.reason, 'backlog')


class ConcurrencyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name) / 'home'
        with closing(store.connect(self.home)) as conn:
            board.init_project(conn, 'demo', Path(temp.name))
            clear_feed(conn)

    def test_concurrent_writers_commit_in_sequence_order_and_a_live_reader_never_skips(self):
        writers, per_writer, seen, failures = 4, 40, [], []
        done = threading.Event()

        def write(index):
            with closing(store.connect(self.home)) as conn:
                for number in range(per_writer):
                    record_todo(conn, entity=index * 1000 + number)

        def follow():
            cursor = 0
            with closing(store.connect(self.home)) as conn:
                while True:
                    finished = done.is_set()
                    for change in changes(conn, cursor):
                        if change['seq'] <= cursor:
                            failures.append((cursor, change['seq']))
                        seen.append(change['seq'])
                        cursor = change['seq']
                    if finished:
                        return

        reader = threading.Thread(target=follow)
        reader.start()
        threads = [threading.Thread(target=write, args=(index,)) for index in range(writers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        done.set()
        reader.join()
        self.assertEqual(failures, [])
        # Each read only moves forward, so seeing every number once proves no change was skipped.
        self.assertEqual(seen, list(range(1, writers * per_writer + 1)))


class MigrationTests(unittest.TestCase):
    def test_an_existing_database_gains_the_feed_without_losing_rows_and_repeats_safely(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / 'home'
            with closing(store.connect(home)) as conn:
                board.init_project(conn, 'demo', Path(temp))
                conn.execute('DROP TABLE change_log')
                conn.execute("DELETE FROM sqlite_sequence WHERE name='change_log'")
            errors = []

            def connect():
                try:
                    with closing(store.connect(home)) as conn:
                        conn.execute('SELECT 1 FROM change_log').fetchall()
                except sqlite3.Error as exc:
                    errors.append(repr(exc))
            threads = [threading.Thread(target=connect) for _ in range(6)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])
            with closing(store.connect(home)) as conn:
                self.assertEqual([p['name'] for p in board.projects(conn)], ['demo'])
                self.assertEqual((change_feed.latest_seq(conn), change_feed.pruned_through(conn)), (0, 0))


class FakeHandler:
    def __init__(self, query_after=None, last_event_id=None, rounds=1):
        self.wfile = io.BytesIO()
        self.headers = {} if last_event_id is None else {'Last-Event-ID': last_event_id}
        self.server = SimpleNamespace()
        self.status, self.sent_headers, self.rounds = None, {}, rounds
        self.sent = None

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.sent_headers[name] = value

    def end_headers(self):
        pass

    def _request_active(self):
        self.rounds -= 1
        return self.rounds >= 0

    def _send(self, status, content):
        self.status, self.sent = status, content

    def frames(self):
        frames = []
        for block in self.wfile.getvalue().decode('utf-8').split('\n\n'):
            if not block:
                continue
            fields = dict(line.split(': ', 1) for line in block.split('\n') if not line.startswith(':'))
            frames.append({'event': fields.get('event', 'message'), 'id': fields.get('id'),
                           'data': json.loads(fields['data']) if 'data' in fields else None})
        return frames


class HttpHandlerTests(FeedFixture):
    def setUp(self):
        super().setUp()
        self.service = SimpleNamespace(home=self.home, stop=threading.Event(),
                                       selected_project=lambda conn, name: board.project(conn, name)['name'],
                                       state=lambda name: {'todos': todo.list(self.conn, name)})

    def serve(self, query, handler=None):
        handler = handler or FakeHandler()
        with mock.patch.object(events, 'POLL_SECONDS', 0):
            events.serve_events(handler, self.service, query)
        return handler

    def test_snapshot_carries_the_sequence_it_covers(self):
        added = todo.add(self.conn, 'member-id', '连接设备', '打开设置连接设备', '设备出现在列表里')
        handler = FakeHandler()
        events.serve_snapshot(handler, self.service, {'project': ['demo']})
        self.assertEqual(handler.status, 200)
        self.assertEqual(handler.sent['seq'], change_feed.latest_seq(self.conn))
        self.assertEqual([item['id'] for item in handler.sent['data']['todos']], [added['id']])

    def test_stream_sends_the_backlog_then_ready_then_live_changes(self):
        first = todo.add(self.conn, 'member-id', '第一项', '步骤', '确认')
        handler = FakeHandler(rounds=1)
        original = change_feed.read
        calls = []

        def read(conn, visible, after, limit=change_feed.RESUME_LIMIT):
            calls.append(after)
            if len(calls) == 2:
                # A write lands while the stream is idle; the next wake-up re-reads the table.
                todo.add(self.conn, 'member-id', '第二项', '步骤', '确认')
            return original(conn, visible, after, limit)
        with mock.patch.object(change_feed, 'read', side_effect=read):
            self.serve({'project': ['demo'], 'after': ['0']}, handler)
        frames = handler.frames()
        self.assertEqual(handler.status, 200)
        self.assertEqual(handler.sent_headers['Content-Type'], 'text/event-stream; charset=utf-8')
        self.assertEqual([frame['event'] for frame in frames], ['message', 'ready', 'message'])
        self.assertEqual(frames[0]['data']['entity'], str(first['id']))
        self.assertEqual(frames[1]['data'], {'seq': 1})
        self.assertEqual((frames[2]['id'], frames[2]['data']['data']['title']), ('2', '第二项'))

    def test_last_event_id_resumes_and_an_impossible_cursor_answers_snapshot_required(self):
        todo.add(self.conn, 'member-id', '第一项', '步骤', '确认')
        todo.add(self.conn, 'member-id', '第二项', '步骤', '确认')
        frames = self.serve({'project': ['demo']}, FakeHandler(last_event_id='1', rounds=0)).frames()
        self.assertEqual([(f['event'], f['id']) for f in frames], [('message', '2'), ('ready', None)])
        frames = self.serve({'project': ['demo'], 'after': ['9']}, FakeHandler(rounds=5)).frames()
        self.assertEqual(frames, [{'event': 'snapshot-required', 'id': None, 'data': {'reason': 'ahead'}}])

    def test_falling_behind_mid_stream_ends_with_snapshot_required(self):
        handler = FakeHandler(rounds=3)
        original, calls = change_feed.read, []

        def read(conn, visible, after, limit=change_feed.RESUME_LIMIT):
            calls.append(after)
            if len(calls) == 1:
                return original(conn, visible, after, limit)
            # A burst larger than the resume limit arrives between two wake-ups.
            for number in range(3):
                todo.add(self.conn, 'member-id', '批量 %s' % number, '步骤', '确认')
            return original(conn, visible, after, 2)
        with mock.patch.object(change_feed, 'read', side_effect=read):
            self.serve({'project': ['demo'], 'after': ['0']}, handler)
        self.assertEqual([f['event'] for f in handler.frames()], ['ready', 'snapshot-required'])
        self.assertEqual(handler.frames()[-1]['data'], {'reason': 'backlog'})

    def test_missing_or_malformed_cursors_and_unknown_workspaces_are_request_errors(self):
        for query, headers in (({'project': ['demo']}, None), ({'project': ['demo'], 'after': ['-1']}, None),
                               ({'project': ['demo']}, 'abc')):
            with self.assertRaises(ValueError):
                self.serve(query, FakeHandler(last_event_id=headers))
        with self.assertRaises(board.BoardError):
            self.serve({'project': ['nowhere'], 'after': ['0']})

    def test_service_stop_and_idle_heartbeats(self):
        with mock.patch.object(events, 'HEARTBEAT_SECONDS', 0):
            handler = self.serve({'after': ['0']}, FakeHandler(rounds=2))
        self.assertEqual(handler.wfile.getvalue().decode().count(': heartbeat'), 2)
        self.service.stop.set()
        handler = self.serve({'after': ['0']}, FakeHandler(rounds=5))
        self.assertEqual([f['event'] for f in handler.frames()], ['ready'])

    def test_remote_streams_register_and_unregister_their_device(self):
        controller = mock.Mock()
        handler = FakeHandler(rounds=0)
        handler.server = SimpleNamespace(remote_controller=controller, remote_stop_event=threading.Event())
        handler.remote_identity = {'device_id': 'phone-1'}
        self.serve({'project': ['demo'], 'after': ['0']}, handler)
        controller.stream_opened.assert_called_once_with('phone-1')
        controller.stream_closed.assert_called_once_with('phone-1')


if __name__ == '__main__':
    unittest.main()
