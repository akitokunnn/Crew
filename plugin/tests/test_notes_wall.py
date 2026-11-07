"""想法墙：想法与标签的本地动作、陈旧计算、助手读写、去聊聊与导出恢复。"""
import tests
from contextlib import closing
import datetime
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from crew import board, codex_tools, personal, personal_actions, settings, store, web, workspace_recovery
from crew.web_api import personal as api
import test_chat_http
from test_codex_runtime import FakeClient
from test_runtimes import RuntimeFixture
from test_shell import ShellFixture, PLUGIN, NOW


T0 = '2026-10-01T00:00:00.000Z'


def later(days=0, ms=0):
    return personal._iso(store.parse_iso(T0) + datetime.timedelta(days=days, milliseconds=ms))


class NotesFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='crew-notes-wall-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / 'home'
        self.conn = store.connect(self.home)
        self.addCleanup(self.conn.close)
        self.counter = 0

    def note(self, action, data, request_id=None, now=T0):
        self.counter += 1
        return personal.note_action(self.conn, action, data, request_id=request_id or 'note-%d' % self.counter, now=now)

    def capture(self, text, **fields):
        return self.note('capture', {'text': text, **fields})['note']

    def tag(self, action, data, request_id=None, now=T0):
        self.counter += 1
        return personal.tag_action(self.conn, action, data, request_id=request_id or 'tag-%d' % self.counter, now=now)

    def get(self, note_id, now=T0):
        return personal.get_note(self.conn, note_id, now=now)

    def snapshot(self, *note_ids):
        return [tuple(self.conn.execute('SELECT * FROM personal_notes WHERE id=?', (identity,)).fetchone()) for identity in note_ids]

    def workspace(self, name):
        directory = self.root / name
        directory.mkdir()
        return board.init_project(self.conn, name, directory)


