# Dispatch a UTF-8 task file to Claude Code without an interactive terminal.
# Exit: 0 success; 2 RequireClosed; 64 input; 127 missing CLI; else CLI code.
param(
    [Parameter(Mandatory = $true)][string]$TaskFile,
    [Parameter(Mandatory = $true)][ValidateSet('read-only', 'full')][string]$Mode,
    [string]$WorkDir,
    [string[]]$RequireClosed = @(),
    [string[]]$AddDir = @(),
    [string]$Model,
    [string]$Effort,
    [switch]$Search,
    [switch]$NoWatch
)

$ErrorActionPreference = 'Stop'
if ($env:CREW_ROLE -eq 'member') {
    Write-Output 'BLOCKED: Crew members cannot dispatch another AI. Ask your leader to split the work.'
    exit 2
}
$utf8 = New-Object Text.UTF8Encoding $false
$crewRunHome = if ($env:CREW_HOME) { [IO.Path]::GetFullPath($env:CREW_HOME) } else { Join-Path $env:USERPROFILE '.crew-v2' }

function ConvertTo-QuotedArg([string]$Value) {
    # Windows argv quoting, including quotes and trailing backslashes.
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Write-CrewRun([Collections.IDictionary]$Run) {
    $mutex = $null
    $locked = $false
    try {
        $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        $mutex = New-Object Threading.Mutex($false, "Local\Crew.ClaudeRuns.$sid")
        try { $locked = $mutex.WaitOne(5000) }
        catch [Threading.AbandonedMutexException] { $locked = $true }
        if (-not $locked) { throw [IO.IOException]::new('Timed out appending the Crew dispatch record') }
        New-Item -ItemType Directory -Force -Path $crewRunHome | Out-Null
        [IO.File]::AppendAllText((Join-Path $crewRunHome 'claude_runs.jsonl'),
            (($Run | ConvertTo-Json -Compress) + "`n"), (New-Object Text.UTF8Encoding $false))
    } catch {
        Write-Output "WARN: Crew dispatch record not written: $($_.Exception.GetType().Name): $($_.Exception.Message)"
    } finally {
        if ($locked) { $mutex.ReleaseMutex() }
        if ($null -ne $mutex) { $mutex.Dispose() }
    }
}

if (-not (Test-Path -LiteralPath $TaskFile -PathType Leaf)) {
    Write-Output "ERROR: task file not found: $TaskFile"
    exit 64
}
$TaskPath = (Resolve-Path -LiteralPath $TaskFile).Path
if (-not $WorkDir) {
    $WorkDir = & git -C (Split-Path -Parent $TaskPath) rev-parse --show-toplevel
    if ($LASTEXITCODE -ne 0 -or -not $WorkDir) {
        Write-Output "ERROR: -WorkDir not given and task file is not inside a git repo: $TaskPath"
        exit 64
    }
}
if (-not (Test-Path -LiteralPath $WorkDir -PathType Container)) {
    Write-Output "ERROR: working directory not found: $WorkDir"
    exit 64
}
$WorkDir = (Resolve-Path -LiteralPath $WorkDir).Path
$promptFile = Join-Path (Split-Path -Parent $PSScriptRoot) 'executor_prompt.md'
if (-not (Test-Path -LiteralPath $promptFile -PathType Leaf)) {
    Write-Output "ERROR: executor prompt not found: $promptFile"
    exit 64
}
foreach ($d in $AddDir) {
    if (-not (Test-Path -LiteralPath $d -PathType Container)) {
        Write-Output "ERROR: additional directory not found: $d"
        exit 64
    }
}
$running = @()
foreach ($name in $RequireClosed) {
    if (Get-Process -Name $name -ErrorAction SilentlyContinue) { $running += $name }
}
if ($running.Count -gt 0) {
    Write-Output ('BLOCKED: close these programs first: ' + ($running -join ', '))
    exit 2
}
$claude = $null
foreach ($name in @('claude.exe', 'claude.cmd')) {
    $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { $claude = $command.Source; break }
}
if (-not $claude) {
    Write-Output 'ERROR: claude.exe / claude.cmd not found in PATH'
    exit 127
}

$base = Join-Path (Split-Path -Parent $TaskPath) ([IO.Path]::GetFileNameWithoutExtension($TaskPath))
$lastFile = "${base}_last.md"
$errLog = "${base}_claude.log"
$outLog = "${base}_claude.jsonl"
$sessionId = [guid]::NewGuid().ToString()
$argList = @('-p', '--output-format', 'stream-json', '--verbose', '--permission-prompts', 'none',
    '--disallowedTools', 'Monitor',
    '--disable-slash-commands', '--session-id', $sessionId,
    '--append-system-prompt-file', (ConvertTo-QuotedArg $promptFile))
if ($Mode -eq 'read-only') {
    $toolNames = 'Read,Grep,Glob'
    if ($Search) { $toolNames += ',WebSearch,WebFetch' }
    $argList += @('--tools', $toolNames, '--permission-mode', 'dontAsk', '--strict-mcp-config')
} else {
    $argList += @('--permission-mode', 'bypassPermissions')
}
foreach ($d in $AddDir) { $argList += @('--add-dir', (ConvertTo-QuotedArg (Resolve-Path -LiteralPath $d).Path)) }
if ($Model) { $argList += @('--model', (ConvertTo-QuotedArg $Model)) }
if ($Effort) { $argList += @('--effort', (ConvertTo-QuotedArg $Effort)) }

$started = Get-Date
$isoFormat = "yyyy-MM-dd'T'HH:mm:ss.fff'Z'"
$run = [ordered]@{
    run_id = [guid]::NewGuid().ToString()
    codex_thread_id = $env:CODEX_THREAD_ID
    claude_session_id = $sessionId
    crew_session_id = $env:CREW_SESSION_ID
    crew_execution_id = $env:CREW_EXECUTION_ID
    task_file = $TaskPath
    workdir = $WorkDir
    mode = $Mode
    model = $(if ($Model) { $Model } else { $null })
    effort = $(if ($Effort) { $Effort } else { $null })
    started_at = $started.ToUniversalTime().ToString($isoFormat)
    ended_at = $null
    exit_code = $null
    pid = $null
    pid_created = $null
}
# Clear inherited dispatcher identities only in this script process.
foreach ($name in @('CLAUDE_CODE_SESSION_ID', 'CREW_SESSION_ID', 'CREW_EXECUTION_ID', 'CREW_GOAL_ID', 'CREW_NAME', 'CREW_ROLE', 'CREW_SPAWN_ID', 'CODEX_THREAD_ID')) {
    [Environment]::SetEnvironmentVariable($name, $null, 'Process')
}
[IO.File]::Delete($lastFile)
# Hold the process handle; do not wait for orphaned daemons such as adb.
$proc = Start-Process -FilePath $claude -ArgumentList ($argList -join ' ') -WorkingDirectory $WorkDir `
    -RedirectStandardInput $TaskPath -RedirectStandardOutput $outLog -RedirectStandardError $errLog `
    -WindowStyle Hidden -PassThru
$null = $proc.Handle
$run.pid = $proc.Id
$run.pid_created = 'win:' + $proc.StartTime.ToFileTimeUtc()
Write-CrewRun $run

if (-not $NoWatch) {
    $watcher = Join-Path $PSScriptRoot 'watch_log.ps1'
    $title = 'Claude - ' + [IO.Path]::GetFileNameWithoutExtension($TaskPath)
    $watchCmd = 'conhost.exe powershell.exe -NoProfile -ExecutionPolicy Bypass -File ' + (ConvertTo-QuotedArg $watcher) +
        ' -LogPath ' + (ConvertTo-QuotedArg $outLog) + ' -WatchPid ' + $proc.Id + ' -Title ' + (ConvertTo-QuotedArg $title)
    # The explicitly requested viewer is minimized without activation.
    try {
        $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]7 }
        $viewer = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $watchCmd; ProcessStartupInformation = $startup }
        if ($viewer.ReturnValue -ne 0) { Write-Output "WARN: log viewer not opened, Win32_Process.Create returned $($viewer.ReturnValue)" }
        else { Write-Output "viewer_pid=$($viewer.ProcessId)" }
    } catch [Microsoft.Management.Infrastructure.CimException] {
        Write-Output "WARN: log viewer not opened: $($_.Exception.GetType().Name): $($_.Exception.Message)"
    }
}

