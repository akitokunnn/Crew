"""Read-only Git views; fetching is an explicit, separate operation."""
from concurrent.futures import ThreadPoolExecutor
import copy
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time

from . import git_process


class GitError(ValueError):
    """Git rejected a view operation."""


def unavailable_reason(root):
    """Ordinary workspaces do not need Git; marked repositories still use native validation."""
    if git_process.repository_root(root) is None:
        return '当前工作区不是 Git 仓库；Git 管理和独立工作树需要已有 Git 仓库。'
    if shutil.which('git') is None:
        return '未找到 Git，请先安装 Git 并将其加入 PATH，再使用 Git 功能。'
    return None


def require_repository(root):
    reason = unavailable_reason(root)
    if reason is not None:
        raise GitError(reason)


# Guards the dictionaries below; Git never runs while it is held, so cached() answers at once.
_lock = threading.Lock()
# One scan of a repository at a time, per repository: {root key: Lock}.
_scan_locks = {}
_overview_cache = {}
# Why the latest background scan (refresh) of a repository failed: {root key: (settings, reason)};
# any successful view() clears it.
_failures = {}
_commit_cache = {}
_last_fetch = {}
# Open pull requests from the last successful GitHub read (CI poller or explicit fetch); views never call gh.
_pull_requests = {}
_OPEN_PR_LIMIT = 2 ** 31 - 1
# Repeated reads within this window (the sidebar polls every few seconds) reuse the last scan.
_FRESH_SECONDS = 10
# `git status` in every worktree is the costly part on large checkouts. Working-tree edits change no ref, so
# besides forced reads and ref/worktree changes the status scan only reruns after this interval.
_STATUS_SECONDS = 60
# Tracked build outputs a local compile rewrites. Untracked outputs are already left out by .gitignore; tracked
# ones (Unreal's Binaries/ and Intermediate/, at the root, under Plugins/* or nested deeper) are recognised by
# directory name at any depth rather than fixed paths.
_BUILD_OUTPUT_DIRS = frozenset({"binaries", "intermediate"})

_REF_FORMAT = ("%(refname)%00%(objectname)%00%(subject)%00%(authorname)%00"
               "%(committerdate:iso-strict)%00%(upstream:short)%00%(symref)")


def _root(root):
    return os.path.normcase(os.path.abspath(root))


def _run(root, args):
    # Git for Windows' launcher may leave descendants holding PIPE handles.
    # Use the shared native executable and file capture so timeouts stay bounded.
    return git_process.run(args[1:], cwd=root,
                           command_executable=None if args[0] == 'git' else args[0],
                           timeout=60 if args[0] != 'git' or args[1] == 'fetch' else 10)


def _git(root, *args, allowed=(0,)):
    result = _run(root, ["git", "-c", "core.quotepath=false", *args])
    if result.returncode not in allowed:
        raise GitError(result.stderr if result.stderr else result.stdout)
    return result


def _refs(root):
    output = _git(root, "for-each-ref", "--format=" + _REF_FORMAT).stdout
    refs = []
    for line in output.splitlines():
        name, sha, subject, author, timestamp, upstream, symbolic = line.split("\0")
        refs.append({"ref": name, "latest": {"sha": sha, "subject": subject,
                                             "author": author, "time": timestamp},
                     "upstream": upstream or None, "symbolic": symbolic})
    return hashlib.sha256(output.encode("utf-8")).hexdigest(), refs


def branch_choices(root):
    """Local branches and the defaults used by both CLI and enable card."""
    require_repository(root)
    _, refs = _refs(root)
    branches = [r["ref"][11:] for r in refs if r["ref"].startswith("refs/heads/")]
    base = next((name for name in ("develop", "dev", "main", "master") if name in branches), None)
    return {"branches": branches, "base": base}


def remotes(root):
    # Display Git's actual fetch/push destinations; never expose embedded credentials.
    output = _git(root, 'remote', '-v').stdout
    result = []
    for line in output.splitlines():
        name, destination = line.split('\t', 1)
        address, direction = destination.rsplit(' ', 1)
        address = re.sub(r'(https?://)[^/@]+@', r'\1***@', address)
        result.append({'name': name, 'url': address, 'direction': direction.strip('()')})
    return result


def _count(root, *args):
    return int(_git(root, "rev-list", "--count", *args, "--").stdout)


