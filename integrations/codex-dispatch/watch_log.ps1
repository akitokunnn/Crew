# Live viewer for a codex exec log, opened in its own console window by run_codex.ps1.
# Colors section headers, folds long command output, closes 10 s after the watched process exits.

param(
    [Parameter(Mandatory = $true)][string]$LogPath,
    [Parameter(Mandatory = $true)][int]$WatchPid,
    [string]$Title = 'Codex',
    [int]$MaxOutputLines = 12
)

$Host.UI.RawUI.WindowTitle = $Title
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

# Hold a handle to the watched process: Windows does not reuse a PID while a handle is open,
# so polling by PID alone can latch onto an unrelated process after codex exits.
$watched = Get-Process -Id $WatchPid -ErrorAction SilentlyContinue
if ($watched) { $null = $watched.Handle }

while (-not (Test-Path -LiteralPath $LogPath)) { Start-Sleep -Milliseconds 300 }
$fs = [IO.File]::Open($LogPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
$reader = New-Object IO.StreamReader($fs, [Text.Encoding]::UTF8)

function Show-Line([string]$line) {
    try { $event = ConvertFrom-Json -InputObject $line -ErrorAction Stop }
    catch [ArgumentException] { Write-Host ('[invalid event] ' + $_.Exception.Message) -ForegroundColor Red; return }
    if ($event.type -eq 'item.started' -and $event.item.type -eq 'command_execution') {
        Write-Host ('[exec] ' + $event.item.command) -ForegroundColor Yellow
    } elseif ($event.type -eq 'item.completed') {
        $item = $event.item
        if ($item.type -eq 'agent_message' -or $item.type -eq 'reasoning') {
            $label = if ($item.type -eq 'agent_message') { '[codex]' } else { '[thinking]' }
            Write-Host $label -ForegroundColor Cyan
            Write-Host $item.text
        } elseif ($item.type -eq 'command_execution') {
            Write-Host ('[exit] ' + $item.exit_code) -ForegroundColor DarkGray
            $lines = @($item.aggregated_output -split "`r?`n")
            $lines | Select-Object -First $MaxOutputLines | ForEach-Object { Write-Host ('    ' + $_) -ForegroundColor DarkGray }
            if ($lines.Count -gt $MaxOutputLines) { Write-Host ('    ... more output (see events log)') -ForegroundColor DarkGray }
        }
    } elseif ($event.type -eq 'turn.completed') {
        Write-Host ('[tokens] input=' + $event.usage.input_tokens + ' output=' + $event.usage.output_tokens) -ForegroundColor Green
    } elseif ($event.type -eq 'turn.failed' -or $event.type -eq 'error') {
        Write-Host $line -ForegroundColor Red
    }
}

$pending = ''
$finished = $false
while ($true) {
    $chunk = $reader.ReadToEnd()
    if ($chunk.Length -gt 0) {
        $text = $pending + $chunk
        $parts = $text -split "`r?`n"
        $pending = $parts[-1]
        for ($i = 0; $i -lt $parts.Length - 1; $i++) { Show-Line $parts[$i] }
        continue
    }
    if ($finished) { break }
    if (-not $watched -or $watched.HasExited) { $finished = $true; continue }
    Start-Sleep -Milliseconds 300
}
if ($pending.Length -gt 0) { Show-Line $pending }
$reader.Close()

Write-Host ''
Write-Host '=== Codex finished. This window closes in 10 seconds (full log is on disk). ===' -ForegroundColor Green
Start-Sleep -Seconds 10
