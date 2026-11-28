"""Versioned Markdown war plans. Approval publishes the board and its goals in one transaction."""
import json
import re
import uuid

from . import board, channel, goals, war_room, war_room_watchdog, war_room_worktree
from .ops.errors import OpError

SECTIONS = {'分工': 'assignment', '等你定': 'decision', '完成标准': 'criterion'}
ENTRY = re.compile(r'^- \[([A-Za-z0-9_-]{1,64})\]\s+(.+)$')


def parse(body):
    section, seen, ids, entries = None, {}, set(), []
    for line, raw in enumerate(body.splitlines(), 1):
        text = raw.strip()
        if text.startswith('## '):
            section = SECTIONS.get(text[3:].strip())
            if section:
                if section in seen:
                    fail(section, line, '小节重复')
                seen[section] = line
        elif section and text:
            match = ENTRY.fullmatch(text)
            if match is None:
                fail(section, line, '条目要写成 - [ID] 内容')
            key, value = match.groups()
            if key in ids:
                fail(section, line, '条目 ID 重复')
            ids.add(key)
            owner, recommendation, answer = None, None, None
            if section == 'assignment':
                owner, sep, value = value.partition('|')
                if not sep or not owner.strip() or not value.strip():
                    fail(section, line, '分工需要负责人和事项')
                owner = owner.strip()
                if len(value.strip()) > 300:
                    fail(section, line, '分工标题最多 300 字')
            elif section == 'decision':
                value, _, recommendation = value.partition('|')
                recommendation, _, answer = recommendation.partition('|')
                recommendation, answer = recommendation.strip(), answer.strip() or None
            if not value.strip():
                fail(section, line, '条目内容为空')
            entries.append(dict(entry_id=key, kind=section, title=value.strip(), owner=owner,
                                recommendation=recommendation, answer=answer, line=line))
    for name, kind in SECTIONS.items():
        if kind not in seen:
            fail(kind, len(body.splitlines()) + 1, '缺少小节：' + name)
    if not any(item['kind'] == 'criterion' for item in entries):
        fail('criterion', seen['criterion'], '完成标准不能为空')
    return entries


def fail(kind, line, message):
    raise OpError('invalid_criteria' if kind == 'criterion' else 'invalid_plan',
                  '第 %d 行：%s' % (line, message), {'line': line})


def owners(conn, room, entries):
    found = {}
    for item in entries:
        name = item['owner']
        if name is None or name in found:
            continue
        if name in (room['lead_session_id'], war_room._name(conn, room['lead_session_id'])):
            fail('assignment', item['line'], war_room.LEAD_ASSIGNMENT_ERROR)
        if name.startswith('拟任：') and name[3:].strip():
            found[name] = None
        else:
            try:
                found[name] = war_room._leaders(conn, room['project'], [name])[0]['session_id']
            except board.BoardError as exc:
                fail('assignment', item['line'], str(exc))
    return found


def _notify(conn, room, sid, text, now):
    return conn.execute("INSERT INTO leader_notices(room_id,project,session_id,source,text,created_at,wake) "
                        "VALUES(?,?,?,'crew',?,?,1)",
                        (room['id'], room['project'], sid, text, board._time(now))).lastrowid


def _kickoff(conn, room, version, now):
    room = war_room._room(conn, room['id'])
    assignments = conn.execute("SELECT owner_session_id,goal_id,status FROM war_room_items "
                               "WHERE room_id=? AND plan_version=? AND kind='assignment' ORDER BY id",
                               (room['id'], version))
    by_owner = {}
    for sid, goal_id, status in assignments:
        goals_for_owner = by_owner.setdefault(sid, [])
        if goal_id is not None and status in ('todo', 'doing') and goal_id not in goals_for_owner:
            goals_for_owner.append(goal_id)
    if not by_owner:
        return
    post = war_room._post(conn, room, room['lead_session_id'], '请按已批准的计划 v%d 开工。' % version,
                          to=[war_room._name(conn, sid) for sid in by_owner], now=now)
    notices = conn.execute('SELECT n.id,n.session_id,n.text FROM channel_mentions t '
                           'JOIN leader_notices n ON n.id=t.notice_id WHERE t.message_id=?', (post['id'],))
    for notice_id, sid, text in notices:
        goal_ids = by_owner[sid]
        if not goal_ids:
            continue
        context, upto = war_room.context(conn, room, sid)
        # The kickoff post supplies the first receipt. Each additional goal retains its own delivery.
        for index, goal_id in enumerate(goal_ids):
            if index:
                notice_id = _notify(conn, room, sid, text, now)
            conn.execute('INSERT INTO war_room_delivery(notice_id,room_id,goal_id,context,upto) VALUES(?,?,?,?,?)',
                         (notice_id, room['id'], goal_id, context+'\n'+text, upto))