def _outside(root, *excluded):
    """Commits on local branches that none of `excluded` reach: {sha: parents}, in one Git call."""
    graph = {}
    for line in _git(root, "rev-list", "--parents", "--branches", "--not", *excluded, "--").stdout.splitlines():
        sha, *parents = line.split()
        graph[sha] = parents
    return graph


def _reach(graph, tip, copied=()):
    """Non-merge commits reachable from `tip` inside `graph`, leaving out those in `copied`.

    Every commit between a tip and an outside commit is outside too, so walking only `graph` is exact.
    Merge commits carry no work of their own: a branch whose only outside commits merge base back in
    (a release branch that merged develop, say) has nothing left to merge.
    """
    seen, stack, work = set(), [tip], 0
    while stack:
        sha = stack.pop()
        if sha in graph and sha not in seen:
            seen.add(sha)
            work += len(graph[sha]) < 2 and sha not in copied
            stack.extend(graph[sha])
    return work


def _copied(root, target, tip):
    """Commits reachable from `tip` whose change `target` already carries as a different commit.

    A cherry-picked or rebased copy has its own SHA, so reachability alone reads the original as missing;
    Git's patch-id comparison (`--cherry-mark`) recognises it. Several commits squashed into one are not
    recognised and stay counted.
    """
    output = _git(root, "rev-list", "--cherry-mark", "--right-only", "--no-merges", target + "..." + tip, "--").stdout
    return {line[1:] for line in output.splitlines() if line.startswith("=")}


def _worktree_paths(output):
    """refs/heads/<name> -> checkout path, from `worktree list --porcelain -z`."""
    paths = {}
    for record in output.split("\0\0"):
        fields = dict(field.split(" ", 1) if " " in field else (field, "")
                      for field in record.split("\0") if field)
        if "branch" in fields:
            paths[fields["branch"]] = fields["worktree"]
    return paths


def _overview(root, refs, trees, base, integration, exclude):
    """Classify every local branch against base and the optional local integration branch.

    Merged means reached by local <base> or origin/<base>, so a stale local base does not hide pushed
    merges; a commit whose copy (cherry-pick, rebase) is there counts as merged too, likewise for the
    integration branch. Merged branches stay in the result only while they have a worktree, whose uncommitted
    changes decide later whether they are shown.
    """
    locals_ = {r["ref"][11:]: r for r in refs if r["ref"].startswith("refs/heads/")}
    for name in (base, integration):
        if name is not None and name not in locals_:
            raise GitError("本地分支不存在: " + name)
    have = {r["ref"] for r in refs}
    origin_base = "refs/remotes/origin/" + base
    base_refs = ["refs/heads/" + base] + ([origin_base] if origin_base in have else [])
    outside_base = _outside(root, *base_refs)
    # Without origin/<base> there is nowhere to push, so merged locally means done.
    unpushed_graph = _outside(root, origin_base) if origin_base in have else {}
    outside_integration = None if integration is None else _outside(root, *base_refs, "refs/heads/" + integration)
    branches, hidden, integrated = [], 0, []
    for name, ref in locals_.items():
        if name in (base, integration) or name in exclude:
            continue
        head, tip = "refs/heads/" + name, ref["latest"]["sha"]
        pending, copies, known = None, {}, set()

        def copied(target):
            if target not in copies:
                copies[target] = _copied(root, target, tip)
            return copies[target]

        # Commits brought over as copies (cherry-pick, rebase) count as merged where their copy landed.
        commits = _reach(outside_base, tip)
        for target in base_refs:
            if not commits:
                break
            known |= copied(target)
            commits = _reach(outside_base, tip, known)
        if commits:
            if outside_integration is not None:
                pending = _reach(outside_integration, tip, known)
                if pending:
                    known |= copied("refs/heads/" + integration)
                    pending = _reach(outside_integration, tip, known)
            state = "unmerged" if pending is None or pending else "integrated"
        else:
            # Copies only in the local base are not pushed yet.
            commits = _reach(unpushed_graph, tip, copied(origin_base) if copies and origin_base in have else ())
            state = "unpushed" if commits else "merged"
        if state == "merged" and head not in trees:
            hidden += 1
            continue
        if state == "integrated":
            integrated.append(name)
        remote = "refs/remotes/origin/" + name
        branches.append({"name": name, "state": state, "commits": commits, "outside_integration": pending,
                         "behind": _count(root, *base_refs, "--not", head),
                         "remote": {"ahead": _count(root, head, "--not", remote)} if remote in have else None,
                         "latest": ref["latest"], "worktree": trees.get(head)})
    branches.sort(key=lambda item: item["latest"]["time"], reverse=True)
    base_data = {"name": base, "origin": origin_base in have, "unpushed": None, "behind_origin": None,
                 "worktree": trees.get("refs/heads/" + base)}
    if origin_base in have:
        behind, ahead = map(int, _git(root, "rev-list", "--left-right", "--count",
                                      origin_base + "...refs/heads/" + base, "--").stdout.split())
        base_data.update(unpushed=ahead, behind_origin=behind)
    integration_data = None
    if integration is not None:
        head = "refs/heads/" + integration
        integration_data = {"name": integration, "ahead": _reach(outside_base, locals_[integration]["latest"]["sha"]),
                            "behind": _count(root, *base_refs, "--not", head), "branches": integrated,
                            "worktree": trees.get(head)}
    return {"base": base_data, "integration": integration_data, "branches": branches, "merged_hidden": hidden}


