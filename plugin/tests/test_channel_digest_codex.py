"""Real JSONL subprocesses and temporary SQLite; no real model calls."""
import tests
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest import mock

from crew import board, channel, channel_digest, settings
import test_channel
from test_channel_digest import CARD


SERVER = r'''
import json,os,sys
from pathlib import Path
mode=os.environ['DIGEST_TEST_MODE']
log=Path(os.environ['DIGEST_TEST_LOG'])
card=os.environ['DIGEST_TEST_CARD']
def send(value):print(json.dumps(value),flush=True)
def event(method,**params):send({'method':method,'params':params})
def reply(message,result):send({'id':message['id'],'result':result})
def finish(status='completed',error=None):
    event('item/agentMessage/delta',threadId='digest-thread',turnId='digest-turn',itemId='answer',delta='intermediate output is not the final card')
    event('item/completed',threadId='digest-thread',turnId='digest-turn',item={'id':'answer','type':'agentMessage','text':card,'phase':'final_answer'})
    # Empty final items reproduce the real app-server notification contract.
    event('turn/completed',threadId='digest-thread',turn={'id':'digest-turn','status':status,'items':[],'error':error})
for line in sys.stdin:
    message=json.loads(line)
    with log.open('a',encoding='utf-8') as stream:stream.write(json.dumps(message)+'\n')
    if 'method' not in message:
        finish();continue
    method=message['method'];params=message.get('params',{})
    if method=='initialize':reply(message,{'userAgent':'fixture-codex'})
    elif method=='initialized':pass
    elif method=='config/read':reply(message,{'config':{'mcp_servers':{'fixture-side-effect-server':{'command':'must-not-run'}}}})
    elif method=='thread/start':
        result={'thread':{'id':'digest-thread','turns':[],'ephemeral':True},'model':'resolved-native-default','sandbox':{'type':'readOnly'}}
        if mode=='missing-model':result.pop('model')
        reply(message,result)
    elif method=='turn/start':
        if mode=='early':finish()
        reply(message,{'turn':{'id':'digest-turn','status':'inProgress','items':[]}})
        if mode=='early':continue
        if mode=='hang':continue
        if mode=='transport':raise SystemExit(7)
        if mode=='action':
            send({'id':'forbidden-tool','method':'item/tool/call','params':{'threadId':'digest-thread','turnId':'digest-turn','tool':'must_not_execute','arguments':{}}})
        elif mode=='approval':
            send({'id':'forbidden-approval','method':'item/commandExecution/requestApproval','params':{'threadId':'digest-thread','turnId':'digest-turn','command':'must-not-run'}})
        elif mode=='native-action':
            event('item/started',threadId='digest-thread',turnId='digest-turn',item={'id':'cmd','type':'commandExecution','command':'must-not-run'})
        elif mode=='failed':finish('failed',{'message':'fixture quota rejected'})
        else:finish()
'''


