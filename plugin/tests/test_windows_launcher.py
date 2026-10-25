"""Exercise installed Windows entry points through real PowerShell processes."""
import tests
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from crew import install


PLUGIN = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'nt', 'Real Windows native argument binding')
class WindowsLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='crew-windows-launcher-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "Crew home 'quoted' 中文 $value % !"
        self.shells = [path for name in ('powershell', 'pwsh') if (path := shutil.which(name))]
        if not self.shells:
            self.skipTest('Windows PowerShell or pwsh is required')
        install.install(self.home, PLUGIN)
        self.write_echo()
        self.env = {name: value for name, value in os.environ.items()
                    if not name.startswith('CREW_') and name != 'CLAUDE_CODE_SESSION_ID'}
        self.env['PATH'] = str(self.home / 'bin') + os.pathsep + self.env.get('PATH', '')

    def write_echo(self, exit_code=0):
        (self.home / 'crew.py').write_text(
            'import json,sys\nprint(json.dumps(sys.argv[1:],ensure_ascii=True))\n'
            'raise SystemExit(%d)\n' % exit_code, encoding='utf-8')

    def run_script(self, shell, script, policy='Restricted'):
        encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
        return subprocess.run([shell, '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', policy,
                               '-EncodedCommand', encoded], env=self.env, cwd=self.root, capture_output=True, timeout=20)

    def run_powershell(self, shell, arguments, *, explicit_cmd=False, capture=False, redirect=False, policy='Restricted'):
        inputs = self.root / 'arguments.json'
        inputs.write_text(json.dumps(arguments, ensure_ascii=False), encoding='utf-8')
        quote = lambda text: "'" + str(text).replace("'", "''") + "'"
        executable = quote(self.home / 'bin' / 'crew.cmd') if explicit_cmd else 'crew'
        script = ("$ErrorActionPreference='Continue'\n"
                  "[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding\n"
                  "$values=ConvertFrom-Json -InputObject (Get-Content -LiteralPath %s -Raw -Encoding UTF8)\n"
                  "Write-Output ('CREW_RESOLVED='+(Get-Command crew).Source)\n" % quote(inputs))
        if redirect:
            script += '& %s @values > %s\nexit $LASTEXITCODE\n' % (executable, quote(self.root / 'redirect.json'))
        elif capture:
            script += ('$captured=@(& %s @values)\n$code=$LASTEXITCODE\n'
                       "Write-Output ('CREW_CAPTURED='+$captured.Count)\n"
                       '$captured | Write-Output\nexit $code\n' % executable)
        else:
            script += '& %s @values\nexit $LASTEXITCODE\n' % executable
        # Restricted disallows a .ps1 harness too; command code does not change
        # the user's policy and exercises normal native command discovery.
        return self.run_script(shell, script, policy)

    def echoed(self, result):
        lines = result.stdout.decode('utf-8-sig').splitlines()
        return next(json.loads(line) for line in lines if line.startswith('['))

    def test_legacy_cmd_reproduces_multiline_truncation_in_both_powershells(self):
        payload = '第一点 Binaries\n第二点 蓝图纯数据'
        for shell in self.shells:
            with self.subTest(shell=shell):
                result = self.run_powershell(shell, ['send', '文档施工', payload], explicit_cmd=True)
                self.assertEqual(self.echoed(result), ['send', '文档施工', '第一点 Binaries'])
                self.assertNotEqual(self.echoed(result)[-1], payload)

    def test_native_entry_is_selected_under_restricted_and_bypass_without_multiline_reparsing(self):
        arguments = ['send', '文档施工', '第一点 Binaries\r\n第二点 蓝图纯数据\n尾行\n',
                     "single ' quote", 'C:\\folder with spaces\\file.txt', '$HOME $(Set-Content injected.txt bad)',
                     '`Set-Content injected.txt bad`', '%PATH% !NAME!', 'a&b|c<d>e;f', '中文 😀', '--flag=a b']
        for shell in self.shells:
            for policy in ('Restricted', 'Bypass'):
                with self.subTest(shell=shell, policy=policy):
                    result = self.run_powershell(shell, arguments, policy=policy)
                    self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
                    self.assertIn(('CREW_RESOLVED=' + str(self.home / 'bin' / 'crew.exe')).encode('utf-8'), result.stdout)
                    self.assertEqual(self.echoed(result), arguments)
                    self.assertFalse((self.root / 'injected.txt').exists())
                    self.assertFalse((self.home / 'bin' / 'crew.ps1').exists())

    def test_native_launcher_and_modern_powershell_preserve_quotes_and_empty_arguments(self):
        arguments = ['send', '文档施工', 'first "quoted" line\r\nsecond line', '', 'C:\\with spaces\\',
                     '\\"quoted\\\\"tail', '中文 😀', '$() ` % !']
        executable = self.home / 'bin' / 'crew.exe'
        result = subprocess.run([str(executable), *arguments], env=self.env, cwd=self.root, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.echoed(result), arguments)
        for shell in self.shells:
            if Path(shell).stem.lower() != 'pwsh':
                continue
            for policy in ('Restricted', 'Bypass'):
                with self.subTest(shell=shell, policy=policy):
                    result = self.run_powershell(shell, arguments, policy=policy)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(self.echoed(result), arguments)

    def test_windows_powershell_legacy_native_binding_limit_is_not_claimed_lossless(self):
        for shell in self.shells:
            if Path(shell).stem.lower() != 'powershell':
                continue
            result = self.run_powershell(shell, ['literal "quotes"', ''])
            self.assertEqual(result.returncode, 0, result.stderr)
            # PowerShell 5.1 removes these before ANY native executable sees
            # argv. Exact message text must use the CLI's --text-file route.
            self.assertEqual(self.echoed(result), ['literal quotes'])

    def test_exit_code_pipeline_and_redirection_remain_shell_visible(self):
        arguments = ['status', 'line one\nline two', '中文']
        self.write_echo(exit_code=37)
        for shell in self.shells:
            with self.subTest(shell=shell, mode='capture'):
                result = self.run_powershell(shell, arguments, capture=True)
                self.assertEqual(result.returncode, 37, result.stderr.decode('utf-8', errors='replace'))
                self.assertIn(b'CREW_CAPTURED=1', result.stdout)
                self.assertEqual(self.echoed(result), arguments)
            with self.subTest(shell=shell, mode='redirect'):
                result = self.run_powershell(shell, arguments, redirect=True)
                self.assertEqual(result.returncode, 37, result.stderr.decode('utf-8', errors='replace'))
                raw = (self.root / 'redirect.json').read_bytes()
                text = raw.decode('utf-16') if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else raw.decode('utf-8-sig')
                self.assertEqual(json.loads(text), arguments)

    def test_literal_here_string_preserves_the_original_two_part_decision(self):
        body = '第一点：Binaries 保留「明确引用」。\n第二点：蓝图纯数据，$() 和 ` 均为原文，%PATH% !原文!'
        script = ("[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding\n"
                  "& crew send '文档施工' @'\n" + body + "\n'@\nexit $LASTEXITCODE\n")
        for shell in self.shells:
            with self.subTest(shell=shell):
                result = self.run_script(shell, script)
                self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
                self.assertEqual(self.echoed(result), ['send', '文档施工', body])

    def test_native_stderr_keeps_the_actual_reason_and_exit_code(self):
        (self.home / 'crew.py').write_text('import sys\nsys.stderr.reconfigure(encoding="utf-8")\n'
                                          'sys.stderr.write("实际原因：第二行未接受\\n原始输入保留\\n")\nraise SystemExit(23)\n', encoding='utf-8')
        for shell in self.shells:
            with self.subTest(shell=shell):
                result = self.run_powershell(shell, ['send', '文档施工', 'first\nsecond'])
                self.assertEqual(result.returncode, 23)
                self.assertIn('实际原因：第二行未接受', result.stderr.decode('utf-8'))
                self.assertIn('原始输入保留', result.stderr.decode('utf-8'))

    def test_native_stdio_streams_before_exit_and_inherits_stdin(self):
        (self.home / 'crew.py').write_text(
            'import sys\nsys.stdout.write("prompt> ");sys.stdout.flush()\n'
            'sys.stderr.write("live warning\\n");sys.stderr.flush()\n'
            'value=sys.stdin.readline()\nsys.stdout.write("received:"+value);sys.stdout.flush()\n'
            'raise SystemExit(19)\n', encoding='utf-8')
        process = subprocess.Popen([str(self.home / 'bin' / 'crew.exe')], cwd=self.root, env=self.env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with ThreadPoolExecutor(max_workers=2) as pool:
            try:
                prompt = pool.submit(process.stdout.read, 8)
                warning = pool.submit(process.stderr.readline)
                self.assertEqual(prompt.result(timeout=5), b'prompt> ', 'A prompt without a newline must remain live')
                self.assertEqual(warning.result(timeout=5), b'live warning\r\n', 'Stderr is visible before exit')
                process.stdin.write(b'input value\n'); process.stdin.flush(); process.stdin.close()
                self.assertEqual(process.stdout.read(), b'received:input value\r\n')
                self.assertEqual(process.wait(timeout=5), 19)
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
        for shell in self.shells:
            with self.subTest(shell=shell, mode='native-stdin-pipeline'):
                result = self.run_script(shell, "[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding; 'pipeline-value' | crew; exit $LASTEXITCODE")
                self.assertEqual(result.returncode, 19, result.stderr.decode('utf-8', errors='replace'))
                self.assertIn(b'prompt> received:pipeline-value', result.stdout)
                self.assertIn(b'live warning', result.stderr)

    def test_vendored_stub_hash_and_native_entry_are_reproducible_without_pip(self):
        stub = (PLUGIN / 'crew' / 'vendor' / 'distlib' / 't64.exe').read_bytes()
        self.assertEqual(hashlib.sha256(stub).hexdigest(), '81a618f21cb87db9076134e70388b6e9cb7c2106739011b6a51772d22cae06b7')
        generated = (self.home / 'bin' / 'crew.exe').read_bytes()
        self.assertTrue(generated.startswith(stub))
        self.assertEqual(generated, install._windows_launcher())

    def test_cli_can_explicitly_reinstall_its_own_running_native_entry(self):
        (self.home / 'crew.py').write_text(install.LAUNCHER, encoding='utf-8', newline='\n')
        executable = self.home / 'bin' / 'crew.exe'
        before = executable.read_bytes()
        result = subprocess.run([str(executable), 'install'], cwd=self.root, env=self.env, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
        self.assertEqual(executable.read_bytes(), before)
        self.assertEqual(json.loads((self.home / 'runtime.json').read_text(encoding='utf-8'))['plugin_root'], str(PLUGIN.resolve()))
        self.assertFalse((self.home / 'web.json').exists())
        self.assertFalse((self.home / 'chat').exists())

    def test_cmd_single_line_still_works_and_ensure_never_switches_source(self):
        command = '"' + os.environ['COMSPEC'] + '" /d /s /c ""' + str(self.home / 'bin' / 'crew.cmd') + '" single-line"'
        result = subprocess.run(command, env=self.env, cwd=self.root, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.echoed(result), ['single-line'])
        runtime = (self.home / 'runtime.json').read_bytes()
        installed = self.home / 'bin' / 'crew.exe'
        self.assertTrue(installed.is_file())
        installed.unlink()
        alternate = self.root / 'other source'
        (alternate / 'defaults').mkdir(parents=True)
        for name in ('blacklist.txt', 'prices.json'):
            (alternate / 'defaults' / name).write_text('unused', encoding='utf-8')
        self.assertIsNone(install.ensure(self.home, alternate))
        self.assertTrue(installed.is_file())
        self.assertEqual((self.home / 'runtime.json').read_bytes(), runtime)
        installed.write_bytes(b'user-owned existing entry')
        install.ensure(self.home, PLUGIN)
        self.assertEqual(installed.read_bytes(), b'user-owned existing entry')
        install.install(self.home, alternate)
        self.assertNotEqual(installed.read_bytes(), b'user-owned existing entry')
        self.assertEqual(json.loads((self.home / 'runtime.json').read_text(encoding='utf-8'))['plugin_root'], str(alternate.resolve()))


if __name__ == '__main__':
    unittest.main()
