"""Restructure plans: a list of structural changes previewed read-only, then applied in one transaction.

Changes run in order on an in-memory copy of the board with the same rules as ``domains`` (a part created earlier
can be used later). Leaders can be merged into another leader, retired (with or without a successor) or split;
tasks can be cancelled or handed to another leader. Merged and retired leaders hand their context to whoever takes
over through the editable draft ``runtime_switch.handoff`` builds; the draft is delivered as a leader notice. Nothing
here starts a model: a split's new leader is only listed in ``to_start`` for the caller to create after its own cost
confirmation. See docs/ASSISTANT_CAMPAIGN_DESIGN.md §4.
"""
import hashlib
import json

from . import board, domain_versions, domains, goals, models, runtime_switch, runtimes, session_ops

CHANGES_MAX = 200
FIELDS = {
    'part.create': ('ref', 'name', 'parent'), 'part.rename': ('part', 'name'), 'part.move': ('part', 'parent'),
    'part.merge': ('part', 'into'), 'part.delete': ('part',),
    'leader.assign': ('leader', 'part'), 'leader.lead': ('leader', 'lead'),
    'leader.merge': ('leader', 'into'), 'leader.retire': ('leader', 'successor'),
    'leader.split': ('leader', 'ref', 'name', 'runtime', 'model', 'effort', 'part'),
    'goal.cancel': ('goal',), 'goal.transfer': ('goal', 'to'),
}
SLOT = {'TB': (280, 130), 'LR': (340, 120)}


def _quote(name):
    return '「%s」' % name


