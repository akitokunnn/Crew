# Dispatch a task file to Codex (codex exec) non-interactively.
# The task file is fed through stdin as raw UTF-8 bytes, so stdin closes at EOF and
# non-ASCII text never goes through the command line.
# Outputs next to the task file: <task>_last.md, <task>_codex.log (stderr), <task>_codex.stdout.log
# Exit codes: 0 ok, 2 blocked by -RequireClosed, 64 bad input, 127 codex not found, else codex's own code.

param(
    [Parameter(Mandatory = $true)][string]$TaskFile,
    [string]$WorkDir,
    [ValidateSet('read-only', 'workspace-write', 'danger-full-access')][string]$Sandbox = 'workspace-write',
    [string[]]$RequireClosed = @(),
    [string[]]$AddDir = @(),
    [string]$Model,
    [string]$Effort,
    [switch]$NoWatch
)

$ErrorActionPreference = 'Stop'

function ConvertTo-QuotedArg([string]$s) {
    return '"' + $s.TrimEnd('\').Replace('"', '\"') + '"'
}

function Write-CrewRun([Collections.IDictionary]$Run) {
    # Multiple Crew members can finish at once. Serialize whole JSONL appends, not the Codex runs.
    $mutex = $null
    $locked = $false
    try {
        $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        $mutex = New-Object Threading.Mutex($false, "Local\Crew.CodexRuns.$sid")
        try { $locked = $mutex.WaitOne(5000) }
        catch [Threading.AbandonedMutexException] { $locked = $true }
        if (-not $locked) { throw [IO.IOException]::new('Timed out appending the Crew dispatch record') }
        $crewHome = Join-Path $env:USERPROFILE '.crew'
        New-Item -ItemType Directory -Force -Path $crewHome | Out-Null
        [IO.File]::AppendAllText((Join-Path $crewHome 'codex_runs.jsonl'),
            (($Run | ConvertTo-Json -Compress) + "`n"), (New-Object Text.UTF8Encoding $false))
    } catch {
        # Analytics must not replace a real Codex result or leave a launched process unobserved.
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
$WorkDir = (Resolve-Path -LiteralPath $WorkDir).Path

$running = @()
foreach ($name in $RequireClosed) {
    if (Get-Process -Name $name -ErrorAction SilentlyContinue) { $running += $name }
}
if ($running.Count -gt 0) {
    Write-Output ("BLOCKED: close these programs first: " + ($running -join ', '))
    exit 2
}

$codex = $null
foreach ($n in @('codex.exe', 'codex.cmd')) {
    $c = Get-Command $n -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { $codex = $c.Source; break }
}
if (-not $codex) {
    Write-Output 'ERROR: codex.exe / codex.cmd not found in PATH'
    exit 127
}

$base = Join-Path (Split-Path -Parent $TaskPath) ([IO.Path]::GetFileNameWithoutExtension($TaskPath))
$lastFile = "$base`_last.md"
$errLog = "$base`_codex.log"
$outLog = "$base`_codex.stdout.log"

$argList = @('exec', '--cd', (ConvertTo-QuotedArg $WorkDir), '--ephemeral', '--json', '--sandbox', $Sandbox, '--color', 'never', '-o', (ConvertTo-QuotedArg $lastFile),
    '-c', 'model_reasoning_summary=detailed', '-c', 'model_supports_reasoning_summaries=true')
foreach ($d in $AddDir) {
    $argList += @('--add-dir', (ConvertTo-QuotedArg (Resolve-Path -LiteralPath $d).Path))
}
if ($Model) { $argList += @('-m', $Model) }
if ($Effort) { $argList += @('-c', "model_reasoning_effort=$Effort") }
$argList += '-'

$started = Get-Date
$isoFormat = "yyyy-MM-dd'T'HH:mm:ss.fff'Z'"
$run = [ordered]@{
    run_id            = [guid]::NewGuid().ToString()
    claude_session_id = $env:CLAUDE_CODE_SESSION_ID
    task_file         = $TaskPath
    workdir           = $WorkDir
    sandbox           = $Sandbox
    model             = $(if ($Model) { $Model } else { $null })
    effort            = $(if ($Effort) { $Effort } else { $null })
    started_at        = $started.ToUniversalTime().ToString($isoFormat)
    ended_at          = $null
    exit_code         = $null
    ephemeral         = $true
    usage             = $null
}
# Not -Wait: it also waits for orphaned daemons the task spawned (e.g. adb server started by UE tests).
$proc = Start-Process -FilePath $codex -ArgumentList ($argList -join ' ') -WorkingDirectory $WorkDir `
    -RedirectStandardInput $TaskPath -RedirectStandardOutput $outLog -RedirectStandardError $errLog `
    -NoNewWindow -PassThru
$null = $proc.Handle
Write-CrewRun $run

if (-not $NoWatch) {
    $watcher = Join-Path $PSScriptRoot 'watch_log.ps1'
    $title = 'Codex - ' + [IO.Path]::GetFileNameWithoutExtension($TaskPath)
    $watchCmd = 'conhost.exe powershell.exe -NoProfile -ExecutionPolicy Bypass -File ' + (ConvertTo-QuotedArg $watcher) +
        ' -LogPath ' + (ConvertTo-QuotedArg $outLog) + ' -WatchPid ' + $proc.Id + ' -Title ' + (ConvertTo-QuotedArg $title)
    # Created through WMI so the viewer does not inherit this shell's foreground rights (the shell descends
    # from the editor the user is typing in), and shown minimized without activation (7). Explicit conhost
    # because Windows Terminal's default-terminal handoff ignores that show state.
    try {
        $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]7 }
        $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $watchCmd; ProcessStartupInformation = $startup }
        if ($r.ReturnValue -ne 0) { Write-Output "WARN: log viewer not opened, Win32_Process.Create returned $($r.ReturnValue)" }
    } catch {
        Write-Output "WARN: log viewer not opened: $($_.Exception.GetType().Name): $($_.Exception.Message)"
    }
}

