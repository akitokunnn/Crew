"""One wording for saved mail, queued delivery and accepted execution input."""

KINDS = frozenset(('task', 'result', 'blocker', 'decision', 'user_answer', 'information', 'ack'))
ACTIONABLE = KINDS - {'information', 'ack'}


def normalize(receipt, *, kind=None):
    value = dict(receipt)
    actual_kind = value.get('kind', kind)
    status = value.get('status')
    if status is None:
        if value.get('accepted') is True:
            status = 'accepted'
        elif value.get('queued') is True:
            status = 'queued'
        elif value.get('error'):
            status = 'failed'
        elif value.get('queued') is False or value.get('delivered_at'):
            status = 'delivered'
        else:
            status = 'unknown'
    if status == 'archived':
        note = '仅归档，未投递到执行，不唤醒对方。'
        if value.get('duplicate_of'):
            note += '重复内容的原消息编号为 %s，未再次投递。' % value['duplicate_of']
        elif actual_kind in ('information', 'ack'):
            note += '需对方处理请明确使用 --kind decision/task/result/blocker。'
        elif value.get('error'):
            note += '原因：' + str(value['error'])
    elif status == 'queued':
        note = '已排队，尚未交给执行输入。'
        if value.get('error'):
            note += '上次尝试：' + str(value['error']) + '；保留同一编号核对，勿重复成功操作。'
    elif status == 'accepted':
        note = '已交给执行输入，尚不代表模型已读、已答复或任务完成。'
    elif status == 'delivered':
        note = '已交给接收通道或原生 Hook 附加上下文，尚未确认执行输入。'
    elif status == 'failed':
        note = '投递失败，未确认执行输入。'
        if value.get('error'):
            note += '原因：' + str(value['error'])
        note += '保留同一编号核对，勿重复成功操作。'
    else:
        note = '投递状态未确认，请先核对原消息。'
    label = actual_kind if actual_kind is not None else 'legacy（旧消息未记录类型）'
    value.pop('duplicate_of', None)
    prefix = '[%s · %s] ' % (label, status)
    message = value['message'] if isinstance(value.get('message'), str) and value['message'].startswith(prefix) else prefix + note
    return {**value, 'kind': actual_kind, 'status': status, 'queued': status == 'queued',
            'accepted': status == 'accepted', 'message': message}
