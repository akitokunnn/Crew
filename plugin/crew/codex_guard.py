"""Adapt native Codex PreToolUse hooks to Crew's existing guard.

The launcher binds --home and --session; the hook's session_id is a Codex
thread id and is never used as the Crew identity. This adapter can explicitly
deny failures it observes. Codex itself may continue after a missing, crashed,
or timed-out hook, so hook health must also be checked by the owning runtime.
"""
import argparse
from contextlib import closing
import json
import ntpath
import os
from pathlib import Path
import sys

from . import board, goals, guard, runtimes, store


class InvalidHook(ValueError):
    pass


def _text(value, field):
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise InvalidHook("invalid " + field)
    return value


def patch_paths(command):
    """Validate the native apply_patch envelope and enumerate every touched path.

    Move sources and destinations both count. Do not infer touched paths from
    added/deleted source lines, which may themselves contain patch-like text.
    """
    # Rust's native parser splits LF/CRLF. Python splitlines() also treats
    # Unicode separators as newlines, which could invent different file paths.
    lines = _text(command, "patch").split("\n")
    if lines[-1] == "":
        lines.pop()
    lines = [line[:-1] if line.endswith("\r") else line for line in lines]
    if len(lines) < 3 or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise InvalidHook("invalid apply_patch envelope")
    paths, index = [], 1

    def path(value):
        value = _text(value, "patch path")
        if value != value.strip() or any(char in value for char in "\r\n"):
            raise InvalidHook("invalid patch path")
        # Drive-relative Windows paths depend on process drive state, not cwd.
        if ntpath.splitdrive(value)[0] and not ntpath.isabs(value):
            raise InvalidHook("drive-relative patch path")
        paths.append(value)

    while index < len(lines) - 1:
        header = lines[index]
        index += 1
        if header.startswith("*** Add File: "):
            path(header[len("*** Add File: "):])
            while index < len(lines) - 1 and lines[index].startswith("+"):
                index += 1
        elif header.startswith("*** Delete File: "):
            path(header[len("*** Delete File: "):])
        elif header.startswith("*** Update File: "):
            path(header[len("*** Update File: "):])
            if index < len(lines) - 1 and lines[index].startswith("*** Move to: "):
                path(lines[index][len("*** Move to: "):])
                index += 1
            chunks = 0
            while index < len(lines) - 1:
                line = lines[index]
                if line.startswith(("*** Add File: ", "*** Update File: ", "*** Delete File: ")):
                    break
                if line == "@@" or line.startswith("@@ "):
                    chunks += 1
                elif line == "*** End of File":
                    if not chunks:
                        raise InvalidHook("patch end-of-file without hunk")
                elif not chunks or not line.startswith((" ", "+", "-")):
                    raise InvalidHook("invalid apply_patch hunk")
                index += 1
            if not chunks:
                raise InvalidHook("apply_patch update without hunk")
        else:
            raise InvalidHook("unknown apply_patch operation")
    if not paths:
        raise InvalidHook("empty apply_patch")
    return list(dict.fromkeys(paths))


def _identity(home, session_id, execution_id=None, native_id=None):
    _text(session_id, "Crew session")
    # Never create or migrate a database in order to authorize a tool call.
    with closing(guard.readonly(home)) as conn:
        session = board.session(conn, session_id)
        if session["project"] is None or session["status"] == "closed" or board.is_archived(conn, session_id):
            raise InvalidHook("Crew session is not an active project member")
        if execution_id:
            execution = goals.get_execution(conn, execution_id)
            if execution['session_id'] != session_id or execution['runtime'] != 'codex':
                raise InvalidHook('goal execution identity mismatch')
            binding = next((b for b in execution['bindings'] if b['runtime'] == 'codex'), None)
            if binding is None or binding['native_id'] != native_id:
                raise InvalidHook('goal native thread mismatch')
            gate = goals.dispatch_check(conn, execution['goal_id'], execution_id=execution_id)
            if not gate['allowed']:
                raise InvalidHook(gate['reason'])
        elif runtimes.get(conn, session_id)['runtime'] != 'codex':
            raise InvalidHook('Crew active runtime is not codex; this Codex carrier is inactive')
        board.project(conn, session["project"])
        # Check the guard's required read paths up front. In particular, its
        # diagnostic-command recovery exception must not hide a broken board.
        board.git_config(conn, session["project"])
        board.resources(conn, session["project"])
        board.leases(conn, session["project"])
        board.claims(conn, session["project"])
    guard.load_blacklist(home)
    return session


def check(home, session_id, payload, execution_id=None):
    """Return None to allow, otherwise an explicit blocking reason.

    Only the native shell and patch paths are supported. Configure the hook
    matcher accordingly; other tools need an explicit adapter before enabling.
    """
    try:
        if not isinstance(payload, dict) or payload.get("hook_event_name") != "PreToolUse":
            raise InvalidHook("expected PreToolUse object")
        _text(payload.get("session_id"), "Codex thread")
        _identity(home, session_id, execution_id, payload['session_id'])
        cwd = _text(payload.get("cwd"), "cwd")
        if not os.path.isabs(cwd):
            raise InvalidHook("cwd must be absolute")
        tool = _text(payload.get("tool_name"), "tool_name")
        inputs = payload.get("tool_input")
        if not isinstance(inputs, dict):
            raise InvalidHook("tool_input must be an object")
        command = _text(inputs.get("command"), "tool_input.command")
        if tool not in ("Bash", "apply_patch"):
            raise InvalidHook("unsupported Codex tool: " + tool)
        data = {**payload, "session_id": session_id, "tool_input": dict(inputs)}
        # Preserve the original tool name and complete arguments for rules on
        # apply_patch itself, including patterns that match patch content.
        reason = guard.blacklist_reason(home, data)
        if reason is not None:
            return str(reason)
        if tool == "Bash":
            return guard.check(home, data)
        paths = patch_paths(command)
        for path in paths:
            absolute = store.norm_path(path if os.path.isabs(path) else Path(cwd) / path)
            for alias in ("apply_patch", "Edit", "Write"):
                normalized = {**data, "tool_name": alias,
                              "tool_input": {**inputs, "file_path": absolute}}
                reason = guard.check(home, normalized)
                if reason is not None:
                    return reason
        return None
    except Exception as exc:
        return "BLOCKED by Crew Codex guard: %s: %s. %s" % (type(exc).__name__, exc, guard.TAIL)


def main(argv=None, stdin=None, stdout=None, stderr=None):
    parser = argparse.ArgumentParser(description="Crew guard for native Codex hooks")
    parser.add_argument("--home", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--execution")
    args = parser.parse_args(argv)
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    try:
        payload = json.load(stdin)
        reason = check(args.home, args.session, payload, args.execution)
    except Exception as exc:
        reason = "BLOCKED by Crew Codex guard: %s: %s. %s" % (type(exc).__name__, exc, guard.TAIL)
    if reason is None:
        return 0
    json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                      "permissionDecisionReason": reason}}, stdout, ensure_ascii=True)
    stdout.write("\n")
    print(reason, file=stderr)
    # Codex executes Windows hooks through PowerShell -Command, which can map
    # native exit 2 to 1. Exit 0 plus an explicit JSON deny is supported on all
    # platforms and preserves the blocking decision through that wrapper.
    return 0
