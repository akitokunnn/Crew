"""Snapshot + sequence change feed: invalidations recorded with the write, values built when read, the SSE handler.

Write side: a business transaction records which entities changed and nothing else, so building a value can
never fail a write. Read side: `change_feed.read` builds each changed entity's current value in one read
transaction, keeps the last row per entity, and stops without moving past an entity it cannot build.
"""
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


def build(conn, kind, project, entity):
    """Database kinds as the service builds them; managed kinds stand in with a marker value."""
    if kind in change_feed.BUILDERS:
        return change_feed.build_database_value(conn, kind, project, entity)
    return {'managed': kind}


def changes(conn, after=0, project='demo', builder=build):
    return change_feed.read(conn, change_feed.scopes(project), after, builder)


def of(kind, items):
    return [item for item in items if item['type'].split('.')[0] == kind]


def rows(conn, after=0):
    return [dict(row) for row in conn.execute('SELECT seq,scope,type,entity,data FROM change_log WHERE seq>? ORDER BY seq', (after,))]


def record_todo(conn, project='demo', entity=1, now=None):
    """A row recorded with its value, as the usage scan's limits are and as rows from before invalidations were."""
    with board.transaction(conn):
        return change_feed.updated(conn, change_feed.workspace(project), 'todo', entity, {'id': entity}, now)


def mark(conn, kind='todo', project='demo', entity=1):
    with board.transaction(conn):
        change_feed.touch(conn, kind, project, entity)
    return change_feed.latest_seq(conn)


def clear_feed(conn):
    """Start a test from an empty feed: creating its workspaces and people already recorded changes."""
    conn.execute('DELETE FROM change_log')
    conn.execute("DELETE FROM sqlite_sequence WHERE name='change_log'")


class FeedFixture(RuntimeFixture):
    def setUp(self):
        super().setUp()
        clear_feed(self.conn)

    def add(self, title='连接设备', **options):
        return todo.add(self.conn, 'member-id', title, '打开设置连接设备', '设备出现在列表里', **options)


