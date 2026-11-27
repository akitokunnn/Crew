"""An assistant-led opening: one confirmed plan builds the board, the leaders, their first tasks and the war room.

``preview`` checks a plan and states what it will create and what it may cost; ``launch`` runs it after the user
confirmed that summary once (docs/ASSISTANT_CAMPAIGN_DESIGN.md §1, ONBOARDING_AND_TEAMWORK_DESIGN.md §4.2–4.4).
Structure is written in one board batch, so the whole opening is one architecture version and rolls back as one.
"""
from pathlib import Path, PurePosixPath, PureWindowsPath

from . import board, domains, models, runtimes, usage, war_room

NAME_LIMIT, BRIEF_LIMIT, NOTE_LIMIT, OWNS_LIMIT = 20, 4000, 200, 200


def _text(value, what, limit):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError('%s必须是 1 到 %d 字的文本' % (what, limit))
    return value.strip()


def _list(plan, key):
    value = plan.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError('%s 必须是对象数组' % key)
    return value


def _owns(value):
    """The files or folders one leader alone changes, relative to the workspace root, in a stable written form."""
    if not isinstance(value, list) or not value:
        raise ValueError('owns 要是非空的路径列表')
    paths = []
    for raw in value:
        if not isinstance(raw, str) or not raw.strip() or len(raw.strip()) > OWNS_LIMIT:
            raise ValueError('owns 的每一项要是 1 到 %d 字的路径' % OWNS_LIMIT)
        text = raw.strip().replace('\\', '/')
        if PureWindowsPath(text).is_absolute() or PureWindowsPath(text).drive or text.startswith('/'):
            raise ValueError('owns 写相对工作区根目录的路径：%s' % raw)
        parts = [part for part in PurePosixPath(text).parts if part != '.']
        if not parts or '..' in parts:
            raise ValueError('owns 要指到工作区里的具体文件或目录，不能是整个工作区或上级目录：%s' % raw)
        path = '/'.join(parts) + ('/' if text.endswith('/') else '')
        if path in paths:
            raise ValueError('owns 里 %s 写了两次' % path)
        paths.append(path)
    return paths


def _overlaps(a, b):
    a, b = a.rstrip('/'), b.rstrip('/')
    return a == b or a.startswith(b + '/') or b.startswith(a + '/')


def boundary(leader, leaders):
    """The file split a leader starts with: its own paths, the others' paths, and how to cross the line."""
    others = '；'.join('「%s」负责 %s' % (other['name'], '、'.join(other['owns']))
                      for other in leaders if other is not leader and other['owns'])
    return ('文件分工（开局方案定的，leader 共用工作区目录时各管各的文件）：你负责 %s%s。只改自己负责的；要改别人负责的文件，'
            '先 @对方商量，由对方改或对方同意后再改；谁都没列的文件，改之前先说一声。' % (
                '、'.join(leader['owns']), '；' + others if others else ''))


def room_rule(room, session_id):
    """How a first task reaches its campaign room when it runs into another member; the lead sends the card itself."""
    if session_id == room['lead_session_id']:
        return ('这个任务属于作战室 #%d《%s》，你是牵头：和成员冲突、要用户定的事，在房里（room=%d）点名对方核对清楚后，'
                '马上用 `war_room_decide` 发拍板卡；不在本任务里先问用户、不自己 `decision_ask`。' % (
                    room['id'], room['title'], room['id']))
    return ('这个任务属于作战室 #%d《%s》（牵头：%s）：和别的成员冲突、要用户定的事，当场用 `war_room_post`（room=%d）'
            '点名牵头和对方，写清分歧、方案和推荐，由牵头发拍板卡；不在本任务里先问用户、不自己 `decision_ask`。' % (
                room['id'], room['title'], room['lead_name'], room['id']))


