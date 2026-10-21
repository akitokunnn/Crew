"""Execute the repository dispatch script with a fake process, never Codex or its viewer."""
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
    def test_start_is_recorded_before_wait_and_completion_updates_the_same_run(self):
        for code, log_failure in ((0, False), (7, False), (7, True)):
            with self.subTest(exit_code=code, log_failure=log_failure), tempfile.TemporaryDirectory() as temp:
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
          $RedirectStandardOutput, $RedirectStandardError, [switch]$NoNewWindow, [switch]$PassThru)
    if ($FilePath -ne 'FAKE-CODEX.exe' -or -not $NoNewWindow -or -not $PassThru) { throw 'Unexpected launch' }
    if ($ArgumentList -notmatch 'exec --cd' -or $ArgumentList -notmatch '--sandbox read-only') { throw 'Wrong arguments' }
    $fake = [pscustomobject]@{ Id = 12345; Handle = 1; ExitCode = [int]$env:FAKE_EXIT }
    $fake | Add-Member ScriptMethod WaitForExit {
        if ($env:FAKE_LOG_FAILURE -ne 'yes') {
            $records = @(Get-Content -LiteralPath (Join-Path $env:USERPROFILE '.crew\codex_runs.jsonl') | ConvertFrom-Json)
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
                env = dict(os.environ, USERPROFILE=str(root), CLAUDE_CODE_SESSION_ID="fake-claude",
                           FAKE_EXIT=str(code), FAKE_LOG_FAILURE="yes" if log_failure else "no")
                result = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
                                        capture_output=True, text=True, env=env, timeout=20)
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                self.assertEqual((root / "waited").read_text(), "yes")
                if log_failure:
                    self.assertIn("WARN: Crew dispatch record not written:", result.stdout)
                    continue
                records = [json.loads(line) for line in (root / ".crew" / "codex_runs.jsonl").read_text(encoding="utf-8").splitlines()]
                self.assertEqual(len(records), 2)
                self.assertEqual(records[0], json.loads((root / "during-wait.json").read_text(encoding="utf-8-sig")))
                self.assertEqual(records[0]["run_id"], records[1]["run_id"])
                self.assertEqual(records[0]["started_at"], records[1]["started_at"])
                self.assertIsNone(records[0]["ended_at"])
                self.assertIsNone(records[0]["exit_code"])
                self.assertEqual(records[1]["exit_code"], code)
                self.assertIsNotNone(records[1]["ended_at"])
                self.assertEqual(records[1]["claude_session_id"], "fake-claude")
                self.assertEqual((records[1]["model"], records[1]["effort"]), ("fake-model", "low"))
                self.assertEqual(Path(records[1]["workdir"]), root)


if __name__ == "__main__":
    unittest.main()
