import sqlite3
import traceback
from contextlib import closing

from .. import board, change_feed, claude_limits, store, usage

# Dispatch receipts are read every tick; transcripts, rate limits and the weekly estimates every full scan.
# Before the background scan, every open page drove a receipt scan with each 3-second state poll and a
# full scan at most once a minute; the background keeps both rates whether or not a page is open.
USAGE_TICK_SECONDS = 2
FULL_SCAN_SECONDS = 60
# What a scan step can meet: a busy or broken database, a source file changing or vanishing, a bad prices
# file, or a Claude quota cache whose fields are missing or of another type.
USAGE_ERRORS = (sqlite3.Error, OSError, ValueError, KeyError, TypeError)


class UsageMixin:
    # State before the first background scan; web.py's __init__ starts scanned_at, crew_usage and
    # claude_budget the same way.
    usage_error = None
    usage_published = None

    def _poll_usage(self, after):
        """Scan steps every USAGE_TICK_SECONDS once `after` is set.

        The agents worker's first pass (auto-resuming natives that died with the last service) still goes
        first, as it did when the first page poll drove the scan.
        """
        while not after.wait(0.5):
            if self.stop.is_set():
                return
        while not self.stop.is_set():
            try:
                self.scan_usage()
            except USAGE_ERRORS as exc:
                self._usage_failed(exc)
            if self.stop.wait(USAGE_TICK_SECONDS):
                break

    def _usage_failed(self, exc):
        traceback.print_exc()
        try:
            self.record_usage_error(exc)
        except USAGE_ERRORS:
            # The page value cannot be built or stored either; the reason is printed, the snapshot read
            # fails with it too, and the next tick retries.
            traceback.print_exc()

    def scan_usage(self):
        """One background usage step; the snapshot only reads what it leaves behind.

        The scan commits file by file, so dispatch receipts keep flowing even while the page values below
        cannot be built (a Claude quota cache in an unknown format, a bad prices file), and other writers
        never wait for more than one transcript. Then a full scan stores the weekly estimate and the
        budget, and the `limits` change is recorded when the value the page shows differs from the last one
        recorded. A crash in between loses nothing: the scan resumes from the saved offsets, and after a
        restart nothing is recorded yet, so the first step records the value.
        """
        now = self.now()
        full = self.scanned_at is None or (now - self.scanned_at).total_seconds() >= FULL_SCAN_SECONDS
        with self.usage_scan_lock, closing(store.connect(self.home)) as conn:
            # Each transcript commits on its own under the write lock; a first scan of every transcript on
            # the machine takes many seconds, and holding the database for all of it made chat delivery
            # receipts, the change feed and operation receipts fail on the busy timeout meanwhile.
            counts = usage.scan(conn, self.claude_root, self.codex_root, self.runs_file, receipts_only=not full,
                                writer=self.write_lock)
            with self.write_lock:
                if counts['inserted'] or counts['updated']:
                    with board.transaction(conn):
                        # Dispatch receipts and the settings observed in transcripts show on the cards.
                        for project in board.projects(conn):
                            change_feed.touch(conn, 'conversations', project['name'], project['name'])
                crew_usage, budget, scanned_at = self.crew_usage, self.claude_budget, self.scanned_at
                with board.transaction(conn):
                    if full:
                        crew_usage, budget, scanned_at = self._crew_usage(conn, now), self._budget(conn, now), now
                    value = self._record_usage(conn, crew_usage, budget, scanned_at, None)
        with self.scan_lock:
            if full:
                self.crew_usage, self.claude_budget, self.scanned_at = crew_usage, budget, scanned_at
            self.usage_error = None
        self.usage_published = value
        return scanned_at

    def _record_usage(self, conn, crew_usage, budget, scanned_at, error):
        value = self.usage_value(conn, crew_usage, budget, scanned_at, error)
        if value != self.usage_published:
            change_feed.updated(conn, change_feed.GLOBAL, 'limits', 'limits', value)
        return value

    def prime_usage(self):
        """Weekly estimate and budget from what earlier scans stored, without reading any transcript.

        A failure is shown like a failed scan step; the background scan retries it.
        """
        now = self.now()
        try:
            with self.write_lock, closing(store.connect(self.home)) as conn:
                with board.transaction(conn):
                    crew_usage = self._crew_usage(conn, now)
                    budget = self._budget(conn, now)
        except USAGE_ERRORS as exc:
            self._usage_failed(exc)
            return
        with self.scan_lock:
            self.crew_usage, self.claude_budget = crew_usage, budget

    def record_usage_error(self, exc):
        """A failed step keeps the last good values, shows its real reason, and the next tick retries."""
        error = '用量扫描失败（%d 秒后重试）：%s: %s' % (USAGE_TICK_SECONDS, type(exc).__name__, exc)
        with self.scan_lock:
            self.usage_error = error
        with self.write_lock, closing(store.connect(self.home)) as conn:
            with board.transaction(conn):
                value = self._record_usage(conn, self.crew_usage, self.claude_budget, self.scanned_at, error)
        self.usage_published = value

    def _crew_usage(self, conn, now):
        cache_path = self.claude_root.parent.parent / '.claude.json'
        week = claude_limits.latest(self.home, cache_path=cache_path)['seven_day']
        seven_day = claude_limits.read(self.home, cache_path=cache_path, now=now)['seven_day']
        usage.record_claude_week(conn, week)
        crew = None
        if seven_day is not None and seven_day['used_percent'] is not None and seven_day['resets_at'] is not None:
            crew = usage.crew_weekly(conn, seven_day, usage.load_prices(self.home / 'prices.json'), now)
        return (seven_day['resets_at'] if seven_day is not None else None, crew)

    def usage_value(self, conn, crew_usage, budget, scanned_at, error):
        """The snapshot fields the usage scan owns; the `limits` change carries exactly these."""
        return {'limits': self._limits(conn, crew_usage, budget),
                'usage_scanned_at': scanned_at.isoformat() if scanned_at is not None else None,
                'usage_error': error}

    def notification_status(self):
        return self.connections.notification_status()

    def set_away(self, away):
        self.connections.set_away(away)
        return self.notification_status()

    def usage(self, period="week", what=None, project_name=None, scope='workspace'):
        try:
            start, end = usage.period(period, self.now())
        except ValueError as exc:
            raise ValueError("\u65f6\u95f4\u6bb5\u975e\u6cd5: " + period) from exc
        if scope not in ('workspace', 'machine'):
            raise ValueError('用量范围必须是 workspace 或 machine')
        with closing(store.connect(self.home)) as conn:
            project = board.project(conn, self.selected_project(conn, project_name)) if scope == 'workspace' else None
            name = project['name'] if project else None
            if what == "codex":
                return usage.codex_tasks(conn, start, end, project=name)
            prices = usage.load_prices(self.home / "prices.json")
            functions = {"summary": usage.summary, "sessions": usage.by_session, "daily": usage.daily,
                         "agents": usage.by_agent_type, "models": usage.by_model, "requests": usage.requests, 'tree': usage.tree}
            if what is not None:
                return functions[what](conn, start, end, prices, project=name)
            result = {key: fn(conn, start, end, prices, project=name) for key, fn in functions.items() if key != "requests"}
            result['summary']['claude_limits'] = self._claude_limits(self.crew_usage, self.claude_budget)
            result['claude_weeks'] = usage.claude_weeks(conn, prices, self.now())
            result["codex"] = usage.codex_tasks(conn, start, end, project=name)
            result.update(scope=scope, project=project)
            return result
