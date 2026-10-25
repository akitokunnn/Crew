"""Deliverable metadata, bounded history scans, and real isolated HTTP previews."""
import tests
import json
import os
import threading
import unittest
from pathlib import Path
from unittest import mock
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from crew import board, documents, goal_api, goals, web
from test_shell import NOW, PLUGIN, ShellFixture


class DocumentFixture(ShellFixture):
    def register(self, session_id, path, **kwargs):
        values={'title':Path(path).stem,'summary':'明确填写的测试摘要'}
        values.update(kwargs)
        return documents.register(self.conn,session_id,path,**values)

    def file(self, name='docs/report.md', text='# report'):
        target=self.project/name; target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text,encoding='utf-8');return target

    def journal(self, records, sid='s1', mode='w'):
        folder=self.home/'chat';folder.mkdir(exist_ok=True)
        with (folder/(sid+'.jsonl')).open(mode,encoding='utf-8') as stream:
            for record in records:stream.write(json.dumps(record,ensure_ascii=False)+'\n')

    def assistant(self, text, seq=1):
        return {'type':'assistant','seq':seq,'at':NOW,'content':[{'type':'text','text':text}]}


class DocumentsTests(DocumentFixture,unittest.TestCase):
    def goal(self, title='任务'):
        return goals.create(self.conn, 'demo', 's3', title, '交付这个任务', ['可以验收'])

    def test_task_materials_require_explicit_binding_and_can_reuse_the_original_file(self):
        first, second = self.goal('第一个'), self.goal('第二个')
        old = self.register('s3', str(self.file('old.md')))
        file = self.file('task.md')
        row = self.register('s3', str(file), goal_id=first['id'])
        self.assertEqual([d['id'] for d in documents.list(self.conn, 'demo', goal_id=first['id'])], [row['id']])
        self.assertEqual(documents.list(self.conn, 'demo', goal_id=second['id']), [])
        self.assertEqual([d['id'] for d in goal_api.detail(self.conn, 'demo', first['id'])['documents']], [row['id']])
        self.assertNotIn(old['id'], [d['id'] for d in documents.list(self.conn, 'demo', goal_id=first['id'])])
        repeated = self.register('s3', str(file), goal_id=second['id'])
        self.assertEqual(repeated['id'], row['id'])
        self.assertEqual(len(documents.list(self.conn, 'demo')), 2)
        self.assertEqual([d['id'] for d in documents.list(self.conn, 'demo', goal_id=second['id'])], [row['id']])

    def test_execution_binding_rejects_other_actor_goal_project_and_stopped_execution(self):
        goal, other = self.goal(), self.goal('其他任务')
        execution = goals.create_execution(self.conn, goal['id'], 's3', runtime='claude')
        file = self.file()
        for sid, extra in (('s1', {'execution_id': execution['id']}),
                           ('s3', {'execution_id': execution['id'], 'goal_id': other['id']}),
                           ('s2', {'goal_id': goal['id']})):
            with self.subTest(sid=sid, extra=extra), self.assertRaises(board.BoardError):
                self.register(sid, str(file), **extra)
        self.assertEqual(documents.list(self.conn, 'demo'), [])
        row = self.register('s3', str(file), execution_id=execution['id'])
        self.assertEqual(documents.list(self.conn, 'demo', goal_id=goal['id'])[0]['id'], row['id'])
        board.init_project(self.conn, 'other', self.root, now=NOW)
        with self.assertRaises(board.BoardError):
            documents.list(self.conn, 'other', goal_id=goal['id'])
        goals.request_pause(self.conn, goal['id'])
        with self.assertRaises(board.Conflict):
            self.register('s3', str(file), execution_id=execution['id'])

    def test_goal_journal_scan_binds_actual_execution_without_mixing_general_history(self):
        goal, other = self.goal(), self.goal('其他任务')
        execution = goals.create_execution(self.conn, goal['id'], 's3', runtime='claude')
        self.file('general.md'); self.file('task.md')
        self.journal([self.assistant('[一般聊天](general.md)')], sid='s3')
        self.journal([self.assistant('[任务汇总](task.md)')], sid=execution['id'])
        goals.request_pause(self.conn, goal['id'])
        documents.refresh(self.conn, self.home, 'demo', now=NOW)
        self.assertEqual(len(documents.list(self.conn, 'demo')), 2)
        self.assertEqual([d['title'] for d in documents.list(self.conn, 'demo', goal_id=goal['id'])], ['任务汇总'])
        self.assertEqual(documents.list(self.conn, 'demo', goal_id=other['id']), [])
        self.assertEqual(documents.refresh(self.conn, self.home, 'demo', now=NOW)['scanned'], 0)

    def test_cli_document_submission_uses_the_bound_execution_and_stays_readable_after_pause(self):
        goal = self.goal()
        execution = goals.create_execution(self.conn, goal['id'], 's3', runtime='claude')
        path = self.file()
        with mock.patch.dict(os.environ, {'CREW_EXECUTION_ID':execution['id'], 'CREW_GOAL_ID':goal['id']}):
            code, output, errors = self.run_cli(['--session', 's3', 'doc', 'add', str(path), '--title', '任务报告', '--summary', '已验证的结果', '--json'])
            self.assertEqual(code, 0, errors)
            doc = json.loads(output)
            self.assertEqual(documents.list(self.conn, 'demo', goal_id=goal['id'])[0]['id'], doc['id'])
            goals.request_pause(self.conn, goal['id'])
            code, output, errors = self.run_cli(['--session', 's3', 'doc', 'list', '--json'])
            self.assertEqual(code, 0, errors)
            self.assertEqual(json.loads(output)[0]['id'], doc['id'])

    def test_register_keeps_original_files_and_deduplicates_canonical_paths(self):
        file=self.file(); original=file.read_bytes()
        first=self.register('s1','docs/report.md',title='验证报告',summary='明确摘要',kind='report',important=True,now=NOW)
        second=self.register('s1',str(file),title='更新的报告',summary='明确摘要',kind='report',now=NOW)
        self.assertEqual(first['id'],second['id']);self.assertEqual(len(documents.list(self.conn,'demo')),1)
        self.assertEqual(second['summary'],'明确摘要');self.assertEqual(second['submitter'],'one')
        self.assertEqual(second['leader_session_id'],'s3');self.assertEqual(second['group'],'lead')
        self.assertEqual(file.read_bytes(),original);self.assertFalse((self.home/'documents').exists())
        self.assertEqual(second['source'],'manual');self.assertTrue(second['exists'])

    def test_manual_metadata_and_user_override_survive_history_and_other_authors(self):
        file=self.file(); first=self.register('s1',str(file),title='真实标题',summary='只保留提交者写的摘要',kind='summary',important=True,now=NOW)
        documents.pin(self.conn,'demo',first['id'],False,now=NOW)
        self.journal([self.assistant('[猜测标题](docs/report.md)',9)])
        documents.refresh(self.conn,self.home,'demo',now=NOW)
        preserved=documents.get(self.conn,'demo',first['id'])
        self.assertEqual((preserved['title'],preserved['summary'],preserved['kind']),('真实标题','只保留提交者写的摘要','summary'))
        self.assertEqual(preserved['session_id'],'s1');self.assertTrue(preserved['ai_important']);self.assertFalse(preserved['important'])
        updated=self.register('s2',str(file),title='新的提交',kind='guide',important=True,now=NOW)
        self.assertEqual(updated['id'],first['id']);self.assertEqual(updated['submitter'],'two');self.assertFalse(updated['important'])
        self.assertTrue(documents.pin(self.conn,'demo',first['id'],None,now=NOW)['important'])

    def test_sort_groups_and_project_isolation(self):
        plain=self.register('s2',str(self.file('plain.md')),now=NOW)
        report=self.register('s1',str(self.file('report.md')),kind='report',important=True,now=NOW)
        summary=self.register('s3',str(self.file('summary.md')),kind='summary',important=True,now=NOW)
        self.assertEqual([row['id'] for row in documents.list(self.conn,'demo')],[summary['id'],report['id'],plain['id']])
        self.assertEqual({row['id'] for row in documents.list(self.conn,'demo','s3')},{summary['id'],report['id']})
        other=self.root/'other';other.mkdir();board.init_project(self.conn,'other',other,now=NOW)
        board.join(self.conn,'other-lead','other','else',other,other,'main',role='leader',now=NOW)
        for operation in (lambda:documents.get(self.conn,'other',report['id']),lambda:documents.pin(self.conn,'other',report['id'],True),
                          lambda:documents.list(self.conn,'demo','other-lead'),lambda:self.register('other-lead',str(self.file()),project='demo')):
            with self.assertRaises(board.BoardError):operation()

    def test_later_historical_reference_cannot_steal_another_groups_document(self):
        self.file()
        board.join(self.conn,'other-lead','demo','other',self.project,self.project,'main',role='leader',now=NOW)
        board.roster(self.conn,'demo','move','other',['two'],now=NOW)
        self.journal([self.assistant('[交付报告](docs/report.md)')],sid='s1')
        documents.refresh(self.conn,self.home,'demo',now=NOW)
        first=documents.list(self.conn,'demo')[0]
        self.journal([self.assistant('[参考旧报告](docs/report.md)',2)],sid='other-lead')
        documents.refresh(self.conn,self.home,'demo',now='2026-10-22T08:00:00.000Z')
        after=documents.get(self.conn,'demo',first['id'])
        self.assertEqual(after['session_id'],'s1')
        self.assertEqual(after['leader_session_id'],'s3')
        self.assertEqual(after['submitter'],'one')
        self.assertEqual(after['group'],'lead')
        self.assertEqual(after['title'],first['title'])
        self.assertEqual([row['id'] for row in documents.list(self.conn,'demo','s3')],[first['id']])
        self.assertEqual(documents.list(self.conn,'demo','other-lead'),[])

    def test_paths_require_existing_supported_files_inside_project_or_owner_worktree(self):
        outside=self.root/'private.md';outside.write_text('private')
        for value in (str(outside),'../private.md','https://example.com/report.md','file://remote/share/report.md','//server/report.md',str(self.project),'missing.md',str(self.file('script.exe'))):
            with self.subTest(value=value),self.assertRaises(board.BoardError):self.register('s1',value)
        worktree=self.root/'own-worktree';worktree.mkdir();(worktree/'guide.md').write_text('guide')
        self.conn.execute("UPDATE sessions SET cwd=?,worktree=? WHERE session_id='s1'",(str(worktree),str(worktree)))
        self.assertTrue(self.register('s1','guide.md')['exists'])
        # Explicitly submitted visual/data deliverables are allowed; history is stricter.
        for name in ('chart.png','data.json'):self.assertTrue(self.register('s1',str(self.file(name)))['exists'])

    def test_symlink_escape_and_retargeted_document_are_rejected(self):
        outside=self.root/'secret.md';outside.write_text('private')
        link=self.project/'escape.md'
        file=self.file();row=self.register('s1',str(file))
        try:
            os.symlink(outside,link);file.unlink();os.symlink(outside,file)
            with self.assertRaises(board.BoardError):self.register('s1',str(link))
            self.assertFalse(documents.get(self.conn,'demo',row['id'])['exists'])
            with self.assertRaises(board.BoardError):documents.resolve_path(self.conn,'s1',row['path'],'demo')
        except OSError:
            # Windows may lack symbolic-link privileges. Still exercise the
            # canonical-path boundary deterministically instead of skipping it.
            real_resolve=Path.resolve
            def redirected(target,*args,**kwargs):
                return outside if target in (link,file) else real_resolve(target,*args,**kwargs)
            with mock.patch.object(Path,'resolve',redirected):
                with self.assertRaises(board.BoardError):self.register('s1',str(link))
                self.assertFalse(documents.get(self.conn,'demo',row['id'])['exists'])
                with self.assertRaises(board.BoardError):documents.resolve_path(self.conn,'s1',row['path'],'demo')

    def test_history_indexes_only_assistant_document_links_and_report_paths(self):
        self.file('docs/汇总.md');self.file('docs/guide.txt');self.file('debug.png');self.file('settings.json')
        self.journal([{'type':'message','text':'[ignore](docs/guide.txt)'},
                      self.assistant('[汇总](<docs/汇总.md>) [debug](debug.png) [config](settings.json)',7),
                      {'type':'tool_result','content':'[ignore](docs/guide.txt)'},
                      {'type':'assistant','content':[{'type':'tool_use','text':'[ignore](docs/guide.txt)'}]}])
        board.report(self.conn,'s1','指南文件： docs/guide.txt',now=NOW)
        result=documents.refresh(self.conn,self.home,'demo',now=NOW);rows=documents.list(self.conn,'demo')
        self.assertEqual(len(rows),2);self.assertGreaterEqual(result['scanned'],5)
        summary=next(row for row in rows if row['kind']=='summary')
        self.assertIsNone(summary['summary']);self.assertFalse(summary['important']);self.assertEqual(summary['source_seq'],7)
        self.assertEqual(summary['source'],'journal');self.assertEqual(documents.refresh(self.conn,self.home,'demo',now=NOW)['scanned'],0)

    def test_partial_append_and_malformed_lines_keep_incremental_offsets(self):
        self.file();self.file('next.md');self.journal([self.assistant('[report](docs/report.md)',1)])
        first=documents.refresh(self.conn,self.home,'demo',now=NOW);self.assertEqual(first['scanned'],1)
        path=self.home/'chat/s1.jsonl';tail=json.dumps(self.assistant('[next](next.md)',2)).encode('utf-8')
        with path.open('ab') as stream:stream.write(tail[:20])
        self.assertEqual(documents.refresh(self.conn,self.home,'demo',now=NOW)['indexed'],0)
        with path.open('ab') as stream:stream.write(tail[20:]+b'\nnot-json\n'+json.dumps(self.assistant('[report](docs/report.md)',3)).encode()+b'\n')
        result=documents.refresh(self.conn,self.home,'demo',now=NOW)
        self.assertEqual(len(documents.list(self.conn,'demo')),2);self.assertTrue(result['errors'])
        self.assertEqual(documents.refresh(self.conn,self.home,'demo',now=NOW)['scanned'],0)

    def test_bounded_scan_resumes_without_reading_whole_journal_or_losing_records(self):
        self.file();self.journal([self.assistant('[report](docs/report.md)',i) for i in range(1,8)])
        with mock.patch.object(documents,'MAX_LINES',2):
            result=documents.refresh(self.conn,self.home,'demo',now=NOW);self.assertEqual(result['scanned'],2);self.assertTrue(result['has_more'])
            for _ in range(4):result=documents.refresh(self.conn,self.home,'demo',now=NOW)
        row=documents.list(self.conn,'demo')[0];self.assertEqual(row['source_seq'],7)
        self.assertEqual(documents.refresh(self.conn,self.home,'demo',now=NOW)['scanned'],0)

    def test_truncated_or_replaced_journal_is_indexed_again(self):
        self.file();self.file('new.md');self.journal([self.assistant('[first](docs/report.md)')]);documents.refresh(self.conn,self.home,'demo',now=NOW)
        self.journal([self.assistant('[new](new.md)')]);documents.refresh(self.conn,self.home,'demo',now=NOW)
        self.assertEqual(len(documents.list(self.conn,'demo')),2)

    def test_reports_are_sized_before_loading_and_oversized_reports_do_not_starve_later_docs(self):
        self.file();board.report(self.conn,'s1','x'*400,now=NOW)
        board.report(self.conn,'s1','[report](docs/report.md)',now=NOW)
        queries=[];self.conn.set_trace_callback(queries.append)
        try:
            with mock.patch.object(documents,'MAX_BYTES',256):result=documents.refresh(self.conn,self.home,'demo',now=NOW)
        finally:self.conn.set_trace_callback(None)
        self.assertTrue(result['errors']);self.assertEqual(len(documents.list(self.conn,'demo')),1)
        reads=[query for query in queries if query.startswith('SELECT body FROM reports')]
        self.assertEqual(len(reads),1);self.assertIn('id=2',reads[0])
        self.assertFalse(any(query.startswith('SELECT * FROM reports') for query in queries))

    def test_validation_and_existing_transaction_do_not_commit_callers_work(self):
        file=self.file();self.conn.execute('BEGIN IMMEDIATE')
        row=self.register('s1',str(file),now=NOW);self.assertTrue(self.conn.in_transaction)
        documents.pin(self.conn,'demo',row['id'],True);self.conn.rollback();self.assertEqual(documents.list(self.conn,'demo'),[])
        for kwargs in ({'kind':'code'},{'important':1},{'summary':''},{'title':'x'*301}):
            with self.assertRaises(board.BoardError):self.register('s1',str(file),**kwargs)


