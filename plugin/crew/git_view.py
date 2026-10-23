"""Read-only Git views; fetching is an explicit, separate operation."""
import copy
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time


class GitError(ValueError):
    """Git rejected a view operation."""


_lock = threading.RLock()
_refs_cache = {}
_dirty_cache = {}
_github_cache = {}
_history_cache = {}
_commit_cache = {}
_last_fetch = {}
_OPEN_PR_LIMIT = 2 ** 31 - 1
_COMMIT_FORMAT = "%H%x00%P%x00%s%x00%an%x00%cI%x00%b"

_REF_FORMAT = ("%(refname)%00%(objectname)%00%(subject)%00%(authorname)%00"
               "%(committerdate:iso-strict)%00%(upstream:short)%00%(symref)")


def _root(root):
    return os.path.normcase(os.path.abspath(root))


def _run(root, args):
    return subprocess.run(args, cwd=root, capture_output=True, text=True,
                          encoding="utf-8", timeout=60)


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
    _, refs = _refs(root)
    branches = [r["ref"][11:] for r in refs if r["ref"].startswith("refs/heads/")]
    base = next((name for name in ("develop", "dev", "main", "master") if name in branches), None)
    stable = next((name for name in ("master", "main") if name in branches and name != base), None)
    return {"branches": branches, "base": base, "stable": stable}


def _branch_data(root, refs, base, stable):
    locals_ = {r["ref"][11:]: r for r in refs if r["ref"].startswith("refs/heads/")}
    for name in (base, stable):
        if name is not None and name not in locals_:
            raise GitError("本地分支不存在: " + name)
    base_ref = "refs/heads/" + base
    branches = []
    for name, ref in locals_.items():
        branch_ref = "refs/heads/" + name
        behind, ahead = map(int, _git(root, "rev-list", "--left-right", "--count",
                                     base_ref + "..." + branch_ref).stdout.split())
        unique = _git(root, "rev-list", branch_ref, "--not", base_ref).stdout.splitlines() if ahead else []
        conflicts = []
        if ahead:
            merge = _git(root, "merge-tree", "--write-tree", "--name-only", "--no-messages",
                         base_ref, branch_ref, allowed=(0, 1))
            if merge.returncode == 1:
                conflicts = merge.stdout.splitlines()[1:]
        branches.append({"name": name, "latest": ref["latest"], "upstream": ref["upstream"],
                         "ahead": ahead, "behind": behind, "merged": ahead == 0,
                         "conflicts": conflicts, "unique_commits": unique})
    remote_only = []
    ref_unique_commits = {branch["name"]: branch["unique_commits"] for branch in branches}
    for ref in refs:
        if ref["ref"].startswith("refs/remotes/origin/") and not ref["symbolic"]:
            name = ref["ref"][13:]
            ref_unique_commits[name] = _git(root, "rev-list", ref["ref"], "--not", base_ref).stdout.splitlines()
            if name[7:] not in locals_:
                remote_only.append({"name": name, "latest": ref["latest"], "upstream": None})
    stable_data = None
    if stable is not None:
        ahead = int(_git(root, "rev-list", "--count", "refs/heads/" + stable + ".." + base_ref).stdout)
        stable_data = {"name": stable, "latest": locals_[stable]["latest"], "ahead": ahead}
    return {"branches": branches, "remote_only": remote_only, "ref_unique_commits": ref_unique_commits,
            "base": {"name": base, "latest": locals_[base]["latest"]}, "stable": stable_data}


def _worktrees(root):
    output = _git(root, "worktree", "list", "--porcelain", "-z").stdout
    trees = []
    now = time.monotonic()
    for record in output.split("\0\0"):
        if not record:
            continue
        fields = dict(field.split(" ", 1) if " " in field else (field, "")
                      for field in record.split("\0") if field)
        path = fields["worktree"]
        key = _root(path)
        cached = _dirty_cache.get(key)
        if cached is None or now - cached[0] >= 60:
            dirty = bool(_git(path, "status", "--porcelain", "--untracked-files=no").stdout)
            _dirty_cache[key] = (now, dirty)
        else:
            dirty = cached[1]
        trees.append({"path": path, "dirty": dirty, "branch": fields.get("branch")})
    return trees


def _commit_labels(refs):
    labels = {}
    for ref in refs:
        if ref["symbolic"]:
            continue
        if ref["ref"].startswith("refs/heads/"):
            name = ref["ref"][11:]
        elif ref["ref"].startswith("refs/remotes/origin/"):
            name = ref["ref"][13:]
        else:
            continue
        labels.setdefault(ref["latest"]["sha"], []).append(name)
    return labels


def _commit_records(output, refs):
    labels = _commit_labels(refs)
    fields = output.split("\0")[:-1]
    commits = []
    for index in range(0, len(fields), 6):
        sha, parents, subject, author, timestamp, body = fields[index:index + 6]
        commits.append({"sha": sha, "parents": parents.split(), "refs": labels.get(sha, []),
                        "subject": subject, "body": body.rstrip("\n"), "author": author, "time": timestamp})
    return commits


def _history_plan(root, refs, stable):
    signature = tuple((ref["ref"], ref["latest"]["sha"], ref["symbolic"]) for ref in refs)
    key = _root(root)
    cached = _history_cache.get(key)
    if cached is not None and cached[0] == (signature, stable):
        return cached[1]
    history = _git(root, "log", "--all", "--topo-order", "--format=%H").stdout.splitlines()
    if stable is None:
        recent = history
    else:
        branches = [ref["ref"] for ref in refs if not ref["symbolic"] and
                    (ref["ref"].startswith("refs/heads/") or ref["ref"].startswith("refs/remotes/origin/"))]
        recent = _git(root, "rev-list", "--topo-order", *branches,
                      "--not", "refs/heads/" + stable).stdout.splitlines()
        recent.append(next(ref["latest"]["sha"] for ref in refs if ref["ref"] == "refs/heads/" + stable))
    shown = set(recent)
    plan = {"shas": recent + [sha for sha in history if sha not in shown], "recent_count": len(recent)}
    _history_cache[key] = ((signature, stable), plan)
    return plan