$proc.WaitForExit()
$code = $proc.ExitCode
$ended = Get-Date
$minutes = [math]::Round(($ended - $started).TotalMinutes, 1)

# Append the completion with the same identity. Crew upserts both records into one run.
$run.ended_at = $ended.ToUniversalTime().ToString($isoFormat)
$run.exit_code = $code
try {
    $totals = @{ input_tokens = [long]0; cached_input_tokens = [long]0; output_tokens = [long]0; reasoning_output_tokens = [long]0 }
    $turns = 0
    foreach ($line in [IO.File]::ReadLines($outLog)) {
        $event = $line | ConvertFrom-Json
        if ($event.type -ne 'turn.completed') { continue }
        foreach ($key in @('input_tokens', 'cached_input_tokens', 'output_tokens')) {
            $value = $event.usage.$key
            if ($null -eq $value -or $value -is [bool] -or $value -notmatch '^\d+$') { throw "Invalid Codex usage: $key" }
            $totals[$key] += [long]$value
        }
        if ($null -ne $event.usage.reasoning_output_tokens) {
            if ($event.usage.reasoning_output_tokens -is [bool] -or $event.usage.reasoning_output_tokens -notmatch '^\d+$') {
                throw 'Invalid Codex usage: reasoning_output_tokens'
            }
            $totals.reasoning_output_tokens += [long]$event.usage.reasoning_output_tokens
        }
        $turns++
    }
    if ($turns -gt 0) { $run.usage = $totals }
} catch {
    Write-Output "WARN: Codex usage not recorded: $($_.Exception.Message)"
}
Write-CrewRun $run

Write-Output "codex exit=$code minutes=$minutes sandbox=$Sandbox workdir=$WorkDir"
Write-Output "last=$lastFile"
Write-Output "log=$errLog"
Write-Output "events=$outLog"
exit $code