def changed(conn, room, kind, detail, now, actor, *, owner=None, previous_owner=None):
    war_room._event(conn, room['id'], actor, kind, detail, now)
    if kind == 'approved':
        _kickoff(conn, room, detail['version'], now)
    elif kind == 'item':
        lead, status = room['lead_session_id'], detail['status']
        heading = '作战室 #%d「%s」' % (room['id'], detail['title'])
        if actor != lead and status in ('done', 'blocked') and detail['was'] != status:
            text = heading + '：' + war_room.ITEM_STATES[status]
            if detail['note']:
                text += '\n' + detail['note']
            _notify(conn, room, lead, text, now)
        if owner is not None and owner != previous_owner:
            _notify(conn, room, owner, heading + '交给你负责。', now)
    war_room._touch(conn, room['id'], room['project'])


@board.atomic
def update(conn, room_id, actor, expected_version, body, summary, budget_tokens, now=None, request_id=None):
    room = war_room.open_room(conn, room_id)
    if actor != room['lead_session_id'] or room['legacy']:
        raise OpError('forbidden', '只有新房领头能修改计划书')
    if request_id:
        saved = conn.execute("SELECT detail FROM war_room_events WHERE room_id=? AND actor=? AND kind='plan_updated' "
                           "AND json_extract(detail,'$.request_id')=?", (room_id, actor, request_id)).fetchone()
        if saved:
            return {'room': room_id, 'version': json.loads(saved['detail'])['version'], 'replayed': True}
    if expected_version != room['draft_version']:
        raise OpError('version_conflict', '计划书版本已经改变')
    entries = parse(body)
    resolved = owners(conn, room, entries)
    for item in entries:
        item['owner_session_id'] = resolved.get(item['owner'])
    advice = conn.execute('SELECT question_id,response FROM war_room_consults WHERE room_id=? AND plan_version=? '
                          'AND response IS NOT NULL ORDER BY created_at,question_id', (room_id, expected_version))
    for question_id, response in advice:
        summary += '\n\n顾问意见 [' + question_id + ']\n' + response
    version = expected_version + 1
    conn.execute('INSERT INTO war_room_plans(room_id,version,body,summary,author,created_at,budget_tokens,entries_json) '
                 'VALUES(?,?,?,?,?,?,?,?)', (room_id, version, body, summary, actor, board._time(now), war_room._budget(budget_tokens),
                                            json.dumps(entries, ensure_ascii=False)))
    conn.execute('UPDATE war_rooms SET draft_version=? WHERE id=?', (version, room_id))
    changed(conn, room, 'plan_updated', {'version': version, 'summary': summary, 'request_id': request_id}, now, actor)
    return {'room': room_id, 'version': version}


