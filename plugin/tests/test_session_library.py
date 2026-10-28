"""Discovery and adoption use isolated transcript files and a fake service."""
import tests
import json
import unittest
from unittest import mock
from pathlib import Path

from crew import board, models, runtimes, session_library, session_ops
import test_vscode_service as fixtures
from test_vscode_service import identity, NOW


class SessionLibraryTests(unittest.TestCase):
    setUp = fixtures.VSCodeServiceTests.setUp
    hidden = fixtures.VSCodeServiceTests.hidden
    record = fixtures.VSCodeServiceTests.record
    register = fixtures.VSCodeServiceTests.register
    agent = fixtures.VSCodeServiceTests.agent
    refresh = fixtures.VSCodeServiceTests.refresh

    def rows(self, scope='project'):
        return session_library.listing(self.service, self.conn, 'alpha', scope)['items']

    def codex(self, number=20, cwd=None, source='cli', text='old Codex task', effort='high'):
        sid = identity(number)
        path = self.service.codex_root / '2026' / ('rollout-' + sid + '.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'type': 'response_item', 'payload': {
            'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': text}]}}) + '\n', encoding='utf-8')
        self.conn.execute('INSERT INTO codex_sessions(rollout_id,path,cwd,source,ended_at,first_user_text,model,effort) '
                          'VALUES(?,?,?,?,?,?,?,?)', (sid, str(path), str(cwd or self.alpha), source, NOW, text, 'gpt-native', effort))
        self.conn.commit()
        return sid, path

    def test_all_carriers_and_states_are_discoverable_without_launch(self):
        for number in range(1, 6):
            self.record(identity(number))
        self.register(identity(2), 'lead', role='leader')
        self.register(identity(3), 'free')
        self.register(identity(4), 'old')
        session_ops.archive(self.conn, identity(4), now=NOW)
        self.hidden([identity(5)])
        native, _ = self.codex()
        self.agents[:] = [self.agent(identity(1))]
        self.refresh()
        bykey = {row['key']: row for row in self.rows()}
        self.assertEqual(bykey['claude:' + identity(1)]['category'], 'available')
        self.assertTrue(bykey['claude:' + identity(1)]['running'])
        self.assertEqual(bykey['crew:' + identity(2)]['category'], 'team')
        self.assertEqual(bykey['crew:' + identity(3)]['category'], 'ungrouped')
        self.assertEqual(bykey['crew:' + identity(4)]['category'], 'archived')
        self.assertEqual(bykey['claude:' + identity(5)]['category'], 'archived')
        self.assertIsNone(bykey['codex:' + native]['running'])
        self.assertNotIn('_path', bykey['codex:' + native])
        self.service.chat.process_factory.assert_not_called()

    def test_scope_is_explicit_and_nested_workspace_wins(self):
        beta = self.alpha / 'nested'; beta.mkdir()
        board.init_project(self.conn, 'beta', beta, now=NOW)
        native, _ = self.codex(cwd=beta)
        self.assertFalse(self.rows())
        self.assertEqual([(r['native_id'], r['project']) for r in self.rows('all')], [(native, 'beta')])
        with self.assertRaisesRegex(board.BoardError, '会话库'):
            session_library.get(self.service, self.conn, 'alpha', 'codex:' + native)
        with self.assertRaises(board.BoardError):
            self.rows('every-path-on-disk')

    def test_registered_membership_is_preserved_after_directory_change(self):
        native, _ = self.codex()
        self.register(identity(1), 'member')
        runtimes.set(self.conn, identity(1), 'codex', native)
        other = self.root / 'other'; other.mkdir()
        board.init_project(self.conn, 'other', other, now=NOW)
        self.conn.execute('UPDATE codex_sessions SET cwd=? WHERE rollout_id=?', (str(other), native)); self.conn.commit()
        rows = self.rows()
        self.assertEqual([(r['key'], r['project']) for r in rows], [('crew:' + identity(1), 'alpha')])

    def test_native_id_less_identity_stays_visible_with_correct_archive(self):
        self.register(identity(1), 'old')
        runtimes.set(self.conn, identity(1), 'codex', None)
        session_ops.archive(self.conn, identity(1), now=NOW)
        row = self.rows()[0]
        self.assertEqual((row['key'], row['category'], row['native_id']), ('crew:' + identity(1), 'archived', None))
        self.assertFalse(row['adoptable'])

    def test_codex_join_preserves_native_thread_and_only_queues_one_notice(self):
        native, _ = self.codex()
        self.register(identity(1), 'lead', role='leader')
        joined = self.service.post('/api/session-library/adopt', {'key': 'codex:' + native, 'name': 'native-member', 'group': 'lead'}, 'alpha')
        sid = joined['session_id']
        self.assertNotEqual(sid, native)
        self.assertEqual(runtimes.get(self.conn, sid), {'runtime': 'codex', 'native_id': native})
        self.assertEqual(models.get(self.conn, sid), {'model': 'gpt-native', 'effort': 'high'})
        self.assertEqual(board.session(self.conn, sid)['group_name'], 'lead')
        self.assertEqual(self.service.chat.path(sid).read_text(), '')
        preview = session_library.preview(self.service, self.conn, 'alpha', 'crew:' + sid)
        self.assertEqual(preview['messages'][0]['text'], 'old Codex task')
        self.assertEqual(len(board.pending_notices(self.conn, identity(1))), 1)
        with self.assertRaises(board.BoardError):
            session_library.adopt(self.service, self.conn, 'alpha', 'codex:' + native, 'native-member', 'lead', NOW)
        self.assertEqual(len(board.pending_notices(self.conn, identity(1))), 1)
        self.service.chat.process_factory.assert_not_called()

    def test_claude_join_reuses_existing_metadata_and_notices(self):
        native = identity(1); self.record(native)
        self.register(identity(2), 'lead', role='leader')
        self.refresh()
        session_library.adopt(self.service, self.conn, 'alpha', 'claude:' + native, 'old-work', 'lead', NOW)
        self.assertEqual(board.session(self.conn, native)['group_name'], 'lead')
        self.assertEqual(len(board.pending_notices(self.conn, identity(2))), 1)

    def test_archives_and_background_runs_are_readable_but_not_joinable(self):
        native, path = self.codex(source='{"subagent":{"parent_thread_id":"root"}}')
        self.assertEqual(self.rows()[0]['category'], 'background')
        preview = session_library.preview(self.service, self.conn, 'alpha', 'codex:' + native)
        self.assertEqual(preview['messages'][0]['text'], 'old Codex task')
        with self.assertRaises(board.Conflict):
            session_library.adopt(self.service, self.conn, 'alpha', 'codex:' + native, 'member', 'lead', NOW)

    def test_preview_contains_only_visible_text_not_reasoning_or_tool_results(self):
        native, path = self.codex()
        with path.open('a', encoding='utf-8') as stream:
            for payload in [
                {'type':'reasoning','summary':[{'text':'private thought'}]},
                {'type':'function_call','name':'exec','arguments':'secret'},
                {'type':'message','role':'user','content':[{'type':'input_text','text':'# AGENTS.md instructions for repo'}]},
                {'type':'message','role':'assistant','content':[{'type':'output_text','text':'<script>text only</script>'}]}]:
                stream.write(json.dumps({'type':'response_item','payload':payload}) + '\n')
        result = session_library.preview(self.service, self.conn, 'alpha', 'codex:' + native)
        self.assertEqual([m['text'] for m in result['messages']], ['old Codex task', '<script>text only</script>'])

    def test_preview_never_uses_an_index_path_outside_native_storage(self):
        native, _ = self.codex(); outside = self.root / 'private.txt'; outside.write_text('secret')
        self.conn.execute('UPDATE codex_sessions SET path=? WHERE rollout_id=?', (str(outside), native)); self.conn.commit()
        self.assertEqual(session_library.preview(self.service, self.conn, 'alpha', 'codex:' + native)['messages'], [])

    def test_invalid_join_rolls_back_identity_and_does_not_launch(self):
        native, _ = self.codex(effort='bogus')
        self.register(identity(1), 'lead', role='leader')
        with self.assertRaises(ValueError):
            session_library.adopt(self.service, self.conn, 'alpha', 'codex:' + native, 'member', 'lead', NOW)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 1)
        self.assertFalse(board.pending_notices(self.conn, identity(1)))

    def test_old_binding_is_not_presented_as_an_unadopted_person(self):
        native, _ = self.codex()
        self.register(identity(1), 'lead', role='leader')
        runtimes.set(self.conn, identity(1), 'codex', native)
        runtimes.set(self.conn, identity(1), 'claude', identity(1))
        self.assertEqual([r['key'] for r in self.rows()], ['crew:' + identity(1)])

    def test_input_log_failure_rolls_back_join_and_notice(self):
        native, _ = self.codex()
        self.register(identity(1), 'lead', role='leader')
        with mock.patch.object(Path, 'touch', side_effect=OSError('isolated log write failure')):
            with self.assertRaisesRegex(OSError, 'isolated log write failure'):
                session_library.adopt(self.service, self.conn, 'alpha', 'codex:' + native, 'member', 'lead', NOW)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 1)
        self.assertFalse(board.pending_notices(self.conn, identity(1)))