class DocumentHTTPTests(DocumentFixture,unittest.TestCase):
    # Keep actual HTTP integration tests separate from the pure storage scenarios.
    def setUp(self):
        super().setUp()
        self.service=web.Service(self.home,PLUGIN,cwd=self.project,agents_provider=lambda:[],claude_root=self.claude,codex_root=self.codex,runs_file=self.runs,process_factory=self.process_factory,vscode_state_db=self.vscode_state_db)
        self.addCleanup(self.service.close)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),web.Handler);self.server.service=self.service
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.01});self.thread.start();self.addCleanup(self.close_server)
        self.opener=build_opener(ProxyHandler({}))

    def close_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join(3);self.assertFalse(self.thread.is_alive())

    def request(self,route,data=None,**query):
        url='http://127.0.0.1:%d'%self.server.server_port+route+('?' + urlencode(query) if query else '')
        request=Request(url,None if data is None else json.dumps(data).encode(),{'X-Crew':'1'})
        try:response=self.opener.open(request,timeout=3)
        except HTTPError as exc:response=exc
        with response:return response.status,json.loads(response.read())

    def test_http_register_list_pin_and_safe_document_id_preview(self):
        file=self.file();code,row=self.request('/api/documents',{'project':'demo','session_id':'s1','path':str(file),'title':'文档','summary':'明确填写的摘要'})
        self.assertEqual(code,200,row);docid=row['id']
        self.assertEqual(self.request('/api/documents',project='demo')[1]['documents'][0]['id'],docid)
        self.assertTrue(self.request('/api/documents/pin',{'project':'demo','id':docid,'important':True})[1]['important'])
        code,info=self.request('/api/files/resolve',{'project':'demo','document_id':docid})
        self.assertEqual(code,200,info);self.assertEqual(info['path'],str(file));self.assertEqual(info['kind'],'markdown')
        self.assertEqual(info['session_id'],'s1');self.assertEqual(info['document_id'],docid)
        code,content=self.request('/api/files/content',project='demo',document_id=docid)
        self.assertEqual(code,200,content);self.assertEqual(content['content'],'# report')
        with mock.patch('crew.web.os.startfile') as opener:
            self.assertEqual(self.request('/api/files/open',{'project':'demo','document_id':docid})[0],200)
            opener.assert_called_once_with(str(file))
        self.assertEqual(self.request('/api/documents/refresh',{'project':'demo'})[0],200)

    def test_http_task_materials_are_strictly_scoped_in_list_and_detail(self):
        goal = goals.create(self.conn, 'demo', 's3', '这个任务', '任务要求', ['可验收'])
        other = goals.create(self.conn, 'demo', 's3', '下个任务', '另一个要求', ['可验收'])
        self.register('s3', str(self.file('old.md')))
        code, doc = self.request('/api/documents', {'project':'demo', 'session_id':'s3',
            'path':str(self.file('task.md')), 'title':'交付', 'summary':'这个任务的交付', 'goal_id':goal['id']})
        self.assertEqual(code, 200, doc)
        self.assertEqual([d['id'] for d in self.request('/api/documents', project='demo', goal_id=goal['id'])[1]['documents']], [doc['id']])
        self.assertEqual(self.request('/api/documents', project='demo', goal_id=other['id'])[1]['documents'], [])
        self.assertEqual(self.request('/api/goal', project='demo', id=goal['id'])[1]['documents'][0]['id'], doc['id'])
        self.assertEqual(self.request('/api/documents', project='demo', goal_id='missing')[0], 400)

    def test_http_explicit_registration_requires_a_title_and_summary(self):
        file=self.file()
        for extra in ({'title':'标题'},{'summary':'摘要'},{'title':'标题','summary':' '}):
            code,result=self.request('/api/documents',{'project':'demo','session_id':'s1','path':str(file),**extra})
            self.assertEqual(code,400,result)
        self.assertEqual(documents.list(self.conn,'demo'),[])

    def test_http_document_ids_and_relative_resources_cannot_cross_projects_or_read_outside(self):
        row=self.register('s1',str(self.file()))
        other=self.root/'other';other.mkdir();board.init_project(self.conn,'other',other,now=NOW)
        self.assertEqual(self.request('/api/files/content',project='other',document_id=row['id'])[0],400)
        outside=self.root/'secret.txt';outside.write_text('private')
        for route,data in (('/api/files/resolve',{'project':'demo','session_id':'s1','path':str(outside)}),
                           ('/api/files/resolve',{'project':'demo','session_id':'s1','path':'../secret.txt','base':str(self.project/'README.md')})):
            self.assertEqual(self.request(route,data)[0],400)
        self.assertEqual(self.request('/api/files/content',project='demo',session_id='s1',path=str(outside))[0],400)
        self.assertEqual(self.request('/api/documents/pin',{'project':'demo','id':row['id'],'important':True},project='other')[0],400)
        self.assertEqual(self.request('/api/files/resolve',{'project':'demo','document_id':True})[0],400)

    def test_all_registered_document_types_have_a_safe_preview_or_default_open(self):
        for suffix in sorted(documents.EXTENSIONS):
            with self.subTest(suffix=suffix):
                row=self.register('s1',str(self.file('deliverable'+suffix)))
                code,info=self.request('/api/files/resolve',{'project':'demo','document_id':row['id']})
                self.assertEqual(code,200,info);self.assertEqual(info['open_with'],'default')
                self.assertIn(info['kind'],('markdown','text','image','external'))

    def test_document_preview_returns_trusted_worktree_context_for_relative_resources(self):
        worktree=self.root/'external-worktree';worktree.mkdir()
        self.conn.execute("UPDATE sessions SET cwd=?,worktree=? WHERE session_id='s1'",(str(worktree),str(worktree)))
        document=worktree/'guide.md';document.write_text('[child](child.txt)')
        (worktree/'child.txt').write_text('relative worktree resource')
        row=self.register('s1',str(document))
        code,info=self.request('/api/files/resolve',{'project':'demo','document_id':row['id'],'session_id':'s2','path':'../not-used.txt'})
        self.assertEqual(code,200,info);self.assertEqual(info['session_id'],'s1')
        code,content=self.request('/api/files/content',project='demo',session_id=info['session_id'],base=info['path'],path='child.txt')
        self.assertEqual(code,200,content);self.assertEqual(content['content'],'relative worktree resource')


if __name__=='__main__':unittest.main()
