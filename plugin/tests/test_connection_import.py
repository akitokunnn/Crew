"""Connection migration reads JSON only; every HOME and channel is isolated."""
import tests
from contextlib import redirect_stdout, redirect_stderr
import concurrent.futures
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from crew import connection_import, connections, notifications, qq
from crew.service_lock import HomeServiceLock
from test_connections import ConnectionFixture


def write(home, name, value):
    home.mkdir(parents=True, exist_ok=True)
    (home / name).write_text(json.dumps(value), encoding='utf-8')


def legacy(home, *, away=False, target='both'):
    for channel, name in (('qq', 'notify.json'), ('lark', 'lark-notify.json')):
        value = {'app_id': channel + '-old-app', 'app_secret': channel + '-very-private-secret'}
        if channel == 'lark':
            value.update(domain='lark', enabled=True)
        write(home, channel + '.json', value)
        write(home, name, {'owner_openid': channel + '-private-recipient', 'manual_away': away,
                          'since': '2001-01-01T00:00:00Z', 'generation': 'old-delivery-generation',
                          'current_project': 'old-project', 'current_leader_session_id': 'old-leader',
                          'leader_numbers': {'old-leader': 1}, 'binding_code': 'OLD-CODE'})
    if target != 'both':
        write(home, 'notification-routing.json', {'target': target})
    (home / 'crew.db').write_bytes(b'Never open or copy this database')
    write(home, 'web.json', {'url': 'http://127.0.0.1:47811'})


class ConnectionImportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='crew-connection-import-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source, self.target = self.root / 'old', self.root / 'new'
        legacy(self.source)

    def apply(self):
        plan = connection_import.preview(self.source, self.target)
        return connection_import.apply(self.source, self.target, plan['fingerprint'])

    def test_preview_and_apply_are_database_free_redacted_and_source_unchanged(self):
        before = {p.name: p.read_bytes() for p in self.source.iterdir()}
        with mock.patch('crew.store.connect', side_effect=AssertionError('Configuration import must not open databases')):
            plan = connection_import.preview(self.source, self.target)
            self.assertFalse(self.target.exists(), 'Preview must not create the destination')
            self.assertTrue(plan['can_apply'])
            result = connection_import.apply(self.source, self.target, plan['fingerprint'])
        self.assertEqual({p.name: p.read_bytes() for p in self.source.iterdir()}, before)
        self.assertEqual(set(result['written']), set(connection_import.FILES))
        public = json.dumps([plan, result])
        for hidden in ('very-private-secret', 'private-recipient', 'old-app', 'OLD-CODE'):
            self.assertNotIn(hidden, public)
        for name in ('crew.db', 'web.json'):
            self.assertFalse((self.target / name).exists())
        for channel, filename in (('qq', 'notify.json'), ('lark', 'lark-notify.json')):
            credentials = json.loads((self.target / (channel + '.json')).read_text())
            self.assertEqual(credentials['app_secret'], channel + '-very-private-secret')
            self.assertTrue(credentials['enabled'])
            binding = json.loads((self.target / filename).read_text())
            self.assertEqual(binding['owner_openid'], channel + '-private-recipient')
            self.assertGreater(binding['since'], '2026-01-01')
            self.assertNotEqual(binding['generation'], 'old-delivery-generation')
            for removed in ('current_project', 'current_leader_session_id', 'leader_numbers'):
                self.assertNotIn(removed, binding)
            self.assertNotIn('binding_code', binding)

    def test_repeat_keeps_binding_generation_cursor_and_all_bytes(self):
        self.apply()
        before = {name: (self.target / name).read_bytes() for name in connection_import.FILES}
        result = self.apply()
        self.assertTrue(result['already_imported'])
        self.assertEqual(result['written'], [])
        self.assertEqual({name: (self.target / name).read_bytes() for name in connection_import.FILES}, before)

    def test_interrupted_publication_can_finish_without_replacing_published_files(self):
        original = connection_import.os.link
        calls = []
        def interrupt(source, destination):
            calls.append(destination)
            if len(calls) == 2:
                raise OSError('fixture interrupted publication')
            return original(source, destination)
        with mock.patch('crew.connection_import.os.link', side_effect=interrupt):
            with self.assertRaisesRegex(ValueError, '写入未完成'):
                self.apply()
        published = {name: (self.target / name).read_bytes() for name in connection_import.FILES if (self.target / name).exists()}
        self.assertEqual(len(published), 1)
        self.apply()
        self.assertEqual({name: (self.target / name).read_bytes() for name in published}, published)
        self.assertEqual(list(self.target.glob('.connection-import-*')), [])

    def test_existing_different_config_prevents_any_partial_import(self):
        write(self.target, 'qq.json', {'app_id': 'different-app', 'app_secret': 'different-secret', 'enabled': False})
        before = (self.target / 'qq.json').read_bytes()
        plan = connection_import.preview(self.source, self.target)
        self.assertFalse(plan['can_apply'])
        self.assertEqual(plan['conflicts'], ['qq.json'])
        with self.assertRaisesRegex(ValueError, '已有不同配置'):
            connection_import.apply(self.source, self.target, plan['fingerprint'])
        self.assertEqual((self.target / 'qq.json').read_bytes(), before)
        self.assertEqual([p.name for p in self.target.iterdir()], ['qq.json'])

    def test_lark_only_presence_conflict_preserves_existing_qq_owner_and_preferences(self):
        (self.source / 'qq.json').unlink()
        (self.source / 'notify.json').unlink()
        write(self.source, 'lark-notify.json', {'owner_openid': 'lark-owner', 'manual_away': True})
        write(self.target, 'notify.json', {'owner_openid': 'existing-qq-owner', 'manual_away': False})
        before = (self.target / 'notify.json').read_bytes()
        plan = connection_import.preview(self.source, self.target)
        self.assertFalse(plan['can_apply'])
        self.assertEqual(plan['conflicts'], ['notify.json'])
        with self.assertRaisesRegex(ValueError, '已有不同配置'):
            connection_import.apply(self.source, self.target, plan['fingerprint'])
        self.assertEqual((self.target / 'notify.json').read_bytes(), before)
        self.assertFalse((self.target / 'lark.json').exists())

    def test_source_or_target_change_invalidates_preview(self):
        for changed in ('source', 'target'):
            with self.subTest(changed=changed):
                plan = connection_import.preview(self.source, self.target)
                write(getattr(self, changed), 'notification-routing.json', {'target': 'qq' if changed == 'source' else 'none'})
                with self.assertRaisesRegex(ValueError, '配置已变化'):
                    connection_import.apply(self.source, self.target, plan['fingerprint'])
                self.assertFalse((self.target / 'qq.json').exists())

    def test_running_target_is_rejected_without_changing_config(self):
        plan = connection_import.preview(self.source, self.target)
        lease = HomeServiceLock(self.target)
        self.assertTrue(lease.acquire())
        try:
            with self.assertRaisesRegex(ValueError, '正在运行'):
                connection_import.apply(self.source, self.target, plan['fingerprint'])
        finally:
            lease.release()
        self.assertFalse((self.target / 'qq.json').exists())

    def test_concurrent_import_never_overwrites_and_can_retry_from_new_preview(self):
        plan = connection_import.preview(self.source, self.target)
        def attempt():
            try:
                return connection_import.apply(self.source, self.target, plan['fingerprint'])
            except ValueError as error:
                return str(error)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(sum(isinstance(value, dict) for value in results), 1)
        self.assertTrue(self.apply()['already_imported'])

    def test_cli_preserves_disabled_channels_routing_presence_and_never_outputs_secrets(self):
        legacy(self.source, away=True, target='lark')
        value = json.loads((self.source / 'qq.json').read_text())
        write(self.source, 'qq.json', {**value, 'enabled': False})
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            result = connection_import.main(['--source', str(self.source), '--target', str(self.target)])
        self.assertEqual(result, 0, err.getvalue())
        plan = json.loads(out.getvalue())
        self.assertEqual(plan['preferences'], {'manual_away': True, 'target': 'lark'})
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(connection_import.main(['--source', str(self.source), '--target', str(self.target),
                '--apply', '--expected', plan['fingerprint']]), 0)
        self.assertFalse(json.loads((self.target / 'qq.json').read_text())['enabled'])
        self.assertTrue(json.loads((self.target / 'notify.json').read_text())['manual_away'])
        self.assertEqual(json.loads((self.target / 'notification-routing.json').read_text()), {'target': 'lark'})
        self.assertNotIn('very-private-secret', out.getvalue() + err.getvalue())
        self.assertNotIn('private-recipient', out.getvalue() + err.getvalue())

    def test_malformed_source_and_same_home_fail_without_writes_or_secret_echo(self):
        with self.assertRaisesRegex(ValueError, '不能相同'):
            connection_import.preview(self.source, self.source)
        (self.source / 'qq.json').write_text('{very-private-secret', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '^qq.json 无法读取为有效配置$'):
            connection_import.preview(self.source, self.target)
        self.assertFalse(self.target.exists())


