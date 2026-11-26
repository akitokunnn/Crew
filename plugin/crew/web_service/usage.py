from contextlib import closing

from .. import board, claude_limits, store, usage


class UsageMixin:
    def scan_usage(self, *, live_dispatch=False):
        with self.write_lock, self.scan_lock, closing(store.connect(self.home)) as conn:
            now = self.now()
            full = self.scanned_at is None or (now - self.scanned_at).total_seconds() >= 60
            if full or live_dispatch:
                usage.scan(conn, self.claude_root, self.codex_root, self.runs_file, receipts_only=not full)
            if full:
                cache_path = self.claude_root.parent.parent / '.claude.json'
                week = claude_limits.latest(self.home, cache_path=cache_path)['seven_day']
                seven_day = claude_limits.read(self.home, cache_path=cache_path, now=now)['seven_day']
                usage.record_claude_week(conn, week)
                crew = None
                if seven_day is not None and seven_day['used_percent'] is not None and seven_day['resets_at'] is not None:
                    crew = usage.crew_weekly(conn, seven_day, usage.load_prices(self.home / 'prices.json'), now)
                self.crew_usage = (seven_day['resets_at'] if seven_day is not None else None, crew)
                self.claude_budget = self._budget(conn, now)
                self.scanned_at = now
            return self.scanned_at

    def notification_status(self):
        return self.connections.notification_status()

    def set_away(self, away):
        return self.connections.set_away(away)

    def usage(self, period="week", what=None, project_name=None, scope='workspace'):
        try:
            start, end = usage.period(period, self.now())
        except ValueError as exc:
            raise ValueError("\u65f6\u95f4\u6bb5\u975e\u6cd5: " + period) from exc
        if scope not in ('workspace', 'machine'):
            raise ValueError('用量范围必须是 workspace 或 machine')
        self.scan_usage()
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
            result['summary']['claude_limits'] = self._claude_limits()
            result['claude_weeks'] = usage.claude_weeks(conn, prices, self.now())
            result["codex"] = usage.codex_tasks(conn, start, end, project=name)
            result.update(scope=scope, project=project)
            return result
