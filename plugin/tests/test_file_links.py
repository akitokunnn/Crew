"""File APIs with isolated data, a random port and fake desktop calls."""
import tests
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from crew import web
from test_shell import PLUGIN, ShellFixture


class FileLinksTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.worktree = self.project / 'worktree'
        self.worktree.mkdir()
        self.conn.execute("UPDATE sessions SET cwd=?, worktree=? WHERE session_id='s1'",
                          (str(self.worktree), str(self.worktree)))
        self.conn.commit()
        self.service = web.Service(self.home, PLUGIN, cwd=self.project, agents_provider=lambda: [],
                                   claude_root=self.claude, codex_root=self.codex, runs_file=self.runs,
                                   process_factory=self.process_factory, vscode_state_db=self.vscode_state_db)
        self.addCleanup(self.service.close)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), web.Handler)
        self.server.service = self.service
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01})
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.assertFalse(self.thread.is_alive())

    def request(self, action, path, host=None, raw=False, base=None):
        data = {'project': 'demo', 'session_id': 's1', 'path': str(path)}
        if base is not None:
            data['base'] = str(base)
        if raw:
            data['raw'] = '1'
        post = action != 'content'
        headers = {'X-Crew': '1'}
        if host is not None:
            headers['Host'] = host
        url = 'http://127.0.0.1:%d/api/files/%s' % (self.server.server_port, action)
        req = Request(url + ('' if post else '?' + urlencode(data)),
                      json.dumps(data).encode() if post else None, headers)
        try:
            response = urlopen(req, timeout=3)
        except HTTPError as exc:
            response = exc
        with response:
            content = response.read()
            return response.status, content if raw and response.status == 200 else json.loads(content)

    def test_cwd_then_root_and_both_absolute_slashes(self):
        for folder in (self.worktree, self.project):
            (folder / 'docs').mkdir()
            (folder / 'docs/x.md').write_text(str(folder))
        own = self.worktree / 'docs/x.md'
        for name in ('docs/x.md', 'docs\\x.md', str(own), own.as_posix()):
            self.assertEqual(self.request('resolve', name)[1]['path'], str(own))
        own.unlink()
        self.assertEqual(self.request('resolve', 'docs/x.md')[1]['path'], str(self.project / 'docs/x.md'))

    def test_missing_directory_and_types(self):
        for path in ('missing.md', self.worktree):
            self.assertIsNotNone(self.request('resolve', path)[1]['error'])
            self.assertEqual(self.request('content', path)[0], 400)
            with mock.patch('crew.web.os.startfile') as opener:
                self.assertEqual(self.request('open', path)[0], 400)
                opener.assert_not_called()
        for ext, kind in (('md', 'markdown'), ('txt', 'text'), ('py', 'text'), ('svg', 'image'),
                          ('html', 'external'), ('pdf', 'external'), ('docx', 'external'),
                          ('xlsx', 'external'), ('pptx', 'external')):
            file = self.worktree / ('x.' + ext)
            file.write_text('sample')
            self.assertEqual(self.request('resolve', file)[1]['kind'], kind)

    def test_host_requires_exact_loopback_and_actual_port(self):
        file = self.worktree / 'x.txt'
        file.write_text('secret')
        for host in ('evil.example', '127.0.0.1:47811', 'localhost', 'localhost.evil:123', '127.0.0.2:123'):
            self.assertEqual(self.request('content', file, host)[0], 403)
        for host in ('localhost', '127.0.0.1'):
            self.assertEqual(self.request('content', file, host + ':' + str(self.server.server_port))[1]['content'], 'secret')

    def test_large_text_head_and_log_tail(self):
        for ext in ('txt', 'log'):
            file = self.worktree / ('x.' + ext)
            file.write_bytes(b'HEAD' + b'x' * (300 * 1024) + b'TAIL')
            code, content = self.request('content', file)
            self.assertEqual(code, 200)
            self.assertTrue(content['truncated'])
            self.assertEqual(len(content['content']), 256 * 1024)
            self.assertEqual(content['content'].startswith('HEAD'), ext == 'txt')
            self.assertEqual(content['content'].endswith('TAIL'), ext == 'log')

    def test_document_relative_resources(self):
        folder = self.worktree / 'docs'
        folder.mkdir()
        doc = folder / 'x.md'
        doc.write_text('# heading')
        image = folder / 'x.svg'
        image.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"/>')
        self.assertEqual(self.request('content', 'x.svg', raw=True, base=doc), (200, image.read_bytes()))
        self.assertEqual(self.request('content', 'x.md', base=doc)[1]['content'], '# heading')

    def test_desktop_open_and_reveal_are_mocked(self):
        file = self.worktree / 'page.html'
        file.write_text('<h1>Hello</h1>')
        with mock.patch('crew.web.os.startfile') as opener, mock.patch('crew.web.subprocess.Popen') as explorer:
            self.assertEqual(self.request('open', file)[0], 200)
            opener.assert_called_once_with(str(file))
            self.assertEqual(self.request('reveal', file)[0], 200)
            explorer.assert_called_once_with(['explorer', '/select,' + str(file)])

    def test_document_whitelist_uses_default_app(self):
        extensions = 'md txt log json csv xml yaml yml toml ini cfg html htm pdf doc docx xls xlsx ppt pptx png jpg jpeg gif webp svg bmp'.split()
        for ext in extensions:
            with self.subTest(ext=ext):
                file = self.worktree / ('x.' + ext.upper())
                file.write_text('sample')
                self.assertEqual(self.request('resolve', file)[1]['open_with'], 'default')
                with mock.patch('crew.web.os.startfile') as opener, mock.patch('crew.web.subprocess.Popen') as explorer:
                    self.assertEqual(self.request('open', file)[0], 200)
                    opener.assert_called_once_with(str(file))
                    explorer.assert_not_called()

    def test_code_whitelist_uses_encoded_vscode_file_uri(self):
        extensions = 'py js cjs mjs ts tsx jsx css scss ps1 bat cmd sh c h cpp hpp cs java go rs rb php sql vue svelte uproject uplugin Build.cs'.split()
        for ext in extensions:
            with self.subTest(ext=ext):
                file = self.worktree / ('代码 #100%.' + ext)
                file.write_text('sample', encoding='utf-8')
                info = self.request('resolve', file)[1]
                self.assertEqual(info['open_with'], 'vscode')
                self.assertEqual(info['kind'], 'text')
                expected = 'vscode://file/' + self.worktree.as_posix() + '/%E4%BB%A3%E7%A0%81%20%23100%25.' + ext
                with mock.patch('crew.web.os.startfile') as opener, mock.patch('crew.web.subprocess.Popen') as explorer:
                    self.assertEqual(self.request('open', file)[0], 200)
                    opener.assert_called_once_with(expected)
                    explorer.assert_not_called()

    def test_other_types_reject_open_without_desktop_calls(self):
        for ext in ('exe', 'msi', 'com', 'scr', 'lnk', 'vbs', 'wsf', 'reg', 'jar', 'unknown', ''):
            with self.subTest(ext=ext):
                file = self.worktree / ('x.' + ext if ext else 'no_extension')
                file.write_text('sample')
                self.assertEqual(self.request('resolve', file)[1]['open_with'], 'blocked')
                with mock.patch('crew.web.os.startfile') as opener, mock.patch('crew.web.subprocess.Popen') as explorer:
                    status, result = self.request('open', file)
                    self.assertEqual(status, 400)
                    self.assertEqual(result['error'], '这类文件不直接打开，请用‘在文件夹中显示’')
                    opener.assert_not_called()
                    explorer.assert_not_called()
                with mock.patch('crew.web.subprocess.Popen') as explorer:
                    self.assertEqual(self.request('reveal', file)[0], 200)
                    explorer.assert_called_once_with(['explorer', '/select,' + str(file)])
