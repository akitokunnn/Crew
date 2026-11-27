"""想法导图：节点编辑、移出与撤销、转成架构图的预览 / 分批转换 / 冲突处理，以及 HTTP 处理函数。"""
import tests  # Install the production-service connection guard.
from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest

from crew import board, domain_versions, domains, mindmap, mindmap_api, personal, store
from crew.ops.errors import OpError
from test_shell import NOW


class MindmapFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='crew-mindmap-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.home = self.base / 'home'
        self.conn = store.connect(self.home)
        self.addCleanup(self.conn.close)
        for name in ('demo', 'other'):
            (self.base / name).mkdir()
            board.init_project(self.conn, name, self.base / name, now=NOW)
        for sid, name in (('lead-ui', '界面'), ('lead-voice', '语音'), ('lead-net', '网络')):
            board.join(self.conn, sid, 'demo', name, self.base / 'demo', self.base / 'demo', 'main', role='leader', now=NOW)
        self.serial = 0

    def note(self, text, **fields):
        self.serial += 1
        return personal.note_action(self.conn, 'capture', {'text': text, **fields}, project='demo', request_id='n-%d' % self.serial, now=NOW)['note']['id']

    def edit(self, *ops):
        return mindmap.apply(self.conn, list(ops), mindmap.version(self.conn, 'demo'), project='demo', now=NOW)

    def topic(self, node_id, label, parent=None, index=None):
        op = {'op': 'create', 'id': node_id, 'label': label, 'parent_id': parent}
        if index is not None:
            op['index'] = index
        return op

    def idea(self, node_id, note_id, parent=None):
        return {'op': 'create', 'id': node_id, 'note_id': note_id, 'parent_id': parent}

    def tree(self):
        """Crew 下一版 › {手机端 › [语音派活(想法), 离线草稿(想法)], 协作 › 作战室}"""
        self.voice, self.draft = self.note('语音派活\n手机上直接说'), self.note('离线草稿')
        self.edit(self.topic('root01', 'Crew 下一版'), self.topic('mobile1', '手机端', 'root01'),
                  self.idea('voice01', self.voice, 'mobile1'), self.idea('draft01', self.draft, 'mobile1'),
                  self.topic('team001', '协作', 'root01'), self.topic('room001', '作战室', 'team001'))

    def shape(self):
        nodes = mindmap.snapshot(self.conn, 'demo')['nodes']
        return {node['id']: (node['parent_id'], node['sort_order']) for node in nodes}

    def request(self, roots, parent_id=None, decisions=None, names=None, leaders=None, project='demo'):
        return {'project': project, 'roots': roots, 'parent_id': parent_id, 'decisions': decisions or {},
                'names': names or {}, 'leaders': leaders or {}}

    def convert(self, roots, **kwargs):
        data = self.request(roots, **kwargs)
        data['map_version'] = mindmap.version(self.conn, 'demo')
        data['board_version'] = domains.version(self.conn, data['project'])
        return mindmap.convert(self.conn, data, now=NOW)

    def board_tree(self, project='demo'):
        snap = domains.snapshot(self.conn, project)
        names = {part['id']: part['name'] for part in snap['parts']}
        return ({part['name']: names.get(part['parent_id']) for part in snap['parts']},
                {place['session_id']: names[place['domain_id']] for place in snap['leaders']})


