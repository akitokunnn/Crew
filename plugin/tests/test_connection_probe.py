"""Explicit reminder checks use real send adapters with only provider HTTP fake."""
import tests
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock
from urllib.error import HTTPError

from crew import connection_import, connection_probe, lark, qq
from test_connection_import import legacy


class ConnectionProbeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='crew-probe-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        source = self.root / 'old'
        self.home, self.receipt = self.root / 'new', self.root / 'receipt.json'
        legacy(source)
        plan = connection_import.preview(source, self.home)
        connection_import.apply(source, self.home, plan['fingerprint'])

    def test_real_adapters_send_one_text_to_each_saved_owner_and_receipt_prevents_replay(self):
        qq_requests, lark_requests = [], []
        def qq_api(_client, method, route, body):
            qq_requests.append((method, route, body))
            return {'id': 'qq-provider-message-1'}
        def lark_api(_client, route, body):
            lark_requests.append((route, body))
            return {'data': {'message_id': 'lark-provider-message-1'}}
        with mock.patch.object(qq.Client, '_api', qq_api), mock.patch.object(lark.Client, '_api', lark_api), \
             mock.patch.object(qq.Client, 'run', side_effect=AssertionError('No receiver')), \
             mock.patch.object(lark.Client, 'run', side_effect=AssertionError('No receiver')), \
             mock.patch('crew.store.connect', side_effect=AssertionError('No database')):
            result = connection_probe.send_once(self.home, self.receipt)
            repeated = connection_probe.send_once(self.home, self.receipt)
        self.assertEqual(len(qq_requests), 1)
        self.assertEqual(qq_requests[0][0], 'POST')
        self.assertIn('/qq-private-recipient/messages', qq_requests[0][1])
        self.assertEqual(qq_requests[0][2]['content'], connection_probe.TEXT)
        self.assertEqual(len(lark_requests), 1)
        self.assertEqual(lark_requests[0][1]['receive_id'], 'lark-private-recipient')
        self.assertEqual(json.loads(lark_requests[0][1]['content'])['text'], connection_probe.TEXT)
        self.assertEqual([row['provider_message_id'] for row in result['results']], ['qq-provider-message-1', 'lark-provider-message-1'])
        self.assertTrue(repeated['reused'])
        self.assertNotIn('private-recipient', self.receipt.read_text(encoding='utf-8'))
        self.assertNotIn('very-private-secret', self.receipt.read_text(encoding='utf-8'))

    def test_one_channel_unknown_does_not_repeat_or_prevent_other_channel_ack(self):
        with mock.patch.object(qq.Client, 'send_text', side_effect=qq.QQError('lost response qq-private-recipient qq-very-private-secret', kind='network')) as qq_send, \
             mock.patch.object(lark.Client, 'send_text', return_value={'id': 'lark-ack'}) as lark_send:
            result = connection_probe.send_once(self.home, self.receipt)
            connection_probe.send_once(self.home, self.receipt)
        self.assertEqual(qq_send.call_count, 1)
        self.assertEqual(lark_send.call_count, 1)
        self.assertEqual([row['status'] for row in result['results']], ['unknown', 'accepted'])
        self.assertIn('lost response', result['results'][0]['error'])
        self.assertNotIn('private-recipient', self.receipt.read_text(encoding='utf-8'))
        self.assertNotIn('very-private-secret', self.receipt.read_text(encoding='utf-8'))

    def test_cli_requires_explicit_send_and_preflights_both_before_any_delivery(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(qq.Client, 'send_text', side_effect=AssertionError('No authorization')):
            with redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(connection_probe.main(['--home', str(self.home), '--receipt', str(self.receipt)]), 1)
        self.assertFalse(self.receipt.exists())
        (self.home / 'lark-notify.json').write_text('{"owner_openid":null,"manual_away":false}')
        with mock.patch.object(qq.Client, 'send_text', side_effect=AssertionError('Preflight must run before delivery')):
            with self.assertRaisesRegex(ValueError, '未启用或尚未绑定'):
                connection_probe.send_once(self.home, self.receipt)
        self.assertFalse(self.receipt.exists())

    def test_pending_or_corrupt_receipt_never_replays_a_possible_prior_send(self):
        for content in ('{incomplete', json.dumps({'home': str(self.home), 'channels': ['qq', 'lark'], 'results': [{'channel': 'qq', 'status': 'sending'}]})):
            with self.subTest(content=content):
                self.receipt.write_text(content)
                with mock.patch.object(qq.Client, 'send_text', side_effect=AssertionError('Never replay')):
                    if content.startswith('{incomplete'):
                        with self.assertRaisesRegex(ValueError, '避免重复发送'):
                            connection_probe.send_once(self.home, self.receipt)
                    else:
                        self.assertTrue(connection_probe.send_once(self.home, self.receipt)['reused'])

    def test_missing_provider_id_is_unknown_instead_of_claiming_success(self):
        with mock.patch.object(qq.Client, 'send_text', return_value={}), mock.patch.object(lark.Client, 'send_text', return_value={'id': 'ok'}):
            result = connection_probe.send_once(self.home, self.receipt)
        self.assertEqual([row['status'] for row in result['results']], ['unknown', 'accepted'])

    def test_http_200_malformed_provider_response_is_unknown_and_never_replayed(self):
        client = qq.Client('qq-old-app', 'qq-very-private-secret')
        client._token_value, client._token_expires = 'isolated-token', time.monotonic() + 3600
        response = io.BytesIO(b'not a JSON response')
        response.status = 200
        with mock.patch.object(client._opener, 'open', return_value=response) as request:
            result = connection_probe.send_once(self.home, self.receipt, channels=('qq',), clients={'qq': client})
            repeated = connection_probe.send_once(self.home, self.receipt, channels=('qq',), clients={'qq': client})
        self.assertEqual(request.call_count, 1)
        self.assertEqual(result['results'][0]['status'], 'unknown')
        self.assertIn('invalid JSON response', result['results'][0]['error'])
        self.assertTrue(repeated['reused'])

    def test_explicit_http_rejection_is_distinct_from_uncertain_server_error(self):
        for status, payload, expected in ((403, b'{}', 'rejected'), (500, b'{}', 'unknown'),
                                          (200, b'{"code":40054013,"message":"refused"}', 'rejected')):
            with self.subTest(status=status):
                client = qq.Client('qq-old-app', 'qq-very-private-secret')
                client._token_value, client._token_expires = 'isolated-token', time.monotonic() + 3600
                if status == 200:
                    response = io.BytesIO(payload)
                    response.status = status
                    fake = {'return_value': response}
                else:
                    fake = {'side_effect': HTTPError('https://fixture.invalid/', status, 'fixture', {}, io.BytesIO(payload))}
                with mock.patch.object(client._opener, 'open', **fake):
                    result = connection_probe.send_once(self.home, self.root / ('receipt-' + str(status) + '.json'),
                                                        channels=('qq',), clients={'qq': client})
                self.assertEqual(result['results'][0]['status'], expected)


if __name__ == '__main__':
    unittest.main()