def _status(path):
    """Uncommitted files in one worktree, build outputs counted apart; never takes the index lock."""
    if not os.path.isdir(path):
        return None
    try:
        result = _run(path, ["git", "--no-optional-locks", "-c", "core.quotepath=false",
                             "status", "--porcelain=v1", "-z", "--untracked-files=all"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"files": None, "build_outputs": None, "error": str(exc)}
    if result.returncode:
        return {"files": None, "build_outputs": None, "error": (result.stderr or result.stdout).strip()}
    records, files, outputs, index = result.stdout.split("\0"), 0, 0, 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if record[0] in "RC" or record[1] in "RC":
            index += 1  # The original path of a rename or copy follows as its own record.
        if _BUILD_OUTPUT_DIRS.intersection(part.lower() for part in record[3:].split("/")[:-1]):
            outputs += 1
        else:
            files += 1
    return {"files": files, "build_outputs": outputs, "error": None}


def _statuses(paths):
    with ThreadPoolExecutor(max_workers=max(1, min(8, len(paths)))) as pool:
        return dict(zip(paths, pool.map(_status, paths)))


def _assemble(overview, statuses, pull_requests):
    """Join the cached graph scan with worktree status and known pull requests; runs no Git."""
    data = copy.deepcopy(overview)
    listed = []
    for branch in data["branches"]:
        dirty = statuses.get(branch["worktree"])
        branch["dirty"] = dirty
        opened = None if pull_requests is None else pull_requests["open"].get(branch["name"])
        branch["pr"] = None if opened is None else copy.deepcopy(opened)
        if branch["state"] != "merged" or (dirty is not None and (dirty["files"] or dirty["error"])):
            listed.append(branch)
        else:
            data["merged_hidden"] += 1
    data["branches"] = listed
    for trunk in (data["base"], data["integration"]):
        if trunk is not None:
            trunk["dirty"] = statuses.get(trunk["worktree"])
    data["pull_requests_at"] = None if pull_requests is None else pull_requests["at"]
    return data


def _settings(config, exclude):
    return config["base"], config["integration"], tuple(sorted(exclude))


def _scan_lock(key):
    with _lock:
        return _scan_locks.setdefault(key, threading.Lock())


def _result(root, key, entry):
    """The page value of a cache entry; the caller holds _lock."""
    data = _assemble(entry["data"], entry["statuses"], _pull_requests.get(key))
    data.update(repository=Path(root).name, last_fetch_at=_last_fetch.get(key))
    return data


def _due(entry, settings, now):
    """Which parts of view()'s scan an unforced read reruns: (graph check, worktree status)."""
    if entry is None or entry["settings"] != settings:
        return True, True
    return now - entry["checked"] >= _FRESH_SECONDS, now - entry["status_checked"] >= _STATUS_SECONDS


def view(root, config, exclude=(), force=False):
    """Branches still to merge, test in the integration branch or push, plus worktrees with uncommitted work.

    Merged-and-pushed branches with clean (or no) worktrees are only counted. The graph scan reruns when
    refs or worktrees change; unforced reads within _FRESH_SECONDS reuse the last result without running
    Git. Worktree status reruns with the graph scan, on forced reads, or after _STATUS_SECONDS.
    A failed scan raises and leaves the last result in place; a successful one clears refresh()'s failure.
    """
    key = _root(root)
    settings = _settings(config, exclude)
    with _scan_lock(key):
        with _lock:
            entry = _overview_cache.get(key)
        now = time.monotonic()
        graph, status = (True, True) if force else _due(entry, settings, now)
        if graph:
            signature, refs = _refs(root)
            trees = _git(root, "worktree", "list", "--porcelain", "-z").stdout
            signature = hashlib.sha256((signature + trees).encode("utf-8")).hexdigest()
            if entry is None or entry["settings"] != settings or entry["signature"] != signature:
                data = _overview(root, refs, _worktree_paths(trees), settings[0], settings[1], settings[2])
                entry = {"settings": settings, "signature": signature, "data": data, "statuses": None}
                status = True
            entry = {**entry, "checked": now}
        if status:
            overview = entry["data"]
            paths = sorted({item["worktree"] for item in [*overview["branches"], overview["base"],
                                                           overview["integration"] or {}] if item.get("worktree")})
            entry = {**entry, "statuses": _statuses(paths), "status_checked": now}
        with _lock:
            _overview_cache[key] = entry
            _failures.pop(key, None)
            return _result(root, key, entry)


def cached(root, config, exclude=()):
    """view()'s last result for these settings without running Git: (data, error, due).

    `data` is None until a scan with these settings has finished; `error` is why the latest scan failed;
    `due` says an unforced view() would run Git now (the same freshness rules), or that the latest scan
    failed, which every read retries.
    """
    key = _root(root)
    settings = _settings(config, exclude)
    with _lock:
        entry = _overview_cache.get(key)
        failure = _failures.get(key)
        error = failure[1] if failure is not None and failure[0] == settings else None
        if entry is None or entry["settings"] != settings:
            return None, error, True
        return _result(root, key, entry), error, error is not None or any(_due(entry, settings, time.monotonic()))


def refresh(root, config, exclude=()):
    """Run an unforced view() for a background reader; True when cached() gives a different answer after it."""
    before = cached(root, config, exclude)[:2]
    try:
        view(root, config, exclude=exclude)
    except (OSError, GitError, subprocess.TimeoutExpired) as exc:
        # Nobody waits on this scan: cached() hands the reason to every reader until a scan succeeds.
        with _lock:
            _failures[_root(root)] = (_settings(config, exclude), str(exc))
    return cached(root, config, exclude)[:2] != before


def _numstat(output):
    records = output.split("\0")[:-1]
    files = []
    index = 0
    while index < len(records):
        added, deleted, path = records[index].split("\t", 2)
        previous_path = None
        if path == "":
            previous_path, path = records[index + 1:index + 3]
            index += 2
        binary = added == "-"
        item = {"path": path, "added": None if binary else int(added),
                "deleted": None if binary else int(deleted), "binary": binary}
        if previous_path is not None:
            item["previous_path"] = previous_path
        files.append(item)
        index += 1
    return files


def commit_detail(root, sha):
    """Read immutable first-parent file statistics without sharing projects' caches."""
    if re.fullmatch(r"[0-9a-fA-F]{4,64}", sha) is None:
        raise GitError("提交 SHA 必须是 4 至 64 位十六进制字符")
    key = _root(root)
    with _lock:
        known = _commit_cache.get((key, sha.lower()))
    if known is not None:
        return copy.deepcopy(known)
    full_sha = _git(root, "rev-parse", "--verify", sha + "^{commit}").stdout.strip()
    with _lock:
        known = _commit_cache.get((key, full_sha))
    if known is not None:
        return copy.deepcopy(known)
    output = _git(root, "show", "--numstat", "--format=", "--find-renames", "--diff-merges=first-parent",
                  "-z", full_sha).stdout
    files = _numstat(output)
    detail = {"sha": full_sha, "files": files, "file_count": len(files),
              "additions": sum(item["added"] for item in files if not item["binary"]),
              "deletions": sum(item["deleted"] for item in files if not item["binary"])}
    with _lock:
        _commit_cache[(key, full_sha)] = detail
    return copy.deepcopy(detail)


def _ci(checks):
    if not checks:
        return "none"
    failed = {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"}
    pending = {"QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "REQUESTED", "EXPECTED"}
    if any(check.get("conclusion") in failed or check.get("state") in failed for check in checks):
        return "failure"
    if any(check.get("status") in pending or check.get("state") in pending for check in checks):
        return "pending"
    return "success"


def _gh_json(root, *args):
    result = _run(root, ["gh", *args])
    if result.returncode:
        raise GitError(result.stderr if result.stderr else result.stdout)
    return json.loads(result.stdout)


def _github_repo(root):
    remote = _git(root, "remote", "get-url", "origin").stdout.strip()
    match = re.fullmatch(r"(?:https?://(?:[^/@]+@)?github\.com/|(?:ssh://)?git@github\.com[:/])([^/]+/[^/]+?)(?:\.git)?/?", remote)
    if match is None:
        raise GitError("origin 不是 GitHub remote: " + remote)
    return match.group(1)


_PR_FIELDS = ("number,title,headRefName,headRefOid,baseRefName,mergeCommit,state,isDraft,statusCheckRollup,url,closedAt,"
              "isCrossRepository")


def _open_pull_requests(root, repo):
    """List open PRs and remember them by head name for the branch view."""
    opened = _gh_json(root, "pr", "list", "--repo", repo, "--state", "open",
                      "--limit", str(_OPEN_PR_LIMIT), "--json", _PR_FIELDS)
    # Local branches match open PRs by head name; a fork's same-named head is someone else's branch.
    by_head = {}
    for pr in opened:
        if not pr["isCrossRepository"]:
            by_head.setdefault(pr["headRefName"], {"number": pr["number"], "url": pr["url"], "draft": pr["isDraft"],
                                                   "base": pr["baseRefName"], "ci": _ci(pr["statusCheckRollup"])})
    with _lock:
        _pull_requests[_root(root)] = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "open": by_head}
    return opened


def _github(root, base, github_repo=None):
    try:
        repo = _github_repo(root) if github_repo is None else github_repo
        opened = _open_pull_requests(root, repo)
        merged = _gh_json(root, "pr", "list", "--repo", repo, "--state", "merged", "--limit", "20", "--json", _PR_FIELDS)
        closed = _gh_json(root, "pr", "list", "--repo", repo, "--state", "closed", "--limit", "20", "--json", _PR_FIELDS)
        recent_by_number = {pr["number"]: pr for pr in merged + closed}
        recent = sorted(recent_by_number.values(), key=lambda pr: pr["closedAt"], reverse=True)[:20]
        prs = [{"number": pr["number"], "title": pr["title"], "head": pr["headRefName"],
                "base": pr["baseRefName"], "state": pr["state"], "draft": pr["isDraft"],
                "ci": _ci(pr["statusCheckRollup"]), "url": pr["url"], "sha": pr["headRefOid"], "head_sha": pr["headRefOid"],
                "merge_sha": pr["mergeCommit"]["oid"] if pr["mergeCommit"] is not None else None,
                "run_key": json.dumps([pr["headRefOid"], sorted(
                    ((check.get("detailsUrl"), check.get("targetUrl"), check.get("startedAt"))
                     for check in pr["statusCheckRollup"]), key=json.dumps)], ensure_ascii=False)} for pr in opened + recent]
        runs = _gh_json(root, "run", "list", "--repo", repo, "--branch", base, "--limit", "5",
                        "--json", "conclusion,status,headSha,createdAt,url")
        return {"pull_requests": prs,
                "runs": [{"conclusion": run["conclusion"], "status": run["status"], "sha": run["headSha"],
                          "time": run["createdAt"], "url": run["url"]} for run in runs]}, None
    except (FileNotFoundError, GitError) as exc:
        return None, str(exc)
    except subprocess.TimeoutExpired as exc:
        return None, str(exc)


def fetch(root):
    """Fetch only when explicitly requested by the refresh endpoint."""
    key = _root(root)
    try:
        result = _run(root, ["git", "fetch", "--prune", "origin"])
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        with _lock:
            return {"ok": False, "output": "", "error": str(exc), "last_fetch_at": _last_fetch.get(key)}
    # A scan still running read the refs before this fetch; drop the cache only after it stored its result.
    with _scan_lock(key), _lock:
        if result.returncode == 0:
            _last_fetch[key] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            _overview_cache.pop(key, None)
        outcome = {"ok": result.returncode == 0, "output": result.stdout, "error": result.stderr,
                   "last_fetch_at": _last_fetch.get(key), "pull_requests_error": None}
    if outcome["ok"]:
        # The explicit remote refresh also renews which branches have open PRs; gh problems keep their reason.
        try:
            _open_pull_requests(root, _github_repo(root))
        except (FileNotFoundError, GitError, subprocess.TimeoutExpired) as exc:
            outcome["pull_requests_error"] = str(exc)
    return outcome
