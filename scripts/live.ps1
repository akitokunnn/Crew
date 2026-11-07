<#
Crew 本地试用与推送。正式 Crew 的“后台程序来源”指向 C:\Tools\Crew-live\plugin，
修好的分支先合入本地 live 分支直接试用，攒一批再用一个 PR 合进 main。

用法（在任意 Crew 工作目录执行）：
  powershell -ExecutionPolicy Bypass -File scripts\live.ps1 status
  powershell -ExecutionPolicy Bypass -File scripts\live.ps1 merge <分支>
  powershell -ExecutionPolicy Bypass -File scripts\live.ps1 sync
  powershell -ExecutionPolicy Bypass -File scripts\live.ps1 ship [-SkipCI]

  status  列出 live 里还没合进 main 的提交
  merge   把修好的分支快进合入 live；分支必须已包含 live（先在分支里 git merge live）
  sync    把远端 main 上别人合入的新提交并进 live
  ship    把 live 推上去开一个 PR 合进 main；默认等 CI 通过再合并，-SkipCI 跳过 CI
#>
param(
  [Parameter(Position = 0)][ValidateSet('status', 'merge', 'sync', 'ship')][string]$Command = 'status',
  [Parameter(Position = 1)][string]$Branch,
  [switch]$SkipCI
)
# git 会把普通提示写到 stderr；Windows PowerShell 在 Stop 模式下会把它当成异常，这里一律按退出码判断。
$ErrorActionPreference = 'Continue'
$Live = if ($env:CREW_LIVE) { $env:CREW_LIVE } else { 'C:\Tools\Crew-live' }
$MainCheckout = if ($env:CREW_MAIN_CHECKOUT) { $env:CREW_MAIN_CHECKOUT } else { 'C:\Tools\Crew' }
$Remote = 'origin'
$Base = 'main'

function Fail([string]$Message) { Write-Host $Message -ForegroundColor Red; exit 1 }

function LiveGit {
  $output = & git.exe -C $Live @args 2>&1
  if ($LASTEXITCODE -ne 0) { Fail ("git " + ($args -join ' ') + " 失败：`n" + ($output -join "`n")) }
  return $output
}

function Test-LiveGit { & git.exe -C $Live @args *> $null; return $LASTEXITCODE -eq 0 }

function Assert-Live {
  if (-not (Test-Path -LiteralPath (Join-Path $Live '.git'))) { Fail "找不到 live 工作目录：$Live" }
  $branch = (LiveGit rev-parse --abbrev-ref HEAD) -join ''
  if ($branch -ne 'live') { Fail "$Live 当前不在 live 分支（而是 $branch），先处理再继续。" }
  $dirty = LiveGit status --porcelain --untracked-files=no
  if ($dirty) { Fail ("$Live 有未提交的改动，live 只接收合并，不在里面直接改：`n" + ($dirty -join "`n")) }
}

# 按改动位置告诉用户怎么让正式 Crew 用上新代码。
function Show-Effect([string]$Before, [string]$After) {
  if ($Before -eq $After) { Write-Host '没有新提交。'; return }
  $rows = LiveGit diff --name-status $Before $After
  $backend = $false; $web = $false; $desktop = $false; $bundled = @()
  $mainCjs = Join-Path $Live 'desktop\src\main.cjs'
  $shellText = if (Test-Path -LiteralPath $mainCjs) { Get-Content -Raw -LiteralPath $mainCjs } else { '' }
  foreach ($row in $rows) {
    $parts = $row -split "`t"
    $status = $parts[0]; $file = $parts[-1]
    if ($file -like 'desktop/*') { $desktop = $true }
    elseif ($file -like 'plugin/crew/web/*' -and $status -eq 'M') {
      $web = $true
      $name = Split-Path $file -Leaf
      if ($shellText.Contains("'$name'")) { $bundled += $name }
    }
    elseif ($file -like 'plugin/*') { $backend = $true }
  }
  Write-Host ''
  if ($backend) { Write-Host '后台有改动：退出 Crew（托盘菜单或 Ctrl+Q）后重新打开。' -ForegroundColor Yellow }
  elseif ($web) { Write-Host '只改了页面：在 Crew 里按 Ctrl+R 刷新。' -ForegroundColor Green }
  if ($bundled.Count -gt 0) {
    Write-Host ('桌面首页设置里的这些页面读取的是安装包自带的版本，要重新打包才变：' + (($bundled | Sort-Object -Unique) -join '、')) -ForegroundColor Yellow
  }
  if ($desktop) { Write-Host '桌面外壳有改动：重新打包安装后才生效，可以攒到下次打包。' -ForegroundColor Yellow }
  if (-not ($backend -or $web -or $desktop)) { Write-Host '只改了文档或规则，Crew 无需操作。' -ForegroundColor Green }
}

# 先确认没有冲突再合并，live 永远不会停在冲突一半的状态。
function Merge-Remote {
  LiveGit fetch --quiet $Remote | Out-Null
  $upstream = "$Remote/$Base"
  if (Test-LiveGit merge-base --is-ancestor $upstream HEAD) { return $false }
  & git.exe -C $Live merge-tree --write-tree HEAD $upstream *> $null
  if ($LASTEXITCODE -ne 0) {
    Fail "远端 $Base 的新提交与 live 有冲突。先开一个工作目录：git worktree add C:\Tools\Crew-fixes\sync-main -b claude/sync-main live，在里面 git merge $upstream 解决冲突并验证，再用 merge 命令合入 live。"
  }
  LiveGit merge --no-edit $upstream | Out-Null
  return $true
}