class MindmapEditTests(MindmapFixture):
    def test_empty_workspace_map_reads_as_version_zero(self):
        self.assertEqual(mindmap.snapshot(self.conn, 'demo'), {'project': 'demo', 'version': 0, 'nodes': [], 'links': []})

    def test_free_move_reparent_and_reset_share_the_map_version(self):
        self.tree()
        at = mindmap.version(self.conn, 'demo')
        moved = self.edit({'op': 'move', 'id': 'voice01', 'x': -90, 'y': 1_000_000})['map']
        self.assertEqual(moved['version'], at + 1)
        voice = next(n for n in moved['nodes'] if n['id'] == 'voice01')
        self.assertEqual((voice['parent_id'], voice['sort_order'], voice['x'], voice['y']), ('mobile1', 0, -90, 1_000_000))
        self.assertTrue(all(n['x'] is None and n['y'] is None for n in moved['nodes'] if n['id'] != 'voice01'))
        reparented = self.edit({'op': 'move', 'id': 'voice01', 'parent_id': 'team001', 'index': 0,
                                'x': 217, 'y': -56})['map']
        self.assertEqual(reparented['version'], at + 2)
        voice = next(n for n in reparented['nodes'] if n['id'] == 'voice01')
        self.assertEqual((voice['parent_id'], voice['sort_order'], voice['x'], voice['y']), ('team001', 0, 217, -56))
        with self.assertRaisesRegex(board.BoardError, '不能把节点'):
            self.edit({'op': 'move', 'id': 'team001', 'parent_id': 'voice01', 'x': 3, 'y': 4})
        with self.assertRaisesRegex(board.BoardError, 'y 坐标无效'):
            self.edit({'op': 'auto_layout'}, {'op': 'move', 'id': 'voice01', 'x': 3, 'y': 1_000_001})
        with self.assertRaises(board.Conflict):
            mindmap.apply(self.conn, [{'op': 'auto_layout'}], at, project='demo')
        self.assertEqual(mindmap.snapshot(self.conn, 'demo'), reparented)
        other = mindmap.apply(self.conn, [dict(self.topic('other01', '其他工作区'), x=3, y=4)], 0, project='other')['map']
        for offset in (1, 2):
            reset = self.edit({'op': 'auto_layout'})['map']
            self.assertEqual(reset['version'], reparented['version'] + offset)
            self.assertTrue(all(n['x'] is None and n['y'] is None for n in reset['nodes']))
            self.assertEqual(self.shape()['voice01'], ('team001', 0))
            self.assertEqual(mindmap.snapshot(self.conn, 'other'), other)

    def test_store_adds_coordinates_to_a_previous_map_without_moving_nodes(self):
        self.tree()
        original = mindmap.snapshot(self.conn, 'demo')
        self.conn.execute('DROP TABLE canvas_settings')
        for column in ('x', 'y'):
            self.conn.execute('ALTER TABLE personal_mindmap_nodes DROP COLUMN ' + column)
        for _ in range(2):
            with closing(store.connect(self.home)) as conn:
                self.assertEqual(mindmap.snapshot(conn, 'demo'), original)
                self.assertFalse(mindmap_api.snapshot(self.home, 'demo')['canvas_wheel_zoom'])

    def test_nodes_are_ideas_or_topics_and_keep_sibling_order(self):
        idea = self.note('想法墙出导图')
        result = self.edit(self.topic('root01', '  Crew   下一版 '), self.idea('idea001', idea, 'root01'),
                           self.topic('first01', '最前面', 'root01', index=0), {'op': 'create', 'label': '自动 id', 'parent_id': None})
        self.assertEqual(result['map']['version'], 1)
        self.assertEqual(result['effects'][0], {'op': 'create', 'id': 'root01'})
        self.assertRegex(result['effects'][3]['id'], r'^[0-9a-f]{12}$')
        nodes = {node['id']: node for node in result['map']['nodes']}
        self.assertEqual(nodes['root01']['label'], 'Crew 下一版', 'labels collapse whitespace')
        self.assertEqual((nodes['idea001']['note_id'], nodes['idea001']['label']), (idea, None))
        self.assertEqual([nodes['first01']['sort_order'], nodes['idea001']['sort_order']], [0, 1])
        for ops, message in ((self.idea('again01', idea), '已经在导图上'),
                             ({'op': 'create', 'parent_id': None}, '要么引用'),
                             ({'op': 'create', 'label': 'x', 'note_id': idea, 'parent_id': None}, '要么引用'),
                             (self.topic('long001', '长' * 41), '最多 40'),
                             (self.topic('root01', '重复'), '已存在'),
                             ({'op': 'create', 'label': '缺父'}, 'parent_id'),
                             (self.idea('ghost01', 'no-such-note'), '没有该想法'),
                             ({'op': 'label', 'id': 'idea001', 'label': '改'}, '跟着想法走'),
                             ({'op': 'explode'}, '不认识')):
            with self.assertRaisesRegex((board.BoardError, OpError), message):
                self.edit(ops)
        self.assertEqual(mindmap.version(self.conn, 'demo'), 1, 'a rejected batch changes nothing')
        with self.assertRaises(board.Conflict):
            mindmap.apply(self.conn, [self.topic('late001', '过期')], 0, project='demo', now=NOW)

    def test_edits_preserve_other_workspace_version_and_root_order(self):
        first = self.edit(self.topic('root-a1', '本区'))
        other = mindmap.apply(self.conn, [self.topic('root-b1', '另一区')], 0, project='other')
        self.edit(self.topic('root-a2', '第二个', index=0))
        result = mindmap.apply(self.conn, [self.topic('root-b2', '另一区第二个')], other['map']['version'], project='other')
        self.assertEqual(first['map']['version'], 1)
        self.assertEqual([n['id'] for n in result['map']['nodes']], ['root-b1','root-b2'])
        self.assertEqual(self.shape(), {'root-a2':(None,0), 'root-a1':(None,1)})

    def test_move_reparents_reorders_and_refuses_cycles(self):
        self.tree()
        self.edit({'op': 'move', 'id': 'draft01', 'parent_id': 'mobile1', 'index': 0})
        self.assertEqual(self.shape()['draft01'], ('mobile1', 0))
        self.assertEqual(self.shape()['voice01'], ('mobile1', 1))
        self.edit({'op': 'move', 'id': 'voice01', 'parent_id': 'team001', 'index': 0})
        shape = self.shape()
        self.assertEqual((shape['voice01'], shape['room001'], shape['draft01']), (('team001', 0), ('team001', 1), ('mobile1', 0)))
        self.edit({'op': 'move', 'id': 'team001', 'parent_id': None, 'index': 0})
        self.assertEqual((self.shape()['team001'], self.shape()['root01']), ((None, 0), (None, 1)))
        with self.assertRaisesRegex(board.BoardError, '下级'):
            self.edit({'op': 'move', 'id': 'root01', 'parent_id': 'draft01', 'index': 0})
        with self.assertRaisesRegex(board.BoardError, 'index'):
            self.edit({'op': 'move', 'id': 'draft01', 'parent_id': 'root01', 'index': 9})
        self.edit({'op': 'collapse', 'id': 'mobile1', 'collapsed': True}, self.topic('room002', '复盘', 'team001'),
                  {'op': 'label', 'id': 'room002', 'label': '复盘会'})
        nodes = {node['id']: node for node in mindmap.snapshot(self.conn, 'demo')['nodes']}
        self.assertTrue(nodes['mobile1']['collapsed'])
        self.assertEqual(nodes['room002']['label'], '复盘会')

    def test_remove_keeps_the_note_moves_children_into_its_place_and_effects_undo_it(self):
        self.tree()
        self.edit({'op': 'collapse', 'id': 'mobile1', 'collapsed': True})
        before = self.shape()
        effect = self.edit({'op': 'remove', 'id': 'mobile1'})['effects'][0]
        self.assertEqual(effect, {'op': 'remove', 'id': 'mobile1', 'parent_id': 'root01', 'index': 0,
                                  'children': ['voice01', 'draft01'], 'note_id': None, 'label': '手机端', 'collapsed': True,
                                  'x': None, 'y': None})
        shape = self.shape()
        self.assertEqual([shape['voice01'], shape['draft01'], shape['team001']], [('root01', 0), ('root01', 1), ('root01', 2)])
        removed = self.edit({'op': 'remove', 'id': 'voice01'})['effects'][0]
        self.assertEqual(personal.get_note(self.conn, self.voice, project='demo')['state'], 'open', 'taking an idea off the map keeps it')
        # The page undoes a removal with the effect: re-create under the same id, then move the children back.
        self.edit({'op': 'create', 'id': removed['id'], 'note_id': removed['note_id'], 'parent_id': removed['parent_id'], 'index': removed['index']})
        self.edit({'op': 'create', 'id': 'mobile1', 'label': effect['label'], 'parent_id': effect['parent_id'], 'index': effect['index']},
                  *[{'op': 'move', 'id': child, 'parent_id': 'mobile1', 'index': index} for index, child in enumerate(effect['children'])],
                  {'op': 'collapse', 'id': 'mobile1', 'collapsed': effect['collapsed']})
        self.assertEqual(self.shape(), before)