class ImportedConnectionAdapterTests(ConnectionFixture, unittest.TestCase):
    def test_lark_only_import_keeps_global_away_without_creating_a_qq_binding(self):
        source = self.root / 'lark-only'
        write(source, 'lark.json', {'app_id': 'lark-app', 'app_secret': 'private-secret', 'domain': 'lark', 'enabled': True})
        write(source, 'lark-notify.json', {'owner_openid': 'lark-owner', 'manual_away': True})
        plan = connection_import.preview(source, self.home)
        self.assertTrue(plan['preferences']['manual_away'])
        connection_import.apply(source, self.home, plan['fingerprint'])
        self.manager.close()
        self.manager = connections.Manager(self.service, lock_status=lambda: False)
        self.service.connections = self.manager
        self.service.inbox = self.manager.inboxes['qq']
        self.manager.clients.update(self.clients)
        self.manager.start()
        snapshot = self.manager.snapshot()
        self.assertTrue(snapshot['notification']['manual_away'])
        self.assertTrue(snapshot['notification']['away'])
        self.assertTrue(self.manager.inboxes['lark'].status()['manual_away'])
        self.assertTrue(self.state('lark')['bound'])
        self.assertFalse(self.state('qq')['bound'])
        self.assertFalse(self.state('qq')['secret_configured'])
        self.assertFalse((self.home / 'qq.json').exists())
        self.assertIsNone(json.loads((self.home / 'notify.json').read_text(encoding='utf-8'))['owner_openid'])
        self.assertTrue(all(not client.sent for client in self.clients.values()))
        self.assertEqual(self.processes, [])

    def test_import_uses_existing_qq_and_lark_binding_and_only_explicit_tests_send(self):
        source = self.root / 'old-connections'
        legacy(source)
        plan = connection_import.preview(source, self.home)
        connection_import.apply(source, self.home, plan['fingerprint'])
        self.manager.close()
        self.manager = connections.Manager(self.service, lock_status=lambda: False)
        self.service.connections = self.manager
        self.service.inbox = self.manager.inboxes['qq']
        self.manager.clients.update(self.clients)
        self.manager.start()
        for channel in connections.CHANNELS:
            self.assertTrue(self.state(channel)['bound'])
            self.assertEqual(self.state(channel)['state'], 'connected')
            self.assertEqual(self.clients[channel].sent, [])
            self.action(channel, 'test')
            sent = self.clients[channel].sent
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0]['openid'], channel + '-private-recipient')
            self.assertIn('连接测试成功', sent[0]['text'])
        self.assertEqual(self.processes, [])
        self.clients['lark'].send_errors.append(qq.QQError('fixture channel rejection', kind='api', status=403))
        with self.assertRaisesRegex(Exception, 'fixture channel rejection'):
            self.action('lark', 'test')
        self.assertEqual(len(self.clients['lark'].sent), 1)
        self.assertEqual(len(self.clients['qq'].sent), 1)


if __name__ == '__main__':
    unittest.main()
