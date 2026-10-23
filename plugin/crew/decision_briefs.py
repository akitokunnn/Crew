"""Explicit decision summaries; never infer or rewrite business meaning."""
import builtins
import json
import re

from . import board, store


INHERIT = object()
FIELDS = ("title", "context", "why_user", "recommendation_reason", "impact", "options")
_MARKER = re.compile(r"^[\s(（\[]*(?:[A-Za-z]|\d+|[①-⑳]+|[一二三四五六七八九十]+|"
                     r"(?:选项|方案)\s*[A-Za-z\d一二三四五六七八九十]+|"
                     r"[A-Za-z](?:\s*[/、,，]\s*[A-Za-z])+)[\s)）\].、:：]*$")


def _table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _text(value, label, maximum=20000, optional=False):
    if optional and value in (None, ""):
        return ""
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise board.BoardError(label + "必须是非空文本，且不能超过 %s 个字符" % maximum)
    return value


def validate(brief, options=None, recommendation=None):
    """Validate only explicit structure, not the user's intended business choice.

    One record has one single-line title and one set of choices. A leader must
    review whether the underlying business question is truly one decision;
    punctuation or keywords cannot safely determine that meaning for them.
    """
    if not isinstance(brief, dict) or any(key not in FIELDS for key in brief):
        raise board.BoardError("brief 必须是拍板摘要对象，不能包含未知字段")
    title = _text(brief.get("title"), "简短标题", 100)
    if "\n" in title or "\r" in title:
        raise board.BoardError("标题只写一件需要选择的事，不要把多个问题列在标题里")
    normalized = {"title": title, "context": _text(brief.get("context"), "背景", optional=True),
                  "why_user": _text(brief.get("why_user"), "为什么需要用户决定"),
                  "recommendation_reason": _text(brief.get("recommendation_reason"), "推荐理由"),
                  "impact": _text(brief.get("impact"), "影响", optional=True)}
    entries = brief.get("options")
    if not isinstance(entries, builtins.list) or not 2 <= len(entries) <= 12:
        raise board.BoardError("结构化拍板需要 2 至 12 个具体行动选项，并写出每项后果")
    normalized["options"] = []
    labels = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"label", "consequence"}:
            raise board.BoardError("每个选项必须包含 label 和 consequence")
        label = _text(entry["label"], "选项动作", 2000)
        if _MARKER.fullmatch(label):
            raise board.BoardError("选项必须写完整动作，不能只写 A/B、①② 或编号")
        consequence = _text(entry["consequence"], "选项后果")
        normalized["options"].append({"label": label, "consequence": consequence})
        labels.append(label)
    if len(labels) != len(set(labels)):
        raise board.BoardError("选项动作不能重复")
    if options is not None and (not isinstance(options, (builtins.list, tuple)) or builtins.list(options) != labels):
        raise board.BoardError("拍板选项与摘要中的动作不一致，请同时补全对应后果")
    if recommendation is not None and recommendation not in labels:
        raise board.BoardError("推荐必须使用一个完整选项动作；理由写入 recommendation_reason")
    return normalized


def _decode(row):
    return {**{field: row[field] for field in FIELDS if field != "options"}, "options": json.loads(row["options_json"])}


def get(conn, decision_id):
    if not _table(conn, "decision_briefs"):
        return None
    row = conn.execute("SELECT * FROM decision_briefs WHERE decision_id=?", (decision_id,)).fetchone()
    return _decode(row) if row is not None else None


def list(conn, project_name=None):
    """Return a decision-id -> brief mapping, for bulk UI enrichment."""
    if not _table(conn, "decision_briefs"):
        return {}
    if project_name is not None:
        board.project(conn, project_name)
    rows = conn.execute("SELECT b.* FROM decision_briefs b JOIN decisions d ON d.id=b.decision_id" +
                        (" WHERE d.project=?" if project_name is not None else ""),
                        (project_name,) if project_name is not None else ())
    return {row["decision_id"]: _decode(row) for row in rows}


def enrich(conn, decisions):
    """Accept one decision dict or a sequence; preserve its original fields."""
    one = isinstance(decisions, dict)
    values = [decisions] if one else decisions
    result = []
    for value in values:
        brief = get(conn, value["id"])
        status = value.get('status')
        if status is None:
            row = conn.execute('SELECT status FROM decisions WHERE id=?', (value['id'],)).fetchone()
            status = row['status'] if row is not None else None
        result.append({**value, "brief": brief, "needs_review": brief is None and status == "open"})
    return result[0] if one else result


def _save(conn, decision_id, brief, now=None):
    """Caller owns the transaction and has already validated the summary."""
    conn.execute("INSERT INTO decision_briefs(decision_id,title,context,why_user,recommendation_reason,impact,options_json,updated_at) "
                 "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(decision_id) DO UPDATE SET title=excluded.title,context=excluded.context,"
                 "why_user=excluded.why_user,recommendation_reason=excluded.recommendation_reason,impact=excluded.impact,"
                 "options_json=excluded.options_json,updated_at=excluded.updated_at",
                 (decision_id, brief["title"], brief["context"], brief["why_user"], brief["recommendation_reason"],
                  brief["impact"], json.dumps(brief["options"], ensure_ascii=False), store.now_iso() if now is None else now))


