#!/usr/bin/env python3
"""Crew 本地试用与推送，Windows 和 Mac 共用。

live 是两台电脑共用的集成分支，GitHub 上有一份。每台电脑各有一个 live 工作目录，
正式 Crew 的“后台程序来源”指向其中的 plugin。修好的分支先合入本机 live 直接试用，
用户说“推live”时再 push 上去，另一台电脑 sync 一下就能试用；用户说“合并main”时用一个 PR 合进 main。

用法（在任意 Crew 工作目录执行）：
  python scripts/live.py setup          第一次在这台电脑上建 live 工作目录
  python scripts/live.py status         live 里还没合进 main 的提交，以及和远端 live 的差异
  python scripts/live.py merge <分支>    把修好的分支合入本机 live（不推送）；本地分支或 origin/<分支> 都行
  python scripts/live.py push           “推live”：并入远端 live 的新提交后把本机 live 推上去
  python scripts/live.py sync           拉另一台电脑推的 live 和别人直接合进 main 的提交（不推送）
  python scripts/live.py ship [--skip-ci]  “合并main”：开批次 PR 合进 main；默认等 CI 通过再合并

live 默认在 Windows 的 C:\\Tools\\Crew-live、Mac 的 ~/Tools/Crew-live，可用 CREW_LIVE 改。
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


REMOTE, BASE, LIVE = 'origin', 'main', 'live'
WINDOWS = os.name == 'nt'
QUIT, RELOAD = ('托盘菜单或 Ctrl+Q', 'Ctrl+R') if WINDOWS else ('⌘Q', '⌘R')


class Failure(Exception):
    pass


def run(cwd, *args, check=True):
    result = subprocess.run(['git', '-C', str(cwd), *args], capture_output=True, text=True,
                            encoding='utf-8', errors='replace')
    if check and result.returncode:
        raise Failure(f"git {' '.join(args)} 失败：\n{(result.stderr or result.stdout).strip()}")
    return result


def git(cwd, *args):
    return run(cwd, *args).stdout.strip()


def ok(cwd, *args):
    return run(cwd, *args, check=False).returncode == 0


def worktrees(repo):
    """(path, branch) of every worktree; the first is the main checkout."""
    result, path = [], None
    for line in git(repo, 'worktree', 'list', '--porcelain').splitlines():
        if line.startswith('worktree '):
            path = Path(line[len('worktree '):])
            result.append([path, None])
        elif line.startswith('branch ') and result:
            result[-1][1] = line[len('branch refs/heads/'):]
    return [tuple(item) for item in result]


def default_live():
    configured = os.environ.get('CREW_LIVE')
    if configured:
        return Path(configured)
    return Path(r'C:\Tools\Crew-live') if WINDOWS else Path.home() / 'Tools' / 'Crew-live'


def find_live(repo):
    configured = os.environ.get('CREW_LIVE')
    if configured:
        return Path(configured)
    for path, branch in worktrees(repo):
        if branch == LIVE:
            return path
    raise Failure('这台电脑还没有 live 工作目录，先运行：python scripts/live.py setup')


def assert_live(live):
    if not (live / '.git').exists():
        raise Failure(f'找不到 live 工作目录：{live}')
    branch = git(live, 'rev-parse', '--abbrev-ref', 'HEAD')
    if branch != LIVE:
        raise Failure(f'{live} 当前不在 live 分支（而是 {branch}），先处理再继续。')
    dirty = git(live, 'status', '--porcelain', '--untracked-files=no')
    if dirty:
        raise Failure(f'{live} 有未提交的改动，live 只接收合并，不在里面直接改：\n{dirty}')


def fixes_dir(live):
    return live.parent / 'Crew-fixes'


def absorb(live, upstream, label):
    """Merge upstream into live when it has new commits; live never stops halfway through a conflict."""
    if not ok(live, 'rev-parse', '--verify', '--quiet', upstream):
        return False
    if ok(live, 'merge-base', '--is-ancestor', upstream, 'HEAD'):
        return False
    if ok(live, 'merge-base', '--is-ancestor', 'HEAD', upstream):
        git(live, 'merge', '--ff-only', upstream)
        return True
    if not ok(live, 'merge-tree', '--write-tree', 'HEAD', upstream):
        work = fixes_dir(live) / 'sync-live'
        raise Failure(f'{label}的新提交和本地 live 冲突。先开一个工作目录解决：\n'
                      f'  git worktree add "{work}" -b claude/sync-live live\n'
                      f'在里面 git merge {upstream}，解决冲突并验证后提交，再运行 python scripts/live.py merge claude/sync-live；'
                      f'要推到 GitHub 时再运行 python scripts/live.py push')
    git(live, 'merge', '--no-edit', upstream)
    return True


def pull(live):
    """Bring in what the other computer pushed to live."""
    git(live, 'fetch', '--quiet', REMOTE)
    return absorb(live, f'{REMOTE}/{LIVE}', '远端 live（另一台电脑推的）')


def unpushed(live):
    """Commits on local live that remote live does not have yet."""
    if not ok(live, 'rev-parse', '--verify', '--quiet', f'{REMOTE}/{LIVE}'):
        return git(live, 'log', '--oneline', '--no-merges', f'{REMOTE}/{BASE}..HEAD')
    return git(live, 'log', '--oneline', '--no-merges', f'{REMOTE}/{LIVE}..HEAD')


def push(live):
    if ok(live, 'rev-parse', '--verify', '--quiet', f'{REMOTE}/{LIVE}') and \
            git(live, 'rev-parse', 'HEAD') == git(live, 'rev-parse', f'{REMOTE}/{LIVE}'):
        return
    result = run(live, 'push', '--quiet', REMOTE, LIVE, check=False)
    if result.returncode:
        raise Failure('推送 live 失败，多半是另一台电脑刚推过。重新运行一次本命令即可：\n' + result.stderr.strip())
    print('已推送到远端 live，另一台电脑运行 python scripts/live.py sync 后也能试用。')


def show_effect(live, before, after):
    """Tell the user how this computer's Crew picks up the new code."""
    if before == after:
        print('没有新提交。')
        return
    backend = web = desktop = False
    bundled = set()
    main_cjs = live / 'desktop' / 'src' / 'main.cjs'
    shell = main_cjs.read_text(encoding='utf-8') if main_cjs.exists() else ''
    for row in git(live, 'diff', '--name-status', before, after).splitlines():
        parts = row.split('\t')
        status, name = parts[0], parts[-1]
        if name.startswith('desktop/'):
            desktop = True
        elif name.startswith(('frontend/', 'plugin/crew/web/')) and status == 'M':
            web = True
            if f"'{Path(name).name}'" in shell:
                bundled.add(Path(name).name)
        elif name.startswith('plugin/'):
            backend = True
    print()
    if backend:
        print(f'后台有改动：退出 Crew（{QUIT}）后重新打开。')
    elif web:
        print(f'只改了页面：在 Crew 里按 {RELOAD} 刷新。')
    if bundled:
        print('桌面首页设置里的这些页面读取的是安装包自带的版本，要重新打包才变：' + '、'.join(sorted(bundled)))
    if desktop:
        print('桌面外壳有改动：重新打包安装后才生效，可以攒到下次打包。')
    if not (backend or web or desktop):
        print('只改了文档或规则，Crew 无需操作。')


