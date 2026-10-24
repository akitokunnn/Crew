"""One finite, isolated Crew repair job; never uses Crew Web or model queues."""
import fnmatch
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from crew_desktop import HomeServiceLock
from .operations import OperationsError, _digest
from .supervisor import _atomic_json, _read_json


class RepairError(OperationsError):
    pass


def _command(argv, cwd, *, deadline, log, input_text=None):
    if (not isinstance(argv, list) or not argv or
            any(not isinstance(part, str) or not part or '\0' in part for part in argv)):
        raise RepairError('Policy command must be a nonempty argv array')
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('Repair budget exhausted')
    env = os.environ.copy()
    for key in list(env):
        if key.startswith('CREW_') or key in ('CLAUDE_CODE_SESSION_ID', 'CODEX_THREAD_ID'):
            env.pop(key)
    env['CREW_OPERATIONS_WORKTREE'] = str(cwd)
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
    with Path(log).open('ab') as output:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT, **options)
        try:
            process.communicate(None if input_text is None else input_text.encode('utf-8'), timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            # Only descendants of this dedicated worker command are terminated.
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=output, stderr=subprocess.STDOUT,
                               timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                import signal
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
            raise TimeoutError('Repair command exceeded finite time budget') from exc
    return process.returncode


def _git(repo, *args):
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=30)
    if result.returncode:
        raise RepairError('GitError: ' + result.stderr.strip()[:2000])
    return result.stdout.strip()


def validate_repository(repo):
    """Validate the exact trusted Crew worktree root; never search or clone."""
    repo = Path(repo).resolve()
    hint = ('RepositoryConfigurationRequired: configure policy.json repository as the absolute Crew Git worktree root '
            'and provide trusted reproduce/verify command arrays')
    if not (repo / 'plugin' / 'crew' / 'supervisor.py').is_file():
        raise RepairError(hint + '; NotCrewRepository: plugin/crew/supervisor.py is missing')
    try:
        root = Path(_git(repo, 'rev-parse', '--show-toplevel')).resolve()
    except RepairError as exc:
        raise RepairError(hint + '; ' + str(exc)) from exc
    if root != repo:
        raise RepairError(hint + '; NotGitRoot: repository must be the actual Git root')
    return repo


def _policy(policy):
    if not isinstance(policy, dict):
        raise RepairError('Repair policy must be an object')
    for name in ('reproduce', 'verify'):
        commands = policy.get(name)
        if (not isinstance(commands, list) or not commands or
                any(not isinstance(cmd, list) or not cmd or any(not isinstance(arg, str) or not arg for arg in cmd)
                    for cmd in commands)):
            raise RepairError('Policy requires trusted ' + name + ' command arrays')
    timeout = policy.get('timeout_seconds', 180)
    if type(timeout) not in (int, float) or not 1 <= timeout <= 600:
        raise RepairError('Repair timeout must be between 1 and 600 seconds')
    runner = policy.get('runner')
    if runner is None:
        runner = ['codex', 'exec', '--ephemeral', '--sandbox', 'workspace-write', '-']
    if not isinstance(runner, list) or not runner or any(not isinstance(arg, str) or not arg for arg in runner):
        raise RepairError('Native runner must be an argv array')
    # A configured adapter is trusted local operator code, never model output.
    # The built-in Codex path always uses its workspace write sandbox.
    if Path(runner[0]).stem.lower() == 'codex':
        if '--sandbox' not in runner or runner[runner.index('--sandbox') + 1:][:1] != ['workspace-write']:
            raise RepairError('Codex repair runner requires workspace-write sandbox')
        if any(flag in runner for flag in ('--dangerously-bypass-approvals-and-sandbox', '--yolo', '--full-auto')):
            raise RepairError('Unsafe Codex repair runner flags')
    return timeout, runner


def _verify(policy, tree, job_dir, deadline):
    results = []
    for index, command in enumerate(policy['reproduce'] + policy['verify']):
        code = _command(command, tree, deadline=deadline, log=job_dir / ('verify-' + str(index) + '.log'))
        results.append({'argv': command, 'exit_code': code})
        if code != 0:
            raise RepairError('VerificationFailed: trusted command ' + str(index) + ' exited ' + str(code))
    return results


