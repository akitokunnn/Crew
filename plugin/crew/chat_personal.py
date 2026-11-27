from contextlib import closing
import hashlib
import uuid

from . import board, personal, settings, store
from .chat_base import _admitted


class PersonalMixin:
    def _personal_leader(self, project, message, *, name, request_id, instruction, selection=None):
        with closing(store.connect(self.home)) as conn:
            chosen = settings.resolve(conn, 'leader') if selection is None else selection
            task = ('原始用户要求：\n' + instruction['instruction'] + '\n\n讨论材料：\n' + message +
                    '\n\n先按用户原话讨论并识别任务，已有执行同意则推进；你可以自行修改对话名称。')
            note = instruction.get('context', {}).get('note') if instruction.get('context', {}).get('action') == 'note.start' else None
            def registered(sid):
                self.journal(sid).emit('personal_note_origin', note_id=note['id'], request_id=request_id, project=project)
                personal.record_note_registration(conn, note['id'], request_id, sid)
            return self.start(conn, project, name or ('leader-' + uuid.uuid4().hex[:8]), task,
                              role='leader', caller_session_id=board.HUMAN, _registered=registered if note is not None else None, **chosen)

    def _recover_personal_note(self, conn, note_id):
        note = personal.get_note(conn, note_id)
        link = conn.execute('SELECT * FROM personal_note_leaders WHERE note_id=?', (note_id,)).fetchone()
        if link is None or link['status'] == 'completed':
            return None
        matches = []
        for leader in personal.note_recovery(conn, note_id)['leaders']:
            sid = leader['session_id']
            origins = [event for event in self.journal(sid).events if event.get('type') == 'personal_note_origin'
                       and event.get('note_id') == note_id and event.get('request_id') == link['request_id']
                       and event.get('project') == link['project']]
            if origins:
                matches.append((sid, origins[0]))
        if len(matches) != 1:
            return None
        sid, origin = matches[0]
        return personal.bind_note_leader(conn, note_id, sid, request_id='note-recovery:' + hashlib.sha256(
            (note_id + ':' + link['request_id']).encode('utf-8')).hexdigest(),
            reason='宿主创建前登记及会话来源日志确认对话已存在；没有重放原输入', actor='crew-recovery',
            evidence={'type': 'personal_note_origin', 'session_id': sid, 'seq': origin['seq'], 'request_id': link['request_id']})

    @_admitted
    def start_personal_note(self, note_id, project, *, request_id):
        """Only the explicit UI Start discussing action enters model work."""
        with self.lock, closing(store.connect(self.home)) as conn:
            note = personal.get_note(conn, note_id)
            if note['leader'] is not None and note['leader']['project'] != project:
                raise board.Conflict('这条想法已有其他工作区的对话关联')
            recovered = self._recover_personal_note(conn, note_id)
            if recovered is not None:
                return recovered
            # Configuration failures occur before reserving an external action.
            # An already-linked note remains navigable without any installed CLI.
            selection = settings.resolve(conn, 'leader') if note['leader'] is None else None
            try:
                return personal.start_note(conn, note_id, project, request_id=request_id,
                    create_leader=lambda *args, **kwargs: self._personal_leader(*args, selection=selection, **kwargs))
            except Exception as error:
                recovered = self._recover_personal_note(conn, note_id)
                if recovered is not None:
                    self.journal(recovered['session_id']).emit('error', text='对话已创建，但首轮启动或返回失败：' + str(error) +
                        '。已找回原对话，没有重放输入。原始想法：' + note['original_text'], note_recovery_id=note_id)
                    return {**recovered, 'warning': str(error), 'model_calls': None}
                raise
