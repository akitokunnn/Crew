param([Parameter(Mandatory = $true)][string]$Spec)
$ErrorActionPreference = 'Stop'
$brief = Get-Content -LiteralPath $Spec -Raw -Encoding UTF8 | ConvertFrom-Json
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
