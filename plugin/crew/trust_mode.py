"""Per-project autonomy, recorded choices and a reviewable report draft.

This module changes no global permissions and never infers completion from a
command exit code. The host chooses when to enable trust mode and injects the
resulting context into each runtime's existing role instructions.
"""
import json
import functools

from . import board, store, todo


def _atomic(function):
    """Also accept the transaction already owned by board.ask's host caller."""
    @functools.wraps(function)
    def wrapped(conn, *args, **kwargs):
        if conn.in_transaction:
            return function(conn, *args, **kwargs)
        with board.transaction(conn):
            return function(conn, *args, **kwargs)
    return wrapped


def _table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def get(conn, project):
    board.project(conn, project)
    row = (conn.execute("SELECT enabled,goal,updated_at FROM trust_settings WHERE project=?", (project,)).fetchone()
           if _table(conn, "trust_settings") else None)
    return {"enabled": bool(row["enabled"]), "goal": row["goal"], "updated_at": row["updated_at"]} if row else {
        "enabled": False, "goal": None, "updated_at": None}


def _text(value, label, *, required=False):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 20000:
        raise board.BoardError(label + "必须是非空文本，且不能超过 20000 个字符")
    return value.strip()


def _active_leaders(conn, project):
    leaders = conn.execute("SELECT DISTINCT s.* FROM sessions s JOIN groups g "
                           "ON g.project=s.project AND g.leader_session_id=s.session_id "
                           "WHERE s.project=? AND s.role='leader' AND s.crew_name IS NOT NULL "
                           "AND s.status!='closed' AND g.closed_at IS NULL ORDER BY s.started_at,s.session_id", (project,))
    return [dict(row) for row in leaders if not board.is_archived(conn, row["session_id"])]


def _instructions(goal):
    return ("本项目已启用信任模式。用户给定目标：\n" + goal +
            "\n\n围绕此目标自行理解需求、选择方案、实施和验收，不再把工程技术取舍交回用户拍板。"
            "判断前读取相关代码和约定，自己提出建议并写清取舍理由；登记决定时必须给出 recommendation，"
            "Crew 会依据该建议记录自主答复。没有建议时先由 AI 自己判断，不能默认选择第一项。"
            "真正需要用户亲手做的事登记为人工待办，写清操作步骤、确认方法与是否阻塞；继续推进其余工作。"
            "及时给所属 leader 留报告。按项目规则保留可追踪的 commits，记录实际提交 SHA、分支和 worktree，"
            "方便用户事后要求修改或 fix。最终提供详细报告：目标对应的实现、方案与理由、实际验证范围/命令/结果/产物、"
            "未验证部分、剩余问题和人工待办、提交及后续修改入口。区分会话自报、命令成功、独立验收和未验证；"
            "不能把 exit 0 或绿色测试直接当成整个目标已经完成。")


@_atomic
def set(conn, project, enabled, goal=None, now=None):
    if type(enabled) is not bool:
        raise board.BoardError("enabled 必须是布尔值")
    before = get(conn, project)
    if not enabled and (goal is None or isinstance(goal, str) and not goal.strip()):
        goal = before["goal"]
    else:
        goal = _text(goal, "信任模式目标", required=enabled)
    if before["enabled"] == enabled and before["goal"] == goal:
        return before
    timestamp = store.now_iso() if now is None else now
    conn.execute("INSERT INTO trust_settings(project,enabled,goal,updated_at) VALUES(?,?,?,?) "
                 "ON CONFLICT(project) DO UPDATE SET enabled=excluded.enabled,goal=excluded.goal,updated_at=excluded.updated_at",
                 (project, int(enabled), goal, timestamp))
    if enabled:
        notice = ("项目的信任模式已启用或目标已更新，请重读当前角色守则并通知你的成员。\n" + _instructions(goal))
    else:
        notice = ("项目的信任模式已关闭。请重读当前角色守则并通知你的成员，后续决定恢复普通模式的拍板流程；"
                  "此前自主决定、报告和 commits 继续保留。用户亲手操作的事项仍登记为人工待办。")
    notices = [board._queue_leader_notice(conn, leader["session_id"], notice, "crew", now)
               for leader in _active_leaders(conn, project)]
    board._event(conn, "trust:set", project, board.HUMAN,
                 {"enabled": enabled, "goal": goal, "previous": before, "notice_ids": notices}, now)
    return get(conn, project)


