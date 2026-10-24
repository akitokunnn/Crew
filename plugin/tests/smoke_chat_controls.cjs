'use strict';
// Isolated renderer + real HTTP + FakeProcess. No installed model CLI is invoked.
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict'),{spawn}=require('node:child_process');
const root=path.resolve(__dirname,'../..'),desktop=path.join(root,'desktop'),out=path.join(desktop,'smoke-output/chat-controls');
if(!process.versions.electron){
  (async()=>{
    const home=await fs.mkdtemp(path.join(desktop,'.cache/chat-controls-')),env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(require(require.resolve('electron',{paths:[desktop]})),['--require',__filename,desktop,'--crew-chat-home='+home],{cwd:desktop,env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    child.stdout.pipe(process.stdout);child.stderr.pipe(process.stderr);const timer=setTimeout(()=>{child.kill();process.exitCode=1;},80000);
    child.once('exit',async code=>{clearTimeout(timer);child.stdout.destroy();child.stderr.destroy();try{const result=JSON.parse(await fs.readFile(path.join(out,'evidence.json'),'utf8'));assert.equal(result.home,home);assert.equal(code,0);assert.equal(result.ok,true);console.log(JSON.stringify({ok:true,checks:result.checks.length,evidence:path.join(out,'evidence.json')}));}catch(error){console.error(error);process.exitCode=1;}});
  })().catch(error=>{console.error(error);process.exitCode=1;});
}else{
  // Use Electron's normal desktop entry solely as a loader for this test app.
  require.cache[require.resolve(path.join(desktop,'src/main.cjs'))]={id:path.join(desktop,'src/main.cjs'),filename:path.join(desktop,'src/main.cjs'),loaded:true,exports:{}};
  const {app,BrowserWindow}=require('electron'),home=process.argv.find(x=>x.startsWith('--crew-chat-home=')).slice('--crew-chat-home='.length);
  require('node:fs').mkdirSync(path.join(home,'electron'),{recursive:true});app.setPath('userData',path.join(home,'electron'));
  const evidence={home,checks:[],screenshots:[],errors:[],modelProcess:'FakeProcess only; actual HTTP handler and renderer',modelTurnsStarted:0};
  const source=`import sys,json,threading\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1]);sys.path.insert(0,str(Path(sys.argv[1])/'tests'))\nfrom test_chat_http import ChatHttpTests\nfrom crew import web\ncase=ChatHttpTests('test_four_image_media_types_round_trip_cli_blocks_files_and_history_timestamps');case.setUp()\ntry:\n sid,process=case.start_leader('界面中断验收')\n code,result,_=case.request('/api/chat/send?project=demo',{'session_id':sid,'text':'这是隔离界面测试，由假进程等待中断。'})\n assert code==200,result\n class OnceUnavailable(web.Handler):\n  failed=False\n  def do_GET(self):\n   if self.path.startswith('/api/chat/history?') and not type(self).failed:\n    type(self).failed=True;self._send(503,{'error':'模拟一次暂时断连'});return\n   return super().do_GET()\n case.server.RequestHandlerClass=OnceUnavailable\n print(json.dumps({'url':case.url,'sid':sid}),flush=True)\n for line in sys.stdin:\n  if line.strip()=='finish':\n   case.finish_turn(sid,process);print(json.dumps({'finished':True,'process_alive':process.poll() is None}),flush=True)\n  elif line.strip()=='shutdown':break\nfinally:\n case.doCleanups()\n print(json.dumps({'closed':True}),flush=True)\n`;
  let fixture,window,ending=false,fixtureClosed=false;
  app.whenReady().then(async()=>{
    try{
      await fs.mkdir(out,{recursive:true});
      fixture=spawn('python',['-u','-c',source,path.join(root,'plugin')],{cwd:root,windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'},stdio:['pipe','pipe','pipe']});
      let pending='',bootstrap;const messages=[];fixture.stderr.on('data',chunk=>process.stderr.write(chunk));
      fixture.stdout.on('data',chunk=>{pending+=chunk;let i;while((i=pending.indexOf('\n'))>=0){const line=pending.slice(0,i);pending=pending.slice(i+1);try{const row=JSON.parse(line);messages.push(row);if(row.url)bootstrap=row;if(row.closed)fixtureClosed=true;}catch(_){}}});
      const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
      async function until(fn,label){const end=Date.now()+12000;while(Date.now()<end){if(await fn())return;await pause(35);}throw Error('Timed out: '+label);}
      await until(()=>bootstrap,'fixture');assert.notEqual(new URL(bootstrap.url).port,'47811');evidence.fixture=bootstrap;
      window=new BrowserWindow({width:1220,height:850,show:false,webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false,backgroundThrottling:false}});
      const wc=window.webContents,js=source=>wc.executeJavaScript(source,true),wait=(s,label)=>until(()=>js(s),label);
      wc.on('console-message',event=>{if(event.level==='error'&&!event.message.includes('503'))evidence.errors.push(event.message);});
      async function check(label,source){assert(await js(source),label);evidence.checks.push(label);}
      async function capture(name){await js('document.fonts.ready.then(()=>true)');await pause(150);wc.debugger.attach('1.3');try{const result=await wc.debugger.sendCommand('Page.captureScreenshot',{format:'png',fromSurface:true,captureBeyondViewport:false});const file=path.join(out,name+'.png');await fs.writeFile(file,Buffer.from(result.data,'base64'));evidence.screenshots.push(file);}finally{wc.debugger.detach();}}
      await window.loadURL(bootstrap.url+'/?project=demo#leader='+bootstrap.sid);
      await wait('document.querySelector(".chat-connection-error")?.textContent.includes("自动重试")','initial retry banner');
      await js('document.querySelector(".composer textarea").value="中断时保留的草稿";document.querySelector(".composer textarea").dispatchEvent(new Event("input",{bubbles:true}));void 0;');
      await check('a one-off real HTTP history failure displays automatic retry without erasing the composer','!document.querySelector(".chat-connection-error").hidden&&document.querySelector(".send").disabled');
      await capture('01-history-retry');
      await wait('document.querySelector(".chat-connection-error").hidden&&!document.querySelector(".chat-stop").disabled','automatic recovery');
      await check('the busy owned turn exposes an explicit interrupt beside the send control','!document.querySelector(".chat-stop").hidden&&document.querySelector(".chat-stop").textContent==="中断"&&document.querySelector(".composer textarea").value==="中断时保留的草稿"');
      await capture('02-busy-interrupt');
      await js('document.querySelector(".chat-stop").click()');await wait('document.querySelector(".chat-stop").textContent.includes("正在中断")','pending stop');await pause(150);
      await check('the actual stop request does not pretend that acknowledgement means completion','document.querySelector(".chat-stop").disabled&&!document.querySelector(".chat-stop").hidden&&document.querySelector(".chat-interrupt-status").textContent.includes("等待本轮停止")');
      await capture('03-waiting-for-stop');fixture.stdin.write('finish\n');
      await wait('document.querySelector(".chat-stop").hidden&&!document.querySelector(".send").disabled','confirmed idle');
      await check('an actual fake CLI result confirms stopped state and leaves the session draft intact','document.querySelector(".chat-interrupt-status").textContent.includes("本轮已停止")&&document.querySelector(".composer textarea").value==="中断时保留的草稿"');
      await capture('04-stopped-draft');assert(messages.some(row=>row.finished&&row.process_alive));assert.equal(evidence.errors.length,0,JSON.stringify(evidence.errors));
      fixture.stdin.end('shutdown\n');await until(()=>fixtureClosed,'fixture closed');evidence.fixtureExited=true;evidence.ok=true;
      await fs.writeFile(path.join(out,'evidence.json'),JSON.stringify(evidence,null,2));ending=true;app.quit();
    }catch(error){evidence.errors.push(error.stack||String(error));fixture?.stdin.end('shutdown\n');await fs.mkdir(out,{recursive:true});await fs.writeFile(path.join(out,'evidence.json'),JSON.stringify(evidence,null,2));console.error(error);process.exitCode=1;ending=true;app.quit();}
  });
  app.on('before-quit',event=>{if(!ending){event.preventDefault();ending=true;fixture?.stdin.end('shutdown\n');setTimeout(()=>app.quit(),200);}});
}
