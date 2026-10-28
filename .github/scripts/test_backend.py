"""Run every unittest module in bounded, isolated Python processes."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import fnmatch
import os
from pathlib import Path
import re
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
IDENTITIES = ('CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'CREW_EXECUTION_ID', 'CREW_GOAL_ID')


def discover(directory):
    modules = []

    def visit(folder, prefix=''):
        for path in sorted(folder.iterdir()):
            if path.is_file() and fnmatch.fnmatch(path.name, 'test*.py'):
                modules.append((prefix + path.stem, path.stat().st_size))
            elif path.is_dir() and (path / '__init__.py').is_file():
                visit(path, prefix + path.name + '.')

    visit(directory)
    # Start larger suites first so a large module is less likely to finish last.
    return [name for name, size in sorted(modules, key=lambda item: (-item[1], item[0]))]


def run_module(module, directory):
    environment = dict(os.environ)
    for key in IDENTITIES:
        environment.pop(key, None)
    environment['PYTHONIOENCODING'] = 'utf-8'
    environment['PYTHONPATH'] = os.pathsep.join(filter(None, (
        str(directory), str(directory.parent), environment.get('PYTHONPATH'))))
    started = time.monotonic()
    result = subprocess.run([sys.executable, '-m', 'unittest', module, '-v'],
                            cwd=directory.parent, env=environment, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
    return module, result, time.monotonic() - started


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start-directory', type=Path, default=ROOT / 'plugin' / 'tests')
    parser.add_argument('--workers', type=int, default=min(4, os.cpu_count() or 1))
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error('--workers must be positive')
    directory = args.start_directory.resolve()
    if not directory.is_dir():
        parser.error('Test directory does not exist: ' + str(directory))
    modules = discover(directory)
    if not modules:
        parser.error('No unittest modules discovered')
    started = time.monotonic()
    failures, tests, skipped = 0, 0, 0
    durations = []
    print(f'Running {len(modules)} modules with {args.workers} workers', flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(run_module, module, directory) for module in modules]
        for future in as_completed(futures):
            module, result, elapsed = future.result()
            counts = re.search(r'Ran (\d+) tests? in ', result.stdout)
            count = int(counts[1]) if counts else 0
            skip = re.search(r'skipped=(\d+)', result.stdout)
            skipped += int(skip[1]) if skip else 0
            tests += count
            failed = result.returncode != 0
            failures += failed
            durations.append((elapsed, module))
            print(f'{"FAIL" if failed else "PASS"} {module}: {count} tests, {elapsed:.2f}s', flush=True)
            if failed:
                print(result.stdout, flush=True)
            else:
                for line in result.stdout.splitlines():
                    if ' ... skipped ' in line:
                        print('  ' + line, flush=True)
    print(f'{len(modules)} modules / {tests} tests / {skipped} skipped / '
          f'{failures} failed modules in {time.monotonic() - started:.2f}s', flush=True)
    print('Slowest modules: ' + ', '.join(f'{module} {elapsed:.2f}s'
          for elapsed, module in sorted(durations, reverse=True)[:5]), flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
