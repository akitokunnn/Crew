'use strict';
// Real Electron main/preload/settings + real Service/auth/SQLite. Only the test
// listener's OS address is forced to loopback:0; no installed service is used.
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process'),http=require('node:http');
const desktop=path.resolve(__dirname,'..'),root=path.dirname(desktop),old=process.argv.includes('--old-bridge');
const output=process.env.CREW_REMOTE_SMOKE_OUTPUT||path.join(root,'Temp/remote-desktop',old?'old-bridge':'current');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(test,label,timeout=20000){const end=Date.now()+timeout;while(Date.now()<end){if(await test())return;await pause(50);}throw Error('Timed out: '+label);}
function request(base,route,body,cookie){return new Promise((resolve,reject)=>{
  const target=new URL(route,base),payload=body===undefined?undefined:Buffer.from(JSON.stringify(body));
  const headers={Origin:target.origin,...(cookie?{Cookie:cookie}:{}),...(payload?{'X-Crew':'1','Content-Type':'application/json','Content-Length':payload.length}:{})};
  const req=http.request(target,{method:payload?'POST':'GET',headers},res=>{const chunks=[];res.on('data',value=>chunks.push(value));res.on('end',()=>{try{resolve({status:res.statusCode,headers:res.headers,value:JSON.parse(Buffer.concat(chunks).toString('utf8'))});}catch(error){reject(error);}});});
  req.on('error',reject);req.setTimeout(5000,()=>req.destroy(Error('HTTP response timed out')));req.end(payload);
});}