class WriteTests(FeedFixture):
    def test_a_mark_must_ride_on_the_business_transaction(self):
        with self.assertRaisesRegex(RuntimeError, 'board.transaction'):
            change_feed.touch(self.conn, 'todo', 'demo', 1)
        with self.assertRaisesRegex(RuntimeError, '同一事务'):
            change_feed.updated(self.conn, change_feed.GLOBAL, 'limits', 'limits', {})
        self.assertEqual(change_feed.latest_seq(self.conn), 0)

    def test_unknown_kinds_scopes_and_empty_values_are_refused(self):
        with board.transaction(self.conn):
            with self.assertRaisesRegex(ValueError, '未登记'):
                change_feed.touch(self.conn, 'todos', 'demo', 1)
            with self.assertRaisesRegex(ValueError, '未登记'):
                change_feed.touch(self.conn, 'limits', None, 'limits')
            with self.assertRaisesRegex(ValueError, '范围'):
                change_feed.touch(self.conn, 'todo', None, 1)
            with self.assertRaisesRegex(ValueError, '范围'):
                change_feed.touch(self.conn, 'ai', 'demo', 'ai')
            with self.assertRaisesRegex(ValueError, '新值'):
                change_feed.updated(self.conn, change_feed.GLOBAL, 'limits', 'limits', None)
        with self.assertRaises(ValueError):
            change_feed.workspace('')

    def test_a_write_records_one_invalidation_per_entity_and_no_value(self):
        with board.transaction(self.conn):
            for _ in range(3):
                change_feed.touch(self.conn, 'todo', 'demo', 7)
            change_feed.touch(self.conn, 'ai', None, 'ai')
        self.assertEqual(rows(self.conn), [
            {'seq': 1, 'scope': 'workspace:demo', 'type': 'todo.invalidated', 'entity': '7', 'data': None},
            {'seq': 2, 'scope': 'global', 'type': 'ai.invalidated', 'entity': 'ai', 'data': None}])

    def test_a_task_or_decision_change_also_invalidates_the_workspace_cards(self):
        with board.transaction(self.conn):
            change_feed.touch(self.conn, 'goal', 'demo', 'g-1')
            change_feed.touch(self.conn, 'decision', 'demo', 3)
        self.assertEqual([(row['type'], row['entity']) for row in rows(self.conn)],
                         [('goal.invalidated', 'g-1'), ('conversations.invalidated', 'demo'), ('decision.invalidated', '3')])

    def test_rolled_back_business_write_leaves_no_change_and_no_used_sequence(self):
        with self.assertRaises(board.BoardError):
            with board.transaction(self.conn):
                change_feed.touch(self.conn, 'todo', 'demo', 1)
                raise board.BoardError('业务校验失败')
        self.assertEqual(change_feed.latest_seq(self.conn), 0)
        self.assertEqual(mark(self.conn), 1)

    def test_a_failed_todo_write_records_nothing(self):
        added = self.add()
        seq = change_feed.latest_seq(self.conn)
        with self.assertRaises(board.Conflict):
            todo.resolve(self.conn, 'demo', added['id'], 'done', expected_version=7)
        self.assertEqual(change_feed.latest_seq(self.conn), seq)

    def test_a_value_that_cannot_be_built_never_fails_the_write(self):
        # Before invalidations, the value was built inside the write; a broken builder rolled the user's write back.
        with mock.patch('crew.todo.change_value', side_effect=KeyError('requirement_version')), \
                mock.patch('crew.snapshot_values.decision', side_effect=KeyError('requirement_version')):
            added = self.add()
            decision = board.ask(self.conn, 'member-id', '要不要连接设备？', ['要', '不要'])
            board.answer(self.conn, decision, '要')
        self.assertEqual(todo.list(self.conn, 'demo')[0]['id'], added['id'])
        self.assertEqual(self.conn.execute('SELECT status FROM decisions WHERE id=?', (decision,)).fetchone()[0], 'answered')
        written = {(row['type'], row['entity']) for row in rows(self.conn)}
        self.assertLessEqual({('todo.invalidated', str(added['id'])), ('decision.invalidated', str(decision))}, written)
        with self.assertRaises(change_feed.ProjectionFailed):
            with mock.patch('crew.todo.change_value', side_effect=KeyError('requirement_version')):
                changes(self.conn)

    def test_a_transaction_prunes_when_the_sequence_crosses_the_interval(self):
        with mock.patch.object(change_feed, 'RETAIN_ROWS', 10), mock.patch.object(change_feed, 'PRUNE_EVERY', 16):
            for entity in range(1, 33):
                mark(self.conn, entity=entity)
            self.assertEqual(change_feed.pruned_through(self.conn), 22)
            # Card triggers add rows too; crossing the interval through them prunes as well.
            with board.transaction(self.conn):
                for number in range(20):
                    self.conn.execute('UPDATE sessions SET status_note=? WHERE session_id=?', (str(number), 'chief-id'))
        self.assertEqual(change_feed.latest_seq(self.conn), 72)
        self.assertEqual(change_feed.pruned_through(self.conn), 62)