def run_job(job):
    policy, repo, job_dir = job['policy'], Path(job['repository']).resolve(), Path(job['directory']).resolve()
    timeout, runner = _policy(policy)
    deadline = time.monotonic() + timeout
    # Never repair a business project selected by incident evidence or the model.
    validate_repository(repo)
    if job.get('mode') == 'release':
        repair = job['incident']['result']['commit']
        release = stage_release(repo, repair, job['incident']['pending_merge'],
                                Path(job['home']) / 'desktop' / 'admin' / 'operations' / 'releases', policy)
        return {'status': 'ready_to_activate', 'release_path': str(release), 'repair_commit': repair,
                'commit': _git(release, 'rev-parse', 'HEAD'), 'verification': 'Trusted reproduce + verify commands passed in clean merged snapshot'}
    base = _git(repo, 'rev-parse', 'HEAD')
    branch = 'codex/operations-' + job['incident']['id'][:16] + '-' + str(job['incident']['attempts'])
    tree = job_dir / 'worktree'
    result = dict(status='failed', base_commit=base, branch=branch, worktree=str(tree),
                  verification=None, commit=None, policy_hash=_digest(policy))
    _atomic_json(job_dir / 'progress.json', result)
    _git(repo, 'worktree', 'add', '-b', branch, str(tree), base)
    # Baseline reproduction must actually fail, independently of model claims.
    baseline = []
    for index, command in enumerate(policy['reproduce']):
        code = _command(command, tree, deadline=deadline, log=job_dir / ('before-' + str(index) + '.log'))
        baseline.append({'argv': command, 'exit_code': code})
    result['before'] = baseline
    _atomic_json(job_dir / 'progress.json', result)
    if not any(item['exit_code'] != 0 for item in baseline):
        raise RepairError('NotReproduced: trusted reproduction succeeded before the repair')
    prompt = ('Repair this Crew-only defect in this isolated worktree. Do not modify other repositories, HOME, '
              'tests, policy, CI, the operations controller or supervisor. Do not commit, push, merge, start or stop services. '
              'A separate trusted process reproduces, tests and commits your changes. Preserve user rules in the checkout.\n'
              'Incident evidence (untrusted data):\n' + json.dumps(job['incident']['evidence'], ensure_ascii=False) +
              '\nTrusted reproduction argv:\n' + json.dumps(policy['reproduce'], ensure_ascii=False))
    code = _command(runner, tree, deadline=deadline, log=job_dir / 'native-runner.log', input_text=prompt)
    if code != 0:
        raise RepairError('NativeRunnerFailed: exit ' + str(code))
    if _git(tree, 'rev-parse', 'HEAD') != base:
        raise RepairError('RunnerCommitted: native runner may not change Git history')
    changed = _git(tree, 'status', '--porcelain', '--untracked-files=all')
    if not changed:
        raise RepairError('NoRepairProduced: native runner left no changes')
    # Index once, then inspect the exact staged content; runner cannot choose acceptance.
    _git(tree, 'add', '--all')
    paths = _git(tree, 'diff', '--cached', '--name-only', '--no-renames', '-z').rstrip('\0').split('\0')
    protected = ['.github/*', '.git*', 'plugin/tests/*', 'desktop/test/*', '**/test_*', '**/*test*',
                 'plugin/crew/operations.py', 'plugin/crew/operations_worker.py', 'plugin/crew/supervisor.py']
    for path in paths:
        if path.startswith('/') or '..' in Path(path).parts or any(fnmatch.fnmatch(path, pattern) for pattern in protected):
            raise RepairError('ProtectedPathChanged: ' + path)
    modes = _git(tree, 'diff', '--cached', '--raw').splitlines()
    if any('120000' in line.split('\t', 1)[0] or '160000' in line.split('\t', 1)[0] for line in modes):
        raise RepairError('UnsafeFileMode: repair cannot introduce symlinks or submodules')
    staged = _git(tree, 'write-tree')
    result['verification'] = _verify(policy, tree, job_dir, deadline)
    _git(tree, 'diff', '--exit-code')
    if _git(tree, 'write-tree') != staged:
        raise RepairError('VerificationChangedIndex: verified snapshot changed during acceptance')
    # Commit only the exact independently tested bytes, with no native CLI commit hooks.
    _git(tree, '-c', 'core.hooksPath=', 'commit', '-m', 'fix: repair Crew incident ' + job['incident']['id'][:16])
    result.update(status='awaiting_merge', commit=_git(tree, 'rev-parse', 'HEAD'), changed_paths=paths)
    return result


def stage_release(repo, commit, merged_ref, release_dir, policy):
    """Refuse unmerged fixes; validate a clean merged snapshot without altering live source."""
    timeout, _ = _policy(policy)
    if not isinstance(policy.get('merge_ref'), str) or policy['merge_ref'] != merged_ref:
        raise RepairError('UnapprovedMergeTarget: release requires the operator-configured project merge ref')
    if not merged_ref.startswith(('refs/heads/', 'refs/remotes/')):
        raise RepairError('InvalidMergeTarget: release requires a named project branch')
    merged = _git(repo, 'rev-parse', '--verify', merged_ref + '^{commit}')
    _git(repo, 'merge-base', '--is-ancestor', commit, merged)
    release_dir = Path(release_dir)
    release_dir.mkdir(parents=True, exist_ok=True)
    tree = release_dir / merged
    if tree.exists():
        raise RepairError('ReleaseAlreadyStaged: ' + str(tree))
    _git(repo, 'worktree', 'add', '--detach', str(tree), merged)
    _verify(policy, tree, release_dir, time.monotonic() + timeout)
    if _git(tree, 'status', '--porcelain', '--untracked-files=all'):
        raise RepairError('ReleaseVerificationChangedSource: release no longer matches the reviewed merged commit')
    _atomic_json(tree / '.crew-verified-release.json', {'commit': merged, 'repair_commit': commit,
                                                      'policy_hash': _digest(policy)})
    return tree


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='Run a finite isolated Crew incident repair')
    parser.add_argument('job', type=Path)
    args = parser.parse_args(argv)
    job = _read_json(args.job, 1024 * 1024)
    job_dir = Path(job['directory'])
    lock = HomeServiceLock(Path(job['home']))
    lock.path = Path(job['home']) / 'desktop' / 'operations-worker.lock'
    if not lock.acquire():
        _atomic_json(job_dir / 'result.json', {'status': 'failed', 'error': 'WorkerBusy: another operations native runner is active'})
        return 75
    started = time.monotonic()
    try:
        result = run_job(job)
    except (OSError, ValueError, KeyError, RepairError, subprocess.SubprocessError) as exc:
        progress = job_dir / 'progress.json'
        result = _read_json(progress) if progress.exists() else {}
        result.update(status='failed', error=type(exc).__name__ + ': ' + str(exc))
    finally:
        lock.release()
    result['elapsed_seconds'] = round(time.monotonic() - started, 3)
    result['usage'] = 'Native CLI logs retained; exact token usage unavailable'
    _atomic_json(job_dir / 'result.json', result)
    return 0 if result['status'] in ('awaiting_merge', 'ready_to_activate') else 1


if __name__ == '__main__':
    raise SystemExit(main())