class NoteSchemaTests(NotesFixture):
    def test_legacy_table_gains_columns_with_new_stage_and_tags_register_on_first_read(self):
        self.conn.execute('CREATE TABLE personal_notes (id TEXT PRIMARY KEY, project TEXT REFERENCES projects(name), '
                          'original_text TEXT NOT NULL, text TEXT NOT NULL, tags_json TEXT NOT NULL, '
                          "state TEXT NOT NULL DEFAULT 'open' CHECK(state IN ('open','archived')), "
                          'version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)')
        self.conn.execute('INSERT INTO personal_notes(id,original_text,text,tags_json,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                          ('legacy', '旧想法', '旧想法', json.dumps(['生活', '健身']), T0, T0))
        personal.ensure_schema(self.conn)
        personal.ensure_schema(self.conn)
        note = self.get('legacy')
        self.assertEqual((note['stage'], note['title'], note['summaries'], note['merged_from'], note['source']),
                         ('new', None, [], [], {}))
        self.assertEqual(note['discussions'], {'count': 0, 'last_at': None, 'session_id': None, 'last_seq': None})
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM personal_note_tags').fetchone()[0], 0)
        listing = personal.list_tags(self.conn, now=T0)
        self.assertEqual(listing['palette'], ['c%d' % i for i in range(10)])
        self.assertEqual([(tag['name'], tag['color'], tag['count'], tag['version']) for tag in listing['tags']],
                         [(name, personal._tag_color(name), 1, 1) for name in ('生活', '健身')])
        self.assertTrue(all(tag['color'] in personal.PALETTE for tag in listing['tags']))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE personal_notes SET stage='done' WHERE id='legacy'")


class NoteActionTests(NotesFixture):
    def test_capture_with_title_and_partial_update_keep_original_and_check_versions(self):
        note = self.capture('先做每日打卡', title='健身打卡', tags=['健身'])
        self.assertEqual((note['title'], note['stage'], note['version'], note['stale']), ('健身打卡', 'new', 1, False))
        self.assertEqual([tag['name'] for tag in personal.list_tags(self.conn)['tags']], ['健身'])
        changed = self.note('update', {'note_id': note['id'], 'expected_version': 1, 'title': '打卡 App'}, request_id='title')
        self.assertEqual((changed['note']['title'], changed['note']['text'], changed['note']['version']), ('打卡 App', '先做每日打卡', 2))
        replay = self.note('update', {'note_id': note['id'], 'expected_version': 1, 'title': '打卡 App'}, request_id='title')
        self.assertTrue(replay['duplicate'])
        self.assertEqual(self.get(note['id'])['version'], 2)
        cleared = self.note('update', {'note_id': note['id'], 'expected_version': 2, 'title': None, 'text': '整理后'})['note']
        self.assertEqual((cleared['title'], cleared['text'], cleared['original_text']), (None, '整理后', '先做每日打卡'))
        with self.assertRaisesRegex(board.Conflict, '已变化'):
            self.note('update', {'note_id': note['id'], 'expected_version': 2, 'text': '旧版本覆盖'})
        with self.assertRaisesRegex(board.BoardError, '至少要修改'):
            self.note('update', {'note_id': note['id'], 'expected_version': 3})
        with self.assertRaisesRegex(board.BoardError, '不支持 state'):
            self.note('capture', {'text': '新想法', 'state': 'archived'})
        with self.assertRaisesRegex(board.BoardError, '标题'):
            self.note('capture', {'text': '新想法', 'title': '长' * 81})

    def test_pin_snooze_keep_archive_restore_check_versions_and_replay(self):
        note = self.capture('想法')
        steps = [('pin', {'pinned': True}, lambda value: value['pinned_at'] == later(1)),
                 ('snooze', {'until': '2026-10-20T08:00:00+08:00'}, lambda value: value['snoozed_until'] == '2026-10-20T00:00:00.000Z'),
                 ('keep', {}, lambda value: value['reviewed_at'] == later(1)),
                 ('archive', {}, lambda value: value['state'] == 'archived'),
                 ('restore', {}, lambda value: value['state'] == 'open'),
                 ('snooze', {'until': None}, lambda value: value['snoozed_until'] is None),
                 ('pin', {'pinned': False}, lambda value: value['pinned_at'] is None)]
        for index, (action, extra, check) in enumerate(steps):
            version = self.get(note['id'])['version']
            data = {'note_id': note['id'], 'expected_version': version, **extra}
            result = self.note(action, data, request_id='step-%d' % index, now=later(1))
            self.assertTrue(check(result['note']), action)
            self.assertEqual(result['note']['version'], version + 1)
            self.assertTrue(self.note(action, data, request_id='step-%d' % index)['duplicate'])
            with self.assertRaisesRegex(board.Conflict, '已变化'):
                self.note(action, data, request_id='stale-%d' % index)
        version = self.get(note['id'])['version']
        with self.assertRaisesRegex(board.BoardError, '时区'):
            self.note('snooze', {'note_id': note['id'], 'expected_version': version, 'until': '2026-10-20T08:00:00'})
        with self.assertRaisesRegex(board.BoardError, '没有归档'):
            self.note('restore', {'note_id': note['id'], 'expected_version': version})
        with self.assertRaisesRegex(board.BoardError, '布尔'):
            self.note('pin', {'note_id': note['id'], 'expected_version': version, 'pinned': 1})

    def test_merge_appends_sources_unions_tags_and_unmerge_restores_only_the_child(self):
        a = self.capture('主想法', tags=['x'])
        b = self.capture('重复想法 B', tags=['y'])
        c = self.capture('重复想法 C', tags=['x', 'z'])
        data = {'primary_id': a['id'], 'note_ids': [b['id'], c['id']],
                'expected_versions': {a['id']: 1, b['id']: 1, c['id']: 1}}
        result = self.note('merge', data, request_id='merge')
        primary = result['note']
        self.assertTrue(primary['text'].startswith('主想法\n\n合并来源：\n'))
        self.assertIn('- 重复想法 B（原 id：%s）' % b['id'], primary['text'])
        self.assertIn('- 重复想法 C（原 id：%s）' % c['id'], primary['text'])
        self.assertEqual((primary['original_text'], primary['tags'], primary['version']), ('主想法', ['x', 'y', 'z'], 2))
        self.assertCountEqual(primary['merged_from'], [b['id'], c['id']])
        self.assertEqual([note['id'] for note in result['notes']], [a['id'], b['id'], c['id']])
        child = self.get(b['id'])
        self.assertEqual((child['state'], child['merged_into'], child['text']), ('archived', a['id'], '重复想法 B'))
        self.assertTrue(self.note('merge', data, request_id='merge')['duplicate'])
        with self.assertRaisesRegex(board.BoardError, 'unmerge'):
            self.note('restore', {'note_id': b['id'], 'expected_version': child['version']})
        with self.assertRaisesRegex(board.BoardError, '未归档'):
            self.note('merge', {'primary_id': a['id'], 'note_ids': [b['id']],
                                'expected_versions': {a['id']: 2, b['id']: child['version']}})
        restored = self.note('unmerge', {'note_id': b['id'], 'expected_version': child['version']})['note']
        self.assertEqual((restored['state'], restored['merged_into']), ('open', None))
        after = self.get(a['id'])
        self.assertEqual(after['merged_from'], [c['id']])
        self.assertIn('重复想法 B', after['text'])
        with self.assertRaisesRegex(board.BoardError, '没有被合并'):
            self.note('unmerge', {'note_id': b['id'], 'expected_version': restored['version']})

    def test_merge_and_bulk_fail_as_a_whole_on_any_stale_version_or_invalid_note(self):
        a, b, c = (self.capture(text) for text in ('甲', '乙', '丙'))
        self.note('update', {'note_id': c['id'], 'expected_version': 1, 'text': '丙改'})
        before = self.snapshot(a['id'], b['id'], c['id'])
        stale = {a['id']: 1, b['id']: 1, c['id']: 1}
        with self.assertRaisesRegex(board.Conflict, c['id']):
            self.note('merge', {'primary_id': a['id'], 'note_ids': [b['id'], c['id']], 'expected_versions': stale})
        with self.assertRaisesRegex(board.Conflict, c['id']):
            self.note('bulk', {'note_ids': [a['id'], b['id'], c['id']], 'expected_versions': stale,
                               'op': 'tag_add', 'value': '新标签'})
        with self.assertRaisesRegex(board.BoardError, 'expected_versions'):
            self.note('bulk', {'note_ids': [a['id'], b['id']], 'expected_versions': {a['id']: 1}, 'op': 'archive'})
        self.assertEqual(self.snapshot(a['id'], b['id'], c['id']), before)
        self.assertNotIn('新标签', [tag['name'] for tag in personal.list_tags(self.conn)['tags']])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM personal_note_requests').fetchone()[0], 4)

    def test_bulk_tags_project_and_archive(self):
        self.workspace('fitness')
        a, b = self.capture('甲', tags=['x']), self.capture('乙')
        def bulk(op, *value):
            notes = [self.get(a['id']), self.get(b['id'])]
            data = {'note_ids': [a['id'], b['id']], 'expected_versions': {note['id']: note['version'] for note in notes},
                    'op': op, **({'value': value[0]} if value else {})}
            return self.note('bulk', data)['notes']
        self.assertEqual([note['tags'] for note in bulk('tag_add', '运动')], [['x', '运动'], ['运动']])
        self.assertIn('运动', [tag['name'] for tag in personal.list_tags(self.conn)['tags']])
        self.assertEqual([note['tags'] for note in bulk('tag_remove', 'x')], [['运动'], ['运动']])
        self.assertEqual([note['project'] for note in bulk('project', 'fitness')], ['fitness', 'fitness'])
        self.assertEqual([note['project'] for note in bulk('project', None)], [None, None])
        with self.assertRaisesRegex(board.BoardError, '不接受 value'):
            bulk('archive', '多余')
        with self.assertRaisesRegex(board.BoardError, '批量操作必须是'):
            bulk('delete', 'x')
        self.assertEqual([note['state'] for note in bulk('archive')], ['archived', 'archived'])
        self.assertEqual(personal.list_notes(self.conn), [])

    def test_annotate_appends_a_human_summary_without_touching_original(self):
        note = self.capture('原话')
        first = self.note('annotate', {'note_id': note['id'], 'text': '结论一'}, now=later(2))['note']
        second = self.note('annotate', {'note_id': note['id'], 'text': '结论二'}, now=later(3))['note']
        self.assertEqual([(item['text'], item['at'], item['source']) for item in second['summaries']],
                         [('结论一', later(2), {'type': 'human'}), ('结论二', later(3), {'type': 'human'})])
        self.assertEqual((second['text'], second['original_text'], second['version']), ('原话', '原话', first['version'] + 1))

    def test_start_note_and_recovered_link_promote_and_archive(self):
        root = self.root / 'demo'
        root.mkdir()
        board.init_project(self.conn, 'demo', root)
        board.join(self.conn, 'chief', 'demo', 'chief', root, None, None, role='leader')
        promoted = self.capture('转任务')
        result = personal.start_note(self.conn, promoted['id'], 'demo', request_id='start',
                                     create_leader=lambda *args, **kwargs: {'session_id': 'chief'})
        self.assertEqual((result['note']['stage'], result['note']['state']), ('promoted', 'archived'))
        self.assertEqual(result['note']['leader']['session_id'], 'chief')
        self.assertFalse(result['note']['stale'])
        self.assertNotIn(promoted['id'], [note['id'] for note in personal.list_notes(self.conn)])
        unknown = self.capture('结果未知')
        with self.assertRaises(TimeoutError):
            personal.start_note(self.conn, unknown['id'], 'demo', request_id='lost',
                                create_leader=mock.Mock(side_effect=TimeoutError('lost')))
        self.assertEqual((self.get(unknown['id'])['stage'], self.get(unknown['id'])['state']), ('new', 'open'))
        bound = personal.bind_note_leader(self.conn, unknown['id'], 'chief', request_id='bind', reason='已核实')
        self.assertEqual((bound['note']['stage'], bound['note']['state']), ('promoted', 'archived'))


class StaleTests(NotesFixture):
    def stale(self, note_id, now):
        return personal.get_note(self.conn, note_id, now=now)['stale']

    def test_fourteen_day_boundary_uses_latest_edit_discussion_or_review(self):
        note = self.capture('放着')
        self.assertFalse(self.stale(note['id'], later(14)))
        self.assertTrue(self.stale(note['id'], later(14, ms=1)))
        listing = api.notes(self.home)
        self.assertEqual(listing['stale_days'], 14)
        self.conn.execute('UPDATE personal_notes SET last_discussed_at=? WHERE id=?', (later(10), note['id']))
        self.assertFalse(self.stale(note['id'], later(24)))
        self.assertTrue(self.stale(note['id'], later(24, ms=1)))
        self.conn.execute('UPDATE personal_notes SET reviewed_at=? WHERE id=?', (later(20), note['id']))
        self.assertFalse(self.stale(note['id'], later(34)))
        self.assertTrue(self.stale(note['id'], later(34, ms=1)))

    def test_snooze_archive_and_promotion_exclude_from_review(self):
        note = self.capture('暂缓')
        self.conn.execute('UPDATE personal_notes SET snoozed_until=? WHERE id=?', (later(20), note['id']))
        self.assertFalse(self.stale(note['id'], later(20, ms=-1)))
        self.assertTrue(self.stale(note['id'], later(20)))
        self.conn.execute("UPDATE personal_notes SET stage='promoted' WHERE id=?", (note['id'],))
        self.assertFalse(self.stale(note['id'], later(40)))
        self.conn.execute("UPDATE personal_notes SET stage='discussed',state='archived' WHERE id=?", (note['id'],))
        self.assertFalse(self.stale(note['id'], later(40)))


class TagTests(NotesFixture):
    def tags(self):
        return {tag['name']: tag for tag in personal.list_tags(self.conn)['tags']}

    def test_create_recolor_pin_reorder_check_versions_and_palette(self):
        self.tag('create', {'name': '甲', 'color': 'c3'})
        self.tag('create', {'name': '乙'})
        self.assertEqual(self.tags()['乙']['color'], personal._tag_color('乙'))
        with self.assertRaisesRegex(board.Conflict, '已存在'):
            self.tag('create', {'name': '甲'})
        with self.assertRaisesRegex(board.BoardError, '颜色'):
            self.tag('create', {'name': '丙', 'color': 'red'})
        self.assertEqual(self.tag('recolor', {'name': '甲', 'color': 'c5', 'expected_version': 1}, request_id='recolor')['tags'][0]['color'], 'c5')
        self.assertTrue(self.tag('recolor', {'name': '甲', 'color': 'c5', 'expected_version': 1}, request_id='recolor')['duplicate'])
        with self.assertRaisesRegex(board.Conflict, '标签已变化'):
            self.tag('recolor', {'name': '甲', 'color': 'c6', 'expected_version': 1})
        listing = self.tag('pin', {'name': '乙', 'pinned': True, 'expected_version': 1})['tags']
        self.assertEqual([(tag['name'], tag['pinned']) for tag in listing], [('乙', True), ('甲', False)])
        self.tag('create', {'name': '丙'})
        self.tag('reorder', {'names': ['丙', '甲']})
        self.assertEqual([tag['name'] for tag in personal.list_tags(self.conn)['tags']], ['乙', '丙', '甲'])
        with self.assertRaisesRegex(board.BoardError, '标签不存在'):
            self.tag('reorder', {'names': ['丁']})

    def test_rename_syncs_every_note_and_existing_name_requires_merge(self):
        first = self.capture('一', tags=['旧', '保留'])
        second = self.capture('二', tags=['旧'])
        third = self.capture('三', tags=['保留'])
        self.note('archive', {'note_id': second['id'], 'expected_version': 1})
        renamed = self.tag('rename', {'name': '旧', 'new_name': '新', 'expected_version': 1}, now=later(5))
        self.assertEqual(renamed['affected'], 2)
        one, two = self.get(first['id']), self.get(second['id'])
        self.assertEqual((one['tags'], two['tags']), (['新', '保留'], ['新']))
        self.assertEqual((one['version'], one['updated_at']), (2, T0))
        self.assertEqual(set(self.tags()), {'新', '保留'})
        before = self.snapshot(first['id'], second['id'], third['id'])
        with self.assertRaises(personal.TagMergeRequired) as caught:
            self.tag('rename', {'name': '保留', 'new_name': '新', 'expected_version': 1})
        self.assertEqual(caught.exception.detail, {'merge_required': True, 'target': '新', 'affected': 2})
        self.assertEqual(self.snapshot(first['id'], second['id'], third['id']), before)
        merged = self.tag('merge', {'from': ['保留'], 'into': '新'})
        self.assertEqual(merged['affected'], 2)
        self.assertEqual([self.get(note['id'])['tags'] for note in (first, second, third)], [['新'], ['新'], ['新']])
        self.assertEqual(set(self.tags()), {'新'})
        removed = self.tag('remove', {'name': '新', 'expected_version': 2})
        self.assertEqual((removed['affected'], removed['tags']), (3, []))
        self.assertEqual([self.get(note['id'])['tags'] for note in (first, second, third)], [[], [], []])
        self.assertEqual(self.get(second['id'])['state'], 'archived')
        self.assertEqual(self.get(first['id'])['text'], '一')


class AssistantNotesTests(NotesFixture):
    def setUp(self):
        super().setUp()
        self.sid = personal.ensure_assistant(self.conn, self.home)['session_id']
        self.events = [{'type': 'message', 'id': 'user-1', 'seq': 7, 'role': 'user', 'source': 'human',
                        'text': '帮我整理想法', 'at': T0}]

    def dispatch(self, action, data, request_id='assistant-1', message_id='user-1'):
        return personal_actions.dispatch(self.home, self.sid, action,
            {'data': data, 'message_id': message_id, 'request_id': request_id}, read_events=lambda _: self.events)

    def test_reads_filter_notes_get_one_and_register_tags(self):
        self.workspace('fitness')
        a = self.capture('甲', tags=['x', 'y'], project='fitness')
        b = self.capture('乙', tags=['x'])
        c = self.capture('丙')
        self.conn.execute("UPDATE personal_notes SET stage='discussed',tags_json=? WHERE id=?", (json.dumps(['legacy']), c['id']))
        self.note('archive', {'note_id': b['id'], 'expected_version': 1})
        def ids(**data):
            return [note['id'] for note in self.dispatch('notes.list', data)['notes']]
        self.assertEqual(set(ids()), {a['id'], c['id']})
        self.assertEqual(ids(tags=['x', 'y']), [a['id']])
        self.assertEqual(set(ids(tags=['x', 'legacy'], mode='any', include_archived=True)), {a['id'], b['id'], c['id']})
        self.assertEqual(ids(tags=['x', 'legacy'], mode='all'), [])
        self.assertEqual(ids(stage='discussed'), [c['id']])
        self.assertEqual(ids(project='fitness'), [a['id']])
        self.assertCountEqual(ids(stale_only=True), [a['id'], c['id']])  # captured on T0, long ago
        page = self.dispatch('notes.list', {'include_archived': True, 'limit': 2, 'offset': 1})
        self.assertEqual((page['total'], page['offset'], page['next_offset'], page['stale_days'], len(page['notes'])), (3, 1, None, 14, 2))
        with self.assertRaisesRegex(board.BoardError, 'untagged'):
            self.dispatch('notes.list', {'untagged': True, 'tags': ['x']})
        with self.assertRaisesRegex(board.BoardError, '只支持参数'):
            self.dispatch('notes.list', {'state': 'open'})
        got = self.dispatch('notes.get', {'note_id': a['id']})['note']
        self.assertEqual((got['id'], got['tags'], got['discussions']['count']), (a['id'], ['x', 'y'], 0))
        tags = self.dispatch('tags.list', {})
        self.assertEqual({tag['name'] for tag in tags['tags']}, {'x', 'y', 'legacy'})
        self.assertEqual(tags['palette'], list(personal.PALETTE))

    def test_writes_need_the_user_message_and_summaries_carry_session_and_seq(self):
        note = self.capture('原话')
        with self.assertRaisesRegex(board.BoardError, '原始用户消息'):
            self.dispatch('note.annotate', {'note_id': note['id'], 'text': '结论'}, message_id='invented')
        result = self.dispatch('note.annotate', {'note_id': note['id'], 'text': '结论'})
        summary = result['result']['note']['summaries'][0]
        self.assertEqual((summary['text'], summary['source']), ('结论', {'type': 'assistant', 'session_id': self.sid, 'seq': 7}))
        self.assertEqual(result['source']['message_id'], 'user-1')
        self.assertTrue(self.dispatch('note.annotate', {'note_id': note['id'], 'text': '结论'})['duplicate'])
        self.assertTrue(self.dispatch('note.annotate', {'note_id': note['id'], 'text': '结论'}, request_id='retry-key')['duplicate'])
        self.assertEqual(len(self.get(note['id'])['summaries']), 1)
        version = self.get(note['id'])['version']
        for index, (action, data) in enumerate((
                ('note.pin', {'pinned': True}), ('note.snooze', {'until': later(30)}),
                ('note.archive', {}), ('note.restore', {}))):
            self.dispatch(action, {'note_id': note['id'], 'expected_version': version + index, **data}, request_id='w%d' % index)
        final = self.get(note['id'])
        self.assertEqual((final['state'], final['snoozed_until'], final['version']), ('open', later(30), version + 4))
        self.assertIsNotNone(final['pinned_at'])
        captured = self.dispatch('note.capture', {'text': '助手记下', 'title': '标题', 'tags': ['新']}, request_id='cap')
        self.assertEqual(captured['result']['note']['source']['verified_by'], 'session-journal')
        updated = self.dispatch('note.update', {'note_id': captured['result']['note']['id'], 'expected_version': 1,
                                                'title': '改标题'}, request_id='upd')
        self.assertEqual(updated['result']['note']['title'], '改标题')

    def test_assistant_merge_bulk_and_tag_actions_leave_no_receipt_when_refused(self):
        a, b = self.capture('甲', tags=['旧']), self.capture('乙', tags=['别的'])
        merged = self.dispatch('note.merge', {'primary_id': a['id'], 'note_ids': [b['id']],
                                              'expected_versions': {a['id']: 1, b['id']: 1}}, request_id='merge')
        self.assertEqual(merged['result']['note']['merged_from'], [b['id']])
        before = self.snapshot(a['id'], b['id'])
        with self.assertRaises(board.Conflict):
            self.dispatch('note.bulk', {'note_ids': [a['id']], 'expected_versions': {a['id']: 1}, 'op': 'archive'}, request_id='stale-bulk')
        with self.assertRaises(personal.TagMergeRequired):
            self.dispatch('tag.rename', {'name': '旧', 'new_name': '别的', 'expected_version': 1}, request_id='collide')
        self.assertEqual(self.snapshot(a['id'], b['id']), before)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM personal_action_receipts WHERE request_id IN ('stale-bulk','collide')").fetchone())
        self.dispatch('tag.create', {'name': '新标签', 'color': 'c1'}, request_id='create')
        self.dispatch('tag.recolor', {'name': '新标签', 'color': 'c2', 'expected_version': 1}, request_id='recolor')
        self.dispatch('tag.merge', {'from': ['旧'], 'into': '别的'}, request_id='tmerge')
        self.dispatch('tag.rename', {'name': '别的', 'new_name': '统一', 'expected_version': 1}, request_id='rename')
        self.assertEqual(self.get(a['id'])['tags'], ['统一'])
        self.dispatch('tag.remove', {'name': '新标签', 'expected_version': 2}, request_id='remove')
        self.assertEqual({tag['name'] for tag in personal.list_tags(self.conn)['tags']}, {'统一'})
        self.dispatch('note.bulk', {'note_ids': [a['id']], 'expected_versions': {a['id']: self.get(a['id'])['version']},
                                    'op': 'tag_add', 'value': '整理过'}, request_id='bulk')
        self.assertEqual(self.get(a['id'])['tags'], ['统一', '整理过'])

    def test_contract_actions_are_registered_and_described_for_cc_and_codex(self):
        writes = ['note.capture', 'note.update', 'note.annotate', 'note.archive', 'note.restore', 'note.pin', 'note.snooze',
                  'note.merge', 'note.bulk', 'tag.create', 'tag.rename', 'tag.merge', 'tag.recolor', 'tag.remove']
        reads = ['notes.list', 'notes.get', 'tags.list']
        self.assertTrue(set(writes) <= personal.WRITE_ACTIONS)
        self.assertTrue(set(reads) <= personal_actions.READ_ACTIONS)
        skill = (PLUGIN / 'skills' / 'assistant' / 'SKILL.md').read_text(encoding='utf-8')
        tool = next(spec for spec in codex_tools._SPECS if spec['name'] == 'crew_personal')['description']
        context = personal.assistant_context(self.conn, self.sid)
        for action in writes + reads:
            self.assertIn(action, skill, action)
            self.assertIn(action, tool, action)
        self.assertTrue(set(writes) <= set(context['write_actions']))
        self.assertIn('[Crew assistant]', context['instructions'])


class NotesHttpFixture(ShellFixture):
    request = test_chat_http.ChatHttpTests.request
    close_server = test_chat_http.ChatHttpTests.close_server

    def setUp(self):
        super().setUp()
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=lambda: [], claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: store.parse_iso(NOW), process_factory=self.process_factory)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = 'http://127.0.0.1:%d' % self.server.server_port

    def post(self, path, action, data, request_id):
        return self.request(path, {'action': action, 'data': data, 'request_id': request_id})

    def capture(self, request_id='idea', **data):
        code, value, _ = self.post('/api/personal/notes', 'capture', {'text': '健身打卡 App', 'title': '打卡',
                                   'tags': ['生活'], **data}, request_id)
        self.assertEqual(code, 200, value)
        return value['note']

    def discuss(self, sid, note, message_id):
        return self.request('/api/chat/send', {'session_id': sid, 'text': '帮我把这个想法想清楚',
                                               'message_id': message_id, 'note_id': note['id']})


