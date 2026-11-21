"""Dollar budget for Claude billed through the API (Claude Platform / Console).

Such accounts have no subscription windows to observe, and Anthropic offers no
endpoint for a credit balance or spend limit, so the user states the budget once
and its spending is this machine's Claude requests priced at API rates.
"""
import datetime as dt
import json
import math
import threading
from pathlib import Path

from . import store, usage


CYCLES = ('monthly', 'once')
FILE = 'claude-budget.json'
_lock = threading.Lock()


class BudgetError(ValueError):
    pass


def validate(value):
    """None turns the budget off (subscription quota); otherwise amount, cycle and, for once, a start date."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {'amount_usd', 'cycle', 'start'}:
        raise BudgetError('额度设置必须包含 amount_usd、cycle、start')
    amount = value['amount_usd']
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or not 0 < amount <= 1_000_000:
        raise BudgetError('额度金额必须是 0 到 1000000 之间的美元数')
    if value['cycle'] not in CYCLES:
        raise BudgetError('额度周期必须是 monthly（每月重置）或 once（一次性充值）')
    start = value['start']
    if value['cycle'] == 'monthly':
        if start is not None:
            raise BudgetError('每月额度不需要开始日期')
    else:
        try:
            dt.date.fromisoformat(start)
        except (TypeError, ValueError) as exc:
            raise BudgetError('一次性额度需要 YYYY-MM-DD 格式的充值日期') from exc
    return {'amount_usd': float(amount), 'cycle': value['cycle'], 'start': start}


def load(home):
    try:
        text = (Path(home) / FILE).read_text(encoding='utf-8')
    except FileNotFoundError:
        return None
    return validate(json.loads(text))


def save(home, value):
    value = validate(value)
    path = Path(home) / FILE
    with _lock:
        if value is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Written in place: renaming over a file a Windows scanner holds open fails.
            path.write_text(json.dumps(value, ensure_ascii=False) + '\n', encoding='utf-8')
    return value


def period(config, now):
    """[since, resets_at) of the budget; a monthly budget follows Console's UTC calendar month."""
    if config['cycle'] == 'monthly':
        current = now.astimezone(dt.timezone.utc)
        since = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        reset = since.replace(year=since.year + 1, month=1) if since.month == 12 else since.replace(month=since.month + 1)
        return since, reset
    since = dt.datetime.combine(dt.date.fromisoformat(config['start']), dt.time()).astimezone()
    return since.astimezone(dt.timezone.utc), None


def _iso(value):
    return None if value is None else value.isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def spending(conn, config, prices, now):
    """This machine's Claude spending in the budget period, with Crew's part.

    Requests from models without a price still count as tokens in unpriced_tokens,
    so the dollar figure is then a lower bound.
    """
    since, reset = period(config, now)
    crew = usage.crew_sessions(conn)
    spent = crew_spent = 0.0
    unpriced = 0
    for row in conn.execute('SELECT * FROM claude_requests WHERE ts>=? AND ts<=?', (_iso(since), _iso(now))):
        _, priced, missing = usage.price(row, prices)
        spent += priced
        unpriced += missing
        if row['session_id'] in crew:
            crew_spent += priced
    amount = config['amount_usd']
    return {
        **config, 'since': _iso(since), 'resets_at': _iso(reset),
        'spent_usd': spent, 'crew_usd': crew_spent, 'remaining_usd': max(0.0, amount - spent),
        'used_percent': spent / amount * 100, 'unpriced_tokens': unpriced, 'captured_at': _iso(now),
    }


def read(home, conn, now=None):
    """None while no budget is set; prices are read only for a set budget."""
    config = load(home)
    if config is None:
        return None
    prices = usage.load_prices(Path(home) / 'prices.json')
    return spending(conn, config, prices, store.parse_iso(store.now_iso()) if now is None else now)
