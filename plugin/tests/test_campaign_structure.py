"""Board versions, rollback, restructure plans and leader merge / retire (docs/ASSISTANT_CAMPAIGN_DESIGN.md)."""
import tests  # Install the production-service connection guard.
from contextlib import closing
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from crew import board, change_feed, domain_versions, domains, goals, personal, session_ops, store, web
from crew.ops import executor, registry
from crew.ops.errors import OpError
from ops_helpers import ai_call
from test_shell import NOW, PLUGIN, ShellFixture


def create(part_id, name, parent=None, x=0, y=0):
    return {'op': 'create', 'id': part_id, 'name': name, 'parent_id': parent, 'x': x, 'y': y}


class BoardVersionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='crew-versions-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.conn = store.connect(self.base / 'home')
        self.addCleanup(self.conn.close)
        (self.base / 'demo').mkdir()
        board.init_project(self.conn, 'demo', self.base / 'demo', now=NOW)
        for sid, name in (('cam', '相机'), ('depth', '深度'), ('vision', '视觉')):
            board.join(self.conn, sid, 'demo', name, self.base / 'demo', self.base / 'demo', 'main', role='leader', now=NOW)

    def apply(self, *ops):
        return domains.apply(self.conn, 'demo', list(ops), now=NOW)

    def listing(self):
        return domain_versions.listing(self.conn, 'demo')

    def tree(self):
        self.apply(create('sense01', '感知', None, 10, 20), create('sight01', '视觉', 'sense01', 30, 40),
                   {'op': 'assign', 'session_id': 'vision', 'domain_id': 'sight01'},
                   {'op': 'lead', 'session_id': 'vision', 'lead': True})

    def test_structural_batches_save_versions_after_a_baseline_and_layout_does_not(self):
        self.assertEqual(self.listing()['versions'], [])
        self.tree()
        versions = self.listing()['versions']
        self.assertEqual([(v['number'], v['source'], v['parts'], v['leaders']) for v in versions],
                         [(2, 'edit', 2, 1), (1, 'baseline', 0, 0)])
        self.assertEqual(versions[0]['note'], '新建 part 2 · leader 换位置 1 · 负责人 1')
        self.assertEqual((versions[0]['board_version'], versions[1]['board_version']), (1, 0))
        self.apply({'op': 'move', 'items': [{'id': 'sense01', 'x': 500, 'y': 500}]},
                   {'op': 'collapse', 'id': 'sense01', 'collapsed': True}, {'op': 'direction', 'direction': 'LR'})
        self.assertEqual(len(self.listing()['versions']), 2, 'layout alone is not a version')
        self.assertTrue(self.listing()['latest_matches'])
        self.apply({'op': 'rename', 'id': 'sight01', 'name': '视觉组'})
        meta, state = domain_versions.get(self.conn, 'demo', 3)
        self.assertEqual((meta['source_label'], meta['note']), ('手动编辑', '改名 1'))
        self.assertEqual({p['id']: p['name'] for p in state['parts']}, {'sense01': '感知', 'sight01': '视觉组'})
        self.assertEqual(state['leaders'], [{'session_id': 'vision', 'domain_id': 'sight01', 'lead': True, 'name': '视觉'}])

    def test_listing_pages_and_notices_unrecorded_changes(self):
        self.tree()
        for index in range(domain_versions.PAGE):
            self.apply({'op': 'rename', 'id': 'sense01', 'name': '感知 %d' % index})
        first = self.listing()
        self.assertEqual(len(first['versions']), domain_versions.PAGE)
        self.assertTrue(first['more'])
        rest = domain_versions.listing(self.conn, 'demo', first['versions'][-1]['number'])
        self.assertEqual([v['number'] for v in rest['versions']], [2, 1])
        self.assertFalse(rest['more'])
        session_ops.delete(self.conn, 'vision', now=NOW)
        self.assertFalse(self.listing()['latest_matches'], 'deleting a placed leader changes the board without a version')

    def test_diff_reports_structure_only(self):
        self.tree()
        before = domain_versions.state(self.conn, 'demo')
        self.apply(create('camera1', '相机', 'sight01'), {'op': 'rename', 'id': 'sense01', 'name': '感知层'},
                   {'op': 'parent', 'id': 'sight01', 'parent_id': None},
                   {'op': 'assign', 'session_id': 'cam', 'domain_id': 'camera1'},
                   {'op': 'assign', 'session_id': 'vision', 'domain_id': 'camera1'},
                   {'op': 'move', 'items': [{'id': 'sense01', 'x': 900, 'y': 900}]})
        change = domain_versions.diff(before, domain_versions.state(self.conn, 'demo'))
        self.assertEqual(change['parts']['added'], [{'id': 'camera1', 'name': '相机', 'path': ['视觉', '相机']}])
        self.assertEqual(change['parts']['renamed'], [{'id': 'sense01', 'from': '感知', 'to': '感知层'}])
        self.assertEqual(change['parts']['moved'], [{'id': 'sight01', 'name': '视觉', 'from': ['感知'], 'to': []}])
        self.assertEqual(change['leaders']['placed'], [{'session_id': 'cam', 'name': '相机', 'to': ['视觉', '相机']}])
        self.assertEqual(change['leaders']['moved'], [{'session_id': 'vision', 'name': '视觉', 'from': ['感知', '视觉'],
                                                      'to': ['视觉', '相机']}])
        self.assertEqual(change['leaders']['lead_off'], [{'session_id': 'vision', 'name': '视觉', 'part': ['感知', '视觉']}])
        self.assertEqual(change['count'], 6)

    def test_rollback_restores_structure_keeps_layout_and_lists_leaders_it_cannot_place(self):
        self.tree()
        self.apply(create('camera1', '相机', 'sight01', 70, 80), {'op': 'assign', 'session_id': 'cam', 'domain_id': 'camera1'},
                   {'op': 'assign', 'session_id': 'depth', 'domain_id': 'sense01'})
        self.apply({'op': 'delete', 'id': 'sight01'}, {'op': 'move', 'items': [{'id': 'sense01', 'x': 600, 'y': 700}]})
        session_ops.archive(self.conn, 'depth', now=NOW)
        preview = domains.rollback_preview(self.conn, 'demo', 2)
        self.assertEqual(preview['rollback']['unplaced'], [{'session_id': 'cam', 'name': '相机', 'from': ['感知', '相机']},
                                                           {'session_id': 'depth', 'name': '深度', 'from': ['感知']}])
        self.assertEqual(preview['diff']['parts']['added'], [{'id': 'sight01', 'name': '视觉', 'path': ['感知', '视觉']}])
        self.assertEqual(preview['diff']['parts']['removed'], [{'id': 'camera1', 'name': '相机', 'path': ['感知', '相机']}])
        self.assertEqual(preview['rollback']['gone'], [])
        board_version = domains.version(self.conn, 'demo')
        with self.assertRaises(board.Conflict):
            domains.rollback(self.conn, 'demo', 2, board_version - 1, now=NOW)
        seq = self.conn.execute('SELECT COALESCE(MAX(seq),0) FROM change_log').fetchone()[0]
        result = domains.rollback(self.conn, 'demo', 2, board_version, now=NOW)
        snap = domains.snapshot(self.conn, 'demo')
        parts = {p['id']: p for p in snap['parts']}
        self.assertEqual(set(parts), {'sense01', 'sight01'})
        self.assertEqual((parts['sight01']['name'], parts['sight01']['parent_id'], parts['sight01']['x']), ('视觉', 'sense01', 30.0))
        self.assertEqual((parts['sense01']['x'], parts['sense01']['y']), (600.0, 700.0), 'a surviving part keeps its layout')
        self.assertEqual({l['session_id']: (l['domain_id'], l['lead']) for l in snap['leaders']}, {'vision': ('sight01', True)})
        self.assertEqual(board.session(self.conn, 'cam')['status'] != 'closed', True, 'rollback never touches sessions')
        self.assertEqual(result['version'], 5)
        latest = self.listing()['versions'][0]
        self.assertEqual((latest['source'], latest['restored_from'], latest['note']), ('rollback', 2, '回滚到版本 2'))
        self.assertEqual(latest['detail']['unplaced'], ['相机', '深度'])
        kinds = [row['type'] for row in self.conn.execute('SELECT type FROM change_log WHERE seq>?', (seq,))]
        self.assertIn('domains.invalidated', kinds)
        with self.assertRaisesRegex(board.BoardError, '不需要回滚'):
            domains.rollback(self.conn, 'demo', 2, domains.version(self.conn, 'demo'), now=NOW)

    def test_rollback_skips_deleted_leaders_and_flags_archived_ones(self):
        self.tree()
        self.apply({'op': 'assign', 'session_id': 'depth', 'domain_id': 'sense01'})
        self.apply({'op': 'assign', 'session_id': 'depth', 'domain_id': None}, {'op': 'assign', 'session_id': 'vision', 'domain_id': None})
        session_ops.archive(self.conn, 'depth', now=NOW)
        session_ops.delete(self.conn, 'vision', now=NOW)
        preview = domains.rollback_preview(self.conn, 'demo', 3)
        self.assertEqual(preview['rollback']['gone'], [{'session_id': 'vision', 'name': '视觉'}])
        self.assertEqual(preview['rollback']['archived'], [{'session_id': 'depth', 'name': '深度'}])
        domains.rollback(self.conn, 'demo', 3, domains.version(self.conn, 'demo'), now=NOW)
        self.assertEqual({l['session_id']: (l['domain_id'], l['archived']) for l in domains.snapshot(self.conn, 'demo')['leaders']},
                         {'depth': ('sense01', True)})

    def test_rollback_to_the_baseline_empties_the_board_and_restores_it_again(self):
        self.tree()
        domains.rollback(self.conn, 'demo', 1, domains.version(self.conn, 'demo'), now=NOW)
        self.assertEqual(domains.snapshot(self.conn, 'demo')['parts'], [])
        domains.rollback(self.conn, 'demo', 2, domains.version(self.conn, 'demo'), now=NOW)
        self.assertEqual({p['id'] for p in domains.snapshot(self.conn, 'demo')['parts']}, {'sense01', 'sight01'})
        self.assertEqual([v['source'] for v in self.listing()['versions']], ['rollback', 'rollback', 'edit', 'baseline'])


class RestructureTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.service = web.Service(
            self.home, PLUGIN, cwd=self.project, agents_provider=lambda: [],
            process_factory=self.process_factory, now=lambda: store.parse_iso(NOW),
            claude_root=self.claude, codex_root=self.codex, runs_file=self.runs,
            vscode_state_db=self.vscode_state_db, isolated_sources=True)
        self.addCleanup(self.service.close)
        for sid, name in (('l-ui', '界面'), ('l-net', '网络')):
            board.join(self.conn, sid, 'demo', name, self.project, self.project, 'main', role='leader', now=NOW)
        domains.apply(self.conn, 'demo', [create('app0001', '应用'), create('front01', '前端', 'app0001'),
                                          create('back001', '后端', 'app0001'),
                                          {'op': 'assign', 'session_id': 's3', 'domain_id': 'front01'},
                                          {'op': 'lead', 'session_id': 's3', 'lead': True},
                                          {'op': 'assign', 'session_id': 'l-ui', 'domain_id': 'front01'},
                                          {'op': 'assign', 'session_id': 'l-net', 'domain_id': 'back001'}], now=NOW)

    def call(self, name, args):
        return executor.human_call(self.service, name, args, project='demo')

    def preview(self, *changes):
        return self.call('domains.restructure.preview', {'project': 'demo', 'changes': list(changes)})

    def execute(self, plan, *changes, note='架构重整', handoffs=None):
        return self.call('domains.restructure.apply', {
            'project': 'demo', 'changes': list(changes), 'note': note, 'board_version': plan['board_version'],
            'revision': plan['revision'],
            'handoffs': [{'index': h['index'], 'text': h['text'], 'wake': False} for h in plan['handoffs']]
            if handoffs is None else handoffs})

    def goal(self, leader, title):
        return goals.create(self.conn, 'demo', leader, title, '需求：' + title, ['做完'], now=NOW)['id']

    def counts(self):
        return {table: self.conn.execute('SELECT COUNT(*) FROM %s' % table).fetchone()[0]
                for table in ('domains', 'domain_leaders', 'domain_board_versions', 'leader_notices', 'goal_transfers',
                              'change_log', 'session_controls')}

    def test_preview_is_read_only_and_compares_before_and_after(self):
        before = self.counts()
        plan = self.preview({'type': 'part.create', 'ref': 'mobile1', 'name': '手机端', 'parent': 'app0001'},
                            {'type': 'part.merge', 'part': 'back001', 'into': 'front01'},
                            {'type': 'leader.assign', 'leader': 'l-ui', 'part': 'mobile1'},
                            {'type': 'part.rename', 'part': 'front01', 'name': '客户端'})
        self.assertEqual(self.counts(), before)
        self.assertTrue(plan['ok'])
        self.assertEqual([item['label'] for item in plan['changes']],
                         ['新建 part「手机端」，挂到「应用」下', 'part「后端」并入「前端」', '「界面」放进「应用 › 手机端」',
                          '「前端」改名为「客户端」'])
        after = {p['id']: (p['name'], p['parent_id']) for p in plan['after']['parts']}
        self.assertEqual(after, {'app0001': ('应用', None), 'front01': ('客户端', 'app0001'), 'mobile1': ('手机端', 'app0001')})
        self.assertEqual({l['session_id']: l['domain_id'] for l in plan['after']['leaders']},
                         {'s3': 'front01', 'l-ui': 'mobile1', 'l-net': 'front01'})
        self.assertEqual(plan['summary']['parts_created'], 1)
        self.assertEqual(plan['summary']['parts_removed'], 1)
        effects = {item['name']: item['effects'] for item in plan['leaders']}
        self.assertEqual(effects['界面'], ['从「应用 › 前端」移到「应用 › 手机端」'])
        self.assertEqual(effects['网络'], ['从「应用 › 后端」移到「应用 › 客户端」'])
        self.assertEqual((plan['goals'], plan['archived'], plan['handoffs'], plan['to_start']), ([], [], [], []))

    def test_preview_lists_each_bad_change(self):
        bad = [{'type': 'part.move', 'part': 'app0001', 'parent': 'front01'}, {'type': 'part.rename', 'part': 'nope', 'name': 'x'},
               {'type': 'leader.assign', 'leader': 'l-ui', 'part': 'front01', 'extra': 1}]
        plan = self.preview(*bad)
        self.assertFalse(plan['ok'])
        self.assertIsNone(plan['after'])
        self.assertEqual([(e['index'], e['message']) for e in plan['errors']],
                         [(0, '不能把 part 挂到它自己或它的下级下面'), (1, 'part 不存在：nope'), (2, '字段不对：多了 extra')])
        with self.assertRaisesRegex(board.BoardError, '方案还有 3 处问题'):
            self.call('domains.restructure.apply', {'project': 'demo', 'changes': bad, 'note': '坏方案',
                                                    'board_version': 1, 'revision': 'x', 'handoffs': []})
        with self.assertRaisesRegex(OpError, 'changes'):
            self.preview({'type': 'party'})

    def test_apply_matches_the_preview_and_saves_one_version(self):
        changes = [{'type': 'part.create', 'ref': 'mobile1', 'name': '手机端', 'parent': 'app0001'},
                   {'type': 'part.merge', 'part': 'back001', 'into': 'front01'},
                   {'type': 'leader.assign', 'leader': 'l-ui', 'part': 'mobile1'}]
        plan = self.preview(*changes)
        result = self.execute(plan, *changes, note='合并前后端，拆出手机端')
        state = domain_versions.state(self.conn, 'demo')
        self.assertEqual(domain_versions.diff(plan['after'], state)['count'], 0, 'what ran is what the preview showed')
        versions = domain_versions.listing(self.conn, 'demo')['versions']
        self.assertEqual((versions[0]['number'], versions[0]['source'], versions[0]['note']),
                         (result['version'], 'restructure', '合并前后端，拆出手机端'))
        self.assertEqual(versions[0]['detail']['changes'], [item['label'] for item in plan['changes']])
        parts = {p['id']: p for p in domains.snapshot(self.conn, 'demo')['parts']}
        self.assertEqual((parts['mobile1']['x'], parts['mobile1']['y']), (parts['front01']['x'] + 280, parts['front01']['y']),
                         'a created part sits after the existing sub-parts of its parent')
        with self.assertRaises(board.Conflict):
            self.execute(plan, *changes)

    def test_created_parts_never_cover_existing_ones(self):
        domains.apply(self.conn, 'demo', [{'op': 'move', 'items': [{'id': 'front01', 'x': 0, 'y': 160},
                                                                   {'id': 'back001', 'x': 300, 'y': 160}]}], now=NOW)
        changes = [{'type': 'part.create', 'ref': 'mobile1', 'name': '手机端', 'parent': 'front01'},
                   {'type': 'part.create', 'ref': 'tablet1', 'name': '平板', 'parent': 'front01'},
                   {'type': 'part.create', 'ref': 'gate001', 'name': '网关', 'parent': 'back001'}]
        self.execute(self.preview(*changes), *changes)
        spots = [(p['x'], p['y']) for p in domains.snapshot(self.conn, 'demo')['parts']]
        for index, (x, y) in enumerate(spots):
            for ox, oy in spots[index + 1:]:
                self.assertFalse(abs(x - ox) < 280 and abs(y - oy) < 130, spots)
        parts = {p['id']: (p['x'], p['y']) for p in domains.snapshot(self.conn, 'demo')['parts']}
        self.assertEqual(parts['mobile1'], (0.0, 330.0), 'a part without sub-parts gets its first child right under it')

    def test_a_created_part_takes_the_place_a_merged_one_frees(self):
        domains.apply(self.conn, 'demo', [create('voice01', '语音', 'front01'),
                                          {'op': 'move', 'items': [{'id': 'front01', 'x': 0, 'y': 160},
                                                                   {'id': 'voice01', 'x': 0, 'y': 330}]}], now=NOW)
        changes = [{'type': 'part.merge', 'part': 'voice01', 'into': 'front01'},
                   {'type': 'part.create', 'ref': 'mobile1', 'name': '手机端', 'parent': 'front01'}]
        self.execute(self.preview(*changes), *changes)
        parts = {p['id']: (p['x'], p['y']) for p in domains.snapshot(self.conn, 'demo')['parts']}
        self.assertEqual(parts['mobile1'], (0.0, 330.0))

    def test_a_changed_leader_or_task_after_the_preview_needs_a_new_preview(self):
        goal = self.goal('l-net', '接口')
        changes = [{'type': 'goal.transfer', 'goal': goal, 'to': 'l-ui'}]
        plan = self.preview(*changes)
        self.assertEqual(plan['goals'][0]['action'], 'transfer')
        self.conn.execute("UPDATE goals SET status='paused' WHERE id=?", (goal,))
        with self.assertRaisesRegex(board.Conflict, '重新预览'):
            self.execute(plan, *changes)

    def test_merging_a_leader_hands_over_tasks_context_and_lead_then_archives_its_group(self):
        goal = self.goal('s3', '登录页')
        changes = [{'type': 'leader.merge', 'leader': 's3', 'into': 'l-ui'}]
        plan = self.preview(*changes)
        self.assertTrue(plan['ok'], plan['errors'])
        self.assertEqual([(g['goal_id'], g['action'], g['implicit'], g['to']['name']) for g in plan['goals']],
                         [(goal, 'transfer', True, '界面')])
        self.assertEqual([(p['name'], p['role']) for p in plan['archived']], [('lead', 'leader'), ('one', 'member')])
        draft = plan['handoffs'][0]
        self.assertEqual((draft['from']['name'], draft['to']['name'], draft['delivers']), ('lead', '界面', True))
        self.assertTrue(draft['text'].startswith('Crew leader 交接。'))
        self.assertIn('成员：lead', draft['text'])
        self.assertEqual(plan['diff']['leaders']['lead_on'], [{'session_id': 'l-ui', 'name': '界面', 'part': ['应用', '前端']}])
        result = self.execute(plan, *changes, handoffs=[{'index': 0, 'text': '用户改过的交接稿', 'wake': True}])
        self.assertEqual(result['starts_turn'], True)
        self.assertEqual(plan['goals'][0]['wakes'], True, 'a queued task handed over tells its new leader to start')
        self.assertEqual([item['name'] for item in result['wakes']], ['界面', '界面'])
        self.assertEqual(goals.get(self.conn, goal)['leader_session_id'], 'l-ui')
        for sid in ('s3', 's1'):
            self.assertTrue(board.is_archived(self.conn, sid), sid)
            self.assertIsNotNone(board.session(self.conn, sid), 'the original session is kept')
        notice = self.conn.execute("SELECT * FROM leader_notices WHERE session_id='l-ui' AND source='web'").fetchone()
        self.assertIn('用户把「lead」并入你的职责', notice['text'])
        self.assertTrue(notice['text'].endswith('用户改过的交接稿'))
        self.assertEqual(notice['wake'], 1)
        places = {l['session_id']: (l['domain_id'], l['lead']) for l in domains.snapshot(self.conn, 'demo')['leaders']}
        self.assertEqual(places, {'l-ui': ('front01', True), 'l-net': ('back001', False)})
        version = domain_versions.listing(self.conn, 'demo')['versions'][0]
        self.assertEqual(version['detail']['handoffs'], [{'from': 'lead', 'to': '界面', 'chars': 8}])
        self.assertEqual(version['detail']['archived'], ['lead', 'one'])

    def test_retiring_needs_idle_leaders_and_handled_tasks(self):
        goal = self.goal('l-net', '网关')
        plan = self.preview({'type': 'leader.retire', 'leader': 'l-net', 'successor': None})
        self.assertEqual(plan['errors'], [{'index': 0, 'message': '「网络」还有 1 个未结束的任务（「网关」），没有接手人时请在方案里逐个作废或改派'}])
        self.conn.execute("UPDATE goals SET status='running' WHERE id=?", (goal,))
        plan = self.preview({'type': 'goal.cancel', 'goal': goal}, {'type': 'leader.retire', 'leader': 'l-net', 'successor': None})
        self.assertEqual(plan['errors'], [{'index': 1, 'message': '「网络」的任务「网关」正在进行，请先暂停'}])
        self.conn.execute("UPDATE goals SET status='paused' WHERE id=?", (goal,))
        changes = [{'type': 'goal.cancel', 'goal': goal}, {'type': 'leader.retire', 'leader': 'l-net', 'successor': None}]
        plan = self.preview(*changes)
        self.assertTrue(plan['ok'], plan['errors'])
        self.assertEqual(plan['handoffs'], [])
        self.execute(plan, *changes)
        self.assertEqual(goals.get(self.conn, goal)['status'], 'cancelled')
        self.assertTrue(board.is_archived(self.conn, 'l-net'))
        self.assertNotIn('l-net', {l['session_id'] for l in domains.snapshot(self.conn, 'demo')['leaders']})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM leader_notices WHERE source='web'").fetchone()[0], 0)

    def test_a_successor_cannot_leave_in_the_same_plan(self):
        plan = self.preview({'type': 'leader.retire', 'leader': 'l-net', 'successor': 'l-ui'},
                            {'type': 'leader.merge', 'leader': 'l-ui', 'into': 's3'})
        self.assertEqual(plan['errors'], [{'index': 0, 'message': '接手「网络」的「界面」在本方案里也会合并或退役'}])

    def test_a_failed_step_writes_nothing(self):
        changes = [{'type': 'part.rename', 'part': 'front01', 'name': '客户端'},
                   {'type': 'leader.retire', 'leader': 'l-net', 'successor': 'l-ui'}]
        plan = self.preview(*changes)
        before = self.counts()
        with self.assertRaisesRegex(board.BoardError, '还缺第 2 条变更的交接稿'):
            self.execute(plan, *changes, handoffs=[])
        self.assertEqual(self.counts(), before)
        self.assertEqual({p['id']: p['name'] for p in domains.snapshot(self.conn, 'demo')['parts']}['front01'], '前端')

    def test_split_only_lists_the_new_leader_to_start(self):
        changes = [{'type': 'part.create', 'ref': 'gate001', 'name': '网关', 'parent': 'back001'},
                   {'type': 'leader.split', 'leader': 'l-net', 'ref': 'newlead', 'name': '网关', 'runtime': 'codex',
                    'model': 'gpt-6-luna', 'effort': 'low', 'part': 'gate001'}]
        plan = self.preview(*changes)
        self.assertEqual(plan['to_start'], [{'index': 1, 'ref': 'newlead', 'name': '网关', 'runtime': 'codex',
                                             'model': 'gpt-6-luna', 'effort': 'low',
                                             'part': {'id': 'gate001', 'name': '应用 › 后端 › 网关'},
                                             'split_from': {'session_id': 'l-net', 'name': '网络'}}])
        self.assertEqual((plan['handoffs'][0]['to'], plan['handoffs'][0]['delivers']), ({'ref': 'newlead', 'name': '网关'}, False))
        sessions = self.conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]
        with self.assertRaisesRegex(board.BoardError, '不能唤醒'):
            self.execute(plan, *changes, handoffs=[{'index': 1, 'text': '负责网关', 'wake': True}])
        result = self.execute(plan, *changes, handoffs=[{'index': 1, 'text': '负责网关', 'wake': False}])
        self.assertEqual(result['to_start'][0]['brief'], '负责网关')
        self.assertEqual(result['to_start'][0]['part_id'], 'gate001')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], sessions, 'no leader is started')
        self.assertFalse(result['starts_turn'])

    def test_standalone_merge_and_retire_operations(self):
        plan = self.preview({'type': 'leader.retire', 'leader': 'l-net', 'successor': 'l-ui'})
        result = self.call('leader.retire', {'project': 'demo', 'leader': 'l-net', 'successor': 'l-ui',
                                             'handoff': {'text': plan['handoffs'][0]['text'], 'wake': False},
                                             'board_version': plan['board_version'], 'revision': plan['revision']})
        self.assertEqual(result['archived'], ['l-net'])
        version = domain_versions.listing(self.conn, 'demo')['versions'][0]
        self.assertEqual(version['note'], '退役 leader「网络」，由「界面」接手')
        notice = self.conn.execute("SELECT * FROM leader_notices WHERE session_id='l-ui' AND source='web'").fetchone()
        self.assertIn('退役，由你接手', notice['text'])
        self.assertEqual(notice['wake'], 0)
        plan = self.preview({'type': 'leader.merge', 'leader': 's3', 'into': 'l-ui'})
        with self.assertRaisesRegex(board.BoardError, '还缺第 1 条变更的交接稿'):
            self.call('leader.merge', {'project': 'demo', 'leader': 's3', 'into': 'l-ui', 'handoff': None,
                                       'board_version': plan['board_version'], 'revision': plan['revision']})
        self.call('leader.merge', {'project': 'demo', 'leader': 's3', 'into': 'l-ui', 'handoff': {'text': '交接', 'wake': False},
                                   'board_version': plan['board_version'], 'revision': plan['revision']})
        self.assertTrue(board.is_archived(self.conn, 's3'))

    def test_version_operations_through_the_registry(self):
        listed = self.call('domains.versions.list', {'project': 'demo'})
        self.assertEqual([v['source'] for v in listed['versions']], ['edit', 'baseline'])
        detail = self.call('domains.version.get', {'project': 'demo', 'number': 1})
        self.assertEqual(len(detail['rollback']['unplaced']), 3)
        result = self.call('domains.rollback', {'project': 'demo', 'number': 1, 'board_version': detail['board_version']})
        self.assertEqual(result['board']['parts'], [])


    def test_only_the_user_and_the_assistant_change_the_structure_and_ai_writes_wait_for_confirmation(self):
        names = ('domains.versions.list', 'domains.version.get', 'domains.rollback', 'domains.restructure.preview',
                 'domains.restructure.apply', 'leader.merge', 'leader.retire')
        for name in names:
            op = registry.get(name)
            self.assertEqual(op.scopes, {'human', 'device_browser', 'device_desktop', 'assistant'}, name)
            self.assertEqual(op.risk, 'normal' if op.access == 'read' else 'high', name)
            self.assertTrue(op.assistant_as_user, name)
        with self.assertRaisesRegex(OpError, 'leader不能调用 domains.restructure.preview'):
            ai_call(self.service, self.conn, 'l-ui', 'domains.restructure.preview',
                    {'changes': [{'type': 'part.rename', 'part': 'front01', 'name': '客户端'}]})
        assistant = personal.ensure_assistant(self.conn, self.home)['session_id']
        changes = [{'type': 'part.rename', 'part': 'front01', 'name': '客户端'}]
        plan = ai_call(self.service, self.conn, assistant, 'domains.restructure.preview', {'project': 'demo', 'changes': changes})
        self.assertTrue(plan['ok'])
        args = {'project': 'demo', 'changes': changes, 'note': '前端改名', 'board_version': plan['board_version'],
                'revision': plan['revision'], 'handoffs': []}
        before = self.counts()
        waiting = ai_call(self.service, self.conn, assistant, 'domains.restructure.apply', args, request_id='rename-1')
        self.assertEqual(waiting['status'], 'awaiting_user')
        self.assertEqual(self.counts(), before, 'nothing runs before the user confirms')
        with mock.patch.object(self.service.chat, 'send') as send:
            done = executor.human_call(self.service, 'interaction.resolve', {'id': waiting['confirmation_id'], 'approve': True})
        self.assertEqual(done['status'], 'approved', done)
        self.assertIn('已确认并执行', send.call_args.args[1])
        self.assertEqual(domain_versions.listing(self.conn, 'demo')['versions'][0]['note'], '前端改名')
        stale = ai_call(self.service, self.conn, assistant, 'domains.rollback',
                        {'project': 'demo', 'number': 1, 'board_version': plan['board_version']}, request_id='back-1')
        with mock.patch.object(self.service.chat, 'send') as send:
            failed = executor.human_call(self.service, 'interaction.resolve', {'id': stale['confirmation_id'], 'approve': True})
        self.assertEqual(failed['status'], 'failed')
        self.assertIn('已在别处改动', failed['error'])
