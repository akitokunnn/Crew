import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from contextlib import closing
from unittest import mock

from crew import board, store
from crew.storage_cleanup import CleanupError, DAY, Manager


class StorageCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='crew-storage-test-')
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / 'home'
        self.home.mkdir()
        self.now = 1800000000.0
        self.manager = Manager(self.home, Path(__file__).parents[1], clock=lambda: self.now)
        self.addCleanup(self.manager.close)
        with closing(store.connect(self.home)) as conn:
            conn.execute("INSERT INTO projects(name,root,created_at) VALUES('alpha',?,'2026-01-01')", (str(self.home.parent / 'project'),))
            conn.execute("INSERT INTO sessions(session_id,project,status,started_at,ended_at) VALUES('old','alpha','closed','2026-01-01','2026-01-02')")

    def write(self, relative, value=b'old data', age=40):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value.encode() if isinstance(value, str) else value)
        os.utime(path, (self.now - age * DAY, self.now - age * DAY))
        return path

    def backup(self, number, age=40):
        return self.write('backups/update-20260101-1200%02d-%06x/crew.db' % (number, number), age=age + number)

    def category(self, identity):
        return next(value for value in self.manager.snapshot()['categories'] if value['id'] == identity)

    def orphan(self, name='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-1.png', journal=''):
        self.write('chat/old.jsonl', journal)
        return self.write('chat/files/old/' + name)

    def test_inventory_precisely_sums_home_and_protects_history(self):
        self.write('chat/old.jsonl', '{"type":"message","images":["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-1.png"]}\n')
        self.write('chat/files/old/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-1.png')
        self.write('chat/files/old/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-1.png')
        self.write('desktop/backend/abc/.ready', 'abc')
        self.write('storage-v2/objects/raw', b'x' * 5)
        self.write('reports/final.md')
        self.write('desktop/logs/service.log')
        self.write('desktop/logs/startup-123.log')
        result = self.manager.snapshot()
        total = sum(path.stat().st_size for path in self.home.rglob('*') if path.is_file())
        self.assertEqual(result['total_bytes'], total)
        self.assertEqual(result['reclaimable_bytes'], 8)
        self.assertEqual(result['recyclable_bytes'], 8)
        self.assertEqual(self.category('backend_cache')['candidate_count'], 0)
        self.assertEqual(self.category('attachments')['candidate_count'], 1)
        self.assertEqual(result['workspaces'][0]['file_count'], 3)

    def test_whitelist_and_real_freed_bytes(self):
        old = self.write('desktop/logs/startup-1.log', b'123')
        current = self.write('desktop/logs/startup-2.log', age=1)
        service = self.write('desktop/logs/service.log')
        unknown = self.write('exports/my-work.crewpack')
        export = self.write('exports/' + 'a' * 32 + '.crewpack', b'12')
        preview = self.manager.preview(['logs', 'exports'])
        self.assertEqual(preview['freed_bytes'], 3)
        self.assertEqual(preview['recycled_bytes'], 2)
        result = self.manager.clean(preview['preview_id'])
        self.assertEqual(result['freed_bytes'], 3)
        self.assertEqual(result['recycled_bytes'], 2)
        self.assertFalse(old.exists()); self.assertFalse(export.exists())
        self.assertTrue(current.exists()); self.assertTrue(service.exists()); self.assertTrue(unknown.exists())

    def test_empty_trash_preserves_completed_and_restored_history(self):
        self.write('desktop/logs/startup-21.log')
        logged = self.manager.clean(self.manager.preview(['logs'])['preview_id'])
        self.orphan()
        recycled = self.manager.clean(self.manager.preview(['attachments'])['preview_id'])
        self.manager.restore(recycled['batch_id'])
        self.orphan('bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-1.png')
        newest = self.manager.clean(self.manager.preview(['attachments'])['preview_id'])
        self.manager.empty_trash()
        history = {item['batch_id']: item for item in self.manager.snapshot()['history']}
        self.assertEqual(history[logged['batch_id']]['status'], 'completed')
        self.assertEqual(history[recycled['batch_id']]['status'], 'restored')
        self.assertEqual(history[newest['batch_id']]['status'], 'emptied')

    def test_registered_project_inside_home_is_never_scanned_or_cleaned(self):
        path = self.write('desktop/logs/startup-99.log', b'project-owned')
        with closing(store.connect(self.home)) as conn:
            conn.execute('UPDATE projects SET root=? WHERE name=?', (str(path.parent), 'alpha'))
        result = self.manager.snapshot()
        self.assertEqual(next(item for item in result['categories'] if item['id'] == 'logs')['candidate_count'], 0)
        self.assertTrue(any('未扫描位于 HOME 内的工作目录' in value for value in result['warnings']))
        self.assertEqual(self.manager.preview(['logs'])['file_count'], 0)
        self.assertEqual(path.read_bytes(), b'project-owned')

    def test_project_registered_after_preview_protects_the_previously_eligible_file(self):
        path = self.write('desktop/logs/startup-99.log')
        preview = self.manager.preview(['logs'])
        with closing(store.connect(self.home)) as conn:
            conn.execute('UPDATE projects SET root=? WHERE name=?', (str(path.parent), 'alpha'))
        result = self.manager.clean(preview['preview_id'])
        self.assertEqual(result['freed_bytes'], 0)
        self.assertTrue(result['errors'])
        self.assertTrue(path.exists())

    def test_changed_file_and_policy_change_are_skipped(self):
        path = self.write('desktop/logs/startup-1.log')
        preview = self.manager.preview(['logs'])
        path.write_bytes(b'changed')
        result = self.manager.clean(preview['preview_id'])
        self.assertEqual(result['freed_bytes'], 0)
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(result['errors'])
        self.assertTrue(path.exists())
        self.write('desktop/logs/startup-2.log')
        preview = self.manager.preview(['logs'])
        self.manager.save_policy({'retention_days': 90})
        self.assertEqual(self.manager.clean(preview['preview_id'])['freed_bytes'], 0)

    def test_preview_expiry_invalid_categories_and_policy_validation(self):
        preview = self.manager.preview(['logs'])
        self.now += 601
        with self.assertRaises(board.Conflict):
            self.manager.clean(preview['preview_id'])
        for value in (['../'], 'logs', [1]):
            with self.assertRaises(ValueError):
                self.manager.preview(value)
        for value in ({'retention_days': True}, {'retention_days': 1}, {'keep_backups': 1}, {'path': 'x'}, {'automatic_enabled': 'yes'}):
            with self.assertRaises(ValueError):
                self.manager.save_policy(value)

    def test_backups_respect_age_latest_count_and_unknown_files(self):
        paths = [self.backup(index) for index in range(5)]
        unknown = self.write('backups/user/crew.db')
        neighbor = self.write(str(paths[-1].parent.relative_to(self.home) / 'my-notes.txt'))
        recent = self.write('backups/update-20260102-120000-abcdef/crew.db', age=2)
        preview = self.manager.preview(['backups'])
        self.assertEqual(preview['file_count'], 2)
        result = self.manager.clean(preview['preview_id'])
        self.assertEqual(result['freed_bytes'], 0)
        self.assertEqual(result['recycled_bytes'], 16)
        self.assertTrue(unknown.exists()); self.assertTrue(neighbor.exists()); self.assertTrue(recent.exists())
        self.assertTrue(paths[0].exists()); self.assertTrue(paths[1].exists()); self.assertTrue(paths[-1].exists())
        restored = self.manager.restore(result['batch_id'])
        self.assertEqual(restored['restored_files'], 2)
        self.assertTrue(all(path.exists() for path in paths))

    def test_orphan_references_missing_malformed_active_and_documents(self):
        orphan = self.orphan()
        used = self.write('chat/files/old/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-1.png')
        self.write('chat/old.jsonl', '{"type":"message","images":["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-1.png"]}\n')
        preview = self.manager.preview(['attachments'])
        self.assertEqual(preview['file_count'], 1)
        self.write('chat/old.jsonl', '{"type":"message","images":["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-1.png","aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-1.png"]}\n')
        result = self.manager.clean(preview['preview_id'])
        self.assertEqual(result['recycled_bytes'], 0)
        self.assertTrue(orphan.exists()); self.assertTrue(used.exists())
        (self.home / 'chat/old.jsonl').unlink()
        self.assertEqual(self.manager.preview(['attachments'])['file_count'], 0)
        self.write('chat/old.jsonl', '{bad json')
        self.assertEqual(self.manager.preview(['attachments'])['file_count'], 0)
        self.write('chat/old.jsonl', '')
        self.manager.active_keys = lambda: {'old'}
        self.assertEqual(self.manager.preview(['attachments'])['file_count'], 0)
        self.manager.active_keys = lambda: ()
        with closing(store.connect(self.home)) as conn:
            conn.execute("INSERT INTO documents(project,session_id,submitter,path,path_key,title,kind,source,created_at,updated_at) VALUES('alpha','old','test',?,?, 'old','other','manual','a','a')", (str(orphan), str(orphan)))
        self.assertEqual(self.manager.preview(['attachments'])['file_count'], 1)

    def test_persistent_idempotence_and_partial_failure(self):
        first = self.write('desktop/logs/startup-1.log')
        second = self.write('desktop/logs/startup-2.log')
        preview = self.manager.preview(['logs'])
        mutate = self.manager._mutate
        def fail_one(source, *args):
            if source == second:
                raise PermissionError('test sharing violation')
            return mutate(source, *args)
        with mock.patch.object(self.manager, '_mutate', side_effect=fail_one):
            result = self.manager.clean(preview['preview_id'])
        self.assertEqual(result['file_count'], 1)
        self.assertEqual(result['errors'][0]['error'], 'test sharing violation')
        self.assertFalse(first.exists()); self.assertTrue(second.exists())
        replacement = self.write('desktop/logs/startup-1.log', b'new file')
        other = Manager(self.home, Path(__file__).parents[1], clock=lambda: self.now)
        self.assertEqual(other.clean(preview['preview_id']), result)
        self.assertTrue(replacement.exists()); self.assertTrue(second.exists())

    def test_restore_refuses_overwrite_then_retries_only_unfinished(self):
        path = self.orphan()
        result = self.manager.clean(self.manager.preview(['attachments'])['preview_id'])
        self.assertFalse(path.exists())
        path.write_bytes(b'user replacement')
        conflict = self.manager.restore(result['batch_id'])
        self.assertTrue(conflict['errors'])
        self.assertEqual(path.read_bytes(), b'user replacement')
        path.unlink()
        restored = self.manager.restore(result['batch_id'])
        self.assertEqual(restored['restored_files'], 1)
        self.assertEqual(path.read_bytes(), b'old data')
        self.assertEqual(self.manager.restore(result['batch_id']), restored)

    def test_empty_and_expired_recycle_release_space_automatic_does_not_move_backups(self):
        self.orphan()
        batch = self.manager.clean(self.manager.preview(['attachments'])['preview_id'])
        self.assertEqual(batch['recycled_bytes'], 8)
        self.assertEqual(self.manager.empty_trash(batch['batch_id'])['freed_bytes'], 8)
        self.assertEqual(self.manager.empty_trash(batch['batch_id'])['freed_bytes'], 0)
        self.orphan('cccccccccccccccccccccccccccccccc-1.png')
        batch = self.manager.clean(self.manager.preview(['attachments'])['preview_id'])
        [self.backup(index) for index in range(5)]
        self.now += 8 * DAY
        self.write('desktop/logs/startup-7.log')
        result = self.manager.maintain()
        self.assertEqual(result['freed_bytes'], 16)
        self.assertEqual(self.category('backups')['file_count'], 5)
        self.assertEqual(self.category('trash')['bytes'], 0)

    def test_disabled_maintenance_and_datetime_clock(self):
        path = self.write('desktop/logs/startup-1.log')
        self.manager.clock = lambda: dt.datetime.fromtimestamp(self.now, dt.timezone.utc)
        self.manager.save_policy({'automatic_enabled': False})
        self.assertEqual(self.manager.maintain()['status'], 'disabled')
        self.assertTrue(path.exists())

    def test_disabling_automatic_during_preview_prevents_new_deletions(self):
        path = self.write('desktop/logs/startup-1.log')
        original = self.manager.preview
        def disable(categories):
            self.manager.save_policy({'automatic_enabled': False})
            return original(categories)
        with mock.patch.object(self.manager, 'preview', side_effect=disable):
            result = self.manager.maintain()
        self.assertEqual(result['freed_bytes'], 0)
        self.assertTrue(path.exists())

    def test_exports_and_unknown_attachment_files_are_never_automatic(self):
        export = self.write('exports/' + 'a' * 32 + '.crewpack')
        note = self.write('chat/files/old/user-notes.txt')
        self.write('chat/old.jsonl', '')
        self.assertEqual(self.manager.maintain()['freed_bytes'], 0)
        self.assertTrue(export.exists()); self.assertTrue(note.exists())
        self.assertEqual(self.category('attachments')['candidate_count'], 0)
        self.assertFalse(self.category('exports')['default_selected'])

    def test_pending_plan_crash_is_partial_and_not_reexecuted(self):
        path = self.write('desktop/logs/startup-1.log')
        preview = self.manager.preview(['logs'])
        original = self.manager._save_batch
        def crash(batch, item=None):
            original(batch, item)
            if item is None and batch['status'] == 'running':
                raise KeyboardInterrupt()
        with mock.patch.object(self.manager, '_save_batch', side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):
                self.manager.clean(preview['preview_id'])
        other = Manager(self.home, Path(__file__).parents[1], clock=lambda: self.now)
        history = other.snapshot()['history'][0]
        self.assertEqual(history['status'], 'partial')
        self.assertTrue(history['errors'])
        retry = other.clean(preview['preview_id'])
        self.assertEqual(retry['file_count'], 0)
        self.assertFalse(retry['ok'])
        self.assertTrue(path.exists())

    def test_recycle_action_errors_persist_and_retry_does_not_repeat_success(self):
        self.orphan()
        result = self.manager.clean(self.manager.preview(['attachments'])['preview_id'])
        original = self.manager._mutate
        def fail_trash(source, *args):
            if 'trash' in source.parts:
                raise PermissionError('still in use')
            return original(source, *args)
        with mock.patch.object(self.manager, '_mutate', side_effect=fail_trash):
            failed = self.manager.empty_trash(result['batch_id'])
        self.assertEqual(failed['errors'][0]['error'], 'still in use')
        other = Manager(self.home, Path(__file__).parents[1], clock=lambda: self.now)
        self.assertEqual(other.snapshot()['history'][0]['errors'][0]['error'], 'still in use')
        self.assertEqual(other.empty_trash(result['batch_id'])['freed_bytes'], 8)
        self.assertEqual(other.empty_trash(result['batch_id'])['freed_bytes'], 0)

    def test_batch_writes_scale_linearly_for_many_tiny_files(self):
        for index in range(200):
            self.write('desktop/logs/startup-%d.log' % index, b'abc')
        preview = self.manager.preview(['logs'])
        written, original = [], self.manager._write
        def record(path, value):
            written.append(len(json.dumps(value, ensure_ascii=False).encode('utf-8')))
            return original(path, value)
        with mock.patch.object(self.manager, '_write', side_effect=record):
            result = self.manager.clean(preview['preview_id'])
        self.assertEqual(result['freed_bytes'], 600)
        self.assertLessEqual(len(written), 4)
        self.assertLess(sum(written), 400000)
        self.assertEqual(self.manager.snapshot()['history'][0]['file_count'], 200)

    def test_hardlinked_candidates_are_protected(self):
        path = self.write('desktop/logs/startup-1.log')
        linked = self.home / 'other-copy'
        os.link(path, linked)
        self.assertEqual(self.manager.preview(['logs'])['file_count'], 0)
        self.assertTrue(path.exists()); self.assertTrue(linked.exists())

    def test_close_waits_for_maintenance_and_prevents_subsequent_mutations(self):
        path = self.write('desktop/logs/startup-1.log')
        entered, release = threading.Event(), threading.Event()
        original = self.manager._inventory
        failures = []
        def paused_inventory():
            entered.set()
            release.wait(5)
            return original()
        def worker():
            try:
                self.manager.maintain()
            except Exception as exc:
                failures.append(exc)
        with mock.patch.object(self.manager, '_inventory', side_effect=paused_inventory):
            thread = threading.Thread(target=worker)
            self.manager._thread = thread
            thread.start()
            self.assertTrue(entered.wait(5))
            closed = threading.Thread(target=self.manager.close)
            closed.start()
            self.assertTrue(self.manager._stop.wait(5))
            self.assertTrue(closed.is_alive())
            release.set()
            closed.join(5)
            self.assertFalse(closed.is_alive())
        self.assertFalse(failures)
        self.assertFalse(thread.is_alive())
        self.assertTrue(path.exists())

    def test_compaction_crash_does_not_replay_old_journal_or_duplicate_errors(self):
        first = self.write('desktop/logs/startup-1.log')
        second = self.write('desktop/logs/startup-2.log')
        preview = self.manager.preview(['logs'])
        original = self.manager._mutate
        def crash_compaction(source, *args):
            if source == second:
                raise PermissionError('source busy')
            if source.suffix == '.events':
                raise KeyboardInterrupt()
            return original(source, *args)
        with mock.patch.object(self.manager, '_mutate', side_effect=crash_compaction):
            with self.assertRaises(KeyboardInterrupt):
                self.manager.clean(preview['preview_id'])
        self.assertFalse(first.exists()); self.assertTrue(second.exists())
        other = Manager(self.home, Path(__file__).parents[1], clock=lambda: self.now)
        result = other.clean(preview['preview_id'])
        self.assertEqual(result['freed_bytes'], 8)
        self.assertEqual(len(result['errors']), 1)
        self.assertEqual(other.snapshot()['history'][0]['status'], 'partial')

    def test_truncated_append_is_reconciled_before_any_new_operation(self):
        path = self.orphan()
        preview = self.manager.preview(['attachments'])
        original = self.manager._mutate
        def crash(source, *args):
            original(source, *args)
            raise KeyboardInterrupt()
        with mock.patch.object(self.manager, '_mutate', side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):
                self.manager.clean(preview['preview_id'])
        events = next((self.manager.directory / 'batches').glob('*.events'))
        with events.open('ab') as stream:
            stream.write(b'{"incomplete')
        other = Manager(self.home, Path(__file__).parents[1], clock=lambda: self.now)
        batch = other.snapshot()['history'][0]
        self.assertEqual(batch['trash_bytes'], 8)
        self.assertEqual(other.restore(batch['batch_id'])['restored_files'], 1)
        self.assertTrue(path.exists())

    def test_corrupt_manifest_cannot_escape_home_or_redirect_trash_token(self):
        self.orphan()
        result = self.manager.clean(self.manager.preview(['attachments'])['preview_id'])
        manifest = self.manager._manifest_path(result['batch_id'])
        original = json.loads(manifest.read_text(encoding='utf-8'))
        for field, value in (('path', '../../outside/file'), ('path', '/absolute/file'), ('path', 'C:/outside/file'),
                             ('token', '../../../crew.db'), ('token', 'C:/outside/file')):
            changed = json.loads(json.dumps(original))
            changed['entries'][0][field] = value
            manifest.write_text(json.dumps(changed), encoding='utf-8')
            for action in (lambda: self.manager.restore(result['batch_id']), lambda: self.manager.empty_trash(result['batch_id'])):
                with self.assertRaises(CleanupError):
                    action()
        manifest.write_text(json.dumps(original), encoding='utf-8')
        self.assertEqual(self.manager.restore(result['batch_id'])['restored_files'], 1)

    def test_lexical_dotdot_is_normalized_before_home_containment(self):
        with self.assertRaises(CleanupError):
            self.manager._check_path(self.home / '..' / 'outside', missing=True)

    def test_alias_home_does_not_break_construction_but_refuses_cleanup(self):
        alias = self.home.parent / 'alias'
        if os.name == 'nt':
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(self.home)], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                self.skipTest('junction creation unavailable')
        else:
            alias.symlink_to(self.home, target_is_directory=True)
        other = Manager(alias, Path(__file__).parents[1], clock=lambda: self.now)
        with self.assertRaises(CleanupError):
            other.snapshot()
        self.assertFalse((self.home / 'desktop/storage-management').exists())

    def test_management_lock_and_append_refuse_hardlinked_metadata(self):
        self.manager.snapshot()
        outside = self.home.parent / 'outside-metadata'
        outside.write_bytes(b'keep this exact content')
        lock = self.manager.directory / 'management.lock'
        lock.unlink()
        os.link(outside, lock)
        with self.assertRaises(CleanupError):
            self.manager.snapshot()
        self.assertEqual(outside.read_bytes(), b'keep this exact content')
        lock.unlink()
        events = self.manager.directory / 'probe.events'
        os.link(outside, events)
        with self.assertRaises(CleanupError):
            with self.manager._metadata_stream(events, append=True) as stream:
                stream.write(b'never appended')
        self.assertEqual(outside.read_bytes(), b'keep this exact content')

    def test_metadata_swap_after_path_check_does_not_write_through_hardlink(self):
        self.manager.snapshot()
        outside = self.home.parent / 'outside-swap'
        outside.write_bytes(b'unchanged')
        events = self.manager.directory / 'probe.events'
        original = self.manager._check_path
        replaced = False
        def swap(path, **options):
            nonlocal replaced
            value = original(path, **options)
            if path == events and options.get('missing') and not replaced:
                os.link(outside, events)
                replaced = True
            return value
        with mock.patch.object(self.manager, '_check_path', side_effect=swap):
            with self.assertRaises(CleanupError):
                with self.manager._metadata_stream(events, append=True) as stream:
                    stream.write(b'never appended')
        self.assertTrue(replaced)
        self.assertEqual(outside.read_bytes(), b'unchanged')

    def test_metadata_leaf_symlink_swap_is_not_followed(self):
        self.manager.snapshot()
        outside = self.home.parent / 'outside-symlink'
        outside.write_bytes(b'outside must stay unchanged')
        probe = self.home.parent / 'symlink-capability'
        try:
            probe.symlink_to(outside)
        except OSError as exc:
            self.skipTest('file symlinks unavailable: ' + str(exc))
        probe.unlink()
        events = self.manager.directory / 'probe.events'
        original = self.manager._check_path
        replaced = False
        def swap(path, **options):
            nonlocal replaced
            value = original(path, **options)
            if path == events and options.get('missing') and not replaced:
                events.symlink_to(outside)
                replaced = True
            return value
        with mock.patch.object(self.manager, '_check_path', side_effect=swap):
            with self.assertRaises((CleanupError, OSError)):
                with self.manager._metadata_stream(events, append=True) as stream:
                    stream.write(b'never appended')
        self.assertTrue(replaced)
        self.assertEqual(outside.read_bytes(), b'outside must stay unchanged')

    def test_management_lock_remains_exclusive_between_manager_instances(self):
        self.manager.snapshot()
        other = Manager(self.home, Path(__file__).parents[1], clock=lambda: self.now)
        with self.manager._transaction():
            with self.assertRaises(CleanupError):
                other.snapshot()
        self.assertIn('categories', other.snapshot())

    def test_history_and_previews_remain_bounded(self):
        with mock.patch('crew.storage_cleanup.MAX_BATCHES', 3):
            first = None
            for index in range(6):
                self.write('desktop/logs/startup-%d.log' % index)
                preview = self.manager.preview(['logs'])
                result = self.manager.clean(preview['preview_id'])
                if first is None:
                    first = (preview, result)
            self.assertLessEqual(len(self.manager.snapshot()['history']), 3)
            self.assertEqual(self.manager.clean(first[0]['preview_id']), first[1])
        for index in range(15):
            self.manager.preview(['logs'])
        state = json.loads((self.manager.directory / 'state.json').read_text(encoding='utf-8'))
        self.assertEqual(len(state['previews']), 10)

    def test_crash_after_move_is_reconciled_without_repeating(self):
        path = self.orphan()
        preview = self.manager.preview(['attachments'])
        mutate = self.manager._mutate
        def crash(source, *args):
            mutate(source, *args)
            raise KeyboardInterrupt()
        with mock.patch.object(self.manager, '_mutate', side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):
                self.manager.clean(preview['preview_id'])
        self.assertFalse(path.exists())
        other = Manager(self.home, Path(__file__).parents[1], clock=lambda: self.now)
        self.assertEqual(other.snapshot()['trash_bytes'], 8)
        replayed = other.clean(preview['preview_id'])
        self.assertEqual(replayed['recycled_bytes'], 8)
        self.assertEqual(other.restore(replayed['batch_id'])['restored_files'], 1)

    def test_link_escape_during_scan_and_after_preview(self):
        outside = self.home.parent / 'outside'
        outside.mkdir()
        target = outside / 'startup-9.log'
        target.write_bytes(b'outside')
        link = self.home / 'linked'
        if os.name == 'nt':
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                self.skipTest('junction creation unavailable')
        else:
            link.symlink_to(outside, target_is_directory=True)
        self.assertTrue(self.manager.snapshot()['warnings'])
        path = self.write('desktop/logs/startup-9.log')
        preview = self.manager.preview(['logs'])
        path.unlink()
        directory = path.parent
        directory.rmdir()
        if os.name == 'nt':
            subprocess.run(['cmd', '/c', 'mklink', '/J', str(directory), str(outside)], check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            directory.symlink_to(outside, target_is_directory=True)
        result = self.manager.clean(preview['preview_id'])
        self.assertEqual(result['freed_bytes'], 0)
        self.assertEqual(target.read_bytes(), b'outside')

    def test_file_identity_replacement_with_preserved_size_mtime_is_skipped(self):
        path = self.write('desktop/logs/startup-1.log')
        info = path.stat()
        preview = self.manager.preview(['logs'])
        replacement = self.home / 'replacement'
        replacement.write_bytes(b'old data')
        os.utime(replacement, ns=(info.st_atime_ns, info.st_mtime_ns))
        os.replace(replacement, path)
        self.assertEqual(self.manager.clean(preview['preview_id'])['freed_bytes'], 0)
        self.assertTrue(path.exists())

    def test_corrupt_state_is_preserved_and_fails_explicitly(self):
        self.manager.snapshot()
        path = self.home / 'desktop/storage-management/state.json'
        path.write_text('broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.manager.snapshot()
        self.assertEqual(path.read_text(encoding='utf-8'), 'broken')


if __name__ == '__main__':
    unittest.main()