def preview(conn, project, plan, home):
    """What the plan creates, what it may cost, and every problem in it; nothing is written."""
    board.project(conn, project)
    errors, parts, leaders, tasks, unsplit = [], [], [], [], []

    def check(label, fn):
        try:
            return fn()
        except (ValueError, board.BoardError) as exc:
            errors.append('%s：%s' % (label, exc))
            return None

    known = {row['id']: row['name'] for row in conn.execute('SELECT id,name FROM domains WHERE project=?', (project,))}
    for index, item in enumerate(_list(plan, 'parts'), 1):
        label = '第 %d 个领域' % index
        ref = item.get('ref')
        if not isinstance(ref, str) or not domains.ID.match(ref) or ref in known:
            errors.append('%s：ref 要是 6–64 位字母数字或 _-，且不能和已有领域重复' % label)
            continue
        name = check(label, lambda: _text(item.get('name'), '领域名', 40))
        parent = item.get('parent')
        if parent is not None and parent not in known:
            errors.append('%s：上级领域 %s 不存在（只能是已有领域或前面新建的 ref）' % (label, parent))
        known[ref] = name
        parts.append({'ref': ref, 'name': name, 'parent': parent})
    names = set()
    for index, item in enumerate(_list(plan, 'leaders'), 1):
        label = '第 %d 位 leader' % index
        name = check(label, lambda: _text(item.get('name'), 'leader 名', NAME_LIMIT))
        if name is None:
            continue
        if name in names:
            errors.append('%s：名字 %s 重复' % (label, name))
        names.add(name)
        check(label, lambda: board.available_name(conn, project, name))
        runtime = check(label, lambda: runtimes.validate(item.get('runtime', 'claude')))
        model = check(label, lambda: models.validate(item.get('model'), runtime=runtime)) if runtime else None
        effort = check(label, lambda: models.validate_effort(item.get('effort'), runtime=runtime)) if runtime else None
        part = item.get('part')
        if part is not None and part not in known:
            errors.append('%s：领域 %s 不存在' % (label, part))
        brief = check(label, lambda: _text(item.get('brief'), '职责说明', BRIEF_LIMIT))
        if item.get('owns') is None:
            unsplit.append(name)
            owns = None
        else:
            owns = check(label, lambda: _owns(item.get('owns')))
        leaders.append({'name': name, 'runtime': runtime, 'model': model, 'effort': effort, 'part': part,
                        'part_name': known.get(part), 'lead': item.get('lead') is True, 'brief': brief, 'owns': owns})
    if len(leaders) > 1:
        # New leaders all work in the workspace root: without a written split one edits another's files.
        for name in unsplit:
            errors.append('「%s」：几位 leader 共用工作区目录，要用 owns 写明它负责的文件或目录' % name)
        for index, leader in enumerate(leaders):
            for other in leaders[index + 1:]:
                for mine in leader['owns'] or ():
                    for theirs in other['owns'] or ():
                        if _overlaps(mine, theirs):
                            errors.append('「%s」的 %s 和「%s」的 %s 重叠：一个文件只归一位 leader' % (
                                leader['name'], mine, other['name'], theirs))
    for index, item in enumerate(_list(plan, 'goals'), 1):
        label = '第 %d 个首批任务' % index
        if item.get('leader') not in names:
            errors.append('%s：负责人要是本方案里新建的 leader' % label)
        title = check(label, lambda: _text(item.get('title'), '任务标题', 120))
        request = check(label, lambda: _text(item.get('request'), '任务要求', BRIEF_LIMIT))
        acceptance = item.get('acceptance')
        if not isinstance(acceptance, list) or not acceptance or any(not isinstance(a, str) or not a.strip() for a in acceptance):
            errors.append('%s：acceptance 要是非空的验收标准列表' % label)
        tasks.append({'leader': item.get('leader'), 'title': title, 'request': request, 'acceptance': acceptance})
    room = plan.get('room')
    if room is not None:
        if not isinstance(room, dict):
            errors.append('room 必须是对象')
            room = None
        else:
            check('作战室', lambda: _text(room.get('title'), '作战室标题', war_room.TITLE_LIMIT))
            check('作战室', lambda: _text(room.get('goal'), '作战室目标', war_room.GOAL_LIMIT))
            check('作战室', lambda: war_room._budget(room.get('budget_tokens')))
            if len(names) < 2:
                errors.append('作战室：至少要两位新 leader 一起推进，否则不必开作战室')
    if not leaders:
        errors.append('方案里没有要新建的 leader')
    if not isinstance(plan.get('start'), bool):
        errors.append('start 要是 true（建完立刻开工）或 false（先只建结构）')
    check('方案说明', lambda: _text(plan.get('note'), '方案说明', NOTE_LIMIT))
    return {'project': project, 'ok': not errors, 'errors': errors, 'board_version': domains.version(conn, project),
            'parts': parts, 'leaders': leaders, 'goals': tasks, 'room': room, 'start': plan.get('start'),
            'cost': _cost(leaders, room, plan.get('start'), home)}