def context(conn, session_id):
    session = board.session(conn, session_id)
    if session["project"] is None or session["status"] == "closed" or board.is_archived(conn, session_id):
        return None
    settings = get(conn, session["project"])
    return _instructions(settings["goal"]) if settings["enabled"] else None


def _decision(conn, decision_id):
    if type(decision_id) is not int or decision_id < 1:
        raise board.BoardError("待拍板 id 必须是正整数")
    row = conn.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
    if row is None:
        raise board.BoardError("待拍板不存在: " + str(decision_id))
    value = dict(row)
    value["options"] = json.loads(value["options"])
    return value


@_atomic
def auto_answer(conn, decision_id, reason=None, now=None):
    decision = _decision(conn, decision_id)
    if not get(conn, decision["project"])["enabled"]:
        return None
    existing = conn.execute("SELECT reason,recorded_at FROM trust_decisions WHERE decision_id=?", (decision_id,)).fetchone()
    if decision["status"] != "open":
        return {**decision, "automated": True, "reason": existing["reason"],
                "reason_recorded_at": existing["recorded_at"]} if existing is not None else None
    recommendation = decision["recommendation"]
    if not isinstance(recommendation, str) or not recommendation.strip():
        raise board.BoardError("信任模式下请由 AI 先自行判断并提供建议 recommendation；不能默认选择第一项")
    reason = (_text(reason, "自主决定理由") if reason is not None and (not isinstance(reason, str) or reason.strip()) else
              "未另附取舍理由；本次自主答复依据提出者建议，未补写推测理由。")
    timestamp = store.now_iso() if now is None else now
    # board.answer is atomic itself. Reuse its transaction body so the answer,
    # removal of a hold, audit event and reason are one indivisible operation.
    board.answer.__wrapped__(conn, decision_id, recommendation, actor="trust:" + decision["session_id"], now=now)
    conn.execute("INSERT INTO trust_decisions(decision_id,reason,recorded_at) VALUES(?,?,?)", (decision_id, reason, timestamp))
    board._event(conn, "trust:decision", decision["project"], "trust:" + decision["session_id"],
                 {"id": decision_id, "recommendation": recommendation, "reason": reason}, now)
    return {**_decision(conn, decision_id), "automated": True, "reason": reason, "reason_recorded_at": timestamp}


def _quote(value):
    return "\n".join("> " + line for line in str(value).split("\n"))


def _field(label, value):
    return "**" + label + "**\n\n" + _quote(value if value not in (None, "") else "未记录")


