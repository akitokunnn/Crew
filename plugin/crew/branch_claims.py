"""Remember which Crew session created or committed on a branch, from the shell commands it runs.

Leaders often open worktrees with plain Git, which Crew never sees otherwise. The Claude Code
and Codex PreToolUse hooks pass every allowed shell command here; the Git page uses the
recorded sessions to tie each branch to its leader (and to the task they were running).
"""
from contextlib import closing
import ntpath
import os
import re
import sqlite3
import subprocess
import sys

from . import board, git_overview, git_process, guard, store

SEPARATORS = ("&&", "||", ";", "|", "&", "(", ")", "\n")
# Options of `git` itself that consume the following word.
GIT_VALUE_OPTIONS = ("-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env")
# Shells that run a quoted command string: `pwsh -Command "git ..."`, `bash -lc '...'`, `cmd /c "..."`.
SHELLS = {"pwsh": ("-command", "-c"), "powershell": ("-command", "-c"), "bash": ("-c", "-lc"), "sh": ("-c", "-lc"),
          "zsh": ("-c", "-lc"), "cmd": ("/c",)}
CD = ("cd", "set-location", "sl", "pushd", "chdir")
WORKTREE_VALUE_OPTIONS = ("--reason",)


def _command_flag(executable, word):
    """Only a shell's own -Command / -c flag introduces a command string; git's -c and -C do not."""
    name = executable.replace("\\", "/").rsplit("/", 1)[-1].casefold().removesuffix(".exe")
    flags = SHELLS.get(name, ())
    # PowerShell and cmd flags are case-insensitive; POSIX shells' are not.
    return (word.casefold() if name in ("pwsh", "powershell", "cmd") else word) in flags


def _words(command):
    """Simple commands as word lists; a quoted command string after -Command/-c is split as well."""
    commands, previous = [[]], None
    for match in guard.SHELL_WORDS.finditer(command):
        word = match.group()
        if word in SEPARATORS:
            commands.append([])
            previous = None
            continue
        quoted = len(word) > 1 and word[0] == word[-1] and word[0] in "\"'"
        text = word.strip("\"'")
        if quoted and previous is not None and _command_flag(commands[-1][0] if commands[-1] else "", previous):
            commands.extend(_words(text))
            commands.append([])
        else:
            commands[-1].append(text)
        previous = text
    return [words for words in commands if words]


def _path(directory, value):
    # Git Bash spells C:\x as /c/x; Windows shells understand neither form from the other side.
    if os.name == "nt":
        drive = re.fullmatch(r"/([a-zA-Z])(/.*)?", value)
        if drive is not None:
            value = drive.group(1) + ":" + (drive.group(2) or "/")
        return ntpath.normpath(ntpath.join(directory, os.path.expanduser(value)))
    return os.path.normpath(os.path.join(directory, os.path.expanduser(value)))


def _option_value(arguments, options):
    for index, argument in enumerate(arguments):
        if argument in options and index + 1 < len(arguments):
            return arguments[index + 1]
        for option in options:
            if option.startswith("--") and argument.startswith(option + "="):
                return argument[len(option) + 1:]
    return None


def _worktree_add(arguments, directory):
    created = _option_value(arguments, ("-b", "-B"))
    if created is not None:
        return [("branch", created)]
    if "--detach" in arguments or "-d" in arguments:
        return []
    positional, index = [], 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in WORKTREE_VALUE_OPTIONS:
            index += 2
            continue
        if not argument.startswith("-"):
            positional.append(argument)
        index += 1
    if len(positional) >= 2:
        return [("branch", positional[1])]
    if len(positional) == 1:
        # Without a branch argument Git creates one named after the new directory.
        return [("branch", ntpath.basename(_path(directory, positional[0]).rstrip("\\/")))]
    return []


def _git(arguments, directory):
    index = 0
    while index < len(arguments) and arguments[index].startswith("-"):
        option = arguments[index]
        if option in ("-h", "--help"):
            return []
        if option == "-C" and index + 1 < len(arguments):
            directory = _path(directory, arguments[index + 1])
            index += 2
        elif option in GIT_VALUE_OPTIONS:
            index += 2
        else:
            index += 1
    if index >= len(arguments):
        return []
    command, rest = arguments[index], arguments[index + 1:]
    if any(argument in ("-h", "--help") for argument in rest):
        return []
    if command == "worktree" and rest[:1] == ["add"]:
        return _worktree_add(rest[1:], directory)
    if command == "checkout":
        name = _option_value(rest, ("-b", "-B", "--orphan"))
        return [] if name is None else [("branch", name)]
    if command == "switch":
        name = _option_value(rest, ("-c", "-C", "--create", "--force-create", "--orphan"))
        return [] if name is None else [("branch", name)]
    if command == "branch":
        # Only `git branch <new> [<start>]`; listing, deleting, renaming and options are not creation.
        return [("branch", rest[0])] if rest and not any(argument.startswith("-") for argument in rest) else []
    if command == "commit":
        return [("current", directory)]
    return []


def parse(command, cwd):
    """[("branch", name)] for branches a command creates, [("current", dir)] for commits in a checkout."""
    if "git" not in command.casefold():
        return []
    found, directory = [], cwd
    for words in _words(command):
        executable = words[0].replace("\\", "/").rsplit("/", 1)[-1].casefold()
        targets = [word for word in words[1:] if not word.startswith("-") and word.casefold() != "/d"]
        if executable in CD and len(targets) == 1:
            directory = _path(directory, targets[0])
        elif executable in ("git", "git.exe"):
            found.extend(_git(words[1:], directory))
    return found