def show_pending(live):
    pending = git(live, 'log', '--oneline', '--no-merges', f'{REMOTE}/{BASE}..HEAD')
    if pending:
        print(f'live 里还没合进 {BASE} 的提交（{len(pending.splitlines())} 个）：')
        for line in pending.splitlines():
            print('  ' + line)
    else:
        print(f'live 与 {BASE} 一致，没有待推送的改动。')


def gh(*args, check=True):
    result = subprocess.run(['gh', *args], capture_output=True, text=True, encoding='utf-8', errors='replace')
    if check and result.returncode:
        raise Failure(f"gh {' '.join(args)} 失败：\n{(result.stderr or result.stdout).strip()}")
    return result


def setup(repo):
    for path, branch in worktrees(repo):
        if branch == LIVE:
            print(f'live 工作目录已存在：{path}')
            return
    live = default_live()
    if live.exists():
        raise Failure(f'{live} 已存在但不是 live 工作目录，先确认里面是什么。')
    git(repo, 'fetch', '--quiet', REMOTE)
    if ok(repo, 'rev-parse', '--verify', '--quiet', f'refs/heads/{LIVE}'):
        git(repo, 'worktree', 'add', str(live), LIVE)
    else:
        git(repo, 'worktree', 'add', '--track', '-b', LIVE, str(live), f'{REMOTE}/{LIVE}')
    git(live, 'branch', f'--set-upstream-to={REMOTE}/{LIVE}', LIVE)
    print(f'已建好 live 工作目录：{live}')
    print(f'在 Crew 的“设置 → 连接与目录”里把后台程序来源指向 {live / "plugin"}，正式 Crew 就跑 live 的代码。')
    print(f'修 bug 时从 live 拉分支：git worktree add "{fixes_dir(live) / "<任务>"}" -b claude/<任务> live')


