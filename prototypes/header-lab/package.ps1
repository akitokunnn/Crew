param([string]$OutputRoot,[string]$BundleName = 'Crew-B-Many-Tasks-2026-11-04')
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
if (-not $OutputRoot) { $OutputRoot = Join-Path $repoRoot 'Temp' }
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
$bundlePath = Join-Path $OutputRoot $bundleName
$zipPath = Join-Path $OutputRoot "$bundleName.zip"
if ((Test-Path -LiteralPath $bundlePath) -or (Test-Path -LiteralPath $zipPath)) {
  throw 'Output already exists. Choose a new -OutputRoot to preserve the existing demo.'
}
$demoPath = Join-Path $bundlePath 'prototypes/header-lab'
$fontPath = Join-Path $bundlePath 'plugin/crew/web/fonts'
New-Item -ItemType Directory -Path $demoPath,$fontPath -Force | Out-Null
foreach ($name in 'index.html','demo.html','sidebar-many.html','scenarios.js','lab.js','shared.css','README.md','start-demo.cmd') {
  Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $demoPath
}
foreach ($name in 'variants','previews') {
  Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $demoPath -Recurse
}
foreach ($name in 'HarmonyOS_Sans_SC_Regular.woff2','HarmonyOS_Sans_SC_Medium.woff2','HarmonyOS_Sans_LICENSE.txt') {
  Copy-Item -LiteralPath (Join-Path $repoRoot "plugin/crew/web/fonts/$name") -Destination $fontPath
}
Copy-Item -LiteralPath (Join-Path $repoRoot 'plugin/crew/web/logo.svg') -Destination (Join-Path $bundlePath 'plugin/crew/web')
Set-Content -LiteralPath (Join-Path $bundlePath 'start-demo.cmd') -Encoding ascii -Value '@echo off', 'start "" "%~dp0prototypes\header-lab\sidebar-many.html"'
@{
  kind = 'Crew offline header prototypes'
  sourceCommit = (& git -C $repoRoot rev-parse HEAD)
  entry = 'prototypes/header-lab/sidebar-many.html'
  createdAt = [DateTime]::UtcNow.ToString('o')
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $bundlePath 'source.json') -Encoding utf8
Compress-Archive -LiteralPath $bundlePath -DestinationPath $zipPath -CompressionLevel Optimal
Get-Item -LiteralPath $zipPath | Select-Object FullName,Length
Get-FileHash -LiteralPath $zipPath -Algorithm SHA256
