"""Human decision commands shared by interfaces, without HTTP request knowledge.

The supplied commands execute an explicitly requested explanation. Answers and
holds persist notices; this module does not invent scheduling policy.
"""
from . import board, decision_briefs, decision_flow, goal_links, goals, runtimes


def _decision(conn, project, identity):
    if type(identity) is not int:
        raise ValueError('id 必须是整数')
    value = conn.execute('SELECT * FROM decisions WHERE id=?', (identity,)).fetchone()
    if value is None:
        raise board.BoardError('待拍板不存在: ' + str(identity))
    if value['project'] != project:
        raise board.BoardError('待拍板不属于当前项目')
    return value


class Actions:
    def __init__(self, executions, send, send_goal, participants):
        self.executions, self.send, self.send_goal = executions, send, send_goal
        self.participants = participants

    def discuss(self, conn, project, identity, *, now=None):
        if type(identity) is not int:
            raise ValueError('id 必须是整数')
        result = decision_flow.discuss(conn, identity, project=project, now=now)
        link = goal_links.get(conn, 'decision', identity)
        linked_goal = goals.get(conn, link['goal_id']) if link else None
        conversation_goal = (linked_goal['id'] if linked_goal and linked_goal['status'] in ('queued', 'running')
                             and not linked_goal['manual_pause'] else None)
        notice = conn.execute('SELECT * FROM leader_notices WHERE id=?', (result.get('notice_id'),)).fetchone()
        if notice is not None and notice['sent_at'] is None:
            target = board.session(conn, result['leader_session_id'])
            if conversation_goal:
                self.send_goal(conversation_goal, target['session_id'], notice['text'],
                    source='human', identity='decision-discuss:' + str(identity))
            else:
                if not self.executions.path(target['session_id']).exists():
                    self.executions.journal(target['session_id']).set_status('idle')
                text = ('仅解释这项选择和后果；关联目标当前未运行，保持其状态，不能据此恢复施工。\n'
                        if linked_goal else '') + notice['text']
                self.send(target, text, source='human', identity='decision-discuss:' + str(identity))
            board.mark_notice_sent(conn, notice['id'], now=now)
        return {**result, 'conversation_goal_id': conversation_goal}

    def hold(self, conn, project, identity, *, now=None):
        decision = _decision(conn, project, identity)
        if board.hold(conn, identity, now=now):
            asker, leader = self.participants(conn, decision)
            if leader is not None:
                notice = ('拍板 #%s：%s（SendMessage 地址：%s）\n问题：%s\n'
                          '用户挂起了，暂时不拍。请用 SendMessage 告诉提问的组员：'
                          '不卡流程的按推荐继续；卡流程的先做别的，先别再问这道题。' %
                          (identity, asker['crew_name'], asker['cc_name'], decision['question']))
                if any(runtimes.get(conn, value['session_id'])['runtime'] == 'codex' for value in (asker, leader)):
                    notice = ('拍板 #%s：%s\n问题：%s\n用户挂起了，暂时不拍。请用 crew send %s "<处理安排>" 转告：'
                              '不卡流程的按推荐继续；卡流程的先做别的，先别再问这道题。' %
                              (identity, asker['crew_name'], decision['question'], asker['crew_name']))
                board.queue_leader_notice(conn, leader['session_id'], notice, 'web', now=now)
        return {'ok': True}

    def unhold(self, conn, project, identity, *, now=None):
        _decision(conn, project, identity)
        board.unhold(conn, identity, now=now)
        return {'ok': True}

    def answer(self, conn, project, identity, text, *, now=None):
        decision = _decision(conn, project, identity)
        if not isinstance(text, str) or not text.strip():
            raise ValueError('字段必须是非空文本: text')
        board.answer(conn, identity, text, board.HUMAN, now=now)
        link = goal_links.get(conn, 'decision', identity)
        if link:
            return {'ok': True, 'goal_id': link['goal_id'], 'routed_to': decision['session_id']}
        asker, leader = self.participants(conn, decision)
        if leader is not None:
            notice = ('拍板 #%s：%s（SendMessage 地址：%s）\n问题：%s\n答复：%s\n'
                      '请马上用 SendMessage 转告并叫它继续' %
                      (identity, asker['crew_name'], asker['cc_name'], decision['question'], text))
            if any(runtimes.get(conn, value['session_id'])['runtime'] == 'codex' for value in (asker, leader)):
                action = ('请按用户答复继续。' if asker['session_id'] == leader['session_id'] else
                          '请用 crew send %s "<答复与后续安排>" 转告；离线时用 crew wake 接续原会话。' % asker['crew_name'])
                notice = ('拍板 #%s：%s\n问题：%s\n答复：%s\n%s' %
                          (identity, asker['crew_name'], decision['question'], text, action))
            board.queue_leader_notice(conn, leader['session_id'], notice, 'web', now=now)
        return {'ok': True, 'routed_to': leader['crew_name'] if leader else None}


def clarify(conn, project, identity, *, now=None):
    if type(identity) is not int:
        raise ValueError('id 必须是整数')
    item = conn.execute('SELECT project FROM decisions WHERE id=?', (identity,)).fetchone()
    if item is None or item['project'] != project:
        raise board.BoardError('拍板不属于当前工作区')
    notice = decision_briefs.queue_review(conn, identity, now=now)
    return {'ok': True, 'notice_id': notice, 'message': '已请 leader 重新说明这项选择'}