def merge(live, branch):
    """Fast-forward local live to a finished branch; pushing waits for the user's “推live”."""
    assert_live(live)
    before = git(live, 'rev-parse', 'HEAD')
    ref = f'refs/heads/{branch}' if ok(live, 'rev-parse', '--verify', '--quiet', f'refs/heads/{branch}') else branch
    if not ok(live, 'rev-parse', '--verify', '--quiet', ref + '^{commit}'):
        if branch.startswith(f'{REMOTE}/'):
            git(live, 'fetch', '--quiet', REMOTE)
        if not ok(live, 'rev-parse', '--verify', '--quiet', ref + '^{commit}'):
            raise Failure(f'找不到分支 {branch}；另一台电脑推的分支写成 {REMOTE}/<分支>。')
    if not ok(live, 'merge-base', '--is-ancestor', 'HEAD', ref):
        raise Failure(f'{branch} 还没包含本机 live 的最新内容。在这个分支的工作目录里先执行 '
                      f'git merge {LIVE}，解决冲突、跑相关测试并提交，再重新运行本命令。')
    git(live, 'merge', '--ff-only', ref)
    print(f'已把 {branch} 合入本机 live（还没推到 GitHub，用户说“推live”时再推）。')
    show_effect(live, before, git(live, 'rev-parse', 'HEAD'))


def push_live(live):
    """“推live”: take in what the other computer pushed, then publish local live."""
    assert_live(live)
    before = git(live, 'rev-parse', 'HEAD')
    if pull(live):
        print('先并入了另一台电脑推的 live。')
        show_effect(live, before, git(live, 'rev-parse', 'HEAD'))
    pending = unpushed(live)
    if not pending and ok(live, 'rev-parse', '--verify', '--quiet', f'{REMOTE}/{LIVE}') and \
            git(live, 'rev-parse', 'HEAD') == git(live, 'rev-parse', f'{REMOTE}/{LIVE}'):
        print('本机 live 与远端 live 一致，没有要推的提交。')
        return
    if pending:
        print(f'推送这些提交（{len(pending.splitlines())} 个）：')
        for line in pending.splitlines():
            print('  ' + line)
    push(live)


def sync(live):
    assert_live(live)
    before = git(live, 'rev-parse', 'HEAD')
    if pull(live):
        print('已拉取另一台电脑推的 live。')
    if absorb(live, f'{REMOTE}/{BASE}', f'远端 {BASE} '):
        print(f'已把远端 {BASE} 的新提交并进 live。')
    show_effect(live, before, git(live, 'rev-parse', 'HEAD'))
    pending = unpushed(live)
    if pending:
        print(f'本机 live 有 {len(pending.splitlines())} 个提交还没推到 GitHub，用户说“推live”时运行 push。')


def status(live):
    assert_live(live)
    git(live, 'fetch', '--quiet', REMOTE)
    show_pending(live)
    if ok(live, 'rev-parse', '--verify', '--quiet', f'{REMOTE}/{LIVE}'):
        behind = int(git(live, 'rev-list', '--count', f'HEAD..{REMOTE}/{LIVE}'))
        ahead = int(git(live, 'rev-list', '--count', f'{REMOTE}/{LIVE}..HEAD'))
        if behind:
            print(f'远端 live 有 {behind} 个这台电脑还没拉的提交，运行 sync。')
        if ahead:
            print(f'本机 live 有 {ahead} 个还没推上去的提交，用户说“推live”时运行 push。')
    if shutil.which('gh'):
        url = gh('pr', 'list', '--head', LIVE, '--base', BASE, '--state', 'open', '--json', 'url', '--jq', '.[0].url',
                 check=False).stdout.strip()
        if url:
            print(f'已有待合并的批次 PR：{url}')