def _commits(root, refs, offset, limit, stable):
    plan = _history_plan(root, refs, stable)
    end = min(offset + limit, len(plan["shas"]))
    if offset == 0:
        end = min(end, plan["recent_count"])
    shas = plan["shas"][offset:end]
    commits = []
    if shas:
        output = _git(root, "log", "--no-walk=unsorted", "-z", "--format=" + _COMMIT_FORMAT, *shas).stdout
        commits = _commit_records(output, refs)
    return commits, end if end < len(plan["shas"]) else None


def _latest_merge(root, refs, base):
    output = _git(root, "log", "--first-parent", "--merges", "-1", "-z", "--format=" + _COMMIT_FORMAT,
                  "refs/heads/" + base).stdout
    commits = _commit_records(output, refs)
    return commits[0] if commits else None


def _commit_context(root, commits, refs, base, github):
    labels = _commit_labels(refs)
    parents = list(dict.fromkeys(parent for commit in commits for parent in commit["parents"]))
    unresolved = [sha for sha in parents if sha not in labels]
    names = {}
    for patterns in (("refs/heads/" + base,), ("refs/heads/*", "refs/remotes/origin/*")):
        if not unresolved:
            break
        output = _git(root, "name-rev", "--name-only", "--always",
                      *("--refs=" + pattern for pattern in patterns), *unresolved).stdout.splitlines()
        for sha, name in zip(unresolved, output):
            # A second-parent route cannot identify the branch that was merged.
            if "^" not in name and name != sha:
                names[sha] = re.sub(r"~\d+$", "", name).removeprefix("remotes/")
        unresolved = [sha for sha in unresolved if sha not in names]
    names.update((sha, sha[:7]) for sha in unresolved)
    prs = [] if github is None else github["pull_requests"]
    for commit in commits:
        commit["parent_branches"] = [sorted(labels[sha], key=lambda name: (name != base, name.startswith("origin/"), name))[0]
                                     if sha in labels else names[sha] for sha in commit["parents"]]
        commit["pull_requests"] = [copy.deepcopy(pr) for pr in prs
                                   if commit["sha"] in (pr["head_sha"], pr["merge_sha"])]


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
                "ci": _ci(pr["statusCheckRollup"]), "url": pr["url"], "head_sha": pr["headRefOid"],
                "merge_sha": pr["mergeCommit"]["oid"] if pr["mergeCommit"] is not None else None} for pr in opened + recent]
        runs = _gh_json(root, "run", "list", "--repo", repo, "--branch", base, "--limit", "5",
                        "--json", "conclusion,status,headSha,createdAt,url")
        return {"pull_requests": prs,
                "runs": [{"conclusion": run["conclusion"], "status": run["status"], "sha": run["headSha"],
                          "time": run["createdAt"], "url": run["url"]} for run in runs]}, None
    except (FileNotFoundError, GitError) as exc:
        return None, str(exc)
    except subprocess.TimeoutExpired as exc:
        return None, str(exc)


def view(root, config, sessions, offset=0, limit=80, github_repo=None):
    """Return one graph page and current branch/worktree/session/GitHub details."""
    if offset < 0 or limit < 1:
        raise ValueError("offset 必须非负，limit 必须大于零")
    limit = min(limit, 80)
    key = _root(root)
    base, stable = config["base"], config["stable"]
    with _lock:
        signature, refs = _refs(root)
        cached = _refs_cache.get(key)
        cache_key = (signature, base, stable)
        if cached is None or cached[0] != cache_key:
            data = _branch_data(root, refs, base, stable)
            data["recent_merge"] = _latest_merge(root, refs, base)
            _refs_cache[key] = (cache_key, data)
        else:
            data = cached[1]
        data = copy.deepcopy(data)
        worktrees = _worktrees(root)
        for branch in data["branches"]:
            branch["worktrees"] = [{"path": tree["path"], "dirty": tree["dirty"]} for tree in worktrees
                                   if tree["branch"] == "refs/heads/" + branch["name"]]
            paths = {_root(tree["path"]) for tree in branch["worktrees"]}
            branch["sessions"] = [{"session_id": session["session_id"], "name": session["crew_name"],
                                   "group": session["group_name"], "role": session["role"], "status": session["status"]}
                                  for session in sessions if session["branch"] == branch["name"]
                                  or session["worktree"] is not None and _root(session["worktree"]) in paths]
        commits, next_offset = _commits(root, refs, offset, limit, stable)
        gh_key = (key, base, github_repo)
        now = time.monotonic()
        gh_cached = _github_cache.get(gh_key)
        if gh_cached is None or now - gh_cached[0] >= 60:
            github, reason = _github(root, base, github_repo)
            _github_cache[gh_key] = (now, github, reason)
        else:
            _, github, reason = gh_cached
        _commit_context(root, commits + ([] if data["recent_merge"] is None else [data["recent_merge"]]), refs, base, github)
        data.update(repository=Path(root).name, commits=commits, next_offset=next_offset,
                    github=copy.deepcopy(github), github_reason=reason, last_fetch_at=_last_fetch.get(key))
        return data


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
            _refs_cache.pop(key, None)
            for gh_key in list(_github_cache):
                if gh_key[0] == key:
                    del _github_cache[gh_key]
        return {"ok": result.returncode == 0, "output": result.stdout, "error": result.stderr,
                "last_fetch_at": _last_fetch.get(key)}
