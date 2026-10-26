from contextlib import closing
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'plugin'))
from crew.storage_v2 import Repository
from crew.storage_v2.packages import preview_package,import_package
package,home=map(Path,sys.argv[1:])
preview=preview_package(package)
assert preview['tables']['messages']==120
assert preview['tables']['reports']==215
repo=Repository(home);account,user=repo.ensure_local_identity()
with closing(repo.connect()) as c:
 result=import_package(c,repo.objects,package,target_account_id=account,actor_user_id=user)
 assert c.execute('SELECT count(*) FROM messages').fetchone()[0]==120
 assert c.execute('SELECT count(*) FROM reports').fetchone()[0]==215
 assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
 blob=c.execute("SELECT sha256,size_bytes FROM blob_objects WHERE workspace_id=? AND mime_type LIKE 'text/plain%'",(result['workspace_id'],)).fetchone()
 with repo.objects.open(result['workspace_id'],blob[0],expected_size=blob[1]) as s:assert '附件确实可读' in s.read().decode('utf-8')
print(json.dumps(dict(ok=True,messages=120,reports=215,blob_count=preview['blob_count'],third_machine_workspace=result['workspace_id'])))