class Plan:
    """One simulation of a list of changes against the board as it is in ``conn``."""

    def __init__(self, conn, project, manager):
        self.conn, self.project, self.manager = conn, project, manager
        board.project(conn, project)
        self.before = domain_versions.state(conn, project)
        self.parts = {part['id']: {'id': part['id'], 'parent_id': part['parent_id'], 'name': part['name']}
                      for part in self.before['parts']}
        self.order = [part['id'] for part in self.before['parts']]
        self.place = {row['session_id']: {'domain_id': row['domain_id'], 'lead': bool(row['lead'])}
                      for row in conn.execute('SELECT * FROM domain_leaders WHERE project=?', (project,))}
        self.ops, self.items, self.errors = [], [], []
        self.retired, self.splits, self.goal_actions = {}, [], {}
        self.refs, self.split_refs, self._sessions = set(), set(), {}

    # ---- lookups --------------------------------------------------------------------------------------------------
    def session(self, session_id):
        if session_id not in self._sessions:
            row = self.conn.execute('SELECT * FROM sessions WHERE session_id=? AND project=?', (session_id, self.project)).fetchone()
            self._sessions[session_id] = dict(row) if row is not None else None
        return self._sessions[session_id]

    def name(self, session_id):
        return self.session(session_id)['crew_name'] or session_id

    def leader(self, value, *, active=False):
        if not isinstance(value, str) or not value:
            raise board.BoardError('leader 无效')
        person = self.session(value)
        if person is None or person['project'] != self.project or person['role'] != 'leader':
            raise board.BoardError('不是本工作区的 leader：%s' % value)
        if active and (person['status'] == 'closed' or board.is_archived(self.conn, value)):
            raise board.BoardError('%s已归档或已移除' % _quote(self.name(value)))
        if value in self.retired:
            raise board.BoardError('%s在本方案前面已经合并或退役' % _quote(self.name(value)))
        return value

    def part(self, value, *, nullable=False):
        if value is None and nullable:
            return None
        if not isinstance(value, str) or value not in self.parts:
            raise board.BoardError('part 不存在：%s' % value)
        return value

    def group(self, session_id):
        row = self.conn.execute('SELECT name FROM groups WHERE project=? AND leader_session_id=? AND closed_at IS NULL',
                                (self.project, session_id)).fetchone()
        return row['name'] if row is not None else None

    def goal(self, value):
        row = self.conn.execute('SELECT * FROM goals WHERE id=? AND project=?', (value, self.project)).fetchone() \
            if isinstance(value, str) else None
        if row is None:
            raise board.BoardError('任务不存在：%s' % value)
        if row['status'] in goals.TERMINAL:
            raise board.BoardError('任务%s已经结束' % _quote(row['title']))
        if value in self.goal_actions:
            raise board.BoardError('任务%s在本方案里已经处理过' % _quote(row['title']))
        return dict(row)

    def path(self, part_id):
        chain = []
        while part_id is not None:
            chain.append(self.parts[part_id]['name'])
            part_id = self.parts[part_id]['parent_id']
        return ' › '.join(chain[::-1])

    def under(self, part_id):
        return '挂到' + _quote(self.path(part_id)) + '下' if part_id is not None else '放在顶层'

    # ---- primitive board ops, mirroring domains._Edit -----------------------------------------------------------
    def do(self, op):
        self.ops.append(op)
        kind = op['op']
        if kind == 'create':
            self.parts[op['id']] = {'id': op['id'], 'parent_id': op['parent_id'], 'name': op['name']}
            self.order.append(op['id'])
        elif kind == 'rename':
            self.parts[op['id']]['name'] = op['name']
        elif kind == 'parent':
            self.parts[op['id']]['parent_id'] = op['parent_id']
        elif kind == 'delete':
            parent = self.parts[op['id']]['parent_id']
            for part in self.parts.values():
                if part['parent_id'] == op['id']:
                    part['parent_id'] = parent
            for place in self.place.values():
                if place['domain_id'] == op['id']:
                    place.update(domain_id=parent, lead=False)
            del self.parts[op['id']]
            self.order.remove(op['id'])
        elif kind == 'assign':
            current = self.place.get(op['session_id'])
            if current is None or current['domain_id'] != op['domain_id']:
                self.place[op['session_id']] = {'domain_id': op['domain_id'], 'lead': False}
        elif kind == 'lead':
            domain = self.place[op['session_id']]['domain_id']
            if op['lead']:
                for place in self.place.values():
                    if place['domain_id'] == domain:
                        place['lead'] = False
            self.place[op['session_id']]['lead'] = op['lead']

    def lead_of(self, part_id):
        return next((sid for sid, place in self.place.items() if place['domain_id'] == part_id and place['lead']), None)

    def unplace(self, session_id):
        place = self.place.get(session_id)
        if place is not None and place['domain_id'] is not None:
            self.do({'op': 'assign', 'session_id': session_id, 'domain_id': None})

    # ---- changes ------------------------------------------------------------------------------------------------
    def run(self, changes):
        if not isinstance(changes, list) or not changes:
            raise board.BoardError('changes 必须是非空数组')
        if len(changes) > CHANGES_MAX:
            raise board.BoardError('一个方案最多 %d 条变更' % CHANGES_MAX)
        for index, change in enumerate(changes):
            item = {'index': index, 'type': change.get('type') if isinstance(change, dict) else None,
                    'label': '', 'error': None}
            self.items.append(item)
            try:
                if not isinstance(change, dict) or change.get('type') not in FIELDS:
                    raise board.BoardError('不认识的变更：%s' % (change.get('type') if isinstance(change, dict) else change))
                fields = FIELDS[change['type']]
                extra = sorted(set(change) - {'type', *fields})
                missing = [key for key in fields if key not in change]
                if extra or missing:
                    raise board.BoardError('字段不对：' + '；'.join(
                        (['多了 ' + '、'.join(extra)] if extra else []) + (['缺少 ' + '、'.join(missing)] if missing else [])))
                item['label'] = getattr(self, 'change_' + change['type'].replace('.', '_'))(change, index)
            except ValueError as exc:
                item['error'] = str(exc)
                self.errors.append({'index': index, 'message': str(exc)})
        self.settle()

    def change_part_create(self, change, index):
        ref = change['ref']
        if not isinstance(ref, str) or not domains.ID.match(ref):
            raise board.BoardError('ref 格式不正确（6–64 位字母、数字、_ 或 -）')
        if ref in self.parts or ref in self.refs or self.conn.execute('SELECT 1 FROM domains WHERE id=?', (ref,)).fetchone():
            raise board.BoardError('ref 已被占用：%s' % ref)
        name, parent = domains._name(change['name']), self.part(change['parent'], nullable=True)
        self.refs.add(ref)
        self.do({'op': 'create', 'id': ref, 'name': name, 'parent_id': parent})
        return '新建 part%s，%s' % (_quote(name), self.under(parent))

    def change_part_rename(self, change, index):
        part, name = self.part(change['part']), domains._name(change['name'])
        old = self.parts[part]['name']
        self.do({'op': 'rename', 'id': part, 'name': name})
        return '%s改名为%s' % (_quote(old), _quote(name))

    def change_part_move(self, change, index):
        part, parent = self.part(change['part']), self.part(change['parent'], nullable=True)
        ancestor = parent
        while ancestor is not None:
            if ancestor == part:
                raise board.BoardError('不能把 part 挂到它自己或它的下级下面')
            ancestor = self.parts[ancestor]['parent_id']
        self.do({'op': 'parent', 'id': part, 'parent_id': parent})
        return _quote(self.parts[part]['name']) + self.under(parent)

    def change_part_merge(self, change, index):
        part, into = self.part(change['part']), self.part(change['into'])
        ancestor = into
        while ancestor is not None:
            if ancestor == part:
                raise board.BoardError('不能把 part 并入它自己或它的下级')
            ancestor = self.parts[ancestor]['parent_id']
        name = self.parts[part]['name']
        for child in [pid for pid in self.order if self.parts[pid]['parent_id'] == part]:
            self.do({'op': 'parent', 'id': child, 'parent_id': into})
        for session_id in sorted(sid for sid, place in self.place.items() if place['domain_id'] == part):
            self.do({'op': 'assign', 'session_id': session_id, 'domain_id': into})
        self.do({'op': 'delete', 'id': part})
        return 'part%s并入%s' % (_quote(name), _quote(self.parts[into]['name']))

    def change_part_delete(self, change, index):
        part = self.part(change['part'])
        name = self.parts[part]['name']
        self.do({'op': 'delete', 'id': part})
        return '删除 part%s（下级与 leader 上移一层）' % _quote(name)

    def change_leader_assign(self, change, index):
        leader, part = self.leader(change['leader']), self.part(change['part'], nullable=True)
        self.do({'op': 'assign', 'session_id': leader, 'domain_id': part})
        return _quote(self.name(leader)) + ('放进' + _quote(self.path(part)) if part else '移出到未归属')

    def change_leader_lead(self, change, index):
        leader = self.leader(change['leader'])
        if type(change['lead']) is not bool:
            raise board.BoardError('lead 必须是 true 或 false')
        place = self.place.get(leader)
        if place is None or place['domain_id'] is None:
            raise board.BoardError('先把%s放进一个 part，再设为负责人' % _quote(self.name(leader)))
        self.do({'op': 'lead', 'session_id': leader, 'lead': change['lead']})
        part = _quote(self.parts[place['domain_id']]['name'])
        return _quote(self.name(leader)) + (('负责' + part) if change['lead'] else ('不再负责' + part))

    def _retire(self, change, index, successor, merge):
        leader = self.leader(change['leader'], active=True)
        if successor == leader:
            raise board.BoardError('接手的 leader 不能是自己')
        place = self.place.get(leader)
        led = place['domain_id'] if place is not None and place['lead'] else None
        self.retired[leader] = {'index': index, 'successor': successor, 'merge': merge}
        self.unplace(leader)
        if merge and led is not None and self.place.get(successor, {}).get('domain_id') == led \
                and self.lead_of(led) is None:
            self.do({'op': 'lead', 'session_id': successor, 'lead': True})
        return leader

    def change_leader_merge(self, change, index):
        into = self.leader(change['into'], active=True)
        leader = self._retire(change, index, into, True)
        return '合并 leader：%s并入%s' % (_quote(self.name(leader)), _quote(self.name(into)))

    def change_leader_retire(self, change, index):
        successor = None if change['successor'] is None else self.leader(change['successor'], active=True)
        leader = self._retire(change, index, successor, False)
        return '退役 leader%s%s' % (_quote(self.name(leader)), '，由%s接手' % _quote(self.name(successor)) if successor else '')

    def change_leader_split(self, change, index):
        leader = self.leader(change['leader'], active=True)
        ref, name = change['ref'], change['name']
        if not isinstance(ref, str) or not domains.ID.match(ref) or ref in self.split_refs:
            raise board.BoardError('新 leader 的 ref 格式不正确或重复')
        if not isinstance(name, str) or not name.strip() or len(name) > domains.NAME_MAX:
            raise board.BoardError('新 leader 名字必须是 1 到 %d 字' % domains.NAME_MAX)
        name = name.strip()
        board.available_name(self.conn, self.project, name)
        if any(item['name'] == name for item in self.splits):
            raise board.BoardError('新 leader 重名：%s' % name)
        runtime = runtimes.validate(change['runtime'])
        model = models.validate(change['model'], runtime=runtime)
        effort = models.validate_effort(change['effort'], runtime=runtime)
        part = self.part(change['part'])
        self.split_refs.add(ref)
        self.splits.append({'index': index, 'leader': leader, 'ref': ref, 'name': name, 'runtime': runtime,
                            'model': model, 'effort': effort, 'part': part})
        return '从%s拆出新 leader%s，负责%s（待启动）' % (_quote(self.name(leader)), _quote(name), _quote(self.path(part)))

    def change_goal_cancel(self, change, index):
        goal = self.goal(change['goal'])
        self.goal_actions[goal['id']] = {'index': index, 'action': 'cancel', 'to': None, 'implicit': False, 'goal': goal}
        return '作废任务%s' % _quote(goal['title'])

    def change_goal_transfer(self, change, index):
        goal = self.goal(change['goal'])
        to = self.leader(change['to'], active=True)
        if to == goal['leader_session_id']:
            raise board.BoardError('任务%s已经由%s负责' % (_quote(goal['title']), _quote(self.name(to))))
        goals._transfer_target(self.conn, goal, to)
        blocker = goals.transfer_blocker(self.conn, goal, to)
        if blocker is not None:
            raise board.BoardError('任务%s：%s' % (_quote(goal['title']), blocker))
        self.goal_actions[goal['id']] = {'index': index, 'action': 'transfer', 'to': to, 'implicit': False, 'goal': goal}
        return '任务%s改派给%s' % (_quote(goal['title']), _quote(self.name(to)))

    # ---- what the changes imply ---------------------------------------------------------------------------------
    def fail(self, index, message):
        self.errors.append({'index': index, 'message': message})
        if index is not None and self.items[index]['error'] is None:
            self.items[index]['error'] = message

    def open_goals(self, session_id):
        return [dict(row) for row in self.conn.execute(
            "SELECT * FROM goals WHERE project=? AND leader_session_id=? AND status NOT IN ('completed','failed','cancelled') "
            'ORDER BY created_at,id', (self.project, session_id))]

    def settle(self):
        for goal_id, action in self.goal_actions.items():
            if action['to'] in self.retired:
                self.fail(action['index'], '任务改派给的%s在本方案里会合并或退役' % _quote(self.name(action['to'])))
        for split in self.splits:
            if split['leader'] in self.retired:
                self.fail(split['index'], '%s拆分后又在本方案里合并或退役' % _quote(self.name(split['leader'])))
        for leader, info in self.retired.items():
            index, who = info['index'], _quote(self.name(leader))
            if info['successor'] in self.retired:
                self.fail(index, '接手%s的%s在本方案里也会合并或退役' % (who, _quote(self.name(info['successor']))))
            if self.manager.busy(leader) or self.session(leader)['status'] == 'working':
                self.fail(index, '%s正在工作，请等这一轮结束' % who)
            if self.group(leader) is None:
                self.fail(index, '%s没有在岗的协作组' % who)
            unhandled = []
            for goal in self.open_goals(leader):
                if goal['status'] in ('running', 'pausing'):
                    self.fail(index, '%s的任务%s正在进行，请先暂停' % (who, _quote(goal['title'])))
                if goal['id'] in self.goal_actions:
                    continue
                if info['successor'] is None:
                    unhandled.append(goal['title'])
                    continue
                try:
                    goals._transfer_target(self.conn, goal, info['successor'])
                except board.BoardError as exc:
                    self.fail(index, str(exc))
                    continue
                blocker = goals.transfer_blocker(self.conn, goal, info['successor'])
                if blocker is not None:
                    self.fail(index, '%s的任务%s：%s' % (who, _quote(goal['title']), blocker))
                    continue
                self.goal_actions[goal['id']] = {'index': index, 'action': 'transfer', 'to': info['successor'],
                                                 'implicit': True, 'goal': goal}
            if unhandled:
                self.fail(index, '%s还有 %d 个未结束的任务（%s），没有接手人时请在方案里逐个作废或改派' % (
                    who, len(unhandled), '、'.join(map(_quote, unhandled))))

    def after(self):
        names = {}
        leaders = []
        for session_id, place in sorted(self.place.items()):
            if place['domain_id'] is None:
                continue
            names[session_id] = self.name(session_id)
            leaders.append({'session_id': session_id, 'domain_id': place['domain_id'], 'lead': place['lead'],
                            'name': names[session_id]})
        return {'direction': self.before['direction'],
                'parts': [dict(self.parts[pid]) for pid in self.order], 'leaders': leaders}

    def archived(self):
        people = []
        for leader in self.retired:
            group = self.group(leader)
            people.append({'session_id': leader, 'name': self.name(leader), 'role': 'leader', 'group': group})
            if group is None:
                continue
            for row in self.conn.execute(
                    "SELECT s.session_id,s.crew_name FROM sessions s LEFT JOIN session_controls c ON c.session_id=s.session_id "
                    "WHERE s.project=? AND s.group_name=? AND s.session_id<>? AND s.status<>'closed' "
                    'AND c.archived_status IS NULL AND COALESCE(c.removed,0)=0 ORDER BY s.session_id',
                    (self.project, group, leader)):
                people.append({'session_id': row['session_id'], 'name': row['crew_name'] or row['session_id'],
                               'role': 'member', 'group': group})
        return people

    def handoff_slots(self):
        slots = [{'index': info['index'], 'from': leader, 'to': info['successor'], 'ref': None, 'merge': info['merge']}
                 for leader, info in self.retired.items() if info['successor'] is not None]
        slots += [{'index': split['index'], 'from': split['leader'], 'to': None, 'ref': split['ref'], 'merge': False}
                  for split in self.splits]
        return sorted(slots, key=lambda slot: slot['index'])

    def revision(self):
        """The state the preview relied on besides the board itself; applying needs it unchanged."""
        people = set(self.retired) | {info['successor'] for info in self.retired.values() if info['successor']}
        people |= {action['to'] for action in self.goal_actions.values() if action['to']}
        people |= {split['leader'] for split in self.splits}
        people |= {item['session_id'] for item in self.archived()}
        sessions = {sid: [self.session(sid)['status'], self.session(sid)['crew_name'], self.session(sid)['group_name'],
                          board.is_archived(self.conn, sid), self.group(sid)] for sid in sorted(people)}
        tasks = {}
        for goal_id in sorted(set(self.goal_actions) | {goal['id'] for leader in self.retired for goal in self.open_goals(leader)}):
            row = self.conn.execute('SELECT status,leader_session_id,version FROM goals WHERE id=?', (goal_id,)).fetchone()
            tasks[goal_id] = list(row)
        return hashlib.sha256(json.dumps([sessions, tasks], sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def summary(self, change):
        parts, leaders = change['parts'], change['leaders']
        actions = list(self.goal_actions.values())
        return {'parts_created': len(parts['added']), 'parts_removed': len(parts['removed']),
                'parts_renamed': len(parts['renamed']), 'parts_moved': len(parts['moved']),
                'leaders_moved': len(leaders['placed']) + len(leaders['moved']) + len(leaders['unplaced']),
                'merged': sum(1 for info in self.retired.values() if info['merge']),
                'retired': sum(1 for info in self.retired.values() if not info['merge']),
                'goals_cancelled': sum(1 for action in actions if action['action'] == 'cancel'),
                'goals_transferred': sum(1 for action in actions if action['action'] == 'transfer'),
                'to_start': len(self.splits)}


def _leader_effects(plan, change):
    effects = {}

    def add(session_id, name, text):
        effects.setdefault(session_id, {'session_id': session_id, 'name': name, 'effects': []})['effects'].append(text)
    for item in change['leaders']['placed']:
        add(item['session_id'], item['name'], '放进' + _quote(' › '.join(item['to'])))
    for item in change['leaders']['moved']:
        add(item['session_id'], item['name'], '从%s移到%s' % (_quote(' › '.join(item['from'])), _quote(' › '.join(item['to']))))
    for item in change['leaders']['unplaced']:
        if item['session_id'] not in plan.retired:
            add(item['session_id'], item['name'], '移出到未归属（原在%s）' % _quote(' › '.join(item['from'])))
    for item in change['leaders']['lead_on']:
        add(item['session_id'], item['name'], '成为%s负责人' % _quote(' › '.join(item['part'])))
    for item in change['leaders']['lead_off']:
        if item['session_id'] not in plan.retired:
            add(item['session_id'], item['name'], '不再负责%s' % _quote(' › '.join(item['part'])))
    for leader, info in plan.retired.items():
        successor = info['successor']
        add(leader, plan.name(leader), ('并入' + _quote(plan.name(successor))) if info['merge'] else
            '退役' + ('，由%s接手' % _quote(plan.name(successor)) if successor else ''))
        if successor is not None:
            add(successor, plan.name(successor), '接手%s的交接稿' % _quote(plan.name(leader)))
    for split in plan.splits:
        add(split['leader'], plan.name(split['leader']), '拆出新 leader%s' % _quote(split['name']))
    received = {}
    for action in plan.goal_actions.values():
        if action['to'] is not None:
            received[action['to']] = received.get(action['to'], 0) + 1
    for session_id, count in received.items():
        add(session_id, plan.name(session_id), '接手 %d 个任务' % count)
    return list(effects.values())


def preview(conn, project, changes, manager):
    """Read-only: before / after, the impact and the handoff drafts, or the reasons the plan cannot run."""
    plan = Plan(conn, project, manager)
    plan.run(changes)
    result = {'project': project, 'board_version': domains.version(conn, project), 'ok': not plan.errors,
              'errors': plan.errors, 'changes': plan.items, 'before': plan.before}
    if plan.errors:
        return dict(result, after=None, diff=None, revision=None)
    after = plan.after()
    change = domain_versions.diff(plan.before, after)
    handoffs = []
    for slot in plan.handoff_slots():
        draft = runtime_switch.handoff(conn, plan.session(slot['from']), manager.journal(slot['from']),
                                       intro='leader_handoff')
        split = next((item for item in plan.splits if item['ref'] == slot['ref']), None)
        handoffs.append({'index': slot['index'], 'from': {'session_id': slot['from'], 'name': plan.name(slot['from'])},
                         'to': ({'session_id': slot['to'], 'name': plan.name(slot['to'])} if slot['to'] else
                                {'ref': slot['ref'], 'name': split['name']}),
                         'merge': slot['merge'], 'delivers': slot['to'] is not None, **draft})
    goals_out = [{'goal_id': goal_id, 'title': action['goal']['title'], 'status': action['goal']['status'],
                  'leader': {'session_id': action['goal']['leader_session_id'],
                             'name': plan.name(action['goal']['leader_session_id'])},
                  'action': action['action'], 'implicit': action['implicit'], 'index': action['index'],
                  # goals.finalize_transfer tells the new leader to start a task that is not paused.
                  'wakes': action['action'] == 'transfer' and not action['goal']['manual_pause']
                  and action['goal']['status'] in ('queued', 'running'),
                  'to': {'session_id': action['to'], 'name': plan.name(action['to'])} if action['to'] else None}
                 for goal_id, action in plan.goal_actions.items()]
    return dict(result, after=after, diff=change, revision=plan.revision(), leaders=_leader_effects(plan, change),
                goals=goals_out, archived=plan.archived(), handoffs=handoffs,
                to_start=[{'index': split['index'], 'ref': split['ref'], 'name': split['name'],
                           'runtime': split['runtime'], 'model': split['model'], 'effort': split['effort'],
                           'part': {'id': split['part'], 'name': plan.path(split['part'])},
                           'split_from': {'session_id': split['leader'], 'name': plan.name(split['leader'])}}
                          for split in plan.splits],
                summary=plan.summary(change), nothing=change['count'] == 0 and not plan.goal_actions
                and not plan.retired and not plan.splits)


def _positions(conn, project, plan):
    """Board coordinates for created parts, next to their parent: after its existing sub-parts, or one level under it
    when it has none; new top-level parts go beside the drawing. 自动排列 tidies the rest."""
    # Laid out against the board as the plan leaves it: parts it removes free their place, moved ones count where
    # they end up.
    rows = {row['id']: dict(row, parent_id=plan.parts[row['id']]['parent_id'])
            for row in conn.execute('SELECT id,x,y FROM domains WHERE project=?', (project,)) if row['id'] in plan.parts}
    tb = plan.before['direction'] == 'TB'
    step, depth_step = SLOT['TB' if tb else 'LR']
    kids = {}
    for row in rows.values():
        kids.setdefault(row['parent_id'], []).append(row)
    if rows:
        xs, ys = [row['x'] for row in rows.values()], [row['y'] for row in rows.values()]
        roots = (max(xs) + 320, min(ys)) if tb else (min(xs), max(ys) + 160)
    else:
        roots = (0, 0)
    placed, used = {}, {}
    for op in (op for op in plan.ops if op['op'] == 'create'):
        parent = op['parent_id']
        slot = used[parent] = used.get(parent, -1) + 1
        if parent in placed:
            x, y = placed[parent]
            at = (x + slot * step, y + depth_step) if tb else (x + depth_step, y + slot * step)
        elif parent is None:
            at = (roots[0] + slot * step, roots[1]) if tb else (roots[0], roots[1] + slot * step)
        elif kids.get(parent):
            siblings = kids[parent]
            at = (max(row['x'] for row in siblings) + (slot + 1) * step, min(row['y'] for row in siblings)) if tb else                 (min(row['x'] for row in siblings), max(row['y'] for row in siblings) + (slot + 1) * step)
        else:
            x, y = rows[parent]['x'], rows[parent]['y']
            at = (x + slot * step, y + depth_step + 40) if tb else (x + depth_step, y + slot * step)
        at = _free(at, [(row['x'], row['y']) for row in rows.values()] + list(placed.values()), tb, step)
        placed[op['id']] = at
        op['x'], op['y'] = at


def _free(at, taken, tb, step):
    """Shift a new part along its row (TB) or column (LR) until it no longer covers another part."""
    x, y = at
    while any(abs(x - ox) < SLOT['TB'][0] and abs(y - oy) < SLOT['TB'][1] if tb else
              abs(x - ox) < SLOT['LR'][0] and abs(y - oy) < SLOT['LR'][1] for ox, oy in taken):
        x, y = (x + step, y) if tb else (x, y + step)
    return x, y


def _handoff_texts(plan, handoffs):
    """The edited drafts, one per handoff slot, each with whether delivering it wakes the receiver."""
    slots = {slot['index']: slot for slot in plan.handoff_slots()}
    if not isinstance(handoffs, list):
        raise board.BoardError('handoffs 必须是数组')
    texts = {}
    for entry in handoffs:
        if not isinstance(entry, dict) or set(entry) != {'index', 'text', 'wake'}:
            raise board.BoardError('每份交接稿须正好包含 index、text、wake')
        if entry['index'] not in slots or entry['index'] in texts:
            raise board.BoardError('交接稿对不上方案里的合并、退役或拆分：%s' % entry['index'])
        text = entry['text']
        if not isinstance(text, str) or not text.strip() or len(text) > runtime_switch.MAX_HANDOFF:
            raise board.BoardError('交接稿必须是 1 到 %d 字的文本' % runtime_switch.MAX_HANDOFF)
        if type(entry['wake']) is not bool:
            raise board.BoardError('wake 必须是 true 或 false')
        if slots[entry['index']]['ref'] is not None and entry['wake']:
            raise board.BoardError('拆分出的新 leader 还没有启动，交接稿不能唤醒')
        texts[entry['index']] = entry
    missing = sorted(set(slots) - set(texts))
    if missing:
        raise board.BoardError('还缺第 %s 条变更的交接稿' % '、'.join(str(index + 1) for index in missing))
    return texts


def _notice(plan, slot, text, transferred):
    leader, info = slot['from'], plan.retired[slot['from']]
    head = ('【leader 交接】用户把%s并入你的职责' % _quote(plan.name(leader)) if info['merge'] else
            '【leader 交接】用户让%s退役，由你接手' % _quote(plan.name(leader)))
    tail = '；它未结束的 %d 个任务已改派给你，各有单独通知' % transferred if transferred else ''
    return head + '；%s的会话已归档保留%s。以下交接稿由用户审阅确认：\n\n' % (_quote(plan.name(leader)), tail) + text


def apply(service, conn, project, request, now):
    """Run a previewed plan in one transaction; the caller holds the service write lock."""
    note = request['note']
    if not isinstance(note, str) or not note.strip() or len(note) > domain_versions.NOTE_MAX:
        raise board.BoardError('说明必须是 1 到 %d 字的文本' % domain_versions.NOTE_MAX)
    if type(request['board_version']) is not int or not isinstance(request['revision'], str):
        raise board.BoardError('请先预览，再带着预览给出的 board_version 与 revision 执行')
    manager = service.chat
    with manager.lock:
        with board.transaction(conn):
            if request['board_version'] != domains.version(conn, project):
                raise board.VersionConflict('预览之后架构图又被改动过，请重新预览')
            plan = Plan(conn, project, manager)
            plan.run(request['changes'])
            if plan.errors:
                raise board.BoardError('方案还有 %d 处问题：%s' % (len(plan.errors), plan.errors[0]['message']))
            if request['revision'] != plan.revision():
                raise board.VersionConflict('预览之后相关 leader 或任务有变化，请重新预览')
            texts = _handoff_texts(plan, request['handoffs'])
            if domain_versions.diff(plan.before, plan.after())['count'] == 0 and not (
                    plan.goal_actions or plan.retired or plan.splits):
                raise board.BoardError('方案没有任何改动')
            archived = plan.archived()
            detail = {'changes': [item['label'] for item in plan.items],
                      'archived': [item['name'] for item in archived],
                      'goals': [{'title': action['goal']['title'], 'action': action['action'],
                                 'to': plan.name(action['to']) if action['to'] else None}
                                for action in plan.goal_actions.values()],
                      'handoffs': [{'from': plan.name(slot['from']),
                                    'to': plan.name(slot['to']) if slot['to'] else next(
                                        item['name'] for item in plan.splits if item['ref'] == slot['ref']),
                                    'chars': len(texts[slot['index']]['text'])} for slot in plan.handoff_slots()],
                      'to_start': [item['name'] for item in plan.splits]}
            before = domain_versions.state(conn, project)
            last_notice = conn.execute('SELECT COALESCE(MAX(id),0) FROM leader_notices').fetchone()[0]
            if plan.ops:
                _positions(conn, project, plan)
                number = domains.apply_ops(conn, project, plan.ops, None, now, source='restructure', note=note,
                                           detail=detail)['version']
            else:
                domains.bump(conn, project, now)
                number = domain_versions.record(conn, project, 'restructure', note, before, now, detail=detail)
            cancels = []
            for goal_id, action in plan.goal_actions.items():
                if action['action'] == 'transfer':
                    giver = plan.name(action['goal']['leader_session_id'])
                    goals.request_transfer(conn, goal_id, action['to'],
                                           ('随 leader%s合并或退役转交；' % _quote(giver) if action['implicit'] else '')
                                           + '架构重整：' + note, actor=board.HUMAN, now=now)
                    goals.finalize_transfer(conn, goal_id, now=now)
                else:
                    goals.request_cancel(conn, goal_id, actor=board.HUMAN, now=now)
                    if not goals.finalize_cancel(conn, goal_id, now=now)['finalized']:
                        cancels.append(goal_id)
            befores = []
            for leader in plan.retired:
                befores.append(session_ops._close_group(conn, project, plan.group(leader), True, board.HUMAN, now)['before'])
            delivered = []
            for slot in plan.handoff_slots():
                entry = texts[slot['index']]
                if slot['to'] is None:
                    continue
                transferred = sum(1 for action in plan.goal_actions.values()
                                  if action['implicit'] and action['to'] == slot['to']
                                  and action['goal']['leader_session_id'] == slot['from'])
                notice = board._queue_leader_notice(conn, slot['to'], _notice(plan, slot, entry['text'], transferred),
                                                    'web', now, wake=entry['wake'])
                delivered.append({'from': slot['from'], 'to': slot['to'], 'notice_id': notice, 'wake': entry['wake']})
            to_start = [{'ref': item['ref'], 'name': item['name'], 'runtime': item['runtime'], 'model': item['model'],
                         'effort': item['effort'], 'part_id': item['part'], 'split_from': item['leader'],
                         'brief': texts[item['index']]['text']} for item in plan.splits]
            wakes = [{'session_id': row['session_id'], 'name': plan.name(row['session_id']), 'notice_id': row['id']}
                     for row in conn.execute('SELECT id,session_id FROM leader_notices WHERE id>? AND wake=1 ORDER BY id',
                                             (last_notice,))]
        for before_group in befores:
            service.stop_archived(before_group, with_members=True)
        manager.goal_controller.sync(conn)
        for goal_id in cancels:
            goals.finalize_cancel(conn, goal_id)
    return {'ok': True, 'version': number, 'board': domains.snapshot(conn, project),
            'archived': [item['session_id'] for item in archived],
            'goals': [{'goal_id': goal_id, 'action': action['action'], 'to': action['to']}
                      for goal_id, action in plan.goal_actions.items()],
            'handoffs': delivered, 'to_start': to_start, 'wakes': wakes, 'starts_turn': bool(wakes)}
