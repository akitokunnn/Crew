"""Actual storage HTTP roundtrip, with a process factory that may never run."""
from contextlib import closing
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from crew import board, store, web


class StorageHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);self.home=root/'home';self.project=root/'workspace';self.project.mkdir()
        with closing(store.connect(self.home)) as conn:
            board.init_project(conn,'work',self.project)
            conn.execute("INSERT INTO sessions(session_id,project,crew_name,role,group_name,status,started_at) VALUES(?,?,?,?,?,?,?)",('old-native','work','Captain','leader','Captain','offline',store.now_iso()))
            conn.execute('INSERT INTO groups(project,name,leader_session_id,created_at) VALUES(?,?,?,?)',('work','Captain','old-native',store.now_iso()))
        self.factory=mock.Mock(side_effect=AssertionError('storage operation started a model'))
        self.service=web.Service(self.home,Path(__file__).resolve().parents[1],agents_provider=lambda:[],
            process_factory=self.factory,isolated_sources=True)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),web.Handler);self.server.service=self.service
        self.url='http://127.0.0.1:'+str(self.server.server_port)
        self.worker=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.01});self.worker.start()
        self.addCleanup(self.cleanup)
        self.opener=build_opener(ProxyHandler({}))

    def cleanup(self):
        self.server.shutdown();self.server.server_close();self.worker.join(5);self.service.close()
        self.factory.assert_not_called()

    def request(self,path,data=None,headers=None,raw=False):
        request=Request(self.url+path,None if data is None else json.dumps(data).encode(),{'X-Crew':'1',**(headers or {})})
        try:response=self.opener.open(request,timeout=15)
        except HTTPError as exc:response=exc
        with response:
            content=response.read()
            return response.status,(content if raw else json.loads(content)),response.headers

    def upgrade(self):
        status,discovery,_=self.request('/api/workspace-data/discover',{'root':str(self.project)})
        self.assertEqual(status,200)
        status,result,_=self.request('/api/workspace-data/open',{'root':str(self.project),'action':'upgrade','candidate_id':discovery['candidates'][0]['id']})
        self.assertEqual(status,200,result)
        return result['workspace_id']

    def test_cleanup_preview_and_workspace_package_preview_have_independent_contracts(self):
        status, cleanup, _ = self.request('/api/storage/preview', {'categories':[]})
        self.assertEqual(status, 200, cleanup)
        self.assertIn('preview_id', cleanup)
        self.assertNotIn('workspace', cleanup)
        ws = self.upgrade()
        status, exported, _ = self.request('/api/workspace-data/export', {'workspace_id':ws})
        self.assertEqual(status, 200, exported)
        status, package, _ = self.request('/api/workspace-data/preview', {'path':exported['path']})
        self.assertEqual(status, 200, package)
        self.assertEqual(package['workspace']['id'], ws)
        self.assertNotIn('preview_id', package)

    def test_upgrade_download_fork_import_and_history_roundtrip(self):
        ws=self.upgrade()
        status,result,_=self.request('/api/workspace-data/export',{'workspace_id':ws})
        self.assertEqual(status,200,result)
        status,payload,headers=self.request('/api/workspace-data/download?id='+result['download_id'],raw=True)
        self.assertEqual(status,200);self.assertTrue(payload.startswith(b'PK'))
        status,preview,_=self.request('/api/workspace-data/preview',{'path':result['path']})
        self.assertEqual(status,200,preview);self.assertEqual(preview['workspace']['name'],'work')
        status,incoming,_=self.request('/api/workspace-data/import',{'path':result['path'],'mode':'fork'})
        self.assertEqual(status,200,incoming);self.assertNotEqual(ws,incoming['workspace_id'])
        status,history,_=self.request('/api/workspace-data/history?'+urlencode({'workspace_id':incoming['workspace_id']}))
        self.assertEqual(status,200,history);self.assertEqual(history['agents'][0]['name'],'Captain')
        status,page,_=self.request('/?project=storage:'+incoming['workspace_id'],raw=True)
        self.assertEqual(status,200);self.assertIn(b'storage-history.js',page);self.assertNotIn(b'app.js',page)
        _,projects,_=self.request('/api/projects')
        self.assertTrue(any(p.get('workspace_id')==incoming['workspace_id'] and p.get('read_only') for p in projects['projects']))
        target=self.home.parent/'new-workspace';target.mkdir()
        status,active,_=self.request('/api/workspace-data/activate',{'workspace_id':incoming['workspace_id'],'root':str(target)})
        self.assertEqual(status,200,active)
        self.assertEqual(active['project']['workspace_id'],incoming['workspace_id'])
        with closing(store.connect(self.home)) as conn:
            person=conn.execute('SELECT s.*,r.native_id FROM sessions s JOIN session_runtimes r USING(session_id) WHERE s.project=?',(active['project']['name'],)).fetchone()
            self.assertEqual(person['crew_name'],'Captain');self.assertIsNone(person['native_id'])
            self.assertIsNotNone(conn.execute('SELECT 1 FROM session_stop_gates WHERE session_id=?',(person['session_id'],)).fetchone())
        _,projects,_=self.request('/api/projects')
        self.assertTrue(any(p.get('workspace_id')==incoming['workspace_id'] and not p.get('read_only') for p in projects['projects']))
        _,preserved,_=self.request('/api/workspace-data/history?'+urlencode({'workspace_id':incoming['workspace_id']}))
        self.assertEqual(preserved['agents'][0]['name'],'Captain')

    def test_storage_rejects_foreign_origin_before_any_data_access(self):
        for path in ('/api/workspace-data/history?workspace_id=invalid','/api/workspace-data/discover'):
            status,content,_=self.request(path,{'root':str(self.project)} if path.endswith('discover') else None,headers={'Origin':'https://example.com'})
            self.assertEqual(status,403);self.assertIn('本机',content['error'])

    def test_collection_pagination_does_not_trap_history_after_two_hundred_rows(self):
        ws=self.upgrade();repo=self.service.storage.repository
        with closing(repo.connect()) as conn,repo.transaction(conn):
            import uuid
            at=store.now_iso()
            existing=conn.execute('SELECT COUNT(*) FROM artifacts WHERE workspace_id=?',(ws,)).fetchone()[0]
            for number in range(205):
                conn.execute('INSERT INTO artifacts(id,workspace_id,created_at,updated_at,name,kind) VALUES(?,?,?,?,?,?)',(str(uuid.uuid4()),ws,at,at,'file '+str(number),'document'))
        ids=set();cursor=None
        while True:
            query={'workspace_id':ws,'collection':'artifacts','limit':100}
            if cursor:query['before']=json.dumps(cursor)
            status,page,_=self.request('/api/workspace-data/history?'+urlencode(query));self.assertEqual(status,200,page)
            new={row['id'] for row in page['items']};self.assertFalse(ids&new);ids|=new
            if not page['has_more']:break
            last=page['items'][-1];cursor=[last['created_at'],last['id']]
        self.assertEqual(len(ids),existing+205)

    def test_message_pages_follow_recorded_sequence_and_blob_scope(self):
        import uuid
        ws=self.upgrade();repo=self.service.storage.repository
        with closing(repo.connect()) as conn,repo.transaction(conn):
            cid=conn.execute('SELECT id FROM conversations WHERE workspace_id=?',(ws,)).fetchone()[0]
            at=store.now_iso()
            for number in range(1,6):
                mid=str(uuid.UUID(int=6-number))
                conn.execute("INSERT INTO messages(id,workspace_id,created_at,conversation_id,author_kind,kind,author_name_snapshot) VALUES(?,?,?,?,?,?,?)",(mid,ws,at,cid,'system','notice','system'))
                conn.execute('INSERT INTO message_positions(id,workspace_id,created_at,conversation_id,message_id,accepted_sequence) VALUES(?,?,?,?,?,?)',(str(uuid.uuid4()),ws,at,cid,mid,number))
            blob=repo.register_blob(ws,repo.objects.put_bytes(ws,b'file contents'),'text/plain',conn=conn)
        status,page,_=self.request('/api/workspace-data/history?'+urlencode({'workspace_id':ws,'conversation_id':cid,'limit':2}))
        self.assertEqual(status,200,page);self.assertEqual([m['sequence'] for m in page['messages']],['4','5'])
        cursor=[page['messages'][0]['sequence'],page['messages'][0]['id']]
        _,older,_=self.request('/api/workspace-data/history?'+urlencode({'workspace_id':ws,'conversation_id':cid,'limit':2,'before':json.dumps(cursor)}))
        self.assertEqual([m['sequence'] for m in older['messages']],['2','3'])
        status,payload,headers=self.request('/api/workspace-data/blob?'+urlencode({'workspace_id':ws,'blob_id':blob['id']}),raw=True)
        self.assertEqual(status,200);self.assertEqual(payload,b'file contents');self.assertIn('sandbox',headers['Content-Security-Policy'])
        status,_,_=self.request('/api/workspace-data/blob?'+urlencode({'workspace_id':str(uuid.uuid4()),'blob_id':blob['id']}))
        self.assertEqual(status,400)


if __name__=='__main__':unittest.main()