class MindmapConvertTests(MindmapFixture):
    def test_preview_maps_branches_to_parts_reads_only_and_suggests_promoted_leaders(self):
        self.tree()
        self.conn.execute('INSERT INTO personal_note_leaders(note_id,project,request_id,source_json,status,session_id,error,created_at,updated_at) '
                          "VALUES(?,?,?,?,'completed',?,NULL,?,?)", (self.voice, 'demo', 'start-1', '{}', 'lead-voice', NOW, NOW))
        versions = (mindmap.version(self.conn, 'demo'), domains.version(self.conn, 'demo'))
        plan = mindmap.preview(self.conn, self.request(['mobile1', 'root01']))
        self.assertEqual((mindmap.version(self.conn, 'demo'), domains.version(self.conn, 'demo')), versions, 'preview writes nothing')
        self.assertEqual(domains.snapshot(self.conn, 'demo')['parts'], [])
        rows = {row['node_id']: row for row in plan['rows']}
        self.assertEqual([row['node_id'] for row in plan['rows']], ['root01', 'mobile1', 'voice01', 'draft01', 'team001', 'room001'],
                         'a root inside another picked branch is counted once, in tree order')
        self.assertEqual({row['action'] for row in plan['rows']}, {'create'})
        self.assertEqual(rows['root01']['parent'], {'kind': 'top', 'id': None, 'name': None})
        self.assertEqual(rows['voice01']['parent'], {'kind': 'new', 'id': 'mobile1', 'name': '手机端'})
        self.assertEqual((rows['voice01']['name'], rows['voice01']['kind'], rows['room001']['kind']), ('语音派活', 'note', 'topic'))
        self.assertEqual(rows['voice01']['leader'], {'session_id': 'lead-voice', 'source': 'promoted', 'name': '语音',
                                                     'moved_from': None, 'unchanged': False})
        self.assertEqual({node_id for node_id, row in rows.items() if row['slot']}, {'voice01', 'draft01', 'room001'})
        self.assertEqual(plan['summary'], {'create': 6, 'merge': 0, 'linked': 0, 'skip': 0, 'leaders': 1})
        self.assertEqual(plan['before'], [])
        after = {part['id']: part for part in plan['after']}
        self.assertEqual(after['new:voice01']['parent_id'], 'new:mobile1')
        self.assertEqual(after['new:voice01']['leaders'][0]['moved_in'], True)
        self.assertEqual([item['name'] for item in plan['leaders']], ['界面', '网络', '语音'])

    def test_convert_writes_parts_links_leaders_and_both_versions_beside_the_existing_board(self):
        self.tree()
        domains.apply(self.conn, 'demo', [{'op': 'create', 'id': 'oldpart', 'name': '已有', 'parent_id': None, 'x': 100, 'y': 40}], now=NOW)
        result = self.convert(['mobile1'], leaders={'draft01': 'lead-ui'})
        parents, places = self.board_tree()
        self.assertEqual(parents, {'已有': None, '手机端': None, '语音派活': '手机端', '离线草稿': '手机端'})
        self.assertEqual(places, {'lead-ui': '离线草稿'})
        self.assertEqual(result['summary']['create'], 3)
        self.assertEqual(result['board_version'], 2)
        self.assertEqual(result['map']['version'], 2)
        latest = domain_versions.listing(self.conn, 'demo')['versions'][0]
        self.assertEqual((latest['source'], latest['note']), ('mindmap', '思维导图转换：新建 3 个 part，放入 1 位 leader'))
        self.assertEqual({link['node_id']: link['domain_name'] for link in result['map']['links']},
                         {'mobile1': '手机端', 'voice01': '语音派活', 'draft01': '离线草稿'})
        coords = {part['name']: (part['x'], part['y']) for part in domains.snapshot(self.conn, 'demo')['parts']}
        self.assertEqual(coords['语音派活'], (376.0, 206.0), 'new parts sit to the right of the board, one layer per level')
        self.assertEqual(coords['手机端'], (514.0, 40.0), 'a parent is centred over its new children')

    def test_later_batches_attach_under_parts_already_converted_and_never_duplicate(self):
        self.tree()
        self.convert(['root01'], decisions={'mobile1': 'skip'})
        parents, _ = self.board_tree()
        self.assertEqual(parents, {'Crew 下一版': None, '语音派活': 'Crew 下一版', '离线草稿': 'Crew 下一版',
                                   '协作': 'Crew 下一版', '作战室': '协作'}, 'a skipped node passes its children up')
        self.edit(self.topic('retro01', '复盘', 'team001'))
        plan = mindmap.preview(self.conn, self.request(['retro01', 'team001'], parent_id=None))
        rows = {row['node_id']: row for row in plan['rows']}
        self.assertEqual((rows['team001']['action'], rows['room001']['action'], rows['retro01']['action']), ('linked', 'linked', 'create'))
        self.assertEqual(rows['retro01']['parent']['name'], '协作')
        self.convert(['retro01'])
        parents, _ = self.board_tree()
        self.assertEqual(parents['复盘'], '协作', 'a branch whose parent was converted earlier lands under that part')
        self.assertEqual(len(domains.snapshot(self.conn, 'demo')['parts']), 6)
        with self.assertRaisesRegex(board.BoardError, '都已转过'):
            self.convert(['team001'])
        with self.assertRaisesRegex(board.BoardError, '已经转过'):
            mindmap.preview(self.conn, self.request(['team001'], names={'team001': '改名'}))
        with self.assertRaises(OpError) as denied:
            mindmap.convert(self.conn, {**self.request(['team001'], project='other'), 'map_version': 0, 'board_version': 0})
        self.assertEqual(denied.exception.code, 'not_visible')
        self.assertEqual(self.board_tree('other')[0], {})

    def test_conflicts_merge_by_default_can_create_or_skip_and_deleted_parts_unlink(self):
        self.tree()
        domains.apply(self.conn, 'demo', [{'op': 'create', 'id': 'mobold1', 'name': '手机端', 'parent_id': None, 'x': 0, 'y': 0},
                                          {'op': 'assign', 'session_id': 'lead-net', 'domain_id': 'mobold1'}], now=NOW)
        plan = mindmap.preview(self.conn, self.request(['mobile1'], leaders={'voice01': 'lead-net'}))
        rows = {row['node_id']: row for row in plan['rows']}
        self.assertEqual((rows['mobile1']['action'], rows['mobile1']['conflict']), ('merge', {'id': 'mobold1', 'name': '手机端'}))
        self.assertEqual(rows['voice01']['parent'], {'kind': 'part', 'id': 'mobold1', 'name': '手机端'})
        self.assertEqual(rows['voice01']['leader']['moved_from'], '手机端')
        after = {part['id']: part for part in plan['after']}
        self.assertEqual(after['mobold1']['status'], 'merge')
        self.assertEqual(after['mobold1']['leaders'][0].get('moved_out'), True)
        created = mindmap.preview(self.conn, self.request(['mobile1'], decisions={'mobile1': 'create'}))
        self.assertEqual(created['rows'][0]['action'], 'create')
        renamed = mindmap.preview(self.conn, self.request(['mobile1'], names={'mobile1': '移动端'}))
        self.assertEqual((renamed['rows'][0]['action'], renamed['rows'][0]['conflict']), ('create', None))
        for request, message in ((self.request(['room001'], decisions={'room001': 'merge'}), '没有可并入'),
                                 (self.request(['mobile1'], leaders={'voice01': 'lead-ui', 'draft01': 'lead-ui'}), '只能放进一个'),
                                 (self.request(['mobile1'], decisions={'voice01': 'skip'}, leaders={'voice01': 'lead-ui'}), '不能放 leader'),
                                 (self.request(['mobile1'], leaders={'voice01': 'nobody'}), '在用 leader'),
                                 (self.request(['mobile1'], names={'room001': 'x'}), '范围内'),
                                 (self.request(['mobile1'], parent_id='missing'), '不在这个工作区'),
                                 ({**self.request(['mobile1']), 'extra': 1}, '正好包含'),
                                 (self.request(['mobile1'], decisions={'mobile1': 'maybe'}), '处理方式')):
            with self.assertRaisesRegex((board.BoardError, OpError), message):
                mindmap.preview(self.conn, request)
        self.convert(['mobile1'])
        parents, places = self.board_tree()
        self.assertEqual(parents, {'手机端': None, '语音派活': '手机端', '离线草稿': '手机端'})
        self.assertEqual(places, {'lead-net': '手机端'})
        links = {link['node_id']: link['domain_id'] for link in mindmap.snapshot(self.conn, 'demo')['links']}
        self.assertEqual(links['mobile1'], 'mobold1')
        domains.apply(self.conn, 'demo', [{'op': 'delete', 'id': links['voice01']}], now=NOW)
        self.assertNotIn('voice01', {link['node_id'] for link in mindmap.snapshot(self.conn, 'demo')['links']})
        again = mindmap.preview(self.conn, self.request(['voice01']))
        self.assertEqual((again['rows'][0]['action'], again['rows'][0]['parent']['name']), ('create', '手机端'))

    def test_convert_refuses_versions_older_than_the_preview(self):
        self.tree()
        data = {**self.request(['room001']), 'map_version': mindmap.version(self.conn, 'demo'), 'board_version': 0}
        self.edit({'op': 'collapse', 'id': 'team001', 'collapsed': True})
        with self.assertRaisesRegex(board.Conflict, '重新预览'):
            mindmap.convert(self.conn, data, now=NOW)
        data['map_version'] = mindmap.version(self.conn, 'demo')
        domains.apply(self.conn, 'demo', [{'op': 'create', 'name': '别处', 'parent_id': None, 'x': 0, 'y': 0}], now=NOW)
        with self.assertRaisesRegex(board.Conflict, '重新预览'):
            mindmap.convert(self.conn, data, now=NOW)
        self.assertEqual(mindmap.snapshot(self.conn, 'demo')['links'], [])


class MindmapHandlerTests(MindmapFixture):
    def test_handlers_round_trip_through_json_like_the_http_layer(self):
        self.tree()
        self.conn.close()
        snap = json.loads(json.dumps(mindmap_api.snapshot(self.home, 'demo')))
        self.assertEqual(len(snap['nodes']), 6)
        with self.assertRaisesRegex(board.BoardError, 'ops、base_version'):
            mindmap_api.edit(self.home, {'ops': []})
        edited = mindmap_api.edit(self.home, {'project': 'demo', 'ops': [self.topic('new0001', '新主题', 'root01')], 'base_version': snap['version']})
        plan = mindmap_api.preview(self.home, self.request(['new0001']))
        result = mindmap_api.convert(self.home, {**self.request(['new0001']), 'map_version': plan['map_version'],
                                                 'board_version': plan['board_version']})
        self.assertEqual((edited['map']['version'], result['map']['version'], result['board_version']), (2, 3, 1))
        with closing(store.connect(self.home)) as conn:
            self.assertEqual([part['name'] for part in domains.snapshot(conn, 'demo')['parts']], ['新主题'])


if __name__ == '__main__':
    unittest.main()