if(!process.versions.electron){
  (async()=>{
    await fs.mkdir(output,{recursive:true});
    const env={...process.env,PYTHONIOENCODING:'utf-8'};
    for(const key of ['ELECTRON_RUN_AS_NODE','CREW_SESSION_ID','CLAUDE_CODE_SESSION_ID','CREW_GOAL_ID','CREW_EXECUTION_ID'])delete env[key];
    const fixtureSource=String.raw`
import json, sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path('plugin').resolve()))
sys.path.insert(0, str(Path('plugin/tests').resolve()))
from test_remote_integration import RemoteIntegrationTests
case=RemoteIntegrationTests('test_login_protects_pages_business_events_files_and_local_only_management')
case.setUp()
(case.home/'runtime.json').write_text(json.dumps({'plugin_root':str(Path('plugin').resolve())}),encoding='utf-8')
actions=[]
controller=case.server.service.remote_access
original_action=controller.action
def action(data):
    actions.append({key:value for key,value in data.items() if key != 'code'})
    return original_action(data)
controller.action=action
controller.listener_factory=lambda owner,bind,port: case.server.service._remote_listener(owner,'127.0.0.1',0)
try:
    with patch('crew.codex_runtime._ObservedClient',side_effect=AssertionError('This smoke must not launch a model')):
        print(json.dumps({'url':case.url,'home':str(case.home),'pid':__import__('os').getpid()}),flush=True)
        for line in sys.stdin:
            command=json.loads(line)
            if command['action']=='audit':
                print(json.dumps({'audit':True,'process_count':len(case.processes),'actions':actions}),flush=True)
            elif command['action']=='shutdown':
                break
finally:
    case.doCleanups()
    print(json.dumps({'closed':True}),flush=True)
`;
    let fixture,electron,info,buffer='',records=[],fixtureError='',electronOutput='';
    try{
      fixture=spawn('python',['-u','-c',fixtureSource],{cwd:root,env,windowsHide:true,stdio:['pipe','pipe','pipe']});
      fixture.stderr.on('data',data=>{fixtureError+=data;process.stderr.write(data);});
      fixture.stdout.on('data',data=>{buffer+=data;let end;while((end=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,end);buffer=buffer.slice(end+1);try{const item=JSON.parse(line);records.push(item);if(item.url)info=item;}catch{}}});
      await until(()=>{if(fixture.exitCode!==null)throw Error('Fixture exited: '+fixtureError);return info;},'isolated Python service');
      const childEnv={...env,CREW_REMOTE_SMOKE_OUTPUT:output,CREW_REMOTE_SMOKE_URL:info.url};
      electron=spawn(require('electron'),['--require',__filename,desktop,'--smoke-test','--isolated-sources','--home',info.home,
        '--service-url='+info.url,'--port','0','--smoke-output',path.join(output,'unused.json'),...(old?['--old-bridge']:[])],
        {cwd:desktop,env:childEnv,windowsHide:true,stdio:['ignore','pipe','pipe']});
      electron.stdout.pipe(process.stdout);electron.stderr.pipe(process.stderr);
      for(const stream of [electron.stdout,electron.stderr])stream.on('data',data=>{electronOutput+=data;});
      const exit=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>{electron.kill();reject(Error('Electron remote smoke timed out'));},120000);electron.once('error',error=>{clearTimeout(timer);reject(error);});electron.once('exit',code=>{clearTimeout(timer);resolve(code);});});
      await fs.writeFile(path.join(output,'launch.json'),JSON.stringify({exit,output:electronOutput,home:info.home},null,2));
      fixture.stdin.write(JSON.stringify({action:'audit'})+'\n');await until(()=>records.some(item=>item.audit),'no-model audit');
      const evidence=JSON.parse(await fs.readFile(path.join(output,'evidence.json'),'utf8'));
      evidence.fixtureAudit=records.find(item=>item.audit);evidence.fixturePid=info.pid;
      await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));
      assert.equal(exit,0,'Electron journey failed; see '+output);assert.equal(evidence.ok,true);assert.equal(evidence.home,info.home);
      assert.equal(evidence.fixtureAudit.process_count,0,'No carrier may be started by this journey');
      assert.deepEqual(evidence.fixtureAudit.actions.map(item=>item.action),['start','pair','revoke','stop']);
      assert.deepEqual(evidence.errors,[]);
      assert.equal((await request(info.url,'/api/state?project=demo')).status,200,'Closing the shell must leave its borrowed fixture alive');
      console.log(JSON.stringify({ok:true,checks:evidence.checks.length,screenshots:evidence.screenshots.length,noModelTurns:true,evidence:path.join(output,'evidence.json')}));
    }finally{
      if(electron&&electron.exitCode===null)electron.kill();
      if(fixture&&fixture.exitCode===null){fixture.stdin.write(JSON.stringify({action:'shutdown'})+'\n');await new Promise((resolve,reject)=>{const timer=setTimeout(()=>{fixture.kill();reject(Error('Fixture did not shut down gracefully'));},8000);fixture.once('exit',()=>{clearTimeout(timer);resolve();});});}
      if(info){await assert.rejects(fs.access(path.join(info.home,'web.json')),{code:'ENOENT'});}
    }
  })().catch(error=>{console.error(error);process.exitCode=1;});
}else{
  const {app,ipcMain}=require('electron');
  console.log('Remote access smoke hook loaded');
  const evidence={checks:[],errors:[],screenshots:[],oldBridge:old,
    scope:'Actual source Electron window, main/preload/settings, real HTTP Service/SQLite. Fixture owns a loopback:0 listener and fake carriers; no AI or system firewall changes.'};
  app.once('browser-window-created',(_event,window)=>{
    const wc=window.webContents;wc.setBackgroundThrottling(false);
    ipcMain.removeHandler('desktop:ready');ipcMain.handle('desktop:ready',()=>{});
    if(old)ipcMain.removeHandler('desktop:remote-access-read');
    wc.on('console-message',event=>{if(event.level==='error')evidence.errors.push(event.message);});
    wc.once('did-finish-load',async()=>{
      const js=source=>wc.executeJavaScript(source,true);
      async function wait(source,label){await until(async()=>{const error=await js('document.querySelector(".remote-status.cs-error")?.textContent||""');if(error)throw Error(error);return js(source);},label);}
      async function click(selector){
        await wait(`Boolean(document.querySelector(${JSON.stringify(selector)}))`,'click target '+selector);
        const point=await js(`(()=>{const node=document.querySelector(${JSON.stringify(selector)});node.scrollIntoView({block:'center'});const b=node.getBoundingClientRect();if(node.disabled||!b.width||!b.height)throw Error('Button is unavailable');return{x:Math.round(b.x+b.width/2),y:Math.round(b.y+b.height/2)};})()`);
        wc.sendInputEvent({type:'mouseMove',...point});wc.sendInputEvent({type:'mouseDown',button:'left',clickCount:1,...point});wc.sendInputEvent({type:'mouseUp',button:'left',clickCount:1,...point});
      }
      async function shot(name){
        await js('(async()=>{await document.fonts.ready;for(const animation of document.getAnimations()){if(animation.effect?.getTiming().iterations!==Infinity)animation.finish();}await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));})()');
        const filename=path.join(output,name+'.png');await fs.writeFile(filename,(await wc.capturePage(undefined,{stayHidden:true,stayAwake:true})).toPNG());evidence.screenshots.push(filename);
      }
      try{
        await fs.mkdir(output,{recursive:true});evidence.home=process.argv[process.argv.indexOf('--home')+1];
        await wait('Boolean(window.crewDesktop)&&document.querySelector("#service").textContent==="已连接"','native home connected');
        const boot=await js('window.crewDesktop.bootstrap()');assert.equal(boot.service.url,process.env.CREW_REMOTE_SMOKE_URL);
        evidence.page=wc.getURL();evidence.desktopVersion=app.getVersion();evidence.service=boot.service;
        await shot('01-home');evidence.checks.push('Native home connects only to the private test Service');
        await click('#nav-settings');await wait('Boolean(document.querySelector("[data-settings-category=remote]"))','LAN settings category');await shot('02-settings');
        await click('[data-settings-category=remote]');await wait('Boolean(document.querySelector("[data-remote-action=start]"))','disabled LAN panel');await shot('03-disabled');
        evidence.checks.push('Real native settings inject the shared panel through trusted IPC');
        await click('[data-remote-action=start]');await wait('Boolean(document.querySelector("[data-remote-action=pair]"))&&!document.querySelector("[data-remote-action=pair]").disabled','access enabled');
        const snapshot=(await request(process.env.CREW_REMOTE_SMOKE_URL,'/api/remote-access')).value;
        assert.equal(snapshot.enabled,true);const remote='http://127.0.0.1:'+snapshot.port;
        assert.equal((await request(remote,'/api/state?project=demo')).status,401);
        await shot('04-enabled');evidence.checks.push('The UI starts a real isolated listener whose API rejects anonymous access');
        await click('[data-remote-action=pair]');await wait('Boolean(document.querySelector(".remote-pair strong"))','one-time pairing code');
        const code=await js('document.querySelector(".remote-pair strong").textContent');assert.match(code,/^\d{8}$/);await shot('05-pair-code');
        const login=await request(remote,'/remote/login',{code,device_name:'桌面验收设备'});assert.equal(login.status,200);
        const cookie=login.headers['set-cookie'][0].split(';',1)[0];assert.equal((await request(remote,'/api/state?project=demo',undefined,cookie)).status,200);
        await wait('Boolean(document.querySelector("[data-remote-action=revoke]"))&&!document.querySelector(".remote-pair strong")','paired device appears and used code disappears');
        await shot('06-paired');evidence.checks.push('A real HTTP browser session pairs once, reads state, and appears in native settings');
        await click('[data-remote-action=revoke]');await wait('!document.querySelector("[data-remote-action=revoke]")','paired device removed');
        assert.equal((await request(remote,'/api/state?project=demo',undefined,cookie)).status,401);
        await shot('07-revoked');evidence.checks.push('Removing the device in the native panel invalidates its real cookie');
        await click('[data-remote-action=stop]');await wait('Boolean(document.querySelector("[data-remote-action=start]"))','access disabled again');
        assert.equal((await request(process.env.CREW_REMOTE_SMOKE_URL,'/api/state?project=demo')).status,200);
        await assert.rejects(request(remote,'/api/state?project=demo'),/ECONNREFUSED|ECONNRESET/);
        await shot('08-stopped');evidence.checks.push('Closing access shuts its socket while the same local Service remains available');
        await click('#nav-workspaces');await wait('document.querySelector("#settings-page").hidden','return to workspaces');await shot('09-returned');
        evidence.checks.push('Leaving settings restores the native workspace home');
        assert.deepEqual(evidence.errors,[]);evidence.ok=true;
      }catch(error){evidence.errors.push(error.stack||String(error));try{await shot('failure');}catch(caught){evidence.errors.push(String(caught));}console.error(error);process.exitCode=1;}
      await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));app.quit();
    });
  });
}
