# Stream-json viewer. The requested window closes 10 seconds after completion.
param(
    [Parameter(Mandatory = $true)][string]$LogPath,
    [Parameter(Mandatory = $true)][int]$WatchPid,
    [string]$Title = 'Claude',
    [int]$MaxOutputLines = 12
)
$ErrorActionPreference = 'Stop'
$Host.UI.RawUI.WindowTitle = $Title
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$watched = Get-Process -Id $WatchPid -ErrorAction SilentlyContinue
if ($watched) { $null = $watched.Handle }
while (-not (Test-Path -LiteralPath $LogPath)) {
    if (-not $watched -or $watched.HasExited) { throw "Log file not created: $LogPath" }
    Start-Sleep -Milliseconds 300
}
$fs = [IO.File]::Open($LogPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
$reader = New-Object IO.StreamReader($fs, [Text.Encoding]::UTF8)

function Show-Blocks($Blocks) {
    foreach ($block in $Blocks) {
        switch ($block.type) {
            'text' { Write-Host ('[claude] ' + $block.text) -ForegroundColor Cyan }
            'thinking' { if ($block.thinking) { Write-Host ('[thinking] ' + $block.thinking) -ForegroundColor Magenta } }
            'tool_use' {
                $detail = $block.input.command
                if (-not $detail) { $detail = $block.input.file_path }
                if (-not $detail) { $detail = $block.input.pattern }
                if (-not $detail) { $detail = $block.input | ConvertTo-Json -Depth 10 -Compress }
                $detail = [string]$detail
                if ($detail.Length -gt 200) { $detail = $detail.Substring(0, 200) + '...' }
                Write-Host ("[tool] $($block.name): $detail") -ForegroundColor Yellow
            }
            'tool_result' {
                $color = if ($block.is_error) { 'Red' } else { 'DarkGray' }
                $body = if ($block.content -is [string]) { $block.content } else {
                    ($block.content | ForEach-Object {
                        if ($_.type -eq 'text') { $_.text } else { $_ | ConvertTo-Json -Depth 10 -Compress }
                    }) -join "`n"
                }
                $lines = @(([string]$body) -split "`r?`n")
                Write-Host '[tool_result]' -ForegroundColor $color
                $lines | Select-Object -First $MaxOutputLines | ForEach-Object { Write-Host ('    ' + $_) -ForegroundColor $color }
                if ($lines.Count -gt $MaxOutputLines) {
                    Write-Host ("    ... $($lines.Count - $MaxOutputLines) more lines (see jsonl)") -ForegroundColor DarkGray
                }
            }
        }
    }
}

function Show-Line([string]$Line) {
    $jsonError = @()
    $event = ConvertFrom-Json -InputObject $Line -ErrorAction SilentlyContinue -ErrorVariable jsonError
    if ($jsonError.Count -gt 0) { Write-Host $Line; return }
    switch ($event.type) {
        'system' {
            if ($event.subtype -eq 'init') { Write-Host ("[init] model=$($event.model) cwd=$($event.cwd)") -ForegroundColor Green }
        }
        'assistant' { Show-Blocks $event.message.content }
        'user' { Show-Blocks $event.message.content }
        'result' {
            $color = if ($event.is_error) { 'Red' } else { 'Green' }
            Write-Host ("[result] duration_ms=$($event.duration_ms) turns=$($event.num_turns) cost_usd=$($event.total_cost_usd) error=$($event.is_error)") -ForegroundColor $color
        }
    }
}

$pending = ''
$finished = $false
while ($true) {
    $chunk = $reader.ReadToEnd()
    if ($chunk.Length -gt 0) {
        $parts = ($pending + $chunk) -split "`r?`n"
        $pending = $parts[-1]
        for ($index = 0; $index -lt $parts.Length - 1; $index++) { Show-Line $parts[$index] }
        continue
    }
    if ($finished) { break }
    if (-not $watched -or $watched.HasExited) { $finished = $true; continue }
    Start-Sleep -Milliseconds 300
}
if ($pending.Length -gt 0) { Show-Line $pending }
$reader.Close()
Write-Host '=== Claude finished. This window closes in 10 seconds (full log is on disk). ===' -ForegroundColor Green
Start-Sleep -Seconds 10
