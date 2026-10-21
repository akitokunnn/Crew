param([Parameter(Mandatory = $true)][string]$Spec)
$ErrorActionPreference = 'Stop'
$brief = Get-Content -LiteralPath $Spec -Raw -Encoding UTF8 | ConvertFrom-Json

# crew spawn usually runs inside another Claude session, whose per-session markers
# (CLAUDE_CODE_CHILD_SESSION, CLAUDE_CODE_SESSION_ID, the messaging socket ...) reach this tab.
# With them the new claude runs as a child: no transcript, no inbox, missing from `claude agents`.
# Keep only the CLAUDE* variables the user set persistently.
foreach ($var in @(Get-ChildItem Env: | Where-Object { $_.Name -like 'CLAUDE*' })) {
    $user = [Environment]::GetEnvironmentVariable($var.Name, 'User')
    $machine = [Environment]::GetEnvironmentVariable($var.Name, 'Machine')
    if ($null -eq $user -and $null -eq $machine) {
        Remove-Item -LiteralPath ('Env:' + $var.Name)
    }
}

$env:CREW_NAME = $brief.name
if ($null -ne $brief.group) {
    $env:CREW_GROUP = $brief.group
} elseif (Test-Path Env:CREW_GROUP) {
    Remove-Item Env:CREW_GROUP
}
$env:CREW_ROLE = $brief.role
$env:CREW_SPAWN_ID = $brief.id
Set-Location -LiteralPath $brief.cwd
$instruction = 'Read the file ' + $brief.md + ' with the Read tool and follow it. It is your task brief from Crew.'
$claudeArgs = @('-n', $brief.name, '--permission-mode', 'bypassPermissions')
if ($null -ne $brief.agent) {
    $claudeArgs += @('--agent', $brief.agent)
}
$claudeArgs += $instruction
& claude @claudeArgs
exit $LASTEXITCODE
