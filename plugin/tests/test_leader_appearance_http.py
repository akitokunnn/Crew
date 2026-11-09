"""Leader color and avatar over real loopback HTTP."""
import tests  # noqa: F401
import base64
import threading
import unittest

from crew import appearance, remote_routes, session_ops, store, web
import test_chat_http
from test_shell import NOW, PLUGIN, ShellFixture

WEBP = b'RIFF\x24\x00\x00\x00WEBPVP8 ' + b'\x00' * 24
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 16


def encoded(data):
    return base64.b64encode(data).decode('ascii')


class LeaderAppearanceHttpTests(ShellFixture, unittest.TestCase):
    request = test_chat_http.ChatHttpTests.request
    close_server = test_chat_http.ChatHttpTests.close_server

    def setUp(self):
        super().setUp()
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=lambda: [], claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: store.parse_iso(NOW), process_factory=self.process_factory)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.url = self.server.url

    def leader(self):
        code, state, _ = self.request('/api/state?project=demo')
        self.assertEqual(code, 200, state)
        return next(item for item in state['leaders'] if item['session_id'] == 's3')

    def save(self, body):
        return self.request('/api/session/appearance?project=demo', {'session_id': 's3', **body})

    def test_color_and_avatar_show_in_state_and_the_avatar_is_served_by_version(self):
        self.assertEqual((self.leader()['color'], self.leader()['avatar_version']), (None, None))
        code, value, _ = self.save({'color': '#a07a3a', 'avatar': {'type': 'image/webp', 'data': encoded(WEBP)}})
        self.assertEqual(code, 200, value)
        leader = self.leader()
        self.assertEqual((leader['color'], leader['avatar_version']), ('#A07A3A', 1))
        code, content, headers = self.request('/api/session/avatar?session_id=s3&v=1', raw=True)
        self.assertEqual((code, content, headers['Content-Type']), (200, WEBP, 'image/webp'))
        self.assertIn('immutable', headers['Cache-Control'])
        # Changing only the color keeps the picture and its cache version.
        code, value, _ = self.save({'color': None})
        self.assertEqual((code, value), (200, {'color': None, 'avatar_version': 1}))
        code, value, _ = self.save({'color': None, 'avatar': {'data': encoded(PNG)}})
        self.assertEqual(value['avatar_version'], 2)
        code, content, headers = self.request('/api/session/avatar?session_id=s3&v=2', raw=True)
        self.assertEqual((content, headers['Content-Type']), (PNG, 'image/png'))
        code, value, _ = self.save({'color': None, 'avatar': None})
        self.assertEqual(value, {'color': None, 'avatar_version': None})
        code, _, _ = self.request('/api/session/avatar?session_id=s3&v=3', raw=True)
        self.assertNotEqual(code, 200)

    def test_rejects_large_unknown_or_malformed_pictures_bad_colors_and_members(self):
        cases = [({'color': 'red'}, '#RRGGBB'),
                 ({'color': None, 'avatar': {'data': encoded(b'GIF89a' + b'\x00' * 20)}}, 'WebP'),
                 ({'color': None, 'avatar': {'data': encoded(b'\xff\xd8\xff' + b'\x00' * appearance.MAX_AVATAR_BYTES)}}, '200KB'),
                 ({'color': None, 'avatar': {'data': '!!not base64'}}, 'base64'),
                 ({'avatar': None}, 'color')]
        for body, message in cases:
            code, value, _ = self.save(body)
            self.assertEqual(code, 400, body)
            self.assertIn(message, value['error'])
        code, value, _ = self.request('/api/session/appearance?project=demo', {'session_id': 's1', 'color': '#000000'})
        self.assertEqual(code, 400)
        self.assertIn('leader', value['error'])
        self.assertEqual(self.leader()['color'], None)

    def test_deleting_the_leader_drops_its_picture_and_remote_pages_may_use_both_routes(self):
        appearance.save(self.conn, 's3', {'color': '#112233', 'avatar': {'data': encoded(WEBP)}}, NOW)
        session_ops.delete(self.conn, 's3', now=NOW)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM session_appearance').fetchone()[0], 0)
        self.assertTrue(remote_routes.permitted('POST', '/api/session/appearance'))
        self.assertTrue(remote_routes.permitted('GET', '/api/session/avatar'))


if __name__ == '__main__':
    unittest.main()
