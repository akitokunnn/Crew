"""Claude skill runner receipts with a fake process and an isolated HOME."""
import tests
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from tests.test_codex_dispatch import literal

SCRIPT = Path(__file__).resolve().parents[2] / 'integrations/claude-dispatch/scripts/run_claude.ps1'
POWERSHELL = shutil.which('powershell')


@unittest.skipUnless(os.name == 'nt' and POWERSHELL, 'Windows PowerShell script')
class ClaudeDispatchTests(unittest.TestCase):
    def test_both_modes_record_start_completion_and_separate_parent_identity(self):
        for mode in ('read-only', 'full'):
            for code, malformed in ((0, False), (7, False), (0, True)):
                with self.subTest(mode=mode, code=code, malformed=malformed), tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    task = root / 'task.md'; task.write_text('Fake task only.', encoding='utf-8')
                    run_home = root / 'private-home'
                    harness = root / 'fake.ps1'
                    harness.write_text(r'''
$ErrorActionPreference='Stop'
function Get-Command {
    param($Name,$CommandType,$ErrorAction)
    return [pscustomobject]@{Source='FAKE-CLAUDE.exe'}
}
function Start-Process {
    param($FilePath,$ArgumentList,$WorkingDirectory,$RedirectStandardInput,$RedirectStandardOutput,$RedirectStandardError,$WindowStyle,[switch]$PassThru)
    if($FilePath -ne 'FAKE-CLAUDE.exe' -or $WindowStyle -ne 'Hidden' -or -not $PassThru){throw 'Unexpected launch'}
    if($ArgumentList -notmatch '--disallowedTools Monitor'){throw 'Monitor must not trigger progress turns'}
    if($env:CREW_SESSION_ID -or $env:CREW_EXECUTION_ID -or $env:CREW_GOAL_ID -or $env:CODEX_THREAD_ID){throw 'Parent identity leaked'}
    $body='{"type":"result","is_error":false,"result":"Fixture delivery","num_turns":1,"total_cost_usd":0}'
    if($env:FAKE_MALFORMED -eq 'yes'){$body='invalid json'}
    [IO.File]::WriteAllText($RedirectStandardOutput,$body)
    [IO.File]::WriteAllText($RedirectStandardError,'')
    $fake=[pscustomobject]@{Id=12345;Handle=1;StartTime=[DateTime]::UtcNow;ExitCode=[int]$env:FAKE_EXIT}
    $fake | Add-Member ScriptMethod WaitForExit {
        $records=@(Get-Content -LiteralPath (Join-Path $env:CREW_HOME 'claude_runs.jsonl') | ConvertFrom-Json)
        if($records.Count -ne 1 -or $null -ne $records[0].ended_at){throw 'Start must precede wait'}
    }
    return $fake
}
& __SCRIPT__ -TaskFile __TASK__ -WorkDir __ROOT__ -Mode __MODE__ -NoWatch
exit $LASTEXITCODE
'''.replace('__SCRIPT__', literal(SCRIPT)).replace('__TASK__', literal(task))
                       .replace('__ROOT__', literal(root)).replace('__MODE__', literal(mode)), encoding='utf-8-sig')
                    env = {key: value for key, value in os.environ.items() if not key.startswith('CREW_')}
                    env.update(USERPROFILE=str(root), CREW_HOME=str(run_home), CREW_SESSION_ID='owner',
                               CREW_EXECUTION_ID='execution', CREW_GOAL_ID='goal', CODEX_THREAD_ID='codex-parent',
                               FAKE_EXIT=str(code), FAKE_MALFORMED='yes' if malformed else 'no')
                    result = subprocess.run([POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(harness)],
                                            capture_output=True, text=True, env=env, timeout=20)
                    expected = 1 if malformed else code
                    self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                    records = [json.loads(line) for line in (run_home / 'claude_runs.jsonl').read_text(encoding='utf-8').splitlines()]
                    self.assertEqual(len(records), 2)
                    self.assertEqual(records[0]['run_id'], records[1]['run_id'])
                    self.assertEqual((records[0]['crew_session_id'], records[0]['crew_execution_id']), ('owner', 'execution'))
                    self.assertEqual(records[0]['codex_thread_id'], 'codex-parent')
                    self.assertEqual(records[0]['pid'], 12345)
                    self.assertTrue(records[0]['pid_created'].startswith('win:'))
                    self.assertEqual(records[1]['exit_code'], expected)
                    self.assertFalse((root / '.crew').exists())
                    if not malformed:
                        self.assertEqual((root / 'task_last.md').read_text(encoding='utf-8'), 'Fixture delivery')


if __name__ == '__main__':
    unittest.main()