class NotesWallHttpTests(NotesHttpFixture, unittest.TestCase):
    def test_note_and_tag_routes_follow_contract_and_rename_collision_is_409_with_merge_detail(self):
        note = self.capture()
        code, listing, _ = self.request('/api/personal/notes?include_archived=1')
        self.assertEqual((code, listing['stale_days'], listing['notes'][0]['id']), (200, 14, note['id']), listing)
        code, error, _ = self.request('/api/personal/notes', {'action': 'capture', 'request_id': 'flat', 'text': '旧格式'})
        self.assertEqual(code, 400, error)
        code, tags, _ = self.request('/api/personal/tags')
        self.assertEqual((code, [tag['name'] for tag in tags['tags']], len(tags['palette'])), (200, ['生活'], 10), tags)
        code, created, _ = self.post('/api/personal/tags', 'create', {'name': '运动', 'color': 'c4'}, 'tag-create')
        self.assertEqual(code, 200, created)
        code, conflict, _ = self.post('/api/personal/tags', 'rename', {'name': '生活', 'new_name': '运动', 'expected_version': 1}, 'tag-rename')
        self.assertEqual(code, 409, conflict)
        self.assertEqual((conflict['merge_required'], conflict['target'], conflict['affected']), (True, '运动', 1))
        self.assertIn('合并', conflict['error'])
        code, merged, _ = self.post('/api/personal/tags', 'merge', {'from': ['生活'], 'into': '运动'}, 'tag-merge')
        self.assertEqual((code, merged['affected']), (200, 1), merged)
        code, pinned, _ = self.post('/api/personal/notes', 'pin', {'note_id': note['id'], 'expected_version': 2, 'pinned': True}, 'pin')
        self.assertEqual(code, 200, pinned)
        code, stale, _ = self.post('/api/personal/notes', 'pin', {'note_id': note['id'], 'expected_version': 2, 'pinned': False}, 'pin-2')
        self.assertEqual(code, 409, stale)
        self.assertNotIn('merge_required', stale)
        self.assertEqual(self.processes, [])

    def test_discussing_a_note_injects_server_context_and_records_the_message_once(self):
        code, opened, _ = self.request('/api/assistant/open', {})
        self.assertEqual(code, 200, opened)
        sid = opened['session_id']
        note = self.capture()
        self.assertEqual(self.processes, [])
        code, error, _ = self.request('/api/chat/send', {'session_id': sid, 'text': '聊聊', 'note_id': 'missing'})
        self.assertEqual(code, 400, error)
        self.assertEqual(self.processes, [])
        code, sent, _ = self.discuss(sid, note, 'discuss-1')
        self.assertEqual(code, 200, sent)
        events = self.server.service.chat.history(sid)['events']
        message = next(event for event in events if event['type'] == 'message' and event['id'] == 'discuss-1')
        self.assertEqual(message['text'], '帮我把这个想法想清楚')
        delivered = self.processes[0].stdin.getvalue()
        self.assertIn('想法标识：' + note['id'], delivered)
        self.assertIn('原文：\\n健身打卡 App', delivered)
        self.assertEqual((sent['note']['stage'], sent['note']['discussions']['count'], sent['note']['discussions']['last_seq'],
                          sent['note']['discussions']['session_id']), ('discussed', 1, message['seq'], sid))
        self.assertIsNotNone(sent['note']['last_discussed_at'])
        code, again, _ = self.discuss(sid, note, 'discuss-1')
        self.assertEqual(code, 200, again)
        self.assertTrue(again['duplicate'])
        self.assertEqual(again['note']['discussions']['count'], 1)
        code, wrong, _ = self.request('/api/chat/send?project=demo', {'session_id': 's3', 'text': '聊聊', 'note_id': note['id']})
        self.assertEqual(code, 400, wrong)
        self.assertIn('全局助手', wrong['error'])


