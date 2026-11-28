"""Isolated growth jobs, controlled journal timestamps and fake native responses."""
import tests
from contextlib import closing
import json
import threading
import unittest
from unittest import mock

from crew import behavior_prompts, board, chat, persona, persona_growth as growth, settings, store
from crew.ops import executor
from crew.ops.errors import OpError
from test_core import operation_service
from tests.test_runtimes import RuntimeFixture

START = '2026-01-02T00:00:00.000Z'
NOW = '2026-01-02T06:00:00.000Z'
ANSWER = '变化：说话更简短了\n\n正文：\n我喜欢开门见山。'


class SampleTests(unittest.TestCase):
    def test_acceptance_filters_deduplication_streaming_and_room_reply_boundaries(self):
        events = []

        def emit(kind, **fields):
            events.append(dict(type=kind, seq=len(events) + 1, at=NOW, **fields))

        def user(identity, body, source='human', status='accepted', **fields):
            emit('message', id=identity, text=body, role='user', source=source, **fields)
            emit('input_delivery', message_id=identity, status=status)

        user('old', '游标之前')
        events.append(dict(type='message', id='legacy', seq=3, text='旧版无角色或时间'))
        user('no-time', '无开启时间证据')
        del events[-2]['at']
        user('a', '早上好')
        emit('text', id='reply', text='局部')
        emit('assistant', id='reply', content=[{'type': 'text', 'text': '完整回复'},
                                               {'type': 'tool_use', 'name': '工具秘密'}])
        emit('turn_result', status='completed')
        user('a', '重复投影')
        user('room', '房内输入')
        emit('input_delivery', status='submitted', message_id='room', room_id=9)
        emit('assistant', id='roomreply', content=[{'type': 'text', 'text': '房内回答'}])
        for identity, source, body in [('p', 'peer', '其他人'), ('q', 'qq', '渠道'), ('l', 'lark', '飞书'),
                                       ('c', 'crew', '系统'), ('decision-discuss:x', 'human', '讨论'),
                                       ('personal:abc', 'human', '转达'), ('wake', 'human', '[Crew] 请继续'),
                                       ('compact', 'human', ' /compact '), ('alias', 'human', '压缩请求')]:
            user(identity, body, source)
        emit('compact_request', id='alias')
        for status in ('submitted', 'rejected', 'unknown'):
            user(status, '没有接收回执', status=status)
        user('b', '晚上好')
        cursor = str(events[-2]['seq'])
        emit('text', id='stream', text='晚')
        emit('text', id='stream', text='安')
        emit('turn_result', status='completed')
        self.assertEqual(growth.sample(events, '1', START), ('用户：早上好\nAI：完整回复\n\n用户：晚上好\nAI：晚安', cursor))
        self.assertEqual(growth.sample(events, cursor, START), ('', cursor))

    def test_unfinished_and_failed_replies_do_not_use_partial_streams(self):
        first = dict(type='message', role='user', source='human', id='user', at=NOW, seq=1, text='问')
        events = [first, dict(type='input_delivery', message_id='user', status='accepted'),
                  dict(type='text', id='reply', text='还在写', seq=3)]
        self.assertEqual(growth.sample(events, None, START), ('用户：问', '1'))
        events.append(dict(type='turn_result', status='failed'))
        self.assertEqual(growth.sample(events, None, START), ('用户：问', '1'))

    def test_strict_latest_enable_time_and_seq_even_for_equal_milliseconds(self):
        events = []
        for i, at in enumerate(('2026-01-01T23:59:59.999Z', START, NOW, NOW)):
            events.extend([dict(type='message', role='user', source='human', id=str(i), at=at, seq=2*i+1, text=str(i)),
                           dict(type='input_delivery', message_id=str(i), status='accepted')])
        self.assertEqual(growth.sample(events, None, START), ('用户：2\n\n用户：3', '7'))
        self.assertEqual(growth.sample(events, '5', START), ('用户：3', '7'))

    def test_budget_includes_labels_and_keeps_newest_first_in_chronological_order(self):
        events = []
        for i, body in enumerate(('oldest', '中' * 24000, 'newest')):
            events.extend([dict(type='message', role='user', source='human', id=str(i), at=NOW, seq=2*i+1, text=body),
                           dict(type='input_delivery', message_id=str(i), status='accepted')])
        text, cursor = growth.sample(events, None, START)
        self.assertEqual((len(text), cursor), (24000, '5'))
        self.assertTrue(text.startswith('用户：中'))
        self.assertTrue(text.endswith('\n\n用户：newest'))
        self.assertNotIn('oldest', text)