def create_planned_leaders(conn, room, by_name, stamp, step):
    room_id = room['id']
    opening = conn.execute("SELECT detail FROM war_room_events WHERE room_id=? AND kind='reserved'", (room_id,)).fetchone()
    campaign = json.loads(opening['detail']).get('campaign')
    proposals = {v['name']: v for v in campaign['leaders']} if campaign else {}
    from . import campaign as openings, domains
    opening_ops = []
    if campaign and room['approved_version'] is None:
        if domains.version(conn, room['project']) != campaign['board_version']:
            raise OpError('version_conflict', '开局后的架构图已改变，请重新核对分工')
        opening_ops = openings.part_ops(campaign['parts'])
    for name, sid in by_name.items():
        if sid is None:
            handle = name[3:].strip()
            sid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'crew:room:%s:%s:%s' % (room['project'], room_id, handle)))
            if not channel._leader(conn, room['project'], sid):
                workspace = board.project(conn, room['project'])
                board.available_name(conn, room['project'], handle)
                board.register_session.__wrapped__(conn, sid, room['project'], workspace['root'], None, None,
                                                   name=handle, role='leader', now=stamp)
                from . import models, runtimes, settings
                chosen = proposals.get(handle) or settings.resolve(conn, 'war_room')
                runtimes.set.__wrapped__(conn, sid, chosen['runtime'], now=stamp)
                models.set(conn, sid, chosen['model'], chosen['effort'], runtime=chosen['runtime'], speed=chosen.get('speed'), now=stamp)
                if handle in proposals:
                    conn.execute('UPDATE sessions SET task=? WHERE session_id=?', (chosen['brief'], sid))
                    if chosen['part']:
                        opening_ops.append({'op': 'assign', 'session_id': sid, 'domain_id': chosen['part']})
                        if chosen['lead']:
                            opening_ops.append({'op': 'lead', 'session_id': sid, 'domain_id': chosen['part'], 'lead': True})
            by_name[name] = sid
            step('leader', sid)
    if opening_ops:
        domains.apply_ops(conn, room['project'], opening_ops, None, stamp, source='restructure', note='作战室批准开局')
    return campaign, proposals


def publish_items(conn, room_id, version, entries, by_name, recommendations, campaign, proposals, stamp, step):
    from . import campaign as openings
    room = war_room._room(conn, room_id)
    actor = board.HUMAN
    criteria = [{'id': e['entry_id'], 'title': e['title']} for e in entries if e['kind'] == 'criterion']
    reassigned = set()
    for entry in entries:
        key, kind, title = entry['entry_id'], entry['kind'], entry['title']
        owner = by_name.get(entry['owner'])
        answer = entry['answer'] or recommendations.get(key)
        old = conn.execute('SELECT * FROM war_room_items WHERE room_id=? AND entry_id=?', (room_id, key)).fetchone()
        if old and old['kind'] != kind:
            fail(kind, entry['line'], '条目 ID 已用于其他小节')
        if old:
            if old['goal_id'] and old['owner_session_id'] != owner:
                if not channel._leader(conn, room['project'], owner):
                    fail(kind, entry['line'], '已绑定任务的分工需要交给本区 leader')
                reassigned.add(old['owner_session_id'])
                goals.request_transfer(conn, old['goal_id'], owner, '批准计划 v%d' % version, now=stamp)
            conn.execute('UPDATE war_room_items SET title=?,owner_session_id=?,plan_version=?,updated_by=?,updated_at=?,answer=? '
                         'WHERE id=?', (title, owner, version, actor, stamp, answer, old['id']))
            item_id = old['id']
        else:
            item_id = conn.execute('INSERT INTO war_room_items(room_id,entry_id,kind,title,owner_session_id,status,answer,'
                                   'plan_version,updated_by,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                                   (room_id, key, kind, title, owner, 'done' if kind == 'decision' else 'todo',
                                    answer, version, actor, stamp)).lastrowid
        if kind == 'assignment' and (not old or not old['goal_id']) and channel._leader(conn, room['project'], owner):
            handle = entry['owner'].removeprefix('拟任：').strip()
            proposal = proposals.get(handle)
            task = next((g for g in campaign['goals'] if g['leader'] == handle and g['title'] == title), None) if campaign else None
            constraints = ['作战室 #%d，计划 v%d，事项 %s' % (room_id, version, key)]
            if proposal and proposal['owns']:
                constraints.append(openings.boundary(proposal, campaign['leaders']))
            goal = goals.create(conn, room['project'], owner, title, task['request'] if task else title,
                                task['acceptance'] if task else criteria, constraints=constraints, now=stamp)
            conn.execute('UPDATE war_room_items SET goal_id=? WHERE id=?', (goal['id'], item_id))
            war_room_worktree.request_rebuild(conn, goal['id'], stamp)
            step('goal', key)
        if old and old['goal_id'] and old['owner_session_id'] != owner:
            war_room_worktree.request_rebuild(conn, old['goal_id'], stamp)
    for sid in reassigned:
        if not conn.execute('SELECT 1 FROM war_room_items WHERE room_id=? AND plan_version=? '
                            'AND owner_session_id=? AND goal_id IS NOT NULL', (room_id, version, sid)).fetchone():
            war_room_worktree.release(conn, room_id, sid, stamp)
    for previous in conn.execute("SELECT DISTINCT g.id FROM war_room_items i JOIN goals g ON g.id=i.goal_id "
                                 "WHERE i.room_id=? AND i.plan_version<>? AND g.status IN ('queued','running')",
                                 (room_id, version)).fetchall():
        goals.request_pause(conn, previous['id'], manual=False, reason='已批准版本移除了这项分工', now=stamp)