class CodexDiscussionTests(NotesHttpFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        settings.save(self.conn, {'default': {'runtime': 'codex', 'model': None, 'effort': None}})
        self.clients = []
        def client(**kwargs):
            self.clients.append(FakeClient(**kwargs))
            return self.clients[-1]
        for patch in (mock.patch('crew.settings.shutil.which', return_value='fake-codex'),
                      mock.patch('crew.codex_runtime._ObservedClient', side_effect=client)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_codex_assistant_receives_the_same_note_context(self):
        code, opened, _ = self.request('/api/assistant/open', {})
        self.assertEqual(code, 200, opened)
        sid = opened['session_id']
        note = self.capture()
        code, sent, _ = self.discuss(sid, note, 'codex-discuss')
        self.assertEqual(code, 200, sent)
        self.assertEqual(len(self.clients), 1)
        turn = next(params for method, params in self.clients[0].calls if method == 'turn/start')
        delivered = json.dumps(turn, ensure_ascii=False)
        self.assertIn('想法标识：' + note['id'], delivered)
        message = next(event for event in self.server.service.chat.history(sid)['events']
                       if event['type'] == 'message' and event['id'] == 'codex-discuss')
        self.assertEqual(message['text'], '帮我把这个想法想清楚')
        self.assertEqual((sent['note']['stage'], sent['note']['discussions']['last_seq']), ('discussed', message['seq']))
        self.clients[0].finish()


class NotesPortabilityTests(RuntimeFixture):
    def note(self, action, data, request_id):
        return personal.note_action(self.conn, action, data, request_id=request_id)

    def prepare(self):
        primary = self.note('capture', {'project': 'demo', 'text': '主想法', 'title': '主', 'tags': ['app']}, 'p')['note']
        child = self.note('capture', {'project': 'demo', 'text': '重复', 'tags': ['dup']}, 'c')['note']
        outside = self.note('capture', {'text': '全局主卡 GLOBAL_PRIVATE_NOTE'}, 'g')['note']
        orphan = self.note('capture', {'project': 'demo', 'text': '并入全局'}, 'o')['note']
        self.note('merge', {'primary_id': primary['id'], 'note_ids': [child['id']],
                            'expected_versions': {primary['id']: 1, child['id']: 1}}, 'm1')
        self.note('merge', {'primary_id': outside['id'], 'note_ids': [orphan['id']],
                            'expected_versions': {outside['id']: 1, orphan['id']: 1}}, 'm2')
        self.note('annotate', {'note_id': primary['id'], 'text': '结论'}, 'a')
        self.note('pin', {'note_id': primary['id'], 'expected_version': 3, 'pinned': True}, 'pin')
        personal.tag_action(self.conn, 'recolor', {'name': 'app', 'color': 'c9', 'expected_version': 1}, request_id='color')
        sid = personal.ensure_assistant(self.conn, self.home)['session_id']
        personal.record_discussion(self.conn, primary['id'], sid, 'user-message', 3)
        return primary, child, orphan

    def test_workspace_package_keeps_note_fields_and_merges_inside_the_workspace(self):
        from crew.storage_v2 import Repository, activation, legacy, packages
        primary, child, orphan = self.prepare()
        repo = Repository(self.root / 'portable')
        repo.ensure_local_identity()
        original = legacy.migrate_workspace(repo, self.home, 'demo')['workspace_id']
        package = self.root / 'notes.crewpack'
        with closing(repo.connect()) as conn:
            packages.export_workspace(conn, repo.objects, original, package)
            payload = '\n'.join(r[0] for r in conn.execute('SELECT data_json FROM workspace_feature_records'))
            self.assertNotIn('GLOBAL_PRIVATE_NOTE', payload)
        destination = Repository(self.root / 'destination')
        account, user = destination.ensure_local_identity()
        with closing(destination.connect()) as conn:
            imported = packages.import_package(conn, destination.objects, package, target_account_id=account, actor_user_id=user)
        local, root = self.root / 'activated-home', self.root / 'activated-project'
        root.mkdir()
        with mock.patch('subprocess.Popen', side_effect=AssertionError('Activation cannot run a model')):
            activation.activate_workspace(destination, imported['workspace_id'], local, root)
        with closing(store.connect(local)) as conn:
            notes = {note['original_text']: note for note in personal.list_notes(conn, include_archived=True)}
            self.assertEqual(set(notes), {'主想法', '重复', '并入全局'})
            restored = notes['主想法']
            self.assertEqual((restored['title'], restored['stage'], [item['text'] for item in restored['summaries']]),
                             ('主', 'discussed', ['结论']))
            self.assertIsNotNone(restored['pinned_at'])
            self.assertIsNotNone(restored['last_discussed_at'])
            self.assertEqual(notes['重复']['merged_into'], restored['id'])
            self.assertEqual(restored['merged_from'], [notes['重复']['id']])
            self.assertEqual((notes['并入全局']['merged_into'], notes['并入全局']['state']), (None, 'archived'))
            self.assertEqual(restored['discussions']['count'], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM personal_note_tags').fetchone()[0], 0)
            self.assertEqual({tag['name'] for tag in personal.list_tags(conn)['tags']}, {'app', 'dup'})

    def test_workspace_recovery_keeps_note_fields_and_leaves_tag_library_and_discussions_in_home(self):
        primary, child, orphan = self.prepare()
        target = self.root / 'recovered-home'
        workspace_recovery.import_workspace(self.home, target, 'demo')
        with closing(store.connect(target)) as conn:
            notes = {note['id']: note for note in personal.list_notes(conn, include_archived=True)}
            self.assertEqual(set(notes), {primary['id'], child['id'], orphan['id']})
            self.assertEqual((notes[primary['id']]['title'], notes[primary['id']]['stage']), ('主', 'discussed'))
            self.assertEqual(notes[child['id']]['merged_into'], primary['id'])
            self.assertEqual(notes[primary['id']]['summaries'][0]['text'], '结论')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM personal_note_discussions').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM personal_note_tags').fetchone()[0], 0)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])


if __name__ == '__main__':
    unittest.main()