class GrowthTests(RuntimeFixture):
    sid = 'chief-id'

    def setUp(self):
        super().setUp()
        self.manager = chat.Manager(self.home, self.root, lambda: [])
        self.addCleanup(self.manager.close)
        self.addCleanup(growth.close, self.manager)
        with mock.patch.object(store, 'now_iso', return_value=START):
            persona.set_enabled(self.conn, True)
        self.jobs = []
        def start(job):
            self.jobs.append(job)
            threading.Thread.start(job)
        patch = mock.patch.object(growth.Job, 'start', start)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(growth.channel_digest, '_claude', return_value=(ANSWER, 0, 'native-haiku'))
        self.native = patch.start()
        self.addCleanup(patch.stop)

    def message(self, sid=None, identity='private'):
        journal = self.manager.journal(sid or self.sid)
        with mock.patch.object(store, 'now_iso', return_value=NOW):
            journal.message(identity, '请简短些')
            journal.emit('input_delivery', message_id=identity, status='accepted')

    def count(self, n, sid=None):
        for i in range(n):
            persona.note_input(self.conn, sid or self.sid, 'human', None, str(i))

    def view(self):
        return persona.get(self.conn, self.sid)

    def finish(self):
        self.jobs[-1].join(5)
        self.assertFalse(self.jobs[-1].is_alive())

    def test_first_four_then_twelve_and_six_hours(self):
        self.message()
        self.count(3)
        growth.tick(self.manager, self.conn, NOW)
        self.assertEqual(self.jobs, [])
        self.count(1)
        growth.tick(self.manager, self.conn, NOW)
        self.finish()
        self.assertEqual((self.view()['version'], self.view()['pending_turns']), (1, 0))
        self.conn.execute('UPDATE personas SET last_growth_at=?', (START,))
        self.message(identity='another')
        self.count(11)
        growth.tick(self.manager, self.conn, NOW)
        self.count(1)
        growth.tick(self.manager, self.conn, '2026-01-02T05:59:59.999Z')
        self.assertEqual(len(self.jobs), 1)
        growth.tick(self.manager, self.conn, NOW)
        self.finish()
        self.assertEqual(self.view()['version'], 2)
        self.assertEqual(self.native.call_count, 2)

    def test_highest_count_wins_global_slot_and_later_input_stays_pending(self):
        other = 'second'
        board.join(self.conn, other, 'demo', '第二位', self.root, None, 'main', role='leader')
        self.count(4)
        self.count(5, other)
        self.message(other)
        ready, release = threading.Event(), threading.Event()
        def blocked(*args, **kwargs):
            ready.set()
            if not release.wait(5):
                raise AssertionError('test did not release carrier')
            return ANSWER, 0, 'actual'
        self.native.side_effect = blocked
        growth.tick(self.manager, self.conn, NOW)
        try:
            self.assertTrue(ready.wait(5))
            self.assertTrue(persona.get(self.conn, other)['growing'])
            for sid in (self.sid, other):
                with self.assertRaises(OpError) as error:
                    growth.grow(self.manager, self.conn, sid)
                self.assertEqual(error.exception.code, 'persona_busy')
            growth.tick(self.manager, self.conn, NOW)
            self.assertEqual([job.sid for job in self.jobs], [other])
            self.count(1, other)
        finally:
            release.set()
        self.finish()
        self.assertEqual(persona.get(self.conn, other)['pending_turns'], 1)

    def test_success_uses_effective_prompt_clears_error_and_records_actual_runtime(self):
        persona.record_growth_error(self.conn, self.sid, '篇幅超限')
        behavior_prompts.save(self.conn, 'persona_growth', '自定义 {{current}} {{percent}} {{limit}} {{dialogue}} {{last_error}}',
                              expected_version=0)
        settings.save(self.conn, {'default': {'runtime': 'codex', 'model': 'chosen', 'effort': 'low'}})
        self.message()
        with mock.patch.object(growth.channel_digest, '_codex', return_value=(ANSWER, None, 'native-luna')) as codex:
            self.assertEqual(growth.grow(self.manager, self.conn, self.sid), {'started': True})
            self.finish()
        prompt = codex.call_args.args[0]
        for text in ('自定义', '20k', '请简短些', '上次整理失败：篇幅超限，这次务必遵守篇幅和格式'):
            self.assertIn(text, prompt)
        self.assertEqual(codex.call_args.args[2]['model'], 'chosen')
        self.assertEqual((self.view()['content'], self.view()['growth_error']), ('我喜欢开门见山。', None))
        self.assertEqual(persona.history(self.conn, self.sid)['history'][0]['action'], 'grow')
        detail = json.loads(self.conn.execute("SELECT detail FROM events WHERE kind='persona:growth'").fetchone()[0])
        self.assertEqual(detail, {'runtime': 'codex', 'model': 'native-luna', 'written': True})
        with self.assertRaises(OpError) as error:
            growth.grow(self.manager, self.conn, self.sid)
        self.assertEqual(error.exception.code, 'persona_nothing_new')

    def test_model_error_malformed_and_oversize_keep_version_without_auto_retry(self):
        self.message()
        persona.save(self.conn, self.sid, '原文', 0, board.HUMAN)
        for reply, reason in [(OSError('载体管道关闭'), '载体管道关闭'), ('无正文', '缺少'),
                              ('变化：一行\n正文：\n' + 'a'*60001, '20001')]:
            self.count(12)
            self.native.side_effect = reply if isinstance(reply, OSError) else None
            self.native.return_value = (reply, 0, 'actual')
            growth.tick(self.manager, self.conn, NOW)
            self.finish()
            count = len(self.jobs)
            growth.tick(self.manager, self.conn, NOW)
            self.assertEqual(len(self.jobs), count)
            self.assertEqual((self.view()['version'], self.view()['content']), (1, '原文'))
            self.assertIn(reason, self.view()['growth_error'])

    def test_inflight_edit_freeze_disable_and_reenable(self):
        self.message()
        def job_with(action):
            def change(*args, **kwargs):
                with closing(store.connect(self.home)) as conn:
                    action(conn)
                return ANSWER, 0, 'actual'
            self.native.side_effect = change
            growth.grow(self.manager, self.conn, self.sid)
            self.finish()
        job_with(lambda c: persona.save(c, self.sid, '我改了', 0, board.HUMAN))
        self.assertEqual(self.view()['growth_error'], growth.SUPERSEDED)
        job_with(lambda c: persona.freeze(c, self.sid, True))
        persona.freeze(self.conn, self.sid, False)
        job_with(lambda c: persona.set_enabled(c, False))
        with mock.patch.object(store, 'now_iso', return_value=START):
            persona.set_enabled(self.conn, True)
        def toggle(conn):
            persona.set_enabled(conn, False)
            persona.set_enabled(conn, True)
        job_with(toggle)
        self.assertEqual((self.view()['content'], self.view()['version']), ('我改了', 1))
        with self.assertRaises(OpError) as error:
            growth.grow(self.manager, self.conn, self.sid)
        self.assertEqual(error.exception.code, 'persona_nothing_new')

    def test_failed_version_write_rolls_back_and_records_cause(self):
        self.message()
        self.conn.execute("CREATE TRIGGER refuse_growth BEFORE INSERT ON persona_history WHEN NEW.action='grow' "
                          "BEGIN SELECT RAISE(ABORT,'history write refused'); END")
        growth.grow(self.manager, self.conn, self.sid)
        self.finish()
        self.assertEqual(self.view()['version'], 0)
        self.assertEqual(self.view()['growth_error'], 'history write refused')
        self.assertEqual(persona.history(self.conn, self.sid)['history'], [])

    def test_switch_cycle_during_sampling_never_submits_old_dialogue(self):
        self.message()
        self.count(4)
        read = growth._sample
        def toggle_during_read(*args):
            material = read(*args)
            with closing(store.connect(self.home)) as conn:
                persona.set_enabled(conn, False)
                persona.set_enabled(conn, True)
            return material
        with mock.patch.object(growth, '_sample', side_effect=toggle_during_read):
            growth.tick(self.manager, self.conn, NOW)
            self.finish()
        self.native.assert_not_called()
        self.assertEqual((self.view()['version'], self.view()['growth_error']), (0, None))

    def test_manual_operation_scopes_error_codes_and_replayed_success(self):
        service = operation_service(self, self.home)
        def op():
            return executor.human_call(service, 'persona.grow', {'session_id': self.sid}, project='demo')
        for code, action in [('persona_nothing_new', lambda: None),
                             ('persona_frozen', lambda: persona.freeze(self.conn, self.sid, True)),
                             ('persona_disabled', lambda: persona.set_enabled(self.conn, False))]:
            action()
            with self.assertRaises(OpError) as error:
                op()
            self.assertEqual(error.exception.code, code)
        persona.freeze(self.conn, self.sid, False)
        with mock.patch.object(store, 'now_iso', return_value=START):
            persona.set_enabled(self.conn, True)
        self.message()
        args = {'session_id': self.sid}
        result = executor.human_call(service, 'persona.grow', args, request_id='manual')
        self.finish()
        self.assertEqual(result, {'started': True})
        self.assertEqual(executor.human_call(service, 'persona.grow', args, request_id='manual'),
                         {'started': True, 'replayed': True})
        self.native.assert_called_once()

    def test_counter_without_material_and_frozen_or_disabled_auto(self):
        self.count(4)
        persona.freeze(self.conn, self.sid, True)
        growth.tick(self.manager, self.conn, NOW)
        persona.freeze(self.conn, self.sid, False)
        persona.set_enabled(self.conn, False)
        growth.tick(self.manager, self.conn, NOW)
        self.assertEqual(self.jobs, [])
        persona.set_enabled(self.conn, True)
        growth.tick(self.manager, self.conn, NOW)
        self.finish()
        self.assertEqual((self.view()['pending_turns'], self.view()['growth_error']), (0, growth.NO_MATERIAL))
        self.native.assert_not_called()

    def test_close_kills_only_owned_process_and_joins_worker(self):
        self.message()
        spawned, killed = threading.Event(), threading.Event()
        process = mock.Mock()
        process.poll.return_value = None
        process.kill.side_effect = killed.set
        self.manager.process_factory = mock.Mock(return_value=process)
        def wait_for_cancel(cwd, text, spawn, choice, *, receipt):
            spawn(['fake-claude'])
            spawned.set()
            if not killed.wait(5):
                raise AssertionError('owned process was not stopped')
            raise RuntimeError('killed')
        self.native.side_effect = wait_for_cancel
        growth.grow(self.manager, self.conn, self.sid)
        self.assertTrue(spawned.wait(5))
        growth.stop(mock.Mock())
        process.kill.assert_not_called()
        growth.close(self.manager)
        self.assertFalse(self.jobs[-1].is_alive())
        process.kill.assert_called_once()
        self.assertEqual((self.view()['version'], self.view()['growing']), (0, False))
        self.assertEqual(self.view()['growth_error'], growth.CANCELLED)
