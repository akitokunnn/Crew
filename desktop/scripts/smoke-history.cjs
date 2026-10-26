'use strict';
// App-development test harness, not automation of an existing user browser.
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn,spawnSync}=require('node:child_process');
const ROOT=path.resolve(__dirname,'../..');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));

if(!process.versions.electron){
 (async()=>{
  const directory=path.join(ROOT,'desktop/smoke-output/history');await fs.mkdir(directory,{recursive:true});
  const output=await fs.mkdtemp(path.join(directory,'run-'));
  const env={...process.env,PYTHONIOENCODING:'utf-8'};for(const key of ['ELECTRON_RUN_AS_NODE','CREW_SESSION_ID','CLAUDE_CODE_SESSION_ID'])delete env[key];
  const backend=spawn('python',[path.join(__dirname,'history-fixture.py'),output],{cwd:ROOT,env,windowsHide:true,stdio:['pipe','pipe','pipe']});
  let stderr='';backend.stderr.on('data',chunk=>{stderr+=chunk;});
  const metadata=await new Promise((resolve,reject)=>{
   let pending='';const timer=setTimeout(()=>reject(Error('Fixture backend timeout: '+stderr)),30000);
   backend.once('error',error=>{clearTimeout(timer);reject(error);});
   backend.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture backend early exit '+code+': '+stderr));});
   backend.stdout.on('data',chunk=>{pending+=chunk;const index=pending.indexOf('\n');if(index>=0){clearTimeout(timer);try{resolve(JSON.parse(pending.slice(0,index)));}catch(error){reject(error);}}});
  });
  assert(!['7122','47811'].includes(new URL(metadata.url).port));
  let code;
  try{
   const electron=require(require.resolve('electron',{paths:[path.join(ROOT,'desktop')]}));
   const child=spawn(electron,[__filename,'--fixture='+path.join(output,'fixture.json'),'--output='+output],{cwd:ROOT,env,windowsHide:true,stdio:['ignore','pipe','pipe']});
   child.stdout.pipe(process.stdout);child.stderr.on('data',chunk=>{stderr+=chunk;});
   code=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>{child.kill();reject(Error('UI timeout: '+stderr));},120000);child.once('error',error=>{clearTimeout(timer);reject(error);});child.once('exit',value=>{clearTimeout(timer);resolve(value);});});
  }finally{
   backend.stdin.end('shutdown\n');
   await new Promise(resolve=>{const timer=setTimeout(()=>{backend.kill();resolve();},10000);backend.once('exit',()=>{clearTimeout(timer);resolve();});});
   await fs.writeFile(path.join(output,'process-stderr.log'),stderr);
  }
  const evidence=JSON.parse(await fs.readFile(path.join(output,'evidence.json'),'utf8'));
  evidence.backend=JSON.parse(await fs.readFile(path.join(output,'backend-evidence.json'),'utf8'));
  assert.equal(evidence.backend.model_process_starts,0);assert.equal(evidence.backend.owned_server_exited,true);
  evidence.backend.registryRemoved=await fs.access(path.join(metadata.home,'web.json')).then(()=>false,()=>true);
  await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));
  assert.equal(code,0,JSON.stringify(evidence.errors));assert.equal(evidence.ok,true);assert(evidence.backend.registryRemoved);
  console.log(JSON.stringify({ok:true,checks:evidence.checks.length,modelCalls:0,output,evidence:path.join(output,'evidence.json')}));
 })().catch(error=>{console.error(error);process.exitCode=1;});
}else{
 const {app,BrowserWindow}=require('electron');
 const argument=name=>process.argv.find(v=>v.startsWith(name+'='))?.slice(name.length+1);
 const output=argument('--output');
 app.setPath('userData',path.join(output,'electron-profile'));app.disableHardwareAcceleration();
 const evidence={checks:[],screenshots:[],errors:[],requests:[],blockedExternal:[],scope:'real isolated browser renderer + HTTP storage service + verified downloaded package'};
 let main;
 const check=(condition,label)=>{assert(condition,label);evidence.checks.push(label);console.log('PASS '+label);};
 const js=source=>main.webContents.executeJavaScript(source,true);
 async function until(callback,label,timeout=20000){const end=Date.now()+timeout;while(Date.now()<end){if(await callback())return;await pause(50);}throw Error('Timed out: '+label);}
 const wait=(source,label)=>until(()=>js(source),label);
 async function dom(win){win.webContents.debugger.attach('1.3');try{const {root}=await win.webContents.debugger.sendCommand('DOM.getDocument',{});return (await win.webContents.debugger.sendCommand('DOM.getOuterHTML',{nodeId:root.nodeId})).outerHTML;}finally{win.webContents.debugger.detach();}}
 async function capture(win,name){try{await win.webContents.executeJavaScript('document.fonts.ready.then(()=>true)');}catch(_){}await pause(120);const file=path.join(output,name+'.png');await fs.writeFile(file,(await win.webContents.capturePage()).toPNG());evidence.screenshots.push(file);}
 async function popup(selector){
  const created=new Promise(resolve=>main.webContents.once('did-create-window',child=>resolve(child)));
  await js(`document.querySelector(${JSON.stringify(selector)}).click()`);
  const child=await Promise.race([created,pause(10000).then(()=>{throw Error('Popup missing');})]);
  await until(()=>!child.webContents.isLoadingMainFrame(),'popup loaded');
  return child;
 }
 app.whenReady().then(async()=>{
  const fixture=JSON.parse(await fs.readFile(argument('--fixture'),'utf8'));evidence.fixture=fixture;
  check(!['7122','47811'].includes(new URL(fixture.url).port),'fixture uses OS-assigned non-production port');
  main=new BrowserWindow({show:false,width:1440,height:960,webPreferences:{contextIsolation:true,nodeIntegration:false,sandbox:true,offscreen:true,backgroundThrottling:false}});
  main.webContents.on('render-process-gone',(_event,details)=>evidence.errors.push(details));
  main.webContents.session.webRequest.onBeforeRequest((details,callback)=>{
   if(details.url.startsWith(fixture.url+'/')){evidence.requests.push({url:details.url,method:details.method});callback({});}
   else if(details.url==='about:blank'){callback({});}
   else{evidence.blockedExternal.push(details.url);callback({cancel:true});}
  });
  main.webContents.setWindowOpenHandler(({url})=>({action:url.startsWith(fixture.url+'/api/workspace-data/')?'allow':'deny',overrideBrowserWindowOptions:{show:false,width:1000,height:700,webPreferences:{contextIsolation:true,nodeIntegration:false,sandbox:true,offscreen:true,backgroundThrottling:false}}}));
  try{
   await main.loadURL(fixture.url+'/?project='+encodeURIComponent('storage:'+fixture.workspace_id));
   await wait('document.querySelectorAll("#content .card").length===50','initial 50 report cards');
   const initial=await js('({name:document.querySelector("#title").textContent,agents:document.querySelectorAll("#agents>div").length,leaders:Array.from(document.querySelectorAll("#agents>div")).filter(n=>n.textContent.endsWith(" · Leader")).map(n=>n.textContent),reports:document.querySelectorAll("#content .card").length})');
   check(initial.name==='股市预警 · UI隔离验收','real history page loads the fixture workspace name');
   check(initial.agents===200&&initial.leaders.length===1&&initial.leaders[0]==='行情 Leader · Leader','leader badge comes from group ownership and initial members are paged');
   evidence.desktop=await js('(()=>{const a=document.querySelector("aside"),c=document.querySelector("#content"),n=document.querySelector("#agents");return {height:innerHeight,pageHeight:document.documentElement.scrollHeight,asideHeight:a.clientHeight,asideScrollHeight:a.scrollHeight,asideOverflow:getComputedStyle(a).overflowY,contentHeight:c.clientHeight,contentScrollHeight:c.scrollHeight,contentOverflow:getComputedStyle(c).overflowY,memberHeight:n.clientHeight,memberScrollHeight:n.scrollHeight};})()');
   check(evidence.desktop.pageHeight<=evidence.desktop.height+1&&evidence.desktop.asideOverflow==='auto'&&evidence.desktop.contentOverflow==='auto'&&evidence.desktop.contentScrollHeight>evidence.desktop.contentHeight&&evidence.desktop.memberScrollHeight>evidence.desktop.memberHeight,'desktop navigation and records have bounded independent scroll areas without stretching the page');
   check(await js('(()=>{const a=document.querySelector("#agents"),c=document.querySelector("#content");a.scrollTop=100;c.scrollTop=200;const valid=a.scrollTop===100&&c.scrollTop===200;const aBefore=a.scrollTop;c.scrollTop=400;const independent=a.scrollTop===aBefore;a.scrollTop=0;c.scrollTop=0;return valid&&independent;})()'),'scrolling report records preserves the independent member-navigation position');
   await capture(main,'01-history-reports');
   await js('Array.from(document.querySelectorAll("#agents button")).find(b=>b.textContent==="读取更多").click()');
   await wait('document.querySelectorAll("#agents>div").length===205','remaining members');
   check(await js('document.querySelectorAll("#agents button").length===0'),'all 205 member entries can be loaded');
   await js('Array.from(document.querySelectorAll("#conversations button")).find(b=>b.textContent==="读取更多").click()');
   await wait('document.querySelectorAll("#conversations button").length===205','remaining conversation navigation');
   check(await js('!Array.from(document.querySelectorAll("#conversations button")).some(b=>b.textContent==="读取更多")'),'all 205 conversation entries can be loaded');
   for(const expected of [100,150,200,215]){
    await js('document.querySelector("#content [data-more]").click()');
    await wait(`document.querySelectorAll("#content .card").length===${expected}`,'report pagination '+expected);
   }
   const titles=await js('Array.from(document.querySelectorAll("#content .card>strong")).map(n=>n.textContent)');
   check(titles.length===215&&new Set(titles).size===215&&titles[0]==='历史报告 #215'&&titles[214]==='历史报告 #001','all 215 reports paginate without omissions or duplicates');
   const report=await popup('#content .card a');
   await until(async()=>(await dom(report)).includes('<h1>Saved delivery proof</h1>'),'HTML report content');
   const reportDOM=await dom(report),policy=(await fetch(report.webContents.getURL())).headers.get('content-security-policy');
   check(!/<html[^>]*data-script-executed=/i.test(reportDOM)&&policy.includes('sandbox')&&!policy.includes('allow-scripts'),'saved HTML is readable while report scripts remain blocked');
   await capture(report,'02-saved-html-report');report.close();
   await js('Array.from(document.querySelectorAll("#conversations button")).find(b=>b.textContent==="会话主线 · 120条").click()');
   await wait('document.querySelectorAll("#content .card").length===50','latest message page');
   let messages=await js('Array.from(document.querySelectorAll("#content .card>.body")).map(n=>n.textContent)');
   check(messages[0]==='消息 #071'&&messages[49]==='消息 #120','message page uses accepted sequence despite identical timestamps and shuffled UUIDs');
   await capture(main,'03-conversation-first-page');
   for(const expected of [100,120]){await js('document.querySelector("#content [data-more]").click()');await wait(`document.querySelectorAll("#content .card").length===${expected}`,'message pagination '+expected);}
   messages=await js('Array.from(document.querySelectorAll("#content .card>.body")).map(n=>n.textContent)');
   check(messages.length===120&&messages.every((text,index)=>text==='消息 #'+String(index+1).padStart(3,'0')),'all 120 messages paginate in exact accepted order');
   const attachment=await popup('#content a[href*="/api/workspace-data/blob"]');
   await until(async()=>(await dom(attachment)).includes('Attachment bytes proof / 附件确实可读'),'blob-only attachment bytes');
   check(true,'a blob-only message attachment opens its real saved bytes');
   await capture(attachment,'04-blob-attachment');attachment.close();
   const downloaded=path.join(output,'downloaded.crewpack');
   const download=new Promise((resolve,reject)=>main.webContents.session.once('will-download',(_event,item)=>{
    item.setSavePath(downloaded);item.once('done',(_done,state)=>state==='completed'?resolve(item.getReceivedBytes()):reject(Error('Download '+state)));
   }));
   await js('document.querySelector("#export").click()');
   const size=await Promise.race([download,pause(30000).then(()=>{throw Error('Export download timeout');})]);
   check(size>100000,'export button creates and downloads a complete crewpack through real HTTP');
   const verify=spawnSync('python',[path.join(__dirname,'verify-history-package.py'),downloaded,path.join(output,'third-machine')],{cwd:ROOT,encoding:'utf8',windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
   assert.equal(verify.status,0,verify.stderr);evidence.download=JSON.parse(verify.stdout);
   check(evidence.download.ok&&evidence.download.messages===120&&evidence.download.reports===215,'downloaded package restores history and attachment bytes in a third isolated repository');
   await capture(main,'05-export-complete');
   await main.loadURL(fixture.url+'/?project='+encodeURIComponent('storage:'+fixture.workspace_id));
   await wait('document.querySelectorAll("#content .card").length===50','reload history route');
   check(await js('document.querySelector("#title").textContent==="股市预警 · UI隔离验收"'),'refreshing the storage history route returns to the same readable workspace');
   main.setSize(420,900);await pause(200);await capture(main,'06-narrow-history');
   evidence.narrow=await js('({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,asideHeight:document.querySelector("aside").getBoundingClientRect().height,contentTop:document.querySelector("#content").getBoundingClientRect().top,exportWhiteSpace:getComputedStyle(document.querySelector("#export")).whiteSpace,exportHeight:document.querySelector("#export").getBoundingClientRect().height})');
   check(evidence.narrow.scrollWidth<=evidence.narrow.width,'narrow history viewport has no horizontal overflow');
   check(evidence.narrow.contentTop<850,'narrow viewport reaches actual report content within the first screen');
   check(evidence.narrow.exportWhiteSpace==='nowrap'&&evidence.narrow.exportHeight<=55,'narrow export button remains one readable line');
   check(evidence.blockedExternal.length===0,'UI performs no external network or model requests');
   check(evidence.requests.every(r=>new URL(r.url).pathname==='/'||new URL(r.url).pathname.startsWith('/api/workspace-data/')||new URL(r.url).pathname.startsWith('/storage-history')),'UI traffic remains within history assets and data endpoints');
   evidence.ok=true;
  }catch(error){evidence.errors.push(error.stack||String(error));try{evidence.body=await js('document.body.innerText');await capture(main,'failure');}catch(_){}console.error(error);}
  finally{
   await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));
   for(const win of BrowserWindow.getAllWindows())win.destroy();
   app.exit(evidence.ok?0:1);
  }
 }).catch(error=>{console.error(error);app.exit(1);});
}
