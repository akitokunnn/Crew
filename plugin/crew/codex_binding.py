"""Crew session/execution ↔ Codex thread binding: native ID and the CODEX_HOME it lives in."""
from pathlib import Path

from . import board, codex_client, native_home, store


class WriterBusy(board.Conflict):
    """Codex refused thread/resume because another native client holds the thread's writer."""

    def __init__(self, native_error):
        self.native_error = str(native_error)
        super().__init__('Codex 桌面或其他 Codex 客户端正打开着这个对话、占用写入连接，Crew 暂时不能续接。'
                         '请先在那边结束执行并关闭持有此对话的 Codex 客户端（仅离开对话页面可能不会释放连接）后重试；'
                         '原对话和历史都已保留。原生原因：' + self.native_error)


def writer_busy(exc):
    """Translate Codex's single-writer refusal; other errors return None and pass through unchanged."""
    if (isinstance(exc, codex_client.RpcError) and exc.method == 'thread/resume'
            and 'already has an active writer' in str(exc)):
        return WriterBusy(exc)
    return None


def scope(session_id, execution_id=None):
    return execution_id or session_id


def home_for(conn, session_id, execution_id=None):
    row = conn.execute('SELECT native_home FROM native_session_controls WHERE scope_id=?',
                       (scope(session_id, execution_id),)).fetchone()
    return Path(row['native_home']) if row is not None else native_home.codex_home()


def remember(conn, session_id, native_id, home, execution_id=None):
    key = scope(session_id, execution_id)
    row = conn.execute('SELECT native_id,native_home FROM native_session_controls WHERE scope_id=?', (key,)).fetchone()
    if row is not None and (row['native_id'] != native_id or Path(row['native_home']) != Path(home)):
        raise board.Conflict('Codex 原生身份或 HOME 与已保存的绑定不一致，未覆盖原绑定')
    conn.execute('INSERT INTO native_session_controls(scope_id,session_id,execution_id,native_id,native_home,state,updated_at) '
                 "VALUES(?,?,?,?,?,'crew',?) ON CONFLICT(scope_id) DO NOTHING",
                 (key, session_id, execution_id, native_id, str(home), store.now_iso()))
