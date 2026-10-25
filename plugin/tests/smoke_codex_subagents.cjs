'use strict';
// Hidden Electron renderer + real isolated HTTP + mocked Codex RPC. No real AI turns.
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict'),{spawn}=require('node:child_process');
const root=path.resolve(__dirname,'../..'),desktop=path.join(root,'desktop'),out=path.join(desktop,'smoke-output/native-subagents');
if(!process.versions.electron){
  (async()=>{
    await fs.mkdir(path.join(desktop,'.cache'),{recursive:true});
    const home=await fs.mkdtemp(path.join(desktop,'.cache/native-subagents-')),env={...process.env};
    for(const key of ['ELECTRON_RUN_AS_NODE','CREW_SESSION_ID','CLAUDE_CODE_SESSION_ID'])delete env[key];
    const child=spawn(require(require.resolve('electron',{paths:[desktop]})),['--require',__filename,desktop,'--crew-subagent-home='+home],{cwd:desktop,env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    child.stdout.pipe(process.stdout);child.stderr.pipe(process.stderr);
    const timer=setTimeout(()=>{child.kill();process.exitCode=1;},80000);
    child.once('error',error=>{clearTimeout(timer);console.error(error);process.exitCode=1;});
    child.once('exit',async code=>{clearTimeout(timer);child.stdout.destroy();child.stderr.destroy();try{const result=JSON.parse(await fs.readFile(path.join(out,'evidence.json'),'utf8'));assert.equal(result.electronHome,home);assert.equal(code,0);assert(result.ok);console.log(JSON.stringify({ok:true,checks:result.checks.length,evidence:path.join(out,'evidence.json')}));}catch(error){console.error(error);process.exitCode=1;}});
  })().catch(error=>{console.error(error);process.exitCode=1;});
}else{
  const entry=path.join(desktop,'src/main.cjs');require.cache[require.resolve(entry)]={id:entry,filename:entry,loaded:true,exports:{}};
  const {app,BrowserWindow}=require('electron'),home=process.argv.find(x=>x.startsWith('--crew-subagent-home=')).split('=').slice(1).join('=');
  require('node:fs').mkdirSync(path.join(home,'electron'),{recursive:true});app.setPath('userData',path.join(home,'electron'));
  const evidence={electronHome:home,checks:[],screenshots:[],errors:[],modelProcess:'FakeClient only',realAiTurns:0};
  const source=`import sys,json\nfrom pathlib import Path\nsys.path[:0]=[sys.argv[1],str(Path(sys.argv[1])/'tests')]\nfrom test_codex_subagents import NativeChildHttpTests,collab\ncase=NativeChildHttpTests('test_leader_has_three_child_task_rows_without_registering_members_and_keeps_updates_after_parent_turn');case.setUp()\ntry:\n case.plugin=Path(sys.argv[1]);case.leader();running,client=case.make_running();running.send('核对登录、界面和结果','initial')\n server,_=case.start_http(running)\n for index,name in enumerate(['检查登录','验证界面','核对结果'],1):\n  client.emit('item/completed',item={'id':'start-'+str(index),'type':'subAgentActivity','kind':'started','agentThreadId':'child-'+str(index),'agentPath':'/root/'+name})\n print(json.dumps({'url':'http://127.0.0.1:'+str(server.server_port),'home':str(case.home)}),flush=True)\n for line in sys.stdin:\n  command=line.strip()\n  if command=='finish':\n   for index,status in enumerate(['completed','failed','interrupted'],1):\n    client.emit('turn/completed',threadId='child-'+str(index),turn={'id':'turn-'+str(index),'status':status,'error':{'message':'隔离验收：未找到预期控件'} if status=='failed' else None})\n   client.finish()\n   print(json.dumps({'finished':True}),flush=True)\n  elif command=='disconnect':\n   client.emit('item/completed',item=collab('spawn-offline',children=('child-4',),prompt='等待断线确认'));running.close();assert running.reader_done.wait(2)\n   print(json.dumps({'disconnected':True}),flush=True)\n  elif command=='shutdown':break\nfinally:\n case.doCleanups();print(json.dumps({'closed':True}),flush=True)\n`;
  let fixture,window,closed=false;
  app.whenReady().then(async()=>{
    try{
      await fs.mkdir(out,{recursive:true});
      fixture=spawn('python',['-u','-c',source,path.join(root,'plugin')],{cwd:root,windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'},stdio:['pipe','pipe','pipe']});
      let pending='',bootstrap;const messages=[];
      fixture.stderr.on('data',chunk=>process.stderr.write(chunk));fixture.stdout.on('data',chunk=>{pending+=chunk;let i;while((i=pending.indexOf('\n'))>=0){const line=pending.slice(0,i);pending=pending.slice(i+1);try{const row=JSON.parse(line);messages.push(row);if(row.url)bootstrap=row;if(row.closed)closed=true;}catch(_){}}});
      const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
      async function until(fn,label){const end=Date.now()+12000;while(Date.now()<end){if(await fn())return;await pause(40);}throw Error('Timed out: '+label);}
      await until(()=>bootstrap,'fixture');assert(!['47811','7122'].includes(new URL(bootstrap.url).port));evidence.fixture=bootstrap;
      window=new BrowserWindow({width:1180,height:820,show:false,webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false,backgroundThrottling:false,offscreen:true}});
      const wc=window.webContents,js=source=>wc.executeJavaScript(source,true),wait=(source,label)=>until(()=>js(source),label);
      wc.on('console-message',event=>{if(event.level==='error')evidence.errors.push(event.message);});
      async function check(label,source){assert(await js(source),label);evidence.checks.push(label);}
      async function capture(name){await pause(150);const image=await Promise.race([wc.capturePage(undefined,{stayHidden:true,stayAwake:true}),pause(3000).then(()=>{throw Error('Screenshot timed out: '+name);})]);const file=path.join(out,name+'.png');assert(!image.isEmpty());await fs.writeFile(file,image.toPNG());evidence.screenshots.push(file);}
      await window.loadURL(bootstrap.url+'/?project=demo#all');
      await wait('document.querySelectorAll(".leader-node .cx .spin").length===3','three spinning native children');
      await check('three native children appear under leader without roster members','document.querySelectorAll(".member-node").length===0&&document.querySelectorAll(".members .session-node").length===0&&document.querySelectorAll(".leader-node [data-codex-source=native_subagent]").length===3');
      await capture('01-running');
      fixture.stdin.write('finish\n');await until(()=>messages.some(x=>x.finished),'fixture completed');
      await wait('document.querySelector(".leader-node .codex-status-succeeded")&&document.querySelector(".leader-node .codex-status-failed")&&document.querySelector(".leader-node .codex-status-interrupted")','three distinct terminal states');
      await check('only successful child is checked and failed child preserves cause','document.querySelectorAll(".leader-node .xs.ok").length===1&&document.querySelectorAll(".leader-node .spin").length===0&&document.querySelector(".codex-status-failed").title.includes("未找到预期控件")');
      await capture('02-finished');
      await js('document.querySelector(".leader-node").click();void 0;');await wait('document.querySelector("[data-act=right-open]")','chat header');
      await js('document.querySelector("[data-act=right-open]").click();void 0;');
      await wait('document.querySelectorAll("#right-panel .leader-node .cx").length===3','chat architecture');
      await check('chat side architecture uses the same native task rows','document.querySelector("#right-panel .codex-status-failed").textContent.includes("失败")');
      await capture('03-chat-architecture');
      fixture.stdin.write('disconnect\n');await until(()=>messages.some(x=>x.disconnected),'fixture disconnected');
      await wait('document.querySelector("#right-panel .codex-status-unknown")','disconnected child');
      await check('disconnected unfinished child shows unknown and no check or spinner','document.querySelector("#right-panel .codex-status-unknown").textContent.includes("状态未确认")&&!document.querySelector("#right-panel .codex-status-unknown .spin")&&!document.querySelector("#right-panel .codex-status-unknown .ok")');
      await capture('04-disconnected');assert.deepEqual(evidence.errors,[]);evidence.ok=true;
    }catch(error){evidence.errors.push(error.stack||String(error));evidence.ok=false;}
    finally{
      if(fixture){fixture.stdin.write('shutdown\n');const deadline=Date.now()+5000;while(!closed&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,50));if(!closed){fixture.kill();evidence.errors.push('fixture did not shut down');evidence.ok=false;}}
      await fs.writeFile(path.join(out,'evidence.json'),JSON.stringify(evidence,null,2));if(window&&!window.isDestroyed())window.destroy();app.exit(evidence.ok?0:1);
    }
  });
}
