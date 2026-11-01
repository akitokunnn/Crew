"""Real Windows Service and a separate WSL network client; no AI or network changes."""
import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
PEER = r'''
import http.client,json,socket,sys
from urllib.parse import urlsplit,urlencode
config=json.loads(sys.stdin.readline());target=urlsplit(config['remote_url']);cookie=None;checks=[]
def request(path,data=None):
    headers={'Origin':config['remote_url']}
    if cookie:headers['Cookie']=cookie
    body=None if data is None else json.dumps(data).encode()
    if body is not None:headers.update({'Content-Type':'application/json','X-Crew':'1'})
    connection=http.client.HTTPConnection(target.hostname,target.port,timeout=8)
    connection.request('GET' if body is None else 'POST',path,body,headers)
    response=connection.getresponse();value=response.read();result=(response.status,value,response.headers)
    connection.close();return result
def emit(stage,**data):print(json.dumps({'stage':stage,**data}),flush=True)
connection=socket.create_connection((target.hostname,target.port),8)
peer_ip=connection.getsockname()[0];connection.close()
assert peer_ip!=target.hostname,'client must have its own network address'
assert request('/api/state?project=demo')[0]==401;checks.append('anonymous client refused')
status,body,headers=request('/remote/login',{'code':config['code'],'device_name':'WSL network client'})
assert status==200;cookie=headers['Set-Cookie'].split(';',1)[0];checks.append('one-time pairing accepted')
query=urlencode({'project':'demo','session_id':config['leader']})
message={'session_id':config['leader'],'message_id':'peer-one-message','text':'remote peer sends exactly once'}
assert request('/api/chat/send?project=demo',message)[0]==200
assert request('/api/chat/send?project=demo',message)[0]==200
status,body,_=request('/api/chat/history?'+query);history=json.loads(body)['events']
assert len([e for e in history if e.get('id')=='peer-one-message'])==1;checks.append('duplicate message ID accepted only once')
after=max(e['seq'] for e in history)
emit('await_reply',peer_ip=peer_ip)
stream=http.client.HTTPConnection(target.hostname,target.port,timeout=10)
stream.request('GET','/api/chat/events?'+query,headers={'Cookie':cookie,'Origin':config['remote_url'],'Last-Event-ID':str(after)})
response=stream.getresponse();assert response.status==200
while True:
    line=response.readline();assert line,'unexpected SSE EOF'
    if line.startswith(b'data: '):
        event=json.loads(line[6:])
        if any(block.get('text')=='reply to independent client' for block in event.get('content',[])):break
response.close();stream.close();checks.append('live SSE reaches independent client')
status,body,_=request('/api/chat/history?'+query)
assert len([e for e in json.loads(body)['events'] if any(block.get('text')=='reply to independent client' for block in e.get('content',[]))])==1
checks.append('reconnected history contains one reply')
path='/api/files/content?'+urlencode({'project':'demo','session_id':config['leader'],'path':'remote-note.txt'})
status,body,_=request(path);assert status==200 and '远程文件预览证据' in json.loads(body)['content']
checks.append('project file preview crosses network boundary')
assert request('/api/remote-access')[0]==403;checks.append('paired client cannot change local settings')
emit('await_revoke');assert sys.stdin.readline().strip()=='revoked'
assert request('/api/chat/history?'+query)[0]==401;checks.append('revoked device refused')
emit('await_stop');assert sys.stdin.readline().strip()=='stopped'
try:request('/api/state?project=demo')
except OSError:checks.append('stopped remote socket unavailable')
else:raise AssertionError('remote socket survived shutdown')
emit('done',checks=checks,peer_ip=peer_ip)
'''


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--address',required=True)
    parser.add_argument('--distro',default='Ubuntu-22.04')
    args=parser.parse_args()
    output=ROOT/'Temp'/'zerotier-peer';output.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ,PYTHONIOENCODING='utf-8')
    for key in ('CREW_SESSION_ID','CLAUDE_CODE_SESSION_ID','CREW_GOAL_ID','CREW_EXECUTION_ID'):env.pop(key,None)
    fixture=subprocess.Popen([sys.executable,'-u',str(Path(__file__).with_name('remote_browser_fixture.py')),'--lan-address',args.address],
                             cwd=ROOT,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
    peer=None;result={'scope':'Windows Service reached through a separate WSL network stack; not a second ZeroTier member'}
    try:
        info=json.loads(fixture.stdout.readline());result['fixture']=info
        opener=build_opener(ProxyHandler({}))
        def local(body=None,path='/api/remote-access'):
            payload=None if body is None else json.dumps(body).encode()
            req=Request(info['url']+path,payload,{'Content-Type':'application/json','X-Crew':'1','Origin':info['url']})
            with opener.open(req,timeout=8) as response:return json.load(response)
        pair=local({'action':'pair'})
        peer=subprocess.Popen(['wsl','-d',args.distro,'--exec','python3','-u','-c',PEER],stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
        peer.stdin.write(json.dumps({**info,'code':pair['code']})+'\n');peer.stdin.flush()
        messages=queue.Queue()
        def read():
            for line in peer.stdout:messages.put(json.loads(line))
            messages.put(None)
        threading.Thread(target=read,daemon=True).start()
        while True:
            event=messages.get(timeout=35)
            if event is None:raise RuntimeError('peer ended: '+peer.stderr.read())
            if event['stage']=='await_reply':
                fixture.stdin.write(json.dumps({'action':'reply','session_id':info['leader'],'id':'peer-answer','text':'reply to independent client'})+'\n');fixture.stdin.flush()
            elif event['stage']=='await_revoke':
                local({'action':'revoke_all'});peer.stdin.write('revoked\n');peer.stdin.flush()
            elif event['stage']=='await_stop':
                local({'action':'stop'});assert local(path='/api/state?project=demo')['project']['name']=='demo'
                peer.stdin.write('stopped\n');peer.stdin.flush()
            elif event['stage']=='done':
                result.update(event);break
        assert peer.wait(timeout=10)==0
        fixture.stdin.write(json.dumps({'action':'audit','session_id':info['leader'],'id':'peer-final'})+'\n');fixture.stdin.flush()
        audit=json.loads(fixture.stdout.readline())
        assert len([e for e in audit['messages'] if e.get('id')=='peer-one-message'])==1
        result['ok']=True
    except Exception as error:
        result['error']=str(error);raise
    finally:
        if peer and peer.poll() is None:peer.kill();peer.wait(timeout=5)
        if fixture.poll() is None:
            fixture.stdin.write('{"action":"shutdown"}\n');fixture.stdin.flush()
            try:fixture.wait(timeout=10)
            except subprocess.TimeoutExpired:fixture.kill();fixture.wait(timeout=5)
        result['fixture_closed']=fixture.returncode==0
        result['backend_stderr']=fixture.stderr.read()
        (output/'evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
