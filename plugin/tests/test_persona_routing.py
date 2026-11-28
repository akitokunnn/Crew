"""Personality routing through the native start, resume and send entry points."""
import tests
import json
import subprocess
import threading
import unittest
from pathlib import Path
from unittest import mock

from crew import behavior_prompts, board, change_feed, channel, channel_digest, chat, codex_client, goals, operations_chat
from crew import persona, personal, runtimes, settings, store, war_room_plan, workspace_assistant
from test_chat import FakeProcess
import test_codex_runtime as fixtures
from test_message_delivery import DeliveryFixture
import test_war_room_plan as room_fixtures

MARK = '这是一段私聊专属口吻'
PLUGIN = Path(chat.__file__).resolve().parents[1]


class RoutingTests(unittest.TestCase):
    make_running = fixtures.CodexRuntimeTests.make_running

    def setUp(self):
        fixtures.CodexRuntimeTests.setUp(self)
        def client_factory(**kwargs):
            client = fixtures.FakeClient(**kwargs)
            client.thread_id = 'test-native-%d' % len(self.clients)
            self.clients.append(client)
            return client
        fixtures.codex_runtime._ObservedClient.side_effect = client_factory
        self.conn = store.connect(self.home)
        self.addCleanup(self.conn.close)
        board.join(self.conn, 'worker', 'demo', 'worker', self.root, None, 'main', role='leader')
        self.session = board.session(self.conn, 'worker')
        persona.set_enabled(self.conn, True)
        persona.save(self.conn, 'worker', MARK, 0, board.HUMAN)
        self.manager = chat.Manager(self.home, PLUGIN, lambda: [], process_factory=FakeProcess)
        self.addCleanup(self.manager.close)
        executable = mock.patch('crew.chat.claude_executable', return_value='fixture-claude')
        executable.start()
        self.addCleanup(executable.stop)

    def cc(self, resume=False, session=None):
        session = session or self.session
        sid = session['session_id']
        runtimes.set(self.conn, sid, 'claude', native_id=sid)
        self.manager.journal(sid).set_status('idle')
        spec, _ = self.manager._resume_spec(self.conn, session)
        self.manager._launch_native(session, spec, resume=resume)
        return self.manager.processes[sid]

    def last_cc(self, current):
        lines = map(json.loads, current.process.stdin.getvalue().splitlines())
        return [line['message']['content'] for line in lines if line['type'] == 'user'][-1]

    def test_cc_long_first_start_and_resume_use_one_utf8_file(self):
        long = 'ab ' * 19900
        self.assertEqual(persona.estimate(long), 19900)
        persona.save(self.conn, 'worker', long, 1, board.HUMAN)
        expected = behavior_prompts.text(self.conn, 'persona_frame', session_id='worker', values={'persona': long})
        for resume in (False, True):
            current = self.cc(resume)
            args = current.process.command
            self.assertEqual('--resume' in args, resume)
            self.assertEqual(args.count('--append-system-prompt'), 1)
            self.assertEqual(args.count('--append-system-prompt-file'), 1)
            path = Path(args[args.index('--append-system-prompt-file') + 1])
            self.assertEqual(path, self.home / 'chat' / 'persona' / 'worker.md')
            self.assertEqual(path.read_bytes(), expected.encode('utf-8'))
            command = subprocess.list2cmdline(args)
            self.assertNotIn(long, command)
            for item in [*args, command]:
                self.assertLess(len(item.encode('utf-16-le')) // 2 + 1, 32000)
            self.manager.stop_managed('worker')

    def test_cc_flush_failure_compact_and_one_shot_edit_and_off(self):
        current = self.cc()
        send = lambda text: self.manager.send(self.session, text)
        prior = current.persona_hash
        persona.save(self.conn, 'worker', '新口头禅', 1, board.HUMAN)
        with mock.patch.object(current.process.stdin, 'flush', side_effect=OSError('flush failed')):
            with self.assertRaisesRegex(OSError, 'flush failed'):
                send('失败')
        self.assertEqual(current.persona_hash, prior)
        send('/compact')
        self.assertEqual(self.last_cc(current), '/compact')
        self.assertEqual(current.persona_hash, prior)
        send('再试')
        self.assertIn('新口头禅', self.last_cc(current))
        send('继续')
        self.assertNotIn('新口头禅', self.last_cc(current))
        persona.set_enabled(self.conn, False)
        off = behavior_prompts.text(self.conn, 'persona_off', session_id='worker')
        send('关闭')
        self.assertIn(off, self.last_cc(current))
        send('普通')
        self.assertNotIn(off, self.last_cc(current))
        self.assertEqual(len(behavior_prompts.inspect_applied(self.conn, 'worker')['applications']), 1)
        self.assertNotIn('新口头禅', self.manager.path('worker').read_text(encoding='utf-8'))

    def test_codex_private_then_room_and_other_sources_keep_display_and_count(self):
        running, client = self.make_running()
        self.manager.processes['worker'] = running
        self.assertIn(MARK, client.calls[0][1]['developerInstructions'])
        for i, (source, room, expected) in enumerate((
                ('human', None, None), ('human', 3, persona.ROOM_FRAME), ('crew', 3, persona.ROOM_FRAME),
                ('crew', None, persona.INDIRECT_FRAME), ('peer', None, persona.INDIRECT_FRAME),
                ('qq', None, None), ('lark', None, None))):
            raw = '原话%d' % i
            args = dict(identity='id-%d' % i, room_id=room, input_mode='voice' if i == 0 else 'text')
            self.manager.send(self.session, raw, source, **args)
            if i == 0:
                self.assertTrue(self.manager.send(self.session, raw, source, **args)['duplicate'])
            native = [v for k, v in client.calls if k == 'turn/start'][-1]['input'][-1]['text']
            self.assertTrue(native.endswith(raw))
            self.assertEqual(persona.ROOM_FRAME in native, expected == persona.ROOM_FRAME)
            self.assertEqual(persona.INDIRECT_FRAME in native, expected == persona.INDIRECT_FRAME)
            client.finish()
        messages = [e['text'] for e in running.journal.after(0) if e['type'] == 'message']
        self.assertEqual(messages, ['原话%d' % i for i in range(7)])
        self.assertEqual(persona.get(self.conn, 'worker')['pending_turns'], 1)

    def test_codex_refusal_and_steer_commit_only_accepted_persona(self):
        running, client = self.make_running()
        self.manager.processes['worker'] = running
        original = running.persona_hash
        behavior_prompts.save(self.conn, 'persona_frame', '新框架 {{persona}}', expected_version=0)
        refused = codex_client.RpcError('turn/start', {'code': -1, 'message': 'start refused'})
        with mock.patch.object(client, 'start_turn', side_effect=refused), self.assertRaisesRegex(RuntimeError, 'start refused'):
            self.manager.send(self.session, '失败', identity='refused')
        self.assertEqual(running.persona_hash, original)
        self.assertEqual(self.manager.journal('worker').inputs['refused']['status'], 'rejected')
        with mock.patch.object(channel, 'preview', return_value=('发言', 1)), \
                mock.patch.object(channel, 'seen', side_effect=OSError('receipt failed')), \
                self.assertRaisesRegex(OSError, 'receipt failed'):
            self.manager.send(self.session, '回执失败', identity='unknown')
        self.assertEqual(self.manager.journal('worker').inputs['unknown']['status'], 'unknown')
        self.assertEqual(running.persona_hash, original)
        client.finish()
        self.manager.send(self.session, '新输入')
        self.assertIn('新框架 ' + MARK, [p for k, p in client.calls if k == 'turn/start'][-1]['input'][-1]['text'])
        self.manager.send(self.session, '继续')
        self.assertNotIn(MARK, [p for k, p in client.calls if k == 'turn/steer'][-1]['input'][-1]['text'])
        persona.reset(self.conn, 'worker', 'clear', 1, board.HUMAN)
        self.manager.send(self.session, '清空')
        off = behavior_prompts.text(self.conn, 'persona_off', session_id='worker')
        self.assertIn(off, [p for k, p in client.calls if k == 'turn/steer'][-1]['input'][-1]['text'])
        client.finish()

    def test_new_leader_first_crew_message_keeps_original_task(self):
        def seed(sid):
            persona.save(self.conn, sid, MARK, 0, board.HUMAN)
        self.manager.start(self.conn, 'demo', '新组长', '先看需求', role='leader', runtime='claude', _registered=seed)
        sid, current = next(iter(self.manager.processes.items()))
        self.assertIn(persona.INDIRECT_FRAME, self.last_cc(current))
        self.assertEqual([e['text'] for e in self.manager.journal(sid).after(0) if e['type'] == 'message'], ['先看需求'])

    def test_empty_disabled_ineligible_and_frozen(self):
        helper = workspace_assistant.ensure(self.conn, self.home, 'demo')['session_id']
        for source, room, identity in [('human', 1, 'room'), ('crew', None, 'notice'), ('qq', None, 'qq'),
                                       ('lark', None, 'lark'), ('human', None, 'decision-discuss:1'),
                                       ('human', None, 'personal:abcdef')]:
            persona.note_input(self.conn, helper, source, room, identity)
        self.assertEqual(persona.get(self.conn, helper)['pending_turns'], 0)
        self.assertIsNone(persona.input_frame(self.conn, helper, 'crew', 1, 'id'))
        persona.note_input(self.conn, helper, 'human', None, 'voice')
        self.assertEqual(persona.get(self.conn, helper)['version'], 0)
        persona.save(self.conn, helper, MARK, 0, board.HUMAN)
        persona.freeze(self.conn, helper, True)
        persona.note_input(self.conn, helper, 'human', None, 'next')
        self.assertIn(MARK, persona.injection(self.conn, helper)[1])
        persona.set_enabled(self.conn, False)
        persona.note_input(self.conn, helper, 'human', None, 'last')
        self.assertEqual(persona.get(self.conn, helper)['pending_turns'], 1)
        self.assertIsNone(persona.input_frame(self.conn, helper, 'crew', None, 'id'))
        current = self.cc()
        self.manager.send(self.session, '你好')
        self.assertNotIn(behavior_prompts.text(self.conn, 'persona_off'), self.last_cc(current))

    def test_member_git_and_admin_commands_exclude_persona(self):
        admin = personal.ensure_assistant(self.conn, self.home)['session_id']
        self.conn.execute('INSERT INTO git_management(project,enabled_at,base,admin_session_id) VALUES(?,?,?,?)',
                          ('demo', store.now_iso(), 'main', 'worker'))
        for sid in ('worker', 'receiver', admin):
            session = board.session(self.conn, sid)
            current = self.cc(session=session)
            self.assertNotIn('--append-system-prompt-file', current.process.command)
            self.assertNotIn(MARK, ' '.join(current.process.command))
            self.assertIsNone(persona.input_frame(self.conn, sid, 'crew', None, 'id'))
            self.manager.stop_managed(sid)
            runtimes.set(self.conn, sid, 'codex')
            self.manager._launch_native(session, {'agent': None})
            self.assertNotIn(MARK, self.clients[-1].calls[0][1]['developerInstructions'])
            self.manager.stop_managed(sid)
        self.assertNotIn(MARK, operations_chat.context(self.home, PLUGIN, []))

    def test_goal_and_delivered_discussion_exclude_persona_on_both_carriers(self):
        for runtime in ('claude', 'codex'):
            for discussion in (False, True):
                goal = goals.create(self.conn, 'demo', 'worker', '测试隔离', '交付', ['完成'])
                goals.assign(self.conn, goal['id'], 'worker', '检查')
                if discussion:
                    self.conn.execute("UPDATE goals SET status='awaiting_acceptance' WHERE id=?", (goal['id'],))
                execution = goals.create_execution(self.conn, goal['id'], 'worker', runtime=runtime,
                                                   discussion_message_id='讨论' if discussion else None)
                target = self.manager.goal_controller.target(self.conn, execution)
                args = self.manager._launch_native(target, {'agent': None})
                if runtime == 'claude':
                    self.assertNotIn('--append-system-prompt-file', args)
                    self.assertNotIn(MARK, ' '.join(args))
                else:
                    self.assertNotIn(MARK, self.clients[-1].calls[0][1]['developerInstructions'])
                self.manager.stop_managed(execution['id'])


class AdvisorTests(DeliveryFixture):
    def test_channel_digest_submits_channel_text_without_its_speakers_persona(self):
        persona.set_enabled(self.conn, True)
        persona.save(self.conn, 'a', MARK, 0, board.HUMAN)
        channel.post(self.conn, 'demo', 'a', '校验完成', [])
        manager = channel_digest.Manager(self.home, threading.Lock(), change_feed.Notifier())
        self.addCleanup(manager.close)
        with mock.patch.object(channel_digest, 'generate', return_value=('频道摘要', 0, 'fake', 'claude')) as native:
            self.assertTrue(manager.start(self.conn, 'demo'))
            manager.workers['demo'].join(5)
            self.assertFalse(manager.workers['demo'].is_alive())
        self.assertIsNone(manager.state('demo')['digest_error'])
        self.assertIn('校验完成', native.call_args.args[1])
        self.assertNotIn(MARK, native.call_args.args[1])

    def test_room_advisor_sees_only_question_and_plan(self):
        settings.save(self.conn, {'default': {'runtime': 'claude', 'model': None, 'effort': None}})
        persona.set_enabled(self.conn, True)
        persona.save(self.conn, 'a', MARK, 0, board.HUMAN)
        room = room_fixtures.PlanTests.open(self)
        room_fixtures.PlanTests.plan(self, room)
        with mock.patch('crew.channel_digest._claude', return_value=('意见', 0, 'fake')) as native:
            war_room_plan.consult(self.conn, room, 'a', 1, '问题', 'routine', '请检查')
        self.assertIn('请检查', native.call_args.args[1])
        self.assertNotIn(MARK, native.call_args.args[1])
