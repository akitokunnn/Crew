"""Import native history only at an explicit return, with stable native identities."""
from . import board, codex_events


def turns(client, thread_id):
    cursor, seen, result = None, set(), []
    while True:
        params = {'threadId': thread_id, 'limit': 100, 'sortDirection': 'asc', 'itemsView': 'full'}
        if cursor is not None:
            params['cursor'] = cursor
        page = client.request('thread/turns/list', params)
        if not isinstance(page.get('data'), list):
            raise board.BoardError('Codex 历史读取失败：缺少原生轮次列表')
        for turn in page['data']:
            if not isinstance(turn.get('id'), str) or not isinstance(turn.get('items'), list):
                raise board.BoardError('Codex 历史读取失败：轮次未包含完整项目')
            result.append(turn)
        cursor = page.get('nextCursor')
        if cursor is None:
            return result
        if cursor in seen:
            raise board.BoardError('Codex 历史分页游标重复，未继续导入')
        seen.add(cursor)


def checkpoint(client, thread_id, journal):
    """An idle boundary prevents Crew's own earlier prompts being shown twice."""
    history = turns(client, thread_id)
    if any(turn.get('status') == 'inProgress' for turn in history):
        raise board.Conflict('Codex 还有未完成轮次，不能交给桌面')
    journal.emit('native_history_checkpoint', native_id=thread_id,
                 turn_ids=[turn['id'] for turn in history])


def sync(client, thread_id, journal):
    history = turns(client, thread_id)
    if any(turn.get('status') == 'inProgress' for turn in history):
        raise board.Conflict('Codex 桌面中的轮次尚未结束，请结束后再回到 Crew')
    with journal.condition:
        journal.refresh()
        baseline = {turn_id for event in journal.events
                    if event['type'] == 'native_history_checkpoint' and event.get('native_id') == thread_id
                    for turn_id in event['turn_ids']}
        imported = {(event.get('native_turn_id'), event.get('native_item_id'), event['type'])
                    for event in journal.events if event.get('native_id') == thread_id}
        count = 0
        for turn in history:
            if turn['id'] in baseline:
                continue
            adapter = codex_events.Events(thread_id)
            for item in turn['items']:
                identity = item.get('id')
                if not isinstance(identity, str):
                    raise board.BoardError('Codex 历史项目缺少稳定 ID')
                if item.get('type') == 'userMessage':
                    content = item.get('content') or []
                    if not isinstance(content, list):
                        raise board.BoardError('Codex 用户消息格式不正确')
                    text = '\n'.join(part.get('text', '') if part.get('type') == 'text' else
                                     '[图片]' if part.get('type') in ('image', 'localImage') else ''
                                     for part in content)
                    events = [{'type': 'message', 'id': 'codex:' + thread_id + ':' + identity,
                               'role': 'user', 'text': text, 'source': 'human', 'native_source': 'codex_desktop'}]
                else:
                    events = adapter.consume('item/completed', {'threadId': thread_id,
                                              'turnId': turn['id'], 'item': item})
                for event in events:
                    kind = event.pop('type')
                    if kind in ('activity', 'context', 'status'):
                        continue
                    key = (turn['id'], identity, kind)
                    if key in imported:
                        continue
                    # Live Crew replies already use the native assistant/tool id.
                    if kind == 'assistant' and event['id'] in journal.assistant:
                        continue
                    if kind == 'tool_result' and event['tool_use_id'] in journal.results:
                        continue
                    journal.emit(kind, **event, native_id=thread_id,
                                 native_turn_id=turn['id'], native_item_id=identity, native_imported=True)
                    imported.add(key)
                    count += 1
            marker = 'turn:' + turn['id']
            status = turn.get('status')
            if status == 'failed' and (turn['id'], marker, 'error') not in imported:
                journal.emit('error', text=(turn.get('error') or {}).get('message') or 'Codex 原生轮次失败',
                             native_id=thread_id, native_turn_id=turn['id'], native_item_id=marker, native_imported=True)
                imported.add((turn['id'], marker, 'error'))
            if status in ('completed', 'failed', 'interrupted') and (turn['id'], marker, 'turn_result') not in imported:
                journal.emit('turn_result', turn_id=turn['id'], status=status, success=status == 'completed',
                             native_id=thread_id, native_turn_id=turn['id'], native_item_id=marker, native_imported=True)
                imported.add((turn['id'], marker, 'turn_result'))
        journal._index()
        return count
