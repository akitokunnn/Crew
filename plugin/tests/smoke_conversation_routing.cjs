'use strict';
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict'),{spawn}=require('node:child_process');
const root=path.resolve(__dirname,'../..'),desktop=path.join(root,'desktop'),out=path.join(desktop,'smoke-output/conversation-routing');
if(!process.versions.electron){
  (async()=>{
    await fs.mkdir(path.join(desktop,'.cache'),{recursive:true});
    const home=await fs.mkdtemp(path.join(desktop,'.cache/conversation-routing-')),env={...process.env};
    for(const key of ['ELECTRON_RUN_AS_NODE','CREW_SESSION_ID','CLAUDE_CODE_SESSION_ID'])delete env[key];
    const child=spawn(require(require.resolve('electron',{paths:[desktop]})),['--require',__filename,desktop,'--crew-routing-home='+home],{cwd:desktop,env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    child.stdout.pipe(process.stdout);child.stderr.pipe(process.stderr);
    const timer=setTimeout(()=>{child.kill();process.exitCode=1;},120000);
    child.once('exit',async code=>{clearTimeout(timer);child.stdout.destroy();child.stderr.destroy();try{const result=JSON.parse(await fs.readFile(path.join(out,'evidence.json'),'utf8'));assert.equal(code,0);assert.equal(result.home,home);assert.equal(result.ok,true);console.log(JSON.stringify({ok:true,checks:result.checks.length,evidence:path.join(out,'evidence.json')}));}catch(error){console.error(error);process.exitCode=1;}});
  })().catch(error=>{console.error(error);process.exitCode=1;});
}else{
  require.cache[require.resolve(path.join(desktop,'src/main.cjs'))]={id:path.join(desktop,'src/main.cjs'),filename:path.join(desktop,'src/main.cjs'),loaded:true,exports:{}};
  const {app,BrowserWindow,nativeTheme}=require('electron'),home=process.argv.find(value=>value.startsWith('--crew-routing-home=')).slice('--crew-routing-home='.length);
  require('node:fs').mkdirSync(path.join(home,'electron'),{recursive:true});app.setPath('userData',path.join(home,'electron'));
  const evidence={home,checks:[],screenshots:[],errors:[],modelTurnsStarted:0,carrier:'FakeProcess only'},messages=[];
  let fixture,window,closed=false;
  app.whenReady().then(async()=>{
    try{
      await fs.mkdir(out,{recursive:true});
      const env={...process.env,PYTHONIOENCODING:'utf-8'};
      for(const key of ['CREW_SESSION_ID','CLAUDE_CODE_SESSION_ID'])delete env[key];
      fixture=spawn('python',['-u',path.join(root,'plugin/tests/conversation_routing_fixture.py')],{cwd:path.join(root,'plugin'),env,windowsHide:true,stdio:['pipe','pipe','pipe']});
      let pending='',bootstrap;fixture.stderr.on('data',chunk=>process.stderr.write(chunk));
      fixture.stdout.on('data',chunk=>{pending+=chunk;let index;while((index=pending.indexOf('\n'))>=0){const line=pending.slice(0,index);pending=pending.slice(index+1);try{const value=JSON.parse(line);messages.push(value);if(value.url)bootstrap=value;if(value.closed)closed=true;}catch(_){}}});
      const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
      async function until(check,label){const deadline=Date.now()+15000;while(Date.now()<deadline){if(await check())return;await pause(40);}throw Error('Timed out: '+label);}
      await until(()=>bootstrap,'isolated fixture');assert.notEqual(new URL(bootstrap.url).port,'47811');evidence.fixture=bootstrap;
      window=new BrowserWindow({width:1280,height:900,show:false,webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false,backgroundThrottling:false,offscreen:true}});
      const wc=window.webContents,js=source=>wc.executeJavaScript(source,true),wait=(source,label=source)=>until(()=>js(source),label);
      wc.on('console-message',event=>{if(event.level==='error')evidence.errors.push(event.message);});
      async function check(label,source){await wait(source,label);assert(await js(source),label);evidence.checks.push(label);}
      const click=selector=>js(`document.querySelector(${JSON.stringify(selector)}).click();true;`);
      async function type(text){await js(`document.querySelector('#chat-host textarea').value=${JSON.stringify(text)};document.querySelector('#chat-host textarea').dispatchEvent(new Event('input',{bubbles:true}));true;`);}
      async function capture(name){await js('document.fonts.ready.then(()=>true)');await pause(120);wc.debugger.attach('1.3');try{const result=await wc.debugger.sendCommand('Page.captureScreenshot',{format:'png',fromSurface:true});const file=path.join(out,name+'.png');await fs.writeFile(file,Buffer.from(result.data,'base64'));evidence.screenshots.push(file);}finally{wc.debugger.detach();}}
      await window.loadURL(bootstrap.url+'/?project=demo#leader='+bootstrap.leader);
      await wait('document.querySelector("#goal-chat-hint")?.textContent.includes("2 个任务")');
      await check('real HTTP serves every stylesheet and script declared by the entry page',`(async()=>{const paths=[...document.querySelectorAll('link[rel=stylesheet],script[src]')].map(el=>el.href||el.src);return (await Promise.all(paths.map(url=>fetch(url).then(r=>r.status)))).every(status=>status===200)})()`);
      await click('[data-act="right-open"]');await pause(450);
      await check('real backend positions the original architecture in the visible right column','(()=>{const root=document.querySelector("#chat-attention"),p=root.getBoundingClientRect(),r=document.querySelector("#right-panel").getBoundingClientRect();return getComputedStyle(root).position==="fixed"&&r.width>200&&r.height>300&&r.right<=innerWidth&&Math.abs(p.top-r.top)<1&&!!document.querySelector("#right-panel .node")})()');
      await js('window.architectureTop=document.querySelector("#right-panel").getBoundingClientRect().top');
      await click('[data-act="attention"]');await pause(450);
      await check('real backend opens pending actions beneath the button and pushes architecture down','(()=>{const a=document.querySelector("#side-attention-section").getBoundingClientRect(),r=document.querySelector("#right-panel").getBoundingClientRect();return a.width>200&&a.height>100&&a.top>0&&a.right<=innerWidth&&a.bottom<innerHeight&&r.top>architectureTop&&r.top>=a.bottom&&!!document.querySelector("#side-attention-section .todo-card")})()');
      await capture('00-real-http-pending-and-architecture');
      await click('[data-act="attention"]');await pause(450);
      await check('closing pending restores architecture without leaving the conversation','Math.abs(document.querySelector("#right-panel").getBoundingClientRect().top-architectureTop)<1&&!document.querySelector("#chat-host textarea").disabled');
      await click('[data-act="right-open"]');await pause(450);
      await check('two active tasks do not make the idle general conversation wait for a model','!document.querySelector("#chat-host .chat-activity")&&!document.querySelector("#chat-host textarea").disabled');
      await type('主对话草稿');await click('[data-goal-act="list"]');
      await wait(`!!document.querySelector('[data-goal-act="open"][data-goal-id="${bootstrap.goals[0]}"]')`);
      await click(`[data-goal-act="open"][data-goal-id="${bootstrap.goals[0]}"]`);
      await wait('!!document.querySelector("#chat-host .chat-activity")');await type('任务草稿');
      await click('[data-goal-act="general"]');await wait('document.querySelector("#chat-host textarea")?.value==="主对话草稿"');
      fixture.stdin.write('request\n');await until(()=>messages.some(value=>value.request),'cross-leader request');
      await wait('document.querySelector("#chat-host")?.textContent.includes("请确认接口格式")');
      await check('a cross-leader request enters general chat while its independent draft survives','document.querySelectorAll("#chat-host .chat-activity").length===1&&document.querySelector("#chat-host textarea").value==="主对话草稿"');
      await capture('01-general-coordination');
      await click('[data-goal-act="list"]');await wait(`!!document.querySelector('[data-goal-act="open"][data-goal-id="${bootstrap.goals[0]}"]')`);await click(`[data-goal-act="open"][data-goal-id="${bootstrap.goals[0]}"]`);
      await wait('document.querySelector("#chat-host textarea")?.value==="任务草稿"&&!document.querySelector("#chat-host textarea").disabled&&!!document.querySelector("#chat-host .chat-activity")');
      await check('task history and drafts remain independent from incoming general collaboration','!!document.querySelector("#chat-host .chat-activity")&&!document.querySelector("#chat-host").textContent.includes("请确认接口格式")');
      nativeTheme.themeSource='dark';window.setSize(850,820);await capture('02-task-keeps-working');
      await click('[data-goal-act="general"]');await wait('document.querySelector("#chat-host textarea")?.value==="主对话草稿"');
      fixture.stdin.write('reply\n');await until(()=>messages.some(value=>value.replied),'reply routed to original task');
      await wait('!document.querySelector("#chat-host .chat-activity")');
      await check('a completed coordination reply clears only general progress while tasks stay active','document.querySelector("#goal-chat-hint").textContent.includes("2 个任务")&&document.querySelector("#chat-host textarea").value==="主对话草稿"');
      await capture('03-general-idle-tasks-active');
      evidence.reply=messages.find(value=>value.replied);assert.equal(evidence.reply.parallel_tasks,2);assert.equal(evidence.reply.source_general_started,false);assert.equal(evidence.errors.length,0,JSON.stringify(evidence.errors));
      fixture.stdin.end('shutdown\n');await until(()=>closed,'fixture shutdown');evidence.ok=true;
      await fs.writeFile(path.join(out,'evidence.json'),JSON.stringify(evidence,null,2));app.quit();
    }catch(error){evidence.errors.push(error.stack||String(error));fixture?.stdin.end('shutdown\n');await fs.mkdir(out,{recursive:true});await fs.writeFile(path.join(out,'evidence.json'),JSON.stringify(evidence,null,2));console.error(error);process.exitCode=1;app.quit();}
  });
}