class CardTriggerTests(FeedFixture):
    def test_card_tables_invalidate_their_workspace_whoever_writes(self):
        self.conn.execute("UPDATE sessions SET status='working' WHERE session_id='chief-id'")
        self.conn.execute("INSERT INTO session_options(session_id,model,set_at) VALUES('member-id','m','t')")
        self.conn.execute("INSERT INTO read_cursors(session_id,seq,updated_at) VALUES('nobody',3,'t')")
        self.assertCountEqual([(row['scope'], row['type'], row['entity']) for row in rows(self.conn)],
                              [('workspace:demo', 'conversations.invalidated', 'demo')] * 2
                              + [('global', 'workspace_activity.invalidated', 'demo')])

    def test_a_person_renamed_regrouped_or_closed_asks_every_page_for_a_new_snapshot(self):
        self.conn.execute("UPDATE sessions SET crew_name='builder-2' WHERE session_id='member-id'")
        self.assertIn('snapshot.invalidated', [row['type'] for row in rows(self.conn)])
        with self.assertRaises(change_feed.SnapshotRequired) as caught:
            changes(self.conn)
        self.assertEqual(caught.exception.reason, 'reset')
        seq = change_feed.latest_seq(self.conn)
        self.conn.execute("UPDATE sessions SET status='idle' WHERE session_id='member-id'")
        self.assertCountEqual([row['type'] for row in rows(self.conn, seq)],
                              ['conversations.invalidated', 'workspace_activity.invalidated'])

    def test_delivery_receipts_invalidate_the_global_notification_field(self):
        self.conn.execute("INSERT INTO notification_messages(item_id,project,leader_session_id,kind,created_at,content,status) "
                          "VALUES('n1','demo','chief-id','report','t','{}','queued')")
        self.assertEqual([(row['scope'], row['type']) for row in rows(self.conn)], [('global', 'notification.invalidated')])

    def test_every_connection_finds_the_trigger_set_installed_once(self):
        with closing(store.connect(self.home)) as other:
            present = {row[0]: row[1] for row in other.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name LIKE 'feed%'")}
        from crew import change_triggers
        self.assertEqual(present, change_triggers.STATEMENTS)


class TodoReadTests(FeedFixture):
    def test_every_todo_write_reads_back_as_the_snapshot_value(self):
        added = self.add(blocking=True)
        [created] = of('todo', changes(self.conn))
        self.assertEqual((created['type'], created['entity']), ('todo.updated', str(added['id'])))
        self.assertEqual(created['data'], todo.list(self.conn, 'demo')[0])

        resolved = todo.resolve(self.conn, 'demo', added['id'], 'done')
        [gone] = of('todo', changes(self.conn, created['seq']))
        self.assertEqual((gone['type'], gone['entity'], gone['data']), ('todo.removed', str(added['id']), None))

        todo.undo(self.conn, 'demo', added['id'], resolved['before'], resolved['version'])
        [back] = of('todo', changes(self.conn, gone['seq']))
        self.assertEqual(back['type'], 'todo.updated')
        self.assertEqual(back['data'], todo.list(self.conn, 'demo')[0])

        todo.withdraw(self.conn, 'member-id', added['id'], '已不需要')
        self.assertEqual([item['type'] for item in of('todo', changes(self.conn, back['seq']))], ['todo.removed'])

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
        latest = of('todo', changes(self.conn))[-1]
        self.assertEqual(latest['data'], todo.list(self.conn, 'demo')[0])
        self.assertEqual(latest['data']['goal_ids'], [goal['id']])

        todo.withdraw(self.conn, 'member-id', item['id'], '自己解决了')
        seq = change_feed.latest_seq(self.conn)
        goal_links._mark_delivered(self.conn, goal_links.get(self.conn, 'todo', item['id']), 2)
        self.assertEqual([change['type'] for change in of('todo', changes(self.conn, seq))], ['todo.removed'])

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
        removed = [change for change in changes(self.conn, seq) if change['type'] != 'conversations.updated']
        # The cleanup boundary also resets the channel's unread count.
        self.assertEqual(sorted((change['type'], change['entity']) for change in removed),
                         sorted([('todo.removed', str(first['id'])), ('todo.removed', str(second['id'])),
                                 ('channel.updated', 'demo'), ('workspace_activity.updated', 'demo')]))


class ReadTests(FeedFixture):
    def test_resume_returns_exactly_the_changes_after_the_cursor_in_visible_scopes(self):
        (self.root / 'other').mkdir()
        board.init_project(self.conn, 'other', self.root / 'other')
        clear_feed(self.conn)
        first = record_todo(self.conn, entity=1)
        record_todo(self.conn, 'other', entity=2)
        with board.transaction(self.conn):
            change_feed.updated(self.conn, change_feed.GLOBAL, 'limits', 'limits', {'limits': 3})
        record_todo(self.conn, entity=4)
        self.assertEqual([c['entity'] for c in changes(self.conn)], ['1', 'limits', '4'])
        self.assertEqual([c['entity'] for c in changes(self.conn, first)], ['limits', '4'])
        self.assertEqual([c['entity'] for c in changes(self.conn, project=None)], ['limits'])
        self.assertEqual(changes(self.conn, change_feed.latest_seq(self.conn)), [])

    def test_an_entity_changed_removed_and_recreated_while_away_arrives_once_as_it_is_now(self):
        added = self.add()
        resolved = todo.resolve(self.conn, 'demo', added['id'], 'done')
        [gone] = of('todo', changes(self.conn))
        self.assertEqual((gone['type'], gone['seq']), ('todo.removed', max(
            row['seq'] for row in rows(self.conn) if row['type'] == 'todo.invalidated')))
        todo.undo(self.conn, 'demo', added['id'], resolved['before'], resolved['version'])
        [back] = of('todo', changes(self.conn))
        self.assertEqual((back['type'], back['data']), ('todo.updated', todo.list(self.conn, 'demo')[0]))
        self.assertEqual(back['seq'], max(row['seq'] for row in rows(self.conn) if row['type'] == 'todo.invalidated'))

    def test_the_same_id_in_two_scopes_stays_two_changes_in_sequence_order(self):
        with board.transaction(self.conn):
            change_feed._record(self.conn, change_feed.workspace('demo'), 'todo.updated', 'todo', '1', '{"id": 1}', None)
            change_feed._record(self.conn, change_feed.GLOBAL, 'todo.updated', 'todo', '1', '{"id": 1, "scope": "global"}', None)
            change_feed._record(self.conn, change_feed.workspace('demo'), 'todo.updated', 'todo', '1', '{"id": 1, "last": true}', None)
        self.assertEqual([(c['seq'], c['data']) for c in changes(self.conn)],
                         [(2, {'id': 1, 'scope': 'global'}), (3, {'id': 1, 'last': True})])

    def test_rows_recorded_with_values_and_invalidations_mix(self):
        added = self.add()
        clear_feed(self.conn)
        record_todo(self.conn, entity=added['id'])
        record_todo(self.conn, entity=999)
        mark(self.conn, entity=added['id'])
        self.assertEqual([(c['seq'], c['type'], c['entity'], c['data']) for c in changes(self.conn)],
                         [(2, 'todo.updated', '999', {'id': 999}),
                          (3, 'todo.updated', str(added['id']), todo.list(self.conn, 'demo')[0])])

    def test_text_ids_reach_integer_builders_as_integers(self):
        added = self.add()
        clear_feed(self.conn)
        seen = []

        def spy(conn, kind, project, entity):
            seen.append((kind, entity))
            return build(conn, kind, project, entity)
        mark(self.conn, entity=added['id'])
        mark(self.conn, 'goal', entity='g-1')
        [todo_change, _, _] = changes(self.conn, builder=spy)
        self.assertEqual(seen[:2], [('todo', added['id']), ('goal', 'g-1')])
        self.assertEqual(todo_change['data'], todo.list(self.conn, 'demo')[0])

    def test_a_commit_while_building_waits_for_the_next_read(self):
        added = self.add()
        clear_feed(self.conn)
        mark(self.conn, entity=added['id'])
        committed = []

        def racing(conn, kind, project, entity):
            if not committed:
                with closing(store.connect(self.home)) as writer:
                    committed.append(todo.resolve(writer, 'demo', added['id'], 'done'))
            return build(conn, kind, project, entity)
        [first] = changes(self.conn, builder=racing)
        # The read transaction saw the database before the resolve: the open action, at the marked sequence.
        self.assertEqual((first['seq'], first['type'], first['data']['id']), (1, 'todo.updated', added['id']))
        [second] = of('todo', changes(self.conn, first['seq']))
        self.assertEqual(second['type'], 'todo.removed')

    def test_a_value_that_cannot_be_built_names_kind_entity_and_sequence_and_keeps_its_cause(self):
        added = self.add()
        clear_feed(self.conn)
        mark(self.conn, entity=added['id'])
        with mock.patch('crew.todo.change_value', side_effect=KeyError('requirement_version')):
            with self.assertRaises(change_feed.ProjectionFailed) as caught:
                changes(self.conn)
        failure = caught.exception
        self.assertEqual((failure.kind, failure.entity, failure.seq), ('todo', str(added['id']), 1))
        self.assertIsInstance(failure.__cause__, KeyError)
        self.assertFalse(self.conn.in_transaction)

    def test_a_builder_that_writes_fails_instead_of_being_rolled_back_unseen(self):
        mark(self.conn, 'ai', None, 'ai')

        def writing(conn, kind, project, entity):
            conn.execute("UPDATE projects SET root=root WHERE name='demo'")
        with self.assertRaises(change_feed.ProjectionFailed) as caught:
            changes(self.conn, builder=writing)
        self.assertIsInstance(caught.exception.__cause__, sqlite3.OperationalError)
        # The connection is writable again once the read is over.
        self.assertEqual(mark(self.conn, 'ai', None, 'ai'), 2)

    def test_reading_a_workspace_never_writes_even_for_a_workspace_without_theme(self):
        self.conn.execute("DELETE FROM project_themes WHERE project='demo'")
        with closing(store.connect(self.home)) as conn:
            # Workspaces from before themes get theirs when the database is opened, not while it is read.
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM project_themes WHERE project='demo'").fetchone()[0], 1)
        clear_feed(self.conn)
        self.conn.execute("DELETE FROM project_themes WHERE project='demo'")
        self.assertEqual(board.projects(self.conn), [])
        self.assertEqual(change_feed.latest_seq(self.conn), 0)

    def test_a_workspace_reset_requires_a_snapshot(self):
        record_todo(self.conn, entity=1)
        mark(self.conn, 'snapshot', 'demo', 'demo')
        with self.assertRaises(change_feed.SnapshotRequired) as caught:
            changes(self.conn)
        self.assertEqual(caught.exception.reason, 'reset')
        self.assertEqual(changes(self.conn, change_feed.latest_seq(self.conn)), [])
        # Another workspace's reset is not this reader's business.
        self.assertEqual(len(change_feed.read(self.conn, change_feed.scopes(None), 0, build)), 0)

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

    def test_a_backlog_beyond_the_resume_limit_requires_a_snapshot(self):
        for entity in range(1, 5):
            record_todo(self.conn, entity=entity)
        self.assertEqual(len(change_feed.read(self.conn, change_feed.scopes('demo'), 0, build, limit=4)), 4)
        with self.assertRaises(change_feed.SnapshotRequired) as caught:
            change_feed.read(self.conn, change_feed.scopes('demo'), 0, build, limit=3)
        self.assertEqual(caught.exception.reason, 'backlog')


class NotifierTests(unittest.TestCase):
    def test_marks_collapse_until_taken_and_come_back_when_recording_failed(self):
        notifier = change_feed.Notifier()
        for _ in range(3):
            notifier.mark('conversations', 'demo', 'demo')
            notifier.session('chief-id')
        notifier.everywhere('conversations')
        self.assertTrue(notifier.wake.is_set())
        taken = notifier.take()
        self.assertEqual(taken, ([('conversations', 'demo', 'demo')], {'chief-id'}, {'conversations'}))
        self.assertFalse(notifier.wake.is_set())
        notifier.restore(*taken)
        self.assertEqual(notifier.take(), taken)
        with self.assertRaises(ValueError):
            notifier.mark('runtimes', 'demo', 'runtimes')
        with self.assertRaises(ValueError):
            notifier.everywhere('snapshot')


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
                    mark(conn, entity=index * 1000 + number)

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
        # Every entity is distinct, so nothing merges: seeing every number once proves no change was skipped.
        self.assertEqual(seen, list(range(1, writers * per_writer + 1)))


class MigrationTests(unittest.TestCase):
    def test_an_existing_database_gains_the_feed_without_losing_rows_and_repeats_safely(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / 'home'
            with closing(store.connect(home)) as conn:
                board.init_project(conn, 'demo', Path(temp))
                for name in [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'feed%'")]:
                    conn.execute('DROP TRIGGER ' + name)
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
                                       state=lambda name: {'todos': todo.list(self.conn, name)},
                                       change_value=lambda conn, kind, project, entity, viewer: build(conn, kind, project, entity))

    def serve(self, query, handler=None):
        handler = handler or FakeHandler()
        with mock.patch.object(events, 'POLL_SECONDS', 0):
            events.serve_events(handler, self.service, None, query)
        return handler

    def todos(self, handler):
        return [frame for frame in handler.frames() if frame['event'] != 'message' or frame['data']['type'].startswith('todo.')]

    def test_snapshot_carries_the_sequence_it_covers(self):
        added = self.add()
        handler = FakeHandler()
        events.serve_snapshot(handler, self.service, None, {'project': ['demo']})
        self.assertEqual(handler.status, 200)
        self.assertEqual(handler.sent['seq'], change_feed.latest_seq(self.conn))
        self.assertEqual([item['id'] for item in handler.sent['data']['todos']], [added['id']])

    def test_stream_sends_the_backlog_then_ready_then_live_changes(self):
        first = self.add('第一项')
        handler = FakeHandler(rounds=1)
        original = change_feed.read
        calls = []

        def read(conn, visible, after, builder, limit=change_feed.RESUME_LIMIT):
            calls.append(after)
            if len(calls) == 2:
                # A write lands while the stream is idle; the next wake-up re-reads the table.
                self.add('第二项')
            return original(conn, visible, after, builder, limit)
        with mock.patch.object(change_feed, 'read', side_effect=read):
            self.serve({'project': ['demo'], 'after': ['0']}, handler)
        frames = self.todos(handler)
        self.assertEqual(handler.status, 200)
        self.assertEqual(handler.sent_headers['Content-Type'], 'text/event-stream; charset=utf-8')
        self.assertEqual([frame['event'] for frame in frames], ['message', 'ready', 'message'])
        self.assertEqual(frames[0]['data']['entity'], str(first['id']))
        self.assertEqual(frames[1]['data'], {'seq': calls[1]})
        self.assertEqual(frames[2]['data']['data']['title'], '第二项')
        self.assertEqual(int(frames[2]['id']), frames[2]['data']['seq'])

    def test_last_event_id_resumes_and_an_impossible_cursor_answers_snapshot_required(self):
        first = self.add('第一项')
        cursor = change_feed.latest_seq(self.conn)
        self.add('第二项')
        frames = self.todos(self.serve({'project': ['demo']}, FakeHandler(last_event_id=str(cursor), rounds=0)))
        self.assertEqual([f['event'] for f in frames], ['message', 'ready'])
        self.assertNotEqual(frames[0]['data']['entity'], str(first['id']))
        frames = self.serve({'project': ['demo'], 'after': ['999']}, FakeHandler(rounds=5)).frames()
        self.assertEqual(frames, [{'event': 'snapshot-required', 'id': None, 'data': {'reason': 'ahead'}}])

    def test_falling_behind_mid_stream_ends_with_snapshot_required(self):
        handler = FakeHandler(rounds=3)
        original, calls = change_feed.read, []

        def read(conn, visible, after, builder, limit=change_feed.RESUME_LIMIT):
            calls.append(after)
            if len(calls) == 1:
                return original(conn, visible, after, builder, limit)
            # A burst larger than the resume limit arrives between two wake-ups.
            for number in range(3):
                self.add('批量 %s' % number)
            return original(conn, visible, after, builder, 2)
        with mock.patch.object(change_feed, 'read', side_effect=read):
            self.serve({'project': ['demo'], 'after': ['0']}, handler)
        self.assertEqual([f['event'] for f in handler.frames()], ['ready', 'snapshot-required'])
        self.assertEqual(handler.frames()[-1]['data'], {'reason': 'backlog'})

    def test_a_value_that_cannot_be_built_while_catching_up_ends_without_ready_or_cursor(self):
        added = self.add()
        stderr = io.StringIO()
        with mock.patch('crew.todo.change_value', side_effect=KeyError('requirement_version')), mock.patch('sys.stderr', stderr):
            handler = self.serve({'project': ['demo'], 'after': ['0']}, FakeHandler(rounds=5))
        [frame] = handler.frames()
        self.assertEqual((frame['event'], frame['id']), ('snapshot-required', None))
        self.assertEqual(frame['data']['reason'], 'projection')
        self.assertEqual((frame['data']['kind'], frame['data']['entity']), ('todo', str(added['id'])))
        self.assertEqual(frame['data']['error'], "KeyError: 'requirement_version'")
        log = stderr.getvalue()
        self.assertIn('kind=todo entity=%s seq=%s' % (added['id'], frame['data']['seq']), log)
        self.assertIn("KeyError: 'requirement_version'", log)

    def test_a_value_that_cannot_be_built_while_live_ends_the_stream_before_that_sequence(self):
        handler = FakeHandler(rounds=4)
        original, calls, added = change_feed.read, [], []

        def read(conn, visible, after, builder, limit=change_feed.RESUME_LIMIT):
            calls.append(after)
            if len(calls) == 2:
                added.append(self.add('坏的一项'))
            with mock.patch('crew.todo.change_value', side_effect=KeyError('requirement_version')):
                return original(conn, visible, after, builder, limit)
        with mock.patch.object(change_feed, 'read', side_effect=read), mock.patch('sys.stderr', io.StringIO()):
            self.serve({'project': ['demo'], 'after': ['0']}, handler)
        frames = handler.frames()
        self.assertEqual([f['event'] for f in frames], ['ready', 'snapshot-required'])
        self.assertIsNone(frames[-1]['id'])
        self.assertEqual(frames[-1]['data']['entity'], str(added[0]['id']))
        # The stream stopped at the failing read: it never went on to read past it.
        self.assertEqual(len(calls), 2)

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