def report(conn, project):
    """Return a DB-only Markdown draft; do not launch tests, Git or a model."""
    project_record = board.project(conn, project)
    settings = get(conn, project)
    sections = ["# " + project + " · 信任模式工作报告草稿",
                "生成时间：" + store.now_iso(),
                "本稿汇总 Crew 已保存的记录，尚未独立检查代码、测试产物、CI、设备或当前 Git 状态。"
                "不据此自动宣告目标已完成。",
                "## 用户目标与当前模式",
                _field("目标", settings["goal"]),
                "模式：" + ("信任模式" if settings["enabled"] else "普通模式") +
                "；设置更新时间：" + str(settings["updated_at"] or "未记录"),
                _field("项目工作区", project_record["root"])]
    decisions = board.decisions(conn, project, all=True)
    audits = ({row["decision_id"]: dict(row) for row in conn.execute(
        "SELECT t.* FROM trust_decisions t JOIN decisions d ON d.id=t.decision_id WHERE d.project=?", (project,))}
        if _table(conn, "trust_decisions") else {})
    sections.append("## 方案、选择与取舍")
    if not decisions:
        sections.append("尚无已登记决定。")
    for item in decisions:
        audit = audits.get(item["id"])
        status = {"open": "待 AI 补充判断或继续处理" if settings["enabled"] else "待拍板",
                  "answered": "已答复", "withdrawn": "已撤回"}.get(item["status"], item["status"])
        sections.extend(["### 决定 #" + str(item["id"]) + " · " + status,
                         _field("问题", item["question"]),
                         _field("提出者 / 时间", item["session_id"] + " / " + item["created_at"]),
                         _field("可选方案", "\n".join(str(index) + ". " + str(option)
                                for index, option in enumerate(item["options"], 1)) or None),
                         _field("提出者建议", item["recommendation"]), _field("实际选择", item["answer"]),
                         _field("答复者 / 时间", str(item["answered_by"] or "未答复") + " / " + str(item["answered_at"] or "未记录")),
                         _field("取舍理由", audit["reason"] if audit else None)])
        if audit:
            sections.append("来源：信任模式自动采纳提出者建议；理由记录时间：" + audit["recorded_at"])
        elif str(item["answered_by"] or "").startswith("trust:"):
            sections.append("来源标为信任模式，但取舍理由记录缺失，需补查。")
    sections.append("## 人工待办")
    items = todo.list(conn, project, all=True) if _table(conn, "todos") else []
    for status, label in (("open", "待用户操作"), ("done", "用户已标记完成"),
                          ("cancelled", "用户已取消"), ("withdrawn", "会话已撤回")):
        matching = [item for item in items if item["status"] == status]
        sections.append("### " + label + "（" + str(len(matching)) + "）")
        for item in matching:
            sections.extend(["**待办 #" + str(item["id"]) + "：" + item["title"] + "**",
                             "提出者：" + item["proposer_name"] + "；" + ("阻塞 AI" if item["blocking"] else "不阻塞 AI"),
                             _field("操作步骤", item["steps"]), _field("预定确认方法（不是验证结果）", item["verification"]),
                             _field("处理备注", item["note"]),
                             _field("处理者 / 时间", str(item["resolved_by"] or "未处理") + " / " + str(item["resolved_at"] or "未记录"))])
    sections.extend(["## 进展与验证依据", "以下是会话自报的原始记录，尚未独立复核；人工待办的完成标记来自用户操作。"
                     "验证方法是计划，命令 exit 0 是一次执行结果，都不能单独证明整个目标完成。"
                     "缺少具体范围、命令、结果或产物的部分仍需补充验证依据。"])
    reports = board.reports(conn, project)
    if not reports:
        sections.append("尚无进展报告或可引用的验证记录。")
    for item in reports:
        sections.extend(["### 报告 #" + str(item["id"]) + " · " + (item["crew_name"] or item["session_id"]),
                         "时间：" + item["created_at"], _quote(item["body"])])
    sections.extend(["## 工作分支、交付记录与后续 fix 入口",
                     "以下分支与 worktree 来自登记记录，未现场运行 git 验证。分支名本身不证明已提交或合并；"
                     "应由交付会话在最终报告补齐实际 commit SHA、改动文件、验证范围与遗留项。"])
    for session in board.sessions(conn, project):
        card = board.card(conn, session["session_id"]) or {}
        sections.extend(["### " + (session["crew_name"] or session["session_id"]),
                         _field("Crew 会话 / 状态", session["session_id"] + " / " + session["status"]),
                         _field("负责范围", card.get("scope")), _field("完成标准", card.get("done_when")),
                         _field("当前任务", session["task"]), _field("worktree", session["worktree"]),
                         _field("分支", session["branch"])])
    if _table(conn, "git_merge_requests"):
        for request in conn.execute("SELECT * FROM git_merge_requests WHERE project=? ORDER BY id", (project,)):
            sections.extend(["### Git 交付请求 #" + str(request["id"]) + " · " + request["status"],
                             _field("分支 / PR", request["branch"] + " / " + ("#" + str(request["pr_number"]) if request["pr_number"] else "未记录")),
                             _field("提交者说明（自报）", request["description"]), _field("处理记录", request["note"])])
    sections.extend(["## 交付前仍需核对", "由负责的 leader 对照用户目标和各会话完成标准逐项给出证据，"
                     "明确已实现、已验证、未验证、尚未完成与待用户操作的内容。保留上述决定、理由和 commits，"
                     "后续 fix 引用决定编号、会话、worktree、分支及实际 commit SHA，重新验证受影响范围。"])
    return "\n\n".join(sections) + "\n"