def _cost(leaders, room, start, home):
    """What the user agrees to spend: who starts on which carrier and model, and the room's cap with a rough price."""
    carriers = {}
    for leader in leaders:
        key = (leader['runtime'], leader['model'] or '默认模型', leader['effort'] or '默认强度')
        carriers[key] = carriers.get(key, 0) + 1
    budget = room.get('budget_tokens') if room else None
    estimate = None
    if budget:
        prices = usage.load_prices(Path(home) / 'prices.json')
        rates = [prices[leader['model']] for leader in leaders if leader['model'] in prices]
        if rates:
            # Uncached input is the cheapest token the cap counts, output the dearest: the spend lies in between.
            estimate = {'low_usd': round(budget * min(r['input'] for r in rates) / 1e6, 2),
                        'high_usd': round(budget * max(r['output'] for r in rates) / 1e6, 2),
                        'priced_models': sorted({leader['model'] for leader in leaders if leader['model'] in prices})}
    return {'start': start is True, 'new_leaders': len(leaders),
            'carriers': [{'runtime': runtime, 'model': model, 'effort': effort, 'count': count}
                         for (runtime, model, effort), count in sorted(carriers.items())],
            'budget_tokens': budget, 'estimate': estimate,
            'note': ('确认后立即启动 %d 位 leader 并开始首批任务；作战室预算到了会暂停等你确认。' % len(leaders)
                     if start is True else '确认后只建架构图，不启动模型，leader 与首批任务留到之后再开。')}


def launch(service, conn, project, plan, board_version, actor, now):
    """Run a previewed plan. Leaders and tasks start model work, so they come after the structure is checked; the
    board is written once (parts, places, leads) as one architecture version."""
    checked = preview(conn, project, plan, service.home)
    if not checked['ok']:
        raise board.BoardError('方案还有 %d 处问题：%s' % (len(checked['errors']), checked['errors'][0]))
    if board_version != checked['board_version']:
        raise board.Conflict('预览之后架构图又被改动过，请重新预览')
    note = '助手开局：' + plan['note'].strip()
    depth = {}
    for part in checked['parts']:
        depth[part['ref']] = depth.get(part['parent'], -1) + 1 if part['parent'] in depth else 0
    # New parts start on a plain grid (one row per level); the user lays them out on the board afterwards.
    ops = [{'op': 'create', 'id': part['ref'], 'name': part['name'], 'parent_id': part['parent'],
            'x': 260 * index, 'y': 160 * depth[part['ref']]} for index, part in enumerate(checked['parts'])]
    if not checked['start']:
        if ops:
            with board.transaction(conn):
                version = domains.apply_ops(conn, project, ops, None, now, source='restructure', note=note)['version']
        else:
            version = None
        return {'version': version, 'started': False, 'to_start': checked['leaders'], 'goals': checked['goals']}
    created = []
    for leader in checked['leaders']:
        split = None if leader['owns'] is None else boundary(leader, checked['leaders'])
        session = service.chat.start(conn, project, leader['name'],
                                     leader['brief'] if split is None else leader['brief'] + '\n\n' + split,
                                     role='leader', now=now,
                                     runtime=leader['runtime'], model=leader['model'], effort=leader['effort'])
        created.append({**leader, 'session_id': session['session_id'], 'boundary': split})
    for leader in created:
        if leader['part'] is not None:
            ops.append({'op': 'assign', 'session_id': leader['session_id'], 'domain_id': leader['part']})
            if leader['lead']:
                ops.append({'op': 'lead', 'session_id': leader['session_id'], 'lead': True})
    with board.transaction(conn):
        version = domains.apply_ops(conn, project, ops, None, now, source='restructure', note=note)['version'] if ops else None
    room = None
    if checked['room'] is not None:
        spec = checked['room']
        room = war_room.start(conn, project, actor, spec['title'], spec['goal'], [leader['name'] for leader in created],
                              lead=None if actor != board.HUMAN else created[0]['name'],
                              budget_tokens=spec.get('budget_tokens'), now=now)
    by_name = {leader['name']: leader for leader in created}
    control = service.chat.goal_controller
    tasks = []
    for task in checked['goals']:
        owner = by_name[task['leader']]
        # The first task's execution is a separate conversation from the opening brief, and room posts reach only the
        # leader's own conversation: the split and the room it works in ride on the task itself.
        constraints = [] if owner['boundary'] is None else [owner['boundary']]
        if room is not None:
            constraints.append(room_rule(room, owner['session_id']))
        result = control.agent_action('create', {'project': project, 'leader_session_id': owner['session_id'],
                                                 'title': task['title'], 'request': task['request'],
                                                 'acceptance': task['acceptance'], 'constraints': constraints,
                                                 'start': True}, board.HUMAN)
        goal = result['goal'] if 'goal' in result else result
        tasks.append({'id': goal['id'], 'title': goal['title'], 'leader': task['leader']})
    return {'version': version, 'started': True,
            'leaders': [{'name': leader['name'], 'session_id': leader['session_id']} for leader in created],
            'goals': tasks, 'room': None if room is None else {'id': room['id'], 'title': room['title']}}