def approve(conn, room_id, actor, version, recommendations, budget_tokens, now=None):
    room = war_room.open_room(conn, room_id)
    root, top = war_room_worktree.repository(conn, room['project'])
    return _approve(conn, room_id, actor, version, recommendations, budget_tokens, now, root, top)


@board.atomic
def _approve(conn, room_id, actor, version, recommendations, budget_tokens, now, root, top):
    if actor != board.HUMAN:
        raise OpError('forbidden', '只有用户能批准计划书')
    room = war_room.open_room(conn, room_id)
    plan = conn.execute('SELECT * FROM war_room_plans WHERE room_id=? AND version=?', (room_id, version)).fetchone()
    if not plan or room['draft_version'] != version:
        raise OpError('version_conflict', '确认期间计划书已经改变，请重新查看')
    if budget_tokens != plan['budget_tokens']:
        raise OpError('version_conflict', '预算与所见版本不一致')
    entries = parse(plan['body'])
    expected = {e['entry_id']: e['recommendation'] for e in entries if e['kind'] == 'decision' and not e['answer']}
    if any(not value or not recommendations.get(key) for key, value in expected.items()):
        raise OpError('missing_recommendation', '每个待定项都需要推荐值')
    if expected != recommendations:
        raise OpError('version_conflict', '推荐值与所见版本不一致')
    if plan['approval'] is not None:
        return {**war_room.get(conn, room_id, actor), 'replayed': True}
    by_name = owners(conn, room, entries)
    for displayed in json.loads(plan['entries_json']):
        if displayed['owner_session_id'] != by_name.get(displayed['owner']):
            raise OpError('version_conflict', '人员身份在确认期间变了，请领头重新核对分工')
    stamp = board._time(now)
    def step(kind, target):
        war_room._event(conn, room_id, actor, 'launch_step',
                        {'version': version, 'step': kind, 'target': target,
                         'request_id': 'room:%d:v%d:%s:%s' % (room_id, version, kind, target)}, stamp)

    if board.project(conn, room['project'])['root'] != root:
        raise board.Conflict('工作区目录已改变，请重试')
    existing = conn.execute('SELECT 1 FROM war_room_worktrees WHERE room_id=? LIMIT 1', (room_id,)).fetchone()
    if existing:
        previous = war_room_worktree.repository_record(conn, room_id)
        if (root, top) != tuple(previous):
            raise board.Conflict('作战室登记的仓库已改变')
    war_room_worktree.remember_repository(conn, room_id, root, top, stamp)
    campaign, proposals = create_planned_leaders(conn, room, by_name, stamp, step)
    conn.execute("UPDATE war_rooms SET phase='active',approved_version=?,budget_tokens=? WHERE id=?",
                 (version, budget_tokens, room_id))
    for sid in dict.fromkeys(by_name.values()):
        if sid != room['lead_session_id'] and not war_room.is_member(conn, room_id, sid):
            conn.execute('INSERT INTO war_room_members(room_id,session_id,joined_at) VALUES(?,?,?) '
                         'ON CONFLICT(room_id,session_id) DO UPDATE SET left_at=NULL,joined_at=excluded.joined_at',
                         (room_id, sid, stamp))
            step('invite', sid)
    publish_items(conn, room_id, version, entries, by_name, recommendations, campaign, proposals, stamp, step)
    if top is not None and (room['approved_version'] is None or existing):
        war_room_worktree.register(conn, room, root, version, stamp)
    if room['opened_message_id'] is None:
        post = channel.post.__wrapped__(conn, room['project'], actor, '作战室 #%d《%s》已批准开打。' % (room_id, room['title']),
                                        source='web', now=stamp, generated=True)
        conn.execute('UPDATE war_rooms SET opened_message_id=? WHERE id=?', (post['id'], room_id))
        step('announce', room_id)
    conn.execute('UPDATE war_room_plans SET approval=? WHERE room_id=? AND version=?',
                 (json.dumps({'recommendations': recommendations, 'budget_tokens': budget_tokens, 'at': stamp}), room_id, version))
    changed(conn, room, 'approved', {'version': version, 'request_id': 'room:%d:v%d:approve' % (room_id, version)}, stamp, actor)
    hint = (war_room_worktree.NOT_GIT if top is None else
            war_room_worktree.NO_ADMIN if board.git_config(conn, room['project']) is None else None)
    if hint:
        _notify(conn, room, room['lead_session_id'], '作战室 #%d 已批准 v%d。%s' % (room_id, version, hint), stamp)
    war_room_watchdog.note_turn(room_id)
    return war_room.get(conn, room_id, actor)


