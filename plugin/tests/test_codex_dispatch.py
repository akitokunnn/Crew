"""Execute the repository dispatch script with a fake process, never Codex or its viewer."""
import tests  # Install the production-service connection guard.
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


POWERSHELL = shutil.which("powershell")
SCRIPT = Path(__file__).resolve().parents[2] / "integrations" / "codex-dispatch" / "run_codex.ps1"


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(os.name == "nt" and POWERSHELL, "Windows PowerShell script")
class TestCodexDispatch(unittest.TestCase):
    def test_json_log_viewer_shows_events_without_launching_a_window_or_model(self):
        viewer = SCRIPT.with_name('watch_log.ps1')
        command = r'''
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile(__VIEWER__,[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors | Out-String)}
$show=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Show-Line'},$true)
Invoke-Expression $show.Extent.Text
$MaxOutputLines=2
Show-Line '{"type":"item.started","item":{"type":"command_execution","command":"fake test"}}'
Show-Line '{"type":"item.completed","item":{"type":"agent_message","text":"Fixture progress"}}'
Show-Line '{"type":"item.completed","item":{"type":"command_execution","exit_code":0,"aggregated_output":"one\ntwo\nthree"}}'
Show-Line '{"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":3}}'
'''.replace('__VIEWER__', literal(viewer))
        result = subprocess.run([POWERSHELL, '-NoProfile', '-Command', command], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('[exec] fake test', result.stdout); self.assertIn('Fixture progress', result.stdout)
        self.assertIn('more output', result.stdout); self.assertIn('[tokens] input=12 output=3', result.stdout)

    def test_start_is_recorded_before_wait_and_completion_updates_the_same_run(self):
        for code, log_failure, malformed, private_home in ((0, False, False, False), (7, False, False, False),
                                                          (7, True, False, False), (7, False, True, False),
                                                          (0, False, False, True)):
            with self.subTest(exit_code=code, log_failure=log_failure, malformed=malformed), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                task = root / "task.md"
                task.write_text("Fake task only. No model will be called.", encoding="utf-8")
                if log_failure:
                    (root / ".crew").write_text("not a directory", encoding="utf-8")
                harness = root / "fake-dispatch.ps1"
                harness.write_text(r"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
[void][Management.Automation.Language.Parser]::ParseFile(__SCRIPT__, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
function Get-Command {
    param($Name, $CommandType, $ErrorAction)
    return [pscustomobject]@{ Source = 'FAKE-CODEX.exe' }
}
function Start-Process {
    param($FilePath, $ArgumentList, $WorkingDirectory, $RedirectStandardInput,
          $RedirectStandardOutput, $RedirectStandardError, $WindowStyle, [switch]$PassThru)
    if ($FilePath -ne 'FAKE-CODEX.exe' -or $WindowStyle -ne 'Hidden' -or -not $PassThru) { throw 'Unexpected launch' }
    if ($env:CREW_SESSION_ID -or $env:CREW_EXECUTION_ID -or $env:CREW_GOAL_ID -or $env:CLAUDE_CODE_SESSION_ID) { throw 'Parent identity leaked into executor' }
    if ($ArgumentList -notmatch 'exec --cd' -or $ArgumentList -notmatch '--sandbox read-only' -or $ArgumentList -notmatch '--ephemeral --json') { throw 'Wrong arguments' }
    $receipt = '{"type":"turn.completed","usage":{"input_tokens":1200,"cached_input_tokens":1000,"output_tokens":100,"reasoning_output_tokens":40}}'
    if ($env:FAKE_MALFORMED -eq 'yes') { $receipt = 'invalid receipt' }
    [IO.File]::WriteAllText($RedirectStandardOutput, $receipt)
    $fake = [pscustomobject]@{ Id = 12345; Handle = 1; StartTime = [DateTime]::UtcNow; ExitCode = [int]$env:FAKE_EXIT }
    $fake | Add-Member ScriptMethod WaitForExit {
        if ($env:FAKE_LOG_FAILURE -ne 'yes') {
            $records = @(Get-Content -LiteralPath (Join-Path $env:FAKE_CREW_RUN_HOME 'codex_runs.jsonl') | ConvertFrom-Json)
            if ($records.Count -ne 1 -or $null -ne $records[0].ended_at -or $null -ne $records[0].exit_code) {
                throw 'The started record must be visible before waiting'
            }
            $records[0] | ConvertTo-Json -Compress | Set-Content -LiteralPath (Join-Path $env:USERPROFILE 'during-wait.json')
        }
        [IO.File]::WriteAllText((Join-Path $env:USERPROFILE 'waited'), 'yes')
    }
    return $fake
}
& __SCRIPT__ -TaskFile __TASK__ -WorkDir __ROOT__ -Sandbox read-only -Model fake-model -Effort low -NoWatch
exit $LASTEXITCODE
""".replace("__SCRIPT__", literal(SCRIPT)).replace("__TASK__", literal(task)).replace("__ROOT__", literal(root)),
                                   encoding="utf-8-sig")
                run_home = root / ('selected-home' if private_home else '.crew')
                env = {key: value for key, value in os.environ.items() if not key.startswith('CREW_')}
                env.update(USERPROFILE=str(root), CLAUDE_CODE_SESSION_ID="fake-claude",
                           CREW_SESSION_ID='logical-chief', CREW_EXECUTION_ID='goal-execution', CREW_GOAL_ID='goal',
                           FAKE_CREW_RUN_HOME=str(run_home),
                           FAKE_EXIT=str(code), FAKE_LOG_FAILURE="yes" if log_failure else "no",
                           FAKE_MALFORMED="yes" if malformed else "no")
                if private_home:
                    env['CREW_HOME'] = str(run_home)
                result = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
                                        capture_output=True, text=True, env=env, timeout=20)
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                self.assertEqual((root / "waited").read_text(), "yes")
                if log_failure:
                    self.assertIn("WARN: Crew dispatch record not written:", result.stdout)
                    continue
                records = [json.loads(line) for line in (run_home / 'codex_runs.jsonl').read_text(encoding="utf-8").splitlines()]
                self.assertEqual(len(records), 2)
                self.assertEqual(records[0], json.loads((root / "during-wait.json").read_text(encoding="utf-8-sig")))
                self.assertEqual(records[0]["run_id"], records[1]["run_id"])
                self.assertEqual(records[0]["started_at"], records[1]["started_at"])
                self.assertIsNone(records[0]["ended_at"])
                self.assertIsNone(records[0]["exit_code"])
                self.assertEqual(records[1]["exit_code"], code)
                self.assertIsNotNone(records[1]["ended_at"])
                self.assertEqual(records[1]["claude_session_id"], "fake-claude")
                self.assertEqual((records[1]['crew_session_id'], records[1]['crew_execution_id']), ('logical-chief', 'goal-execution'))
                self.assertEqual(records[0]['pid'], 12345)
                self.assertTrue(records[0]['pid_created'].startswith('win:'))
                if private_home:
                    self.assertFalse((root / '.crew').exists())
                self.assertTrue(records[1]['ephemeral'])
                if malformed:
                    self.assertIsNone(records[1]['usage'])
                    self.assertIn('WARN: Codex usage not recorded:', result.stdout)
                else:
                    self.assertEqual(records[1]['usage'], {'input_tokens': 1200, 'cached_input_tokens': 1000,
                                                          'output_tokens': 100, 'reasoning_output_tokens': 40})
                self.assertEqual((records[1]["model"], records[1]["effort"]), ("fake-model", "low"))
                self.assertEqual(Path(records[1]["workdir"]), root)


if __name__ == "__main__":
    unittest.main()
