"""Observe GitHub CI and queue durable, once-per-run Git administrator notices."""
from contextlib import closing
import shutil

from . import board, git_view, store


class Poller:
    def __init__(self, home, write_lock):
        self.home, self.write_lock = home, write_lock

    def poll(self, now=None):
        if shutil.which("gh") is None:
            return
        now = store.now_iso() if now is None else now
        with self.write_lock, closing(store.connect(self.home)) as conn:
            configs = [dict(row) for row in conn.execute(
                "SELECT g.*, p.root FROM git_management g JOIN projects p ON p.name=g.project "
                "WHERE g.admin_session_id IS NOT NULL")]
        for config in configs:
            github, _ = git_view._github(config["root"], config["base"])
            if github is None:
                continue
            with self.write_lock, closing(store.connect(self.home)) as conn, board.transaction(conn):
                current = board.git_config(conn, config["project"])
                if current is None or any(current[key] != config[key] for key in current):
                    continue
                runs = github["runs"]
                if runs:
                    run = runs[0]
                    failed = run["status"] == "completed" and run["conclusion"] in {
                        "failure", "timed_out", "action_required", "startup_failure", "cancelled", "stale"}
                    self._observe(conn, config, "base:" + config["base"], run["url"], run["sha"],
                                  run["conclusion"] if run["status"] == "completed" else "pending",
                                  failed, "base 分支 %s CI 失败：提交 %s，%s" % (
                                      config["base"], run["sha"], run["url"]), now)
                numbers = {row[0] for row in conn.execute(
                    "SELECT DISTINCT pr_number FROM git_merge_requests WHERE project=? "
                    "AND status IN ('open','merging','waiting_user') AND pr_number IS NOT NULL",
                    (config["project"],))}
                for pr in github["pull_requests"]:
                    if pr["number"] not in numbers:
                        continue
                    subject = "pr:" + str(pr["number"])
                    previous = conn.execute(
                        "SELECT status FROM git_ci_state WHERE project=? AND subject=? AND sha=? "
                        "ORDER BY updated_at DESC,rowid DESC LIMIT 1", (config["project"], subject, pr["sha"])).fetchone()
                    completed = previous is not None and previous["status"] == "pending" and pr["ci"] in {"success", "failure"}
                    result = "成功" if pr["ci"] == "success" else "失败"
                    self._observe(conn, config, subject, pr["run_key"], pr["sha"], pr["ci"], completed,
                                  "PR #%s CI %s：提交 %s，%s" % (
                                      pr["number"], result, pr["sha"], pr["url"]), now)

    @staticmethod
    def _observe(conn, config, subject, run_key, sha, status, notify, text, now):
        previous = conn.execute(
            "SELECT notified FROM git_ci_state WHERE project=? AND subject=? AND run_key=?",
            (config["project"], subject, run_key)).fetchone()
        notified = previous is not None and previous["notified"] == 1
        if notify and not notified:
            board._queue_leader_notice(conn, config["admin_session_id"], text, "crew", now=now)
            notified = True
        conn.execute(
            "INSERT INTO git_ci_state(project,subject,run_key,sha,status,notified,updated_at) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(project,subject,run_key) DO UPDATE SET status=excluded.status, "
            "notified=excluded.notified,updated_at=excluded.updated_at",
            (config["project"], subject, run_key, sha, status, int(notified), now))