function Show-Pending {
  $pending = LiveGit log --oneline --no-merges "$Remote/$Base..HEAD"
  if ($pending) {
    Write-Host ("live 里还没合进 $Base 的提交（" + @($pending).Count + ' 个）：')
    $pending | ForEach-Object { Write-Host "  $_" }
  } else { Write-Host "live 与 $Base 一致，没有待推送的改动。" }
}

switch ($Command) {
  'status' {
    Assert-Live
    LiveGit fetch --quiet $Remote | Out-Null
    Show-Pending
    $pr = & gh pr list --head live --base $Base --state open --json number,url --jq '.[0].url' 2>$null
    if ($pr) { Write-Host "已有待合并的批次 PR：$pr" }
  }
  'merge' {
    if (-not $Branch) { Fail '用法：live.ps1 merge <分支>' }
    Assert-Live
    if (-not (Test-LiveGit rev-parse --verify --quiet "refs/heads/$Branch")) { Fail "本地没有分支 $Branch。" }
    if (-not (Test-LiveGit merge-base --is-ancestor live "refs/heads/$Branch")) {
      Fail "$Branch 还没包含 live 的最新内容。在这个分支的工作目录里先执行 git merge live，解决冲突、跑相关测试，再重新运行本命令。"
    }
    $before = (LiveGit rev-parse HEAD) -join ''
    LiveGit merge --ff-only "refs/heads/$Branch" | Out-Null
    $after = (LiveGit rev-parse HEAD) -join ''
    Write-Host "已把 $Branch 合入 live。"
    Show-Effect $before $after
  }
  'sync' {
    Assert-Live
    $before = (LiveGit rev-parse HEAD) -join ''
    if (Merge-Remote) { Write-Host "已把远端 $Base 的新提交并进 live。" } else { Write-Host "远端 $Base 没有 live 缺少的提交。" }
    Show-Effect $before ((LiveGit rev-parse HEAD) -join '')
  }
  'ship' {
    Assert-Live
    $before = (LiveGit rev-parse HEAD) -join ''
    if (Merge-Remote) { Write-Host "推送前先并入了远端 $Base 的新提交。"; Show-Effect $before ((LiveGit rev-parse HEAD) -join '') }
    $subjects = LiveGit log --format='- %s' --no-merges "$Remote/$Base..HEAD"
    if (-not $subjects) { Write-Host "live 与 $Base 一致，没有要推送的改动。"; exit 0 }
    if ($SkipCI) { LiveGit commit --allow-empty --quiet -m '[skip ci] 本批改动已在本地试用，跳过 CI' | Out-Null }
    LiveGit push --quiet $Remote live | Out-Null
    $count = @($subjects).Count
    $number = & gh pr list --head live --base $Base --state open --json number --jq '.[0].number' 2>$null
    if (-not $number) {
      $body = "本地试用通过的一批改动：`n`n" + ($subjects -join "`n")
      $url = & gh pr create --base $Base --head live --title "合入本地试用通过的 $count 个改动" --body $body
      if ($LASTEXITCODE -ne 0) { Fail '创建 PR 失败。' }
      $number = ($url -split '/')[-1]
    }
    Write-Host "批次 PR：#$number"
    if (-not $SkipCI) {
      Write-Host '等待 CI……'
      $registered = $false
      for ($i = 0; $i -lt 18 -and -not $registered; $i++) {
        $checks = & gh pr checks $number --json name --jq 'length' 2>$null
        if ($checks -and [int]$checks -gt 0) { $registered = $true } else { Start-Sleep -Seconds 10 }
      }
      if ($registered) {
        & gh pr checks $number --watch --fail-fast
        if ($LASTEXITCODE -ne 0) { Fail "CI 没通过，PR #$number 保留未合并。修好后重新 merge 进 live，再运行 ship。" }
      } else { Write-Host '这个 PR 没有触发 CI，直接合并。' }
    }
    & gh pr merge $number --merge
    if ($LASTEXITCODE -ne 0) { Fail "合并 PR #$number 失败。" }
    LiveGit fetch --quiet $Remote | Out-Null
    LiveGit merge --ff-only "$Remote/$Base" | Out-Null
    Write-Host "已合进 $Base，live 已对齐。"
    $mainBranch = (& git.exe -C $MainCheckout rev-parse --abbrev-ref HEAD 2>$null) -join ''
    $mainDirty = & git.exe -C $MainCheckout status --porcelain --untracked-files=no 2>$null
    if ($mainBranch -eq $Base -and -not $mainDirty) {
      & git.exe -C $MainCheckout merge --ff-only --quiet "$Remote/$Base" 2>$null
      if ($LASTEXITCODE -eq 0) { Write-Host "主目录 $MainCheckout 也已更新到最新 $Base。" }
    } elseif (Test-Path -LiteralPath $MainCheckout) {
      Write-Host "主目录 $MainCheckout 不在干净的 $Base 上，没有替它更新。"
    }
  }
}
