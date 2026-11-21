# 兼容旧命令：实际逻辑在 live.py，Windows 和 Mac 共用。用法见 live.py 开头。
$forwarded = @($args | ForEach-Object { if ($_ -eq '-SkipCI') { '--skip-ci' } else { $_ } })
& python (Join-Path $PSScriptRoot 'live.py') @forwarded
exit $LASTEXITCODE
