'use strict';
// Both native carriers, real isolated HTTP and hidden Electron; fake AI processes only.
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict'),{spawn}=require('node:child_process');
const root=path.resolve(__dirname,'../..'),desktop=path.join(root,'desktop');
const runtime=process.argv.find(x=>x.startsWith('--native-runtime='))?.split('=')[1]||'codex';
const out=path.join(desktop,'smoke-output/native-subagents',runtime);
if(!process.versions.electron){
  (async()=>{
    await fs.mkdir(path.join(desktop,'.cache'),{recursive:true});
    for(const carrier of ['claude','codex']){
      const home=await fs.mkdtemp(path.join(desktop,'.cache/native-subagents-')),env={...process.env};
      for(const key of ['ELECTRON_RUN_AS_NODE','CREW_SESSION_ID','CLAUDE_CODE_SESSION_ID'])delete env[key];
      await new Promise((resolve,reject)=>{
        const child=spawn(require(require.resolve('electron',{paths:[desktop]})),['--require',__filename,desktop,'--crew-subagent-home='+home,'--native-runtime='+carrier],{cwd:desktop,env,windowsHide:true,stdio:['ignore','pipe','pipe']});
        child.stdout.pipe(process.stdout);child.stderr.pipe(process.stderr);
        const timer=setTimeout(()=>{child.kill();reject(Error('Native smoke timed out: '+carrier));},80000);
        child.once('error',error=>{clearTimeout(timer);reject(error);});
        child.once('exit',async code=>{clearTimeout(timer);child.stdout.destroy();child.stderr.destroy();try{
          assert.equal(code,0,carrier+' smoke exit');const file=path.join(desktop,'smoke-output/native-subagents',carrier,'evidence.json');
          const result=JSON.parse(await fs.readFile(file,'utf8'));assert.equal(result.electronHome,home);assert(result.ok);
          console.log(JSON.stringify({runtime:carrier,ok:true,checks:result.checks.length,evidence:file}));resolve();
        }catch(error){reject(error);}});
      });
    }
  })().catch(error=>{console.error(error);process.exitCode=1;});
}else{
  const entry=path.join(desktop,'src/main.cjs');require.cache[require.resolve(entry)]={id:entry,filename:entry,loaded:true,exports:{}};
  const {app,BrowserWindow}=require('electron'),home=process.argv.find(x=>x.startsWith('--crew-subagent-home=')).split('=').slice(1).join('=');
  require('node:fs').mkdirSync(path.join(home,'electron'),{recursive:true});app.setPath('userData',path.join(home,'electron'));
  const evidence={electronHome:home,runtime,checks:[],screenshots:[],errors:[],modelProcess:'FakeClient/FakeProcess only',realAiTurns:0};
  let fixture,window,closed=false;
  app.whenReady().then(async()=>{
    try{
      await fs.mkdir(out,{recursive:true});
      fixture=spawn('python',['-u',path.join(__dirname,'native_subagents_fixture.py'),runtime],{cwd:root,windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'},stdio:['pipe','pipe','pipe']});
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
      await check('three native children appear under leader without roster members','document.querySelectorAll(".members .session-node").length===0&&document.querySelectorAll(".leader-node [data-codex-source=native_subagent]").length===3');
      await check('native carrier is correctly labelled',`document.querySelector(".leader-node .cx").title.includes(${JSON.stringify(runtime==='claude'?'Claude 子代理':'Codex 子代理')})`);
      const compactNames='Array.from(document.querySelectorAll(".leader-node .cx")).every(row=>{const n=row.querySelector(".n"),s=row.querySelector(".s");return Array.from(n.textContent).length<=48&&!n.querySelector("br,a,p")&&row.getBoundingClientRect().height<=parseFloat(getComputedStyle(row).lineHeight)+2&&s.getBoundingClientRect().right<=row.getBoundingClientRect().right+1;})';
      evidence.names=await js('Array.from(document.querySelectorAll(".leader-node .cx .n")).map(n=>({name:n.textContent,details:n.title}))');
      await check('task instructions never replace native names','Array.from(document.querySelectorAll(".leader-node .cx .n")).some(n=>n.textContent==="检查登录"&&n.title.includes("用户的原始要求"))');
      await check('legacy multiline prompt names stay in one line and statuses fit',compactNames);
      await capture('01-running');
      fixture.stdin.write('finish\n');await until(()=>messages.some(x=>x.finished),'fixture completed');
      await wait('document.querySelector(".leader-node .codex-status-succeeded")&&document.querySelector(".leader-node .codex-status-failed")&&document.querySelector(".leader-node .codex-status-interrupted")','three distinct terminal states');
      await check('only successful child is checked and failed child preserves cause','document.querySelectorAll(".leader-node .xs.ok").length===1&&document.querySelectorAll(".leader-node .spin").length===0&&document.querySelector(".codex-status-failed").title.includes("未找到预期控件")');
      await capture('02-finished');
      await js('document.querySelector(".leader-node").click();void 0;');await wait('document.querySelector("[data-act=right-open]")','chat header');
      await js('document.querySelector("[data-act=right-open]").click();void 0;');
      await wait('document.querySelectorAll("#right-panel .leader-node .cx").length===3','chat architecture');
      await check('chat side architecture uses the same native task rows','document.querySelector("#right-panel .codex-status-failed").textContent.includes("失败")');
      await check('side architecture also keeps legacy names and status in one line',compactNames);
      await wc.setZoomFactor(1.5);
      await check('names and status remain compact with enlarged text',compactNames);
      await capture('03-chat-architecture');
      await wc.setZoomFactor(1);
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