class CodexDigestTests(unittest.TestCase):
    setUp = test_channel.ChannelTests.setUp
    post = test_channel.ChannelTests.post

    def manager(self, mode='ok', card=CARD):
        settings.save(self.conn, {'default': {'runtime': 'codex', 'model': None, 'effort': None}})
        self.mode, self.card = mode, card
        self.log = self.base / 'digest-protocol.jsonl'
        self.processes, self.options = [], []
        def factory(command, **options):
            self.assertEqual(command[-3:], ['app-server', '--listen', 'stdio://'])
            self.options.append(options)
            options = {**options, 'env': {**options['env'], 'DIGEST_TEST_MODE': self.mode,
                                         'DIGEST_TEST_LOG': str(self.log), 'DIGEST_TEST_CARD': self.card,
                                         'PYTHONIOENCODING': 'utf-8'}}
            process = subprocess.Popen([sys.executable, '-u', '-c', SERVER], **options)
            self.processes.append(process)
            return process
        self.factory = mock.Mock(side_effect=factory)
        discovery = mock.patch('crew.runtimes.shutil.which',
                               side_effect=lambda name, **options: 'fixture-codex.exe' if name == 'codex' else None)
        no_claude = mock.patch('crew.channel_digest.chat.claude_executable',
                               side_effect=AssertionError('No Claude is installed'))
        discovery.start(); no_claude.start()
        self.addCleanup(discovery.stop); self.addCleanup(no_claude.stop)
        manager = channel_digest.Manager(self.home, threading.Lock(), self.factory)
        self.addCleanup(manager.close)
        return manager

    def finish(self, manager):
        for worker in manager.workers.values():
            worker.join(5)
            self.assertFalse(worker.is_alive())
        self.assertFalse(manager.state('demo')['digesting'])
        for process in self.processes:
            self.assertIsNotNone(process.poll(), 'independent app-server was not cleaned up')
        for options in self.options:
            self.assertFalse(Path(options['cwd']).exists(), 'ephemeral digest cwd was not removed')

    def calls(self):
        return [json.loads(line) for line in self.log.read_text(encoding='utf-8').splitlines()]

    def test_codex_only_manual_then_auto_cumulative_cards_preserve_native_metadata(self):
        first = self.post('第一条完整发言\n不能截断')
        counts = {table: self.conn.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                  for table in ('sessions', 'groups', 'goals', 'session_runtimes')}
        manager = self.manager()
        with mock.patch.dict(os.environ, {'CREW_SESSION_ID': 'must-not-inherit', 'CREW_GOAL_ID': 'must-not-inherit'}):
            self.assertTrue(manager.start(self.conn, 'demo'))
            self.finish(manager)
        saved = channel.digest(self.conn, 'demo')
        self.assertEqual((saved['body'], saved['runtime'], saved['model'], saved['cost_usd']),
                         (CARD, 'codex', 'resolved-native-default', None))
        self.assertEqual(saved['upto_message_id'], first['id'])
        for index in range(19):self.post('新发言 ' + str(index))
        self.assertFalse(manager.start(self.conn, 'demo', trigger='auto'))
        self.assertEqual(self.factory.call_count, 1)
        self.post('第20条新发言')
        self.assertTrue(manager.start(self.conn, 'demo', trigger='auto'))
        self.finish(manager)
        saved = channel.digest(self.conn, 'demo')
        self.assertEqual((saved['trigger'], saved['message_count'], saved['total_message_count']), ('auto', 20, 21))
        turns = [call['params'] for call in self.calls() if call.get('method') == 'turn/start']
        self.assertIn('第一条完整发言\\n不能截断', turns[0]['input'][0]['text'])
        self.assertIn(CARD, turns[1]['input'][0]['text'])
        self.assertNotIn('第一条完整发言', turns[1]['input'][0]['text'])
        self.assertIn('第20条新发言', turns[1]['input'][0]['text'])
        for table, count in counts.items():
            self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], count)
        for options in self.options:
            self.assertFalse(any(key.startswith('CREW_') for key in options['env']))

    def test_native_thread_is_ephemeral_readonly_without_external_execution_or_requested_model(self):
        self.post()
        manager = self.manager()
        manager.start(self.conn, 'demo'); self.finish(manager)
        start = next(call['params'] for call in self.calls() if call.get('method') == 'thread/start')
        self.assertTrue(start['ephemeral'])
        self.assertEqual(start['sandbox'], 'read-only')
        self.assertEqual(start['approvalPolicy'], 'never')
        self.assertEqual(start['dynamicTools'], [])
        self.assertEqual(start['environments'], [])
        self.assertNotIn('model', start)
        self.assertEqual(start['config']['web_search'], 'disabled')
        self.assertEqual(start['config']['mcp_servers']['fixture-side-effect-server']['enabled'], False)
        self.assertTrue(all(value is False for value in start['config']['features'].values()))
        self.assertNotEqual(Path(start['cwd']), self.home)
        self.assertFalse(any(call.get('method') == 'thread/resume' for call in self.calls()))

    def test_saving_explicit_model_effort_starts_no_digest_and_native_model_remains_authoritative(self):
        self.post()
        manager = self.manager()
        ai = settings.get(self.conn)
        ai['digest'] = {'runtime': 'codex', 'model': 'requested-native-alias', 'effort': 'requested-effort'}
        settings.save(self.conn, ai)
        self.factory.assert_not_called()
        self.assertEqual(manager.state('demo'), {'digesting': False, 'digest_error': None})
        manager.start(self.conn, 'demo'); self.finish(manager)
        start = next(call['params'] for call in self.calls() if call.get('method') == 'thread/start')
        self.assertEqual(start['model'], 'requested-native-alias')
        self.assertEqual(start['config']['model_reasoning_effort'], 'requested-effort')
        saved = channel.digest(self.conn, 'demo')
        self.assertEqual(saved['model'], 'resolved-native-default')
        self.assertNotEqual(saved['model'], start['model'])
        self.assertEqual(saved['runtime'], 'codex')
        self.assertIsNone(saved['cost_usd'])

    def test_final_notification_before_turn_reply_is_not_lost(self):
        self.post()
        manager = self.manager(mode='early')
        manager.start(self.conn, 'demo'); self.finish(manager)
        self.assertEqual(channel.digest(self.conn, 'demo')['body'], CARD)
        self.assertIsNone(manager.state('demo')['digest_error'])

    def test_failed_and_closed_native_turns_preserve_previous_card_and_surface_reason(self):
        self.post('original')
        manager = self.manager()
        manager.start(self.conn, 'demo'); self.finish(manager)
        saved = channel.digest(self.conn, 'demo')
        self.post('later evidence')
        for mode, reason in (('failed', 'TurnError: Codex turn failed: fixture quota rejected'),
                             ('transport', 'TransportError:'), ('missing-model', '未返回实际模型')):
            with self.subTest(mode=mode):
                self.mode = mode
                manager.start(self.conn, 'demo'); self.finish(manager)
                self.assertEqual(channel.digest(self.conn, 'demo'), saved)
                self.assertIn(reason, manager.state('demo')['digest_error'])

    def test_timeout_cleans_up_native_process_and_keeps_previous_card(self):
        self.post()
        manager = self.manager(mode='hang')
        with mock.patch.object(channel_digest, 'DIGEST_TIMEOUT', .15):
            manager.start(self.conn, 'demo'); self.finish(manager)
        self.assertIsNone(channel.digest(self.conn, 'demo'))
        self.assertIn('TimeoutError:', manager.state('demo')['digest_error'])
        self.assertIn('秒超时', manager.state('demo')['digest_error'])

    def test_native_actions_and_approval_requests_fail_instead_of_publishing_card(self):
        self.post()
        manager = self.manager()
        for mode, reason in (('action', '请求了工具'), ('approval', '请求了授权'), ('native-action', '请求了动作')):
            with self.subTest(mode=mode):
                self.mode = mode
                manager.start(self.conn, 'demo'); self.finish(manager)
                self.assertIsNone(channel.digest(self.conn, 'demo'))
                self.assertIn(reason, manager.state('demo')['digest_error'])
        replies = {call['id']: call['result'] for call in self.calls() if 'method' not in call and 'result' in call}
        self.assertFalse(replies['forbidden-tool']['success'])
        self.assertEqual(replies['forbidden-approval']['decision'], 'decline')

    def test_fixed_markdown_headings_remain_required(self):
        self.post()
        manager = self.manager(card='请提供上一张卡')
        manager.start(self.conn, 'demo'); self.finish(manager)
        self.assertIsNone(channel.digest(self.conn, 'demo'))
        self.assertIn('输出缺少固定小标题', manager.state('demo')['digest_error'])

    def test_state_reads_and_subthreshold_auto_checks_start_no_native_process(self):
        manager = self.manager()
        for index in range(19):self.post(str(index))
        for _ in range(5):
            self.assertEqual(manager.state('demo'), {'digesting': False, 'digest_error': None})
            self.assertFalse(manager.start(self.conn, 'demo', trigger='auto'))
            self.assertIsNone(channel.digest(self.conn, 'demo'))
        self.factory.assert_not_called()

    def test_no_installed_cli_is_explicit_and_never_falls_back_to_a_process(self):
        self.post()
        manager = self.manager()
        settings.save(self.conn, {'default': {'runtime': None, 'model': None, 'effort': None}})
        with mock.patch('crew.runtimes.shutil.which', return_value=None):
            manager.start(self.conn, 'demo'); self.finish(manager)
        self.factory.assert_not_called()
        self.assertIn('请先安装并登录', manager.state('demo')['digest_error'])


if __name__ == '__main__':unittest.main()