def _decision(conn, decision_id):
    if type(decision_id) is not int or decision_id < 1:
        raise board.BoardError("拍板 id 必须是正整数")
    row = conn.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
    if row is None:
        raise board.BoardError("待拍板不存在")
    value = dict(row)
    value["options"] = json.loads(value["options"])
    return value


def _authorized(conn, session_id, decision):
    actor = board.session(conn, session_id)
    if actor["project"] != decision["project"]:
        raise board.BoardError("拍板不属于当前项目")
    if (not actor["crew_name"] or actor["status"] == "closed" or board.is_archived(conn, session_id)):
        raise board.BoardError("归档或未收编的会话不能修改拍板")
    if session_id != decision["session_id"]:
        proposer = board.session(conn, decision["session_id"])
        leader = board.leader_of(conn, proposer["session_id"])
        if actor["role"] != "leader" or leader is None or leader["session_id"] != session_id:
            raise board.BoardError("只有原提出者或它当前所属的 leader 可以修改拍板")
    if decision["status"] != "open":
        raise board.BoardError("只能修改尚未答复的拍板")
    return actor


@board.atomic
def revise(conn, session_id, decision_id, question=INHERIT, options=INHERIT, recommendation=INHERIT, brief=INHERIT, now=None):
    before = _decision(conn, decision_id)
    _authorized(conn, session_id, before)
    question = before["question"] if question is INHERIT else _text(question, "问题")
    recommendation = before["recommendation"] if recommendation is INHERIT else recommendation
    existing = get(conn, decision_id)
    candidate = existing if brief is INHERIT else {**(existing or {}), **brief} if isinstance(brief, dict) else brief
    if candidate is None:
        raise board.BoardError("请补充结构化摘要、为什么需用户决定、推荐理由和各选项后果")
    if options is INHERIT:
        options = ([entry["label"] for entry in validate(candidate, recommendation=recommendation)["options"]]
                   if brief is not INHERIT else before["options"])
    validated = validate(candidate, options=options, recommendation=recommendation)
    conn.execute("UPDATE decisions SET question=?,options=?,recommendation=? WHERE id=?",
                 (question, json.dumps(builtins.list(options), ensure_ascii=False), recommendation, decision_id))
    _save(conn, decision_id, validated, now)
    board._event(conn, "decision:revise", before["project"], session_id,
                 {"id": decision_id, "before": before, "question": question, "brief": validated}, now)
    from . import trust_mode
    trust_mode.auto_answer(conn, decision_id, reason=validated["recommendation_reason"], now=now)
    value = next(d for d in board.decisions(conn, before["project"], all=True) if d["id"] == decision_id)
    return enrich(conn, value)


@board.atomic
def queue_review(conn, decision_id, now=None):
    """Queue one clarification request for an old open question, without replacing it."""
    decision = _decision(conn, decision_id)
    if decision["status"] != "open" or get(conn, decision_id) is not None:
        return None
    if conn.execute("SELECT 1 FROM decision_reviews WHERE decision_id=?", (decision_id,)).fetchone():
        return None
    proposer = board.session(conn, decision["session_id"])
    leader = proposer if proposer["role"] == "leader" else board.leader_of(conn, proposer["session_id"])
    if (leader is not None and (leader["project"] != decision["project"] or leader["status"] == "closed"
                               or board.is_archived(conn, leader["session_id"]))):
        leader = None
    notice = ("请重新审阅这项旧拍板（内部 ID：%s），不要另发重复题。\n原问题：%s\n"
              "确实需要用户选择时，用 crew decision revise %s 原位补清：一事一问的短标题、背景、为什么必须由用户决定、"
              "推荐方案和理由、影响，以及各个完整行动选项的后果。选项不能只写①②或 A/B。"
              "若其实是用户亲手操作，转为 crew todo add，写清步骤与确认方法，再撤回原题。"
              "若信任模式已经授权自主决定，由 AI 自行作出有理由的建议并登记，不能默认选第一项。"
              "请保留原 ID，不要让用户在多个重复题之间选择。" %
              (decision_id, decision["question"], decision_id))
    notice_id, handoff_id = None, None
    if leader is not None:
        notice_id = board._queue_leader_notice(conn, leader["session_id"], notice, "crew", now)
    else:
        handoff_id = conn.execute("INSERT INTO handoffs(project,from_session,to_target,body,created_at) VALUES(?,?,?,?,?)",
                                  (decision["project"], board.HUMAN, proposer["session_id"], notice,
                                   store.now_iso() if now is None else now)).lastrowid
    conn.execute("INSERT INTO decision_reviews(decision_id,notice_id,queued_at) VALUES(?,?,?)",
                 (decision_id, notice_id, store.now_iso() if now is None else now))
    board._event(conn, "decision:review", decision["project"], board.HUMAN,
                 {"id": decision_id, "notice_id": notice_id, "handoff_id": handoff_id}, now)
    return notice_id