def ship(live, main_checkout, skip_ci):
    if not shutil.which('gh'):
        raise Failure('这台电脑没装 GitHub CLI（gh）。装好并 gh auth login 后再推，或在另一台电脑上运行 ship。')
    assert_live(live)
    before = git(live, 'rev-parse', 'HEAD')
    pull(live)
    if absorb(live, f'{REMOTE}/{BASE}', f'远端 {BASE} '):
        print(f'推送前先并入了远端 {BASE} 的新提交。')
    show_effect(live, before, git(live, 'rev-parse', 'HEAD'))
    subjects = git(live, 'log', '--format=- %s', '--no-merges', f'{REMOTE}/{BASE}..HEAD')
    if not subjects:
        push(live)
        print(f'live 与 {BASE} 一致，没有要推送的改动。')
        return
    if skip_ci:
        git(live, 'commit', '--allow-empty', '--quiet', '-m', '[skip ci] 本批改动已在本地试用，跳过 CI')
    head = git(live, 'rev-parse', 'HEAD')
    push(live)
    count = len(subjects.splitlines())
    number = gh('pr', 'list', '--head', LIVE, '--base', BASE, '--state', 'open', '--json', 'number', '--jq', '.[0].number').stdout.strip()
    if not number:
        url = gh('pr', 'create', '--base', BASE, '--head', LIVE, '--title', f'合入本地试用通过的 {count} 个改动',
                 '--body', '本地试用通过的一批改动：\n\n' + subjects).stdout.strip()
        number = url.rsplit('/', 1)[-1]
    print(f'批次 PR：#{number}')
    if not skip_ci:
        print('等待 CI（Windows 和 macOS）……')
        expected = {'backend', 'backend (macOS)', 'desktop', 'desktop (macOS)'}
        for _ in range(18):
            checks = gh('pr', 'checks', number, '--json', 'name', '--jq', '.[].name', check=False).stdout.splitlines()
            if expected.issubset(checks):
                break
            time.sleep(10)
        else:
            missing = '、'.join(sorted(expected.difference(checks)))
            raise Failure(f'PR #{number} 尚未登记这些 CI 检查：{missing}。PR 保留未合并。')
        if subprocess.run(['gh', 'pr', 'checks', number, '--watch', '--fail-fast']).returncode:
            raise Failure(f'CI 没通过，PR #{number} 保留未合并。修好后重新 merge 进 live，再运行 ship。')
    gh('pr', 'merge', number, '--merge', '--match-head-commit', head)
    git(live, 'fetch', '--quiet', REMOTE)
    git(live, 'merge', '--ff-only', f'{REMOTE}/{BASE}')
    push(live)
    print(f'已合进 {BASE}，live 已对齐。')
    if git(main_checkout, 'rev-parse', '--abbrev-ref', 'HEAD') == BASE and \
            not git(main_checkout, 'status', '--porcelain', '--untracked-files=no'):
        if ok(main_checkout, 'merge', '--ff-only', '--quiet', f'{REMOTE}/{BASE}'):
            print(f'主目录 {main_checkout} 也已更新到最新 {BASE}。')
    else:
        print(f'主目录 {main_checkout} 不在干净的 {BASE} 上，没有替它更新。')


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors='replace')
    parser = argparse.ArgumentParser(description='Crew 本地试用与推送（Windows 和 Mac 共用）')
    parser.add_argument('command', nargs='?', default='status', choices=('setup', 'status', 'merge', 'push', 'sync', 'ship'))
    parser.add_argument('branch', nargs='?')
    parser.add_argument('--skip-ci', action='store_true')
    args = parser.parse_args(argv)
    try:
        repo = Path(git(Path(__file__).resolve().parent, 'rev-parse', '--show-toplevel'))
        if args.command == 'setup':
            setup(repo)
            return 0
        live = find_live(repo)
        if args.command == 'merge':
            if not args.branch:
                raise Failure('用法：python scripts/live.py merge <分支>')
            merge(live, args.branch)
        elif args.command == 'push':
            push_live(live)
        elif args.command == 'sync':
            sync(live)
        elif args.command == 'status':
            status(live)
        else:
            ship(live, worktrees(repo)[0][0], args.skip_ci)
    except Failure as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