$proc.WaitForExit()
$code = $proc.ExitCode
$ended = Get-Date
$minutes = [math]::Round(($ended - $started).TotalMinutes, 1)
$resultEvent = $null
$parseFailure = $false
foreach ($line in [IO.File]::ReadLines($outLog, [Text.Encoding]::UTF8)) {
    try {
        $event = ConvertFrom-Json -InputObject $line -ErrorAction Stop
    } catch [ArgumentException] {
        Write-Output "ERROR: invalid stream-json: $($_.Exception.Message)"
        $parseFailure = $true
        break
    }
    if ($event.type -eq 'result') { $resultEvent = $event }
}
if ($parseFailure -or $null -eq $resultEvent) {
    if ($null -eq $resultEvent) {
        Write-Output "ERROR: no result event; inspect $errLog and $outLog"
        Write-Output ([IO.File]::ReadAllText($errLog, [Text.Encoding]::UTF8))
    }
    if ($code -eq 0) { $code = 1 }
} elseif ($resultEvent.is_error -eq $true) {
    Write-Output ("ERROR: Claude result: " + ($resultEvent | ConvertTo-Json -Depth 20 -Compress))
    if ($code -eq 0) { $code = 1 }
} elseif ($resultEvent.result -isnot [string] -or $resultEvent.result.Length -eq 0) {
    Write-Output ("ERROR: result event has no nonempty text: " + ($resultEvent | ConvertTo-Json -Depth 20 -Compress))
    if ($code -eq 0) { $code = 1 }
} elseif (-not $parseFailure) {
    [IO.File]::WriteAllText($lastFile, $resultEvent.result, $utf8)
}
$run.ended_at = $ended.ToUniversalTime().ToString($isoFormat)
$run.exit_code = $code
Write-CrewRun $run
$turns = $resultEvent.num_turns
$cost = $resultEvent.total_cost_usd
Write-Output "claude exit=$code minutes=$minutes mode=$Mode workdir=$WorkDir session=$sessionId turns=$turns cost_usd=$cost"
Write-Output "last=$lastFile"
Write-Output "log=$errLog"
exit $code