def consult(conn, room_id, actor, version, question_id, purpose, question, now=None, catalog=None):
    import subprocess
    import tempfile
    from . import channel_digest, settings, store
    with board.transaction(conn):
        room = war_room.open_room(conn, room_id)
        if actor != room['lead_session_id'] or room['legacy']:
            raise OpError('forbidden', '只有新房领头可以请顾问')
        if room['draft_version'] == 0:
            raise OpError('conflict', '先保存一版计划书')
        existing = conn.execute('SELECT * FROM war_room_consults WHERE room_id=? AND question_id=?',
                                (room_id, question_id)).fetchone()
        if existing:
            if (existing['plan_version'], existing['purpose'], existing['question']) != (version, purpose, question):
                raise OpError('request_conflict', '同一个问题 ID 已用于其他内容')
            return consult_view(existing)
        if room['paused_at'] or (room['budget_tokens'] and war_room.spent(conn, room) >= room['budget_tokens']):
            raise OpError('conflict', '预算已用尽')
        if room['draft_version'] != version:
            raise OpError('version_conflict', '顾问要审的计划书已变更')
        plan = conn.execute('SELECT body FROM war_room_plans WHERE room_id=? AND version=?', (room_id, version)).fetchone()
        choice = settings.resolve(conn, purpose)
        if catalog is not None:
            from . import runtime_switch
            runtime_switch.validate_target(choice['runtime'], choice['model'], choice['effort'], catalog, choice['speed'])
        model, effort = choice['model'], choice['effort']
        if effort is None:
            effort = store.CARRIER_DEFAULT_EFFORT
        conn.execute('INSERT INTO war_room_consults(room_id,question_id,plan_version,purpose,question,runtime,'
                     'model,effort,requested_model,requested_effort,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                     (room_id, question_id, version, purpose, question, choice['runtime'], model, effort,
                      model, effort, board._time(now)))
    prompt = question + '\n\n' + plan['body']
    receipt = {}
    from . import codex_client
    try:
        if choice['runtime'] == 'claude':
            with tempfile.TemporaryDirectory(prefix='crew-consult-') as directory:
                answer, _, native_model = channel_digest._claude(directory, prompt, subprocess.Popen, choice, receipt=receipt)
        else:
            answer, _, native_model = channel_digest._codex(prompt, subprocess.Popen, choice, receipt=receipt)
    except (TimeoutError, OSError, RuntimeError, ValueError, codex_client.CodexError) as exc:
        with board.transaction(conn):
            conn.execute('UPDATE war_room_consults SET error=? WHERE room_id=? AND question_id=?',
                         (str(exc), room_id, question_id))
            war_room._touch(conn, room_id, room['project'])
        raise OpError('outcome_unknown', '顾问未返回完整回执，用量待确认',
                      {'room': room_id, 'question_id': question_id, 'reason': str(exc)}) from exc
    with board.transaction(conn):
        conn.execute('UPDATE war_room_consults SET native_model=?,response=?,tokens=? WHERE room_id=? AND question_id=?',
                     (native_model, answer, receipt.get('tokens'), room_id, question_id))
        war_room._touch(conn, room_id, room['project'])
    return consult_view(conn.execute('SELECT * FROM war_room_consults WHERE room_id=? AND question_id=?',
                                     (room_id, question_id)).fetchone())


def consult_view(row):
    return {**dict(row), 'usage_status': 'pending' if row['tokens'] is None else 'confirmed'}