def _literal(name):
    # `git worktree add -b "$b"` names a shell variable; the real branch is unknown until it is committed on.
    return not any(mark in name for mark in "$`%")


def _worktrees(root):
    """{branch: worktree directory} for every checkout of the repository."""
    result = git_process.run(["-C", root, "worktree", "list", "--porcelain"], timeout=5)
    found, directory = {}, None
    for line in result.stdout.splitlines() if result.returncode == 0 else ():
        if line.startswith("worktree "):
            directory = line[len("worktree "):]
        elif line.startswith("branch refs/heads/") and directory is not None:
            found[line[len("branch refs/heads/"):]] = directory
    return found


def task_branch_reason(home, cwd, command, execution_id):
    """A task works on one branch: block a command in its execution that would create another one.

    The task's branch is one it already claimed that still exists (the base and the local
    integration branch never count); recreating that same branch stays allowed.
    """
    if execution_id is None or not store.db_path(home).exists():
        return None
    created = list(dict.fromkeys(value for kind, value in parse(command, cwd) if kind == "branch"))
    if not created:
        return None
    with closing(guard.readonly(home)) as conn:
        goal = conn.execute("SELECT g.id,g.project FROM goal_executions e JOIN goals g ON g.id=e.goal_id "
                            "WHERE e.id=?", (execution_id,)).fetchone()
        if goal is None:
            return None
        root = board.project(conn, goal["project"])["root"]
        config = board.git_config(conn, goal["project"]) or {}
        shared = {config.get("base"), config.get("integration")}
        claimed = [row["branch"] for row in conn.execute(
            "SELECT DISTINCT branch FROM branch_claims WHERE project=? AND goal_id=? ORDER BY claimed_at,branch",
            (goal["project"], goal["id"]))]
    existing = [name for name in claimed if name not in shared and _literal(name) and git_process.run(
        ["-C", root, "rev-parse", "--verify", "--quiet", "refs/heads/" + name], timeout=5).returncode == 0]
    if existing:
        extra = [name for name in created if name not in existing]
        if not extra:
            return None
        branch = existing[0]
        place = _worktrees(root).get(branch)
        where = ("它检出在 %s，进那个目录工作" % place if place else
                 "需要工作目录就用 git worktree add <目录> %s（不带 -b）" % branch)
        return ("BLOCKED by Crew: 一个任务只用一个分支。本任务已有分支 %s，不要再建 %s；"
                "各轮、返工和成员都在这个分支上提交，%s。%s" % (branch, "、".join(extra), where, guard.TAIL))
    if len(created) > 1:
        return ("BLOCKED by Crew: 一个任务只用一个分支，一次只能建一个，不要同时建 %s；"
                "成员并行时共用这个分支的工作目录，按文件归属分工。%s" % ("、".join(created), guard.TAIL))
    return None


def record(home, session_id, cwd, command, execution_id=None, now=None):
    """Store the branches `command` creates or commits to for a Crew session in a project.

    Only sessions already on the board count; the command has not run yet, so a commit's
    branch is the one currently checked out. A branch another current leader answers for stays
    theirs: working on it does not claim it, only handing the task over moves it.
    """
    found = parse(command, cwd)
    if not found or not store.db_path(home).exists():
        return []
    branches = []
    for kind, value in found:
        if kind == "branch":
            if _literal(value):
                branches.append(value)
            continue
        result = git_process.run(["-C", value, "branch", "--show-current"], timeout=3)
        # Outside a checkout or on a detached HEAD there is no branch to own.
        if result.returncode == 0 and result.stdout.strip():
            branches.append(result.stdout.strip())
    if not branches:
        return []
    with closing(store.connect(home)) as conn:
        session = conn.execute("SELECT project FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if session is None:
            return []
        goal = None
        if execution_id is not None:
            row = conn.execute("SELECT goal_id FROM goal_executions WHERE id=?", (execution_id,)).fetchone()
            goal = None if row is None else row["goal_id"]
        names = list(dict.fromkeys(branches))
        config = board.git_config(conn, session["project"])
        admin = None if config is None else config["admin_session_id"]
        own = board.session(conn, session_id)
        own = own if own["role"] == "leader" else board.leader_of(conn, session_id)

        def free(name):
            holder = git_overview.holder(conn, session["project"], name, admin)
            return holder is None or own is not None and holder["session_id"] == own["session_id"]
        with board.transaction(conn):
            names = [name for name in names if free(name)]
            for name in names:
                conn.execute(
                    "INSERT INTO branch_claims(project,branch,session_id,goal_id,claimed_at) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(project,branch,session_id) DO UPDATE SET "
                    "goal_id=COALESCE(excluded.goal_id,goal_id),claimed_at=excluded.claimed_at",
                    (session["project"], name, session_id, goal, board._time(now)))
        return names


def record_from_hook(home, session_id, cwd, command, execution_id=None, stderr=None):
    """Hook entry: the tool call is already allowed and must run even if recording fails."""
    try:
        return record(home, session_id, cwd, command, execution_id=execution_id)
    except (OSError, sqlite3.Error, subprocess.TimeoutExpired) as exc:
        print("Crew 未能记录分支归属：%s: %s" % (type(exc).__name__, exc), file=sys.stderr if stderr is None else stderr)
        return []
