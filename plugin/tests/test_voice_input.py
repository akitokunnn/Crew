"""Voice input and user approval through the same entry points as a typed turn."""
import tests
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stderr
import hashlib
import io
import json
import threading
import unittest
from unittest import mock

from crew import hooks, store, war_room, web
from crew.ops import executor
from crew.ops.errors import OpError
from ops_helpers import ai_call
import test_batch_contracts
from test_shell import PLUGIN, ShellFixture


class VoiceInputTests(ShellFixture, unittest.TestCase):
    request = test_batch_contracts.BatchContractTests.request
    close_server = test_batch_contracts.BatchContractTests.close_server

    def setUp(self):
        super().setUp()
        # The shared fixture starts a real HTTP service with fake native carriers.
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project, agents_provider=lambda: [],
                                        claude_root=self.claude, codex_root=self.codex, runs_file=self.runs,
                                        process_factory=self.process_factory)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.service = self.server.service
        self.assertTrue(self.service.agents_ready.wait(5))
        self.url = 'http://127.0.0.1:%d' % self.server.server_port
        self.sid = test_batch_contracts.BatchContractTests.workspace_assistant(self)

    def send(self, identity, mode='voice', *, text='记下这个想法', **extra):
        return self.request('/api/op/chat.send', dict(project='demo', session_id=self.sid, text=text,
                            input_mode=mode, message_id=identity, **extra))[:2]

    def act(self, name, args, **envelope):
        return ai_call(self.service, self.conn, self.sid, name, args, **envelope)

    def test_heard_uses_latest_readable_reply_and_unicode_code_points(self):
        self.assertEqual(self.send('first', 'text')[0], 200)
        journal = self.service.chat.journal(self.sid)
        journal.emit('text', id='old', text='旧回复')
        reply = '🌸中A' * 12 + '。'
        journal.emit('text', id='reply', text='被快照替换')
        journal.emit('assistant', id='reply', content=[{'type':'text', 'text':reply[:-1]}])
        journal.emit('text', id='reply', text=reply[-1])
        journal.emit('assistant', id='tool', content=[{'type':'tool_use', 'name':'Read', 'id':'read', 'input':{}}])
        journal.emit('text', id='room', text='房内回复', room_id=7)
        for bad in [dict(reply_id='old', chars=1), dict(reply_id='room', chars=0),
                    dict(reply_id='reply', chars=len(reply) + 1), dict(reply_id='reply', chars=-1)]:
            with self.subTest(bad=bad):
                code, error = self.send('bad', heard=bad)
                self.assertEqual((code, error['code']), (400, 'invalid_arguments'))
        for chars in [0, 1, len(reply)]:
            with self.subTest(chars=chars):
                identity = 'said-%s' % chars
                heard = dict(reply_id='reply', chars=chars)
                with mock.patch('crew.war_room.main_input_note', return_value='作战室说明'), \
                     mock.patch('crew.persona.input_frame', return_value='人格说明'):
                    self.assertEqual(self.send(identity, heard=heard)[0], 200)
                prompt = json.loads(self.processes[0].stdin.getvalue().splitlines()[-1])['message']['content']
                self.assertLess(prompt.index('作战室说明'), prompt.index('人格说明'))
                self.assertLess(prompt.index('人格说明'), prompt.index('这条是语音转写'))
                self.assertIn('前 %d 个字：“%s”' % (chars, reply[:chars][-30:]), prompt)
                self.assertTrue(prompt.endswith('记下这个想法'))
                message, = [e for e in journal.events if e['type'] == 'message' and e['id'] == identity]
                self.assertEqual(message['text'], '记下这个想法')
                submitted, = [e for e in journal.events if e['type'] == 'input_delivery'
                              and e['status'] == 'submitted' and e['message_id'] == identity]
                data = dict(text=message['text'], images=[], source='human', input_mode='voice')
                self.assertEqual(submitted['digest'], hashlib.sha256(
                    json.dumps(data, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest())
        journal.emit('text', id='new', text='更新的一条回复')
        code, receipt = self.send(identity, heard=heard)
        self.assertEqual(code, 200, receipt)
        self.assertTrue(receipt['duplicate'])

    def test_text_proof_precedes_voice_and_voice_reads_are_immediate(self):
        self.assertEqual(self.send('typed', 'text')[0], 200)
        self.assertEqual(self.send('spoken')[0], 200)
        result = self.act('note.capture', {'data':{'text':'文字授权'}}, message_id='typed')
        self.assertEqual(result['result']['note']['source']['message_id'], 'typed')
        self.assertEqual([n['text'] for n in self.act('notes.list', {})['notes']], ['文字授权'])
        self.assertEqual(executor.human_call(self.service, 'interaction.list')['requests'], [])

    def test_unbound_write_stays_on_card_after_new_text_and_approval_rechecks_original(self):
        self.assertEqual(self.send('spoken')[0], 200)
        room = war_room.start(self.conn, 'demo', self.sid, '排期', '排好时间', [])
        args = {'room': room['id'], 'body': '周二再讨论'}
        card = self.act('war_room.post', args, request_id='post')
        self.assertEqual(card['status'], 'awaiting_user')
        self.assertEqual(self.send('typed', 'text')[0], 200)
        self.assertEqual(self.act('war_room.post', args, request_id='post'), card)
        listed, = executor.human_call(self.service, 'interaction.list')['requests']
        self.assertEqual(listed['message_id'], 'spoken')
        self.assertIn('排期', listed['summary'])
        with self.assertRaises(OpError) as mismatch:
            self.act('war_room.post', args, request_id='post', message_id='typed')
        self.assertEqual(mismatch.exception.code, 'request_id_reused')
        # Remove the authorizing message from this test journal. A newer typed message cannot replace it.
        journal = self.service.chat.journal(self.sid)
        events = [e for e in journal.events if e.get('id') != 'spoken']
        journal.path.write_text(''.join(json.dumps(e) + '\n' for e in events), encoding='utf-8')
        failed = executor.human_call(self.service, 'interaction.resolve', {'id':listed['id'], 'approve':True})
        self.assertEqual(failed['status'], 'failed')
        self.assertIn('原始消息', failed['error'])

    def test_spoken_yes_cannot_resolve_or_duplicate_the_pending_request(self):
        self.assertEqual(self.send('first')[0], 200)
        args = {'data':{'text':'周五看结果'}}
        card = self.act('note.capture', args, request_id='friday', message_id='first')
        self.assertEqual(self.send('second', text='确认')[0], 200)
        with self.assertRaises(OpError) as denied:
            self.act('interaction.resolve', {'id':card['confirmation_id'], 'approve':True}, message_id='second')
        self.assertEqual(denied.exception.code, 'forbidden')
        replay = self.act('note.capture', args, request_id='friday', message_id='first')
        self.assertEqual(card, replay)
        self.assertEqual(self.act('notes.list', {})['notes'], [])

    def test_room_summary_names_people_and_keeps_ids_out(self):
        self.assertEqual(self.send('spoken')[0], 200)
        self.act('war_room.open', {'title':'排期', 'goal':'定好日期', 'members':['s3']}, message_id='spoken')
        card, = executor.human_call(self.service, 'interaction.list')['requests']
        for text in ('demo', '排期', '定好日期', 'lead'):
            self.assertIn(text, card['summary'])
        for text in ('s3', self.sid, 'members', 'goal', 'project'):
            self.assertNotIn(text, card['summary'])

    def test_cc_hook_uses_last_accepted_human_input(self):
        def check(tool, sid=None):
            with redirect_stderr(io.StringIO()) as reason:
                code, _ = hooks.handle('guard', dict(session_id=sid or self.sid, cwd=str(self.project), tool_name=tool,
                    tool_input={'file_path':str(self.project / 'note.txt'), 'command':'pwd'}), self.home, PLUGIN)
            return code, reason.getvalue()

        self.assertEqual(self.send('spoken')[0], 200)
        journal = self.service.chat.journal(self.sid)
        journal.message('notice', '系统通知', source='crew')
        journal.emit('input_delivery', message_id='notice', status='accepted')
        journal.emit('input_delivery', message_id='not-delivered', status='submitted')
        journal.message('not-delivered', '未投递')
        journal.emit('input_delivery', message_id='not-delivered', status='rejected')
        for tool in ['Write', 'Edit', 'MultiEdit', 'NotebookEdit']:
            code, reason = check(tool)
            self.assertEqual(code, 2)
            self.assertIn('等用户在屏幕上点确认', reason)
        self.assertEqual(check('Read')[0], 0)
        self.assertEqual(check('Bash')[0], 0)
        self.assertEqual(check('Write', 's3')[0], 0)
        self.assertEqual(self.send('typed', 'text')[0], 200)
        for tool in ['Write', 'Edit', 'MultiEdit', 'NotebookEdit']:
            self.assertEqual(check(tool)[0], 0)

    def test_legacy_confirmation_migration_is_idempotent(self):
        import sqlite3
        old_home = self.root / 'old-home'
        old_home.mkdir()
        with closing(sqlite3.connect(store.db_path(old_home))) as conn:
            ddl = self.conn.execute("SELECT sql FROM sqlite_master WHERE name='interaction_requests'").fetchone()[0]
            conn.execute(ddl.replace(' message_id TEXT,', '').replace(' summary TEXT,', ''))
            self.assertNotIn('message_id', [r[1] for r in conn.execute('PRAGMA table_info(interaction_requests)')])
            conn.execute("INSERT INTO interaction_requests(id,kind,caller_key,request_id,session_id,role,op,args,"
                         "args_hash,status,created_at) VALUES('old','confirmation','s','r','s','leader','test','{}',"
                         "'digest','pending','then')")
            conn.commit()
        for _ in range(2):
            with closing(store.connect(old_home)) as conn:
                row = conn.execute('SELECT status,message_id,summary FROM interaction_requests').fetchone()
                self.assertEqual(tuple(row), ('pending', None, None))

    def test_voice_proof_and_approve_once_after_typed_interruption(self):
        self.assertEqual(self.send('spoken')[0], 200)
        self.assertEqual(self.send('typed', 'text')[0], 200)
        args = {'data': {'text': '买花'}}
        card = self.act('note.capture', args, message_id='spoken', request_id='buy-flowers')
        self.assertEqual(card.get('status'), 'awaiting_user', card)
        self.assertEqual(self.act('notes.list', {})['notes'], [])
        pending, = executor.human_call(self.service, 'interaction.list', {'project': 'demo'})['requests']
        self.assertEqual(pending['message_id'], 'spoken')
        self.assertIn('买花', pending['summary'])
        self.assertIn('demo', pending['summary'])
        self.assertNotIn(self.sid, pending['summary'])
        barrier = threading.Barrier(2)

        def approve():
            barrier.wait(timeout=5)
            try:
                result = executor.human_call(self.service, 'interaction.resolve',
                                             {'project':'demo', 'id':card['confirmation_id'], 'approve':True})
                return result['status']
            except OpError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(approve) for _ in range(2)]
            results = [future.result() for future in futures]
        self.assertEqual(results.count('approved'), 1, results)
        self.assertTrue(set(results) <= {'approved', 'request_conflict', 'conflict'})
        with self.assertRaises(OpError):
            executor.human_call(self.service, 'interaction.resolve', {'id':card['confirmation_id'], 'approve':True})
        notes = self.act('notes.list', {})['notes']
        self.assertEqual([note['text'] for note in notes], ['买花'])
