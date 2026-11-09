"""Leader look: a chosen color and a small avatar image, set by the user.

The browser crops and compresses pictures before upload; this module only
accepts what is already small and sniffs the bytes itself instead of trusting
the declared type.
"""
import base64
import binascii
import re

from . import board

MAX_AVATAR_BYTES = 200 * 1024
_COLOR = re.compile(r'#[0-9a-fA-F]{6}')


def _sniff(data):
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if data[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return None


def _color(value):
    if value is None:
        return None
    if not isinstance(value, str) or not _COLOR.fullmatch(value.strip()):
        raise board.BoardError('颜色必须是 #RRGGBB 格式')
    return value.strip().upper()


def _avatar(value):
    if not isinstance(value, dict) or not isinstance(value.get('data'), str):
        raise board.BoardError('头像需要 data（base64 图片）')
    if len(value['data']) > (MAX_AVATAR_BYTES + 2) // 3 * 4 + 4:
        raise board.BoardError('头像压缩后不能超过 200KB，请换一张图片')
    try:
        content = base64.b64decode(value['data'], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise board.BoardError('头像内容不是有效的 base64：%s' % exc) from None
    if not content:
        raise board.BoardError('头像图片是空的')
    if len(content) > MAX_AVATAR_BYTES:
        raise board.BoardError('头像压缩后不能超过 200KB，请换一张图片')
    media_type = _sniff(content)
    if media_type is None:
        raise board.BoardError('头像只支持 WebP、PNG、JPEG 图片')
    return content, media_type


def all_looks(conn):
    """{session_id: {color, avatar_version}} for one pass over /api/state."""
    return {row['session_id']: {'color': row['color'], 'avatar_version': row['avatar_version'] if row['has_avatar'] else None}
            for row in conn.execute('SELECT session_id,color,avatar_version,avatar IS NOT NULL AS has_avatar FROM session_appearance')}


def get(conn, session_id):
    return all_looks(conn).get(session_id, {'color': None, 'avatar_version': None})


@board.atomic
def save(conn, session_id, data, now):
    """color: '#RRGGBB' or None; avatar: omitted keeps it, None removes it, {data} replaces it."""
    session = board.session(conn, session_id)
    if session['role'] != 'leader':
        raise board.BoardError('只有 leader 可以设置主题色和头像')
    if not isinstance(data, dict) or 'color' not in data:
        raise board.BoardError('需要提供 color（不设置时传 null）')
    color = _color(data['color'])
    row = conn.execute('SELECT avatar,avatar_type,avatar_version FROM session_appearance WHERE session_id=?',
                       (session_id,)).fetchone()
    avatar, media_type, version = (row['avatar'], row['avatar_type'], row['avatar_version']) if row else (None, None, 0)
    if 'avatar' in data:
        avatar, media_type = (None, None) if data['avatar'] is None else _avatar(data['avatar'])
        version += 1
    conn.execute('INSERT OR REPLACE INTO session_appearance(session_id,color,avatar,avatar_type,avatar_version,updated_at) '
                 'VALUES(?,?,?,?,?,?)', (session_id, color, avatar, media_type, version, now))
    return get(conn, session_id)


def avatar(conn, session_id):
    row = conn.execute('SELECT avatar,avatar_type FROM session_appearance WHERE session_id=? AND avatar IS NOT NULL',
                       (session_id,)).fetchone()
    if row is None:
        raise board.BoardError('这个会话没有头像')
    return bytes(row['avatar']), row['avatar_type']
