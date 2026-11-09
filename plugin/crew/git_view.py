"""Read-only Git views; fetching is an explicit, separate operation."""
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


_lock = threading.RLock()
_overview_cache = {}
_commit_cache = {}
_last_fetch = {}
_OPEN_PR_LIMIT = 2 ** 31 - 1
# Repeated reads within this window (the sidebar polls every few seconds) reuse the last scan.
_FRESH_SECONDS = 10

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
    stable = next((name for name in ("master", "main") if name in branches and name != base), None)
    return {"branches": branches, "base": base, "stable": stable}


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


def _calculation_ref(have, name):
    remote = "refs/remotes/origin/" + name
    return remote if remote in have else "refs/heads/" + name


def _count(root, *args):
    return int(_git(root, "rev-list", "--count", *args).stdout)


def _outside(root, *excluded):
    """Commits on local branches that none of `excluded` reach: {sha: parents}, in one Git call."""
    graph = {}
    for line in _git(root, "rev-list", "--parents", "--branches", "--not", *excluded).stdout.splitlines():
        sha, *parents = line.split()
        graph[sha] = parents
    return graph


def _reach(graph, tip):
    # Every commit between a tip and an outside commit is outside too, so walking only `graph` is exact.
    seen, stack = set(), [tip]
    while stack:
        sha = stack.pop()
        if sha in graph and sha not in seen:
            seen.add(sha)
            stack.extend(graph[sha])
    return len(seen)


def _worktree_paths(output):
    """refs/heads/<name> -> checkout path, from `worktree list --porcelain -z`."""
    paths = {}
    for record in output.split("\0\0"):
        fields = dict(field.split(" ", 1) if " " in field else (field, "")
                      for field in record.split("\0") if field)
        if "branch" in fields:
            paths[fields["branch"]] = fields["worktree"]
    return paths


def _overview(root, refs, trees, base, stable, exclude):
    locals_ = {r["ref"][11:]: r for r in refs if r["ref"].startswith("refs/heads/")}
    for name in (base, stable):
        if name is not None and name not in locals_:
            raise GitError("本地分支不存在: " + name)
    have = {r["ref"] for r in refs}
    trunks = [name for name in (base, stable) if name is not None]
    # Commits already on stable count as merged: a branch cut from master is no new work for develop.
    merged_into = ["refs/heads/" + name for name in trunks]
    origin_base = "refs/remotes/origin/" + base
    pushed_into = [ref for ref in ("refs/remotes/origin/" + name for name in trunks) if ref in have]
    unmerged_graph = _outside(root, *merged_into)
    # Without origin/<base> there is nowhere to push, so merged locally means done.
    unpushed_graph = _outside(root, *pushed_into) if origin_base in have else {}
    branches, hidden = [], 0
    for name, ref in locals_.items():
        if name in trunks or name in exclude:
            continue
        head, tip = "refs/heads/" + name, ref["latest"]["sha"]
        unmerged = _reach(unmerged_graph, tip)
        if unmerged:
            state, commits = "unmerged", unmerged
        else:
            commits = _reach(unpushed_graph, tip)
            if not commits:
                hidden += 1
                continue
            state = "unpushed"
        branches.append({"name": name, "state": state, "commits": commits,
                         "behind": _count(root, head + "..refs/heads/" + base),
                         "latest": ref["latest"], "worktree": trees.get(head)})
    branches.sort(key=lambda item: item["latest"]["time"], reverse=True)
    base_data = {"name": base, "origin": origin_base in have, "unpushed": None, "behind_origin": None}
    if origin_base in have:
        behind, ahead = map(int, _git(root, "rev-list", "--left-right", "--count",
                                      origin_base + "...refs/heads/" + base).stdout.split())
        base_data.update(unpushed=ahead, behind_origin=behind)
    stable_data = None
    if stable is not None:
        # How far the release branch trails integrated work; origin refs win when they exist.
        stable_ref, base_ref = _calculation_ref(have, stable), _calculation_ref(have, base)
        stable_data = {"name": stable, "behind": _count(root, stable_ref + ".." + base_ref),
                       "ref": stable_ref[5:], "base_ref": base_ref[5:]}
    return {"base": base_data, "stable": stable_data, "branches": branches, "merged_hidden": hidden}


def view(root, config, exclude=(), force=False):
    """Local branches not merged into base, merged but not pushed, and how far stable trails base.

    Merged-and-pushed branches are only counted. The scan reruns when refs or worktrees change;
    unforced reads within _FRESH_SECONDS reuse the last result without running Git.
    """
    key = _root(root)
    settings = (config["base"], config["stable"], tuple(sorted(exclude)))
    with _lock:
        now = time.monotonic()
        cached = _overview_cache.get(key)
        if force or cached is None or cached["settings"] != settings or now - cached["checked"] >= _FRESH_SECONDS:
            signature, refs = _refs(root)
            trees = _git(root, "worktree", "list", "--porcelain", "-z").stdout
            signature = hashlib.sha256((signature + trees).encode("utf-8")).hexdigest()
            if cached is None or cached["settings"] != settings or cached["signature"] != signature:
                data = _overview(root, refs, _worktree_paths(trees), settings[0], settings[1], settings[2])
                cached = {"settings": settings, "signature": signature, "data": data}
                _overview_cache[key] = cached
            cached["checked"] = now
        data = copy.deepcopy(cached["data"])
        data.update(repository=Path(root).name, last_fetch_at=_last_fetch.get(key))
        return data


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
        cached = _commit_cache.get((key, sha.lower()))
        if cached is not None:
            return copy.deepcopy(cached)
        full_sha = _git(root, "rev-parse", "--verify", sha + "^{commit}").stdout.strip()
        cached = _commit_cache.get((key, full_sha))
        if cached is not None:
            return copy.deepcopy(cached)
        output = _git(root, "show", "--numstat", "--format=", "--find-renames", "--diff-merges=first-parent",
                      "-z", full_sha).stdout
        files = _numstat(output)
        detail = {"sha": full_sha, "files": files, "file_count": len(files),
                  "additions": sum(item["added"] for item in files if not item["binary"]),
                  "deletions": sum(item["deleted"] for item in files if not item["binary"])}
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


def _github(root, base, github_repo=None):
    try:
        repo = _github_repo(root) if github_repo is None else github_repo
        fields = "number,title,headRefName,headRefOid,baseRefName,mergeCommit,state,isDraft,statusCheckRollup,url,closedAt"
        opened = _gh_json(root, "pr", "list", "--repo", repo, "--state", "open",
                          "--limit", str(_OPEN_PR_LIMIT), "--json", fields)
        merged = _gh_json(root, "pr", "list", "--repo", repo, "--state", "merged", "--limit", "20", "--json", fields)
        closed = _gh_json(root, "pr", "list", "--repo", repo, "--state", "closed", "--limit", "20", "--json", fields)
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
    with _lock:
        if result.returncode == 0:
            _last_fetch[key] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            _overview_cache.pop(key, None)
        return {"ok": result.returncode == 0, "output": result.stdout, "error": result.stderr,
                "last_fetch_at": _last_fetch.get(key)}
