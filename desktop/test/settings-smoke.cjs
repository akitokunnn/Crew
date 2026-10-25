'use strict';
// Run with Electron. Uses the actual native renderer/preload and in-memory API fixtures.
const {app,BrowserWindow,ipcMain}=require('electron');
const fs=require('node:fs/promises'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
const {pathToFileURL}=require('node:url');
const root=path.resolve(__dirname,'../..'),output=path.join(root,'desktop/smoke-output/settings');
const source=path.join(root,'desktop/src'),web=path.join(root,'plugin/crew/web');
const home=pathToFileURL(path.join(source,'home.html')).href,error=pathToFileURL(path.join(source,'error.html')).href;
const temporary=require('node:fs').mkdtempSync(path.join(os.tmpdir(),'crew-settings-'));
const sessionData=path.join(temporary,'session');require('node:fs').mkdirSync(sessionData);
app.setPath('userData',temporary);app.setPath('sessionData',sessionData);
let win,saveFailure=false,saves=0,connectionSaveFailure=false;const calls=[],rendererErrors=[],connectionSaves=[],connectionActions=[];
const catalog=[{id:'claude',name:'Claude Code',available:true,models:[{id:'native-a',name:'Native A',efforts:['low','high']}],efforts:['low','high']},{id:'codex',name:'Codex',available:true,models:[{id:'native-b',name:'Native B',efforts:['deep']}],efforts:['deep']}];
let ai={default:{runtime:null,model:null,effort:null},digest:null,git:null,operations:null};
let execution={codex:'workspace-write',claude:'default'};
const snapshot=()=>({ai:JSON.parse(JSON.stringify(ai)),execution:{...execution},runtimes:catalog,home:'C:\\Crew Data',backend:'C:\\Crew\\plugin'});
const tools={checked_at:new Date().toISOString(),autoUpdate:false,tools:catalog.map(item=>({id:item.id,name:item.name,installed_version:'fixture',latest_version:'fixture',phase:'idle',installation:'native',can_update:false,can_queue:false,update_available:false}))};
const health={state:'healthy',checked_at:new Date().toISOString(),recoveries:0,incidents:[],restart:{allowed:false,reason:'后台运行正常'},url:'http://127.0.0.1:9999',supervisor_pid:1,service_pid:2};
const notification={away:false,manual_away:false,error:null,channel:{state:'connected',detail:'隔离测试连接'}};
const channels=['qq','lark'].map(id=>({id,name:id==='qq'?'QQ':'Lark / 飞书',app_id:'',secret_configured:false,enabled:false,domain:'feishu',state:'unconfigured',detail:null,bound:false,binding_code:null,error:null}));
const connectionSnapshot=()=>({channels:JSON.parse(JSON.stringify(channels)),notification:{...notification}});
const windowOptions=()=>({width:1320,height:1120,show:false,webPreferences:{preload:path.join(source,'preload.cjs'),contextIsolation:true,sandbox:true,nodeIntegration:false,backgroundThrottling:false,offscreen:true,additionalArguments:['--crew-desktop-config='+encodeURIComponent(JSON.stringify({home,error}))]}});
function handle(name,callback){ipcMain.handle('desktop:'+name,(event,...args)=>{assert.equal(event.senderFrame.url,home);calls.push(name);return callback(...args);});}
handle('home-ready',()=>({ok:true}));handle('bootstrap',()=>({projects:[],service:{url:'http://127.0.0.1:9999',owned:false}}));handle('ready',()=>({ok:true}));
handle('chrome-ready',async()=>{await win.webContents.insertCSS(await fs.readFile(path.join(source,'chrome.css'),'utf8'));return {maximized:false,focused:true,canGoBack:false,tabs:{activeId:'home',tabs:[{id:'home',name:'工作区',project:null}]}};});
handle('settings-ui',async()=>{await win.webContents.executeJavaScript(await fs.readFile(path.join(web,'launch-settings-ui.js'),'utf8'));for(const name of ['operations-ui','settings-ui']){await win.webContents.insertCSS(await fs.readFile(path.join(web,name+'.css'),'utf8'));await win.webContents.executeJavaScript(await fs.readFile(path.join(web,name+'.js'),'utf8'));}await win.webContents.executeJavaScript(await fs.readFile(path.join(web,'connections-ui.js'),'utf8'));await win.webContents.executeJavaScript('(()=>{const create=window.CrewSettings.create;window.CrewSettings.create=options=>(window.__settingsUnderTest=create(options));})()');return{ok:true};});
handle('settings-get',snapshot);handle('settings-save',body=>{saves++;if(saveFailure){saveFailure=false;throw Error('隔离保存失败，草稿应保留');}ai=JSON.parse(JSON.stringify(body.ai));execution=JSON.parse(JSON.stringify(body.execution));return snapshot();});
handle('settings-connection',connectionSnapshot);handle('settings-away',away=>{notification.manual_away=away;notification.away=away;return notification;});
handle('settings-connection-save',body=>{
  if(connectionSaveFailure){connectionSaveFailure=false;throw Error('隔离连接保存失败，草稿应保留');}
  const channel=channels.find(item=>item.id===body.channel);assert(channel);assert(body.app_id);assert(body.app_secret||channel.secret_configured);
  connectionSaves.push({channel:body.channel,app_id:body.app_id,domain:body.domain,secretSupplied:Boolean(body.app_secret)});
  Object.assign(channel,{app_id:body.app_id,secret_configured:true,enabled:true,domain:body.domain||'feishu',state:'connecting',detail:'正在建立隔离测试连接',bound:false,binding_code:body.channel==='qq'?'QQ-123456':'LARK-654321',error:null});
  return connectionSnapshot();
});
handle('settings-connection-action',body=>{
  connectionActions.push({...body});const channel=channels.find(item=>item.id===body.channel);assert(channel);
  if(body.action==='disconnect')Object.assign(channel,{state:'disabled',enabled:false,detail:'连接已停用'});
  else if(body.action==='reconnect')Object.assign(channel,{state:'connecting',enabled:true,detail:'正在重新连接'});
  else if(body.action==='reset-binding')Object.assign(channel,{state:'unbound',bound:false,binding_code:body.channel==='qq'?'QQ-NEW-4321':'LARK-NEW-4321'});
  else if(body.action==='test')assert(channel.bound&&channel.state==='connected','test sends need a bound, connected channel');
  else throw Error('未支持的隔离连接动作：'+body.action);
  return connectionSnapshot();
});
handle('tools-snapshot',()=>tools);handle('tools-check',()=>tools);handle('tools-settings',value=>{tools.autoUpdate=value.autoUpdate;return tools;});
handle('crew-update-snapshot',()=>({phase:'idle',desktop_version:'fixture',backend:{version:'fixture',plugin_root:'C:\\Crew\\plugin'},target:null,plugin:{state:'not_installed'},online:null,online_checking:false,online_error:null,can_update:false,queued:false,error:null}));
handle('admin-status',()=>health);handle('admin-check',()=>({ok:true}));handle('admin-restart',()=>{throw Error('不允许在 fixture 恢复后台');});
const operationsChat={messages:[],busy:false,error:null,working_since:null};
let operationsConfig={policy_path:'C:\\Crew Data\\desktop\\admin\\operations\\policy.json',configured:false,policy:{enabled:false,repository:'C:\\Crew',reproduce:[],verify:[],timeout_seconds:180,merge_ref:''}};
handle('admin-chat-snapshot',()=>operationsChat);
handle('admin-chat-send',body=>{operationsChat.messages.push({id:body.message_id,role:'user',text:body.text,at:new Date().toISOString()});return operationsChat;});
handle('admin-chat-interrupt',()=>operationsChat);
handle('admin-config-get',()=>operationsConfig);
handle('admin-config-save',policy=>(operationsConfig={...operationsConfig,configured:true,policy}));
handle('launch-settings-get',()=>({actual_service:{home:snapshot().home,plugin_root:snapshot().backend,version:'fixture'},actual_service_error:null}));
const run=async script=>{const result=await win.webContents.executeJavaScript(`(async()=>{try{return {value:await (${script})};}catch(error){return {failure:error.stack||String(error)};}})()`);if(result.failure)throw Error(result.failure+'\nExpression: '+script);return result.value;};
const progress=message=>require('node:fs').appendFileSync(path.join(output,'progress.log'),message+'\n');
async function waitFor(expression,attempts=160){for(let i=0;i<attempts;i++){if(await run(expression))return;await new Promise(resolve=>setTimeout(resolve,30));}throw Error('等待超时：'+expression);}
async function click(selector){await run(`document.querySelector(${JSON.stringify(selector)}).click()`);}
async function change(key,field,value){if(await run(`(()=>{const input=document.querySelector('[data-ai-key="${key}"][data-ai-field="${field}"]');return input.tagName==='SELECT'&&![...input.options].some(option=>option.value===${JSON.stringify(value)});})()`))await click(`[data-ai-custom="${key}:${field}"]`);await run(`(()=>{const input=document.querySelector('[data-ai-key="${key}"][data-ai-field="${field}"]');input.value=${JSON.stringify(value)};input.dispatchEvent(new Event('change',{bubbles:true}));})()`);}
async function permission(runtime,value){await run(`(()=>{const input=document.querySelector('[data-execution-runtime="${runtime}"]');input.value=${JSON.stringify(value)};input.dispatchEvent(new Event('change',{bubbles:true}));})()`);}
async function connectionField(field,value){await run(`(()=>{const input=document.querySelector('[data-connection-field="${field}"]');input.value=${JSON.stringify(value)};input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));})()`);}
async function screenshot(name){progress('capture '+name);await run('Promise.race([document.fonts.ready.then(()=>true),new Promise(resolve=>setTimeout(()=>resolve(false),200))])');await new Promise(resolve=>setTimeout(resolve,100));const image=await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true});assert(!image.isEmpty());await fs.writeFile(path.join(output,name),image.toPNG());}
async function open(){await click('#nav-settings');await waitFor('!!document.querySelector(\'[data-ai-key="default"]\')');}
(async()=>{
  await app.whenReady();await fs.mkdir(output,{recursive:true});await fs.writeFile(path.join(output,'progress.log'),'');progress('ready');
  win=new BrowserWindow(windowOptions());
  win.webContents.on('console-message',(_event,...args)=>{const message=typeof args[0]==='object'?args[0].message:args[1];if(String(message).includes('Uncaught'))rendererErrors.push(message);});
  await win.loadURL(home);progress('home loaded');await waitFor('document.documentElement.dataset.crewDesktopChromeReady === "true"');progress('chrome ready');await open();progress('settings open');
  assert.equal(await run('document.querySelector(\'[data-ai-key="default"][data-ai-field="runtime"]\').value'),'');
  assert.deepEqual(await run('[...document.querySelectorAll("[data-ai-key=default]")].map(input=>input.tagName)'),['SELECT','SELECT','SELECT']);
  for(const task of ['digest','git','operations'])await run(`(()=>{const checkbox=document.querySelector('[data-ai-inherit="${task}"]');checkbox.checked=false;checkbox.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  win.setSize(1120,740);
  const scrollBefore=await run('(()=>{const body=document.querySelector(".cs-body");return{height:innerHeight,top:body.scrollTop,body:body.getBoundingClientRect().toJSON(),scrollHeight:body.scrollHeight,clientHeight:body.clientHeight,documentHeight:document.documentElement.scrollHeight};})()');
  progress('scroll bounds '+JSON.stringify(scrollBefore));
  assert(scrollBefore.scrollHeight>scrollBefore.clientHeight,'AI settings must have an internal scroll region at a short window height');
  assert(scrollBefore.body.bottom<=scrollBefore.height+1,'settings scroll region must fit inside the desktop window');
  win.webContents.debugger.attach('1.3');
  await win.webContents.debugger.sendCommand('Input.dispatchMouseEvent',{type:'mouseMoved',x:scrollBefore.body.x+scrollBefore.body.width/2,y:scrollBefore.body.y+scrollBefore.body.height/2});
  await win.webContents.debugger.sendCommand('Input.dispatchMouseEvent',{type:'mouseWheel',x:scrollBefore.body.x+scrollBefore.body.width/2,y:scrollBefore.body.y+scrollBefore.body.height/2,deltaX:0,deltaY:420});
  await waitFor('document.querySelector(".cs-body").scrollTop>0');
  win.webContents.debugger.detach();
  progress('native wheel scroll '+await run('document.querySelector(".cs-body").scrollTop'));
  await run('document.querySelector(".cs-body").scrollTo(0,10000)');
  await waitFor('document.querySelector(".cs-body").scrollTop>0');
  assert(await run('(()=>{const footer=document.querySelector(".cs-footer").getBoundingClientRect();return footer.bottom<=innerHeight+1&&footer.top>=0;})()'),'save controls must remain reachable after scrolling');
  await run('document.querySelector(".cs-body").scrollTo(0,0)');
  await click('[data-settings-action="reset"]');
  await change('default','runtime','codex');await change('default','model','native-b');await change('default','effort','deep');
  assert.deepEqual(await run('[...document.querySelector(\'[data-ai-key="default"][data-ai-field="effort"]\').options].map(option=>option.value)'),['','deep']);
  await run(`(()=>{const input=document.querySelector('[data-ai-inherit="digest"]');input.checked=false;input.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  await change('digest','runtime','claude');await change('digest','model','future-summary');await change('digest','effort','future-effort');
  await click('[data-settings-category="appearance"]');await click('[data-desktop-theme="light"]');await click('[data-settings-category="ai"]');
  assert.equal(await run('document.querySelector(\'[data-ai-key="digest"][data-ai-field="model"]\').value'),'future-summary');
  await click('[data-settings-category="execution"]');assert.equal(await run('document.querySelector(\'[data-execution-runtime="codex"]\').value'),'workspace-write');assert.equal(await run('document.querySelector(\'[data-execution-runtime="claude"]\').value'),'default');
  await permission('codex','danger-full-access');await permission('claude','acceptEdits');await run('window.__settingsUnderTest.reload()');
  assert.equal(await run('document.querySelector(\'[data-execution-runtime="codex"]\').value'),'danger-full-access');
  saveFailure=true;await click('[data-settings-action="save"]');await waitFor('document.querySelector(".cs-status").textContent.includes("隔离保存失败")');
  assert.equal(await run('document.querySelector(\'[data-execution-runtime="codex"]\').value'),'danger-full-access');await click('[data-settings-category="ai"]');
  assert.equal(await run('document.querySelector(\'[data-ai-key="digest"][data-ai-field="effort"]\').value'),'future-effort');
  await click('[data-settings-action="save"]');await waitFor('document.querySelector(".cs-status").textContent.includes("已保存")');assert.equal(saves,2);
  progress('saved');
  assert.deepEqual(ai,{default:{runtime:'codex',model:'native-b',effort:'deep'},digest:{runtime:'claude',model:'future-summary',effort:'future-effort'},git:null,operations:null});
  assert.deepEqual(execution,{codex:'danger-full-access',claude:'acceptEdits'});
  await screenshot('ai-task-allocation-light.png');progress('light screenshot');
  // Recreate the actual renderer, so readback must come from the saved snapshot.
  const old=win;win=new BrowserWindow(windowOptions());old.destroy();await win.loadURL(home);progress('reopened home');await waitFor('document.documentElement.dataset.crewDesktopChromeReady === "true"');await open();progress('reopened settings');
  assert.equal(await run('document.querySelector(\'[data-ai-key="default"][data-ai-field="runtime"]\').value'),'codex');
  assert.equal(await run('document.querySelector(\'[data-ai-key="digest"][data-ai-field="model"]\').value'),'future-summary');
  await click('[data-settings-category="appearance"]');await click('[data-desktop-theme="dark"]');await click('[data-settings-category="ai"]');
  const light=win;win=new BrowserWindow({...windowOptions(),width:980,height:900});light.destroy();await win.loadURL(home);await waitFor('document.documentElement.dataset.crewDesktopChromeReady === "true"');await open();progress('dark narrow reopened');await screenshot('saved-reopened-dark-narrow.png');
  await click('[data-settings-category="execution"]');assert.equal(await run('document.querySelector(\'[data-execution-runtime="codex"]\').value'),'danger-full-access');assert.equal(await run('document.querySelector(\'[data-execution-runtime="claude"]\').value'),'acceptEdits');await screenshot('execution-permissions-dark.png');
  const bounds=await run(`(()=>{const root=document.querySelector('#native-settings-root'),content=document.querySelector('.cs-body');return{width:innerWidth,rootWidth:root.getBoundingClientRect().width,bodyWidth:content.clientWidth,bodyScrollWidth:content.scrollWidth,theme:document.documentElement.dataset.theme};})()`);assert.equal(bounds.theme,'dark');assert(bounds.bodyScrollWidth<=bounds.bodyWidth+1);
  progress('bounds passed');
  await click('[data-settings-category="tools"]');await waitFor('document.querySelector(".cs-content #tools-list").children.length === 2');
  progress('tools mounted');
  const sameTools=await run('document.querySelectorAll("#tools-page").length');assert.equal(sameTools,1);await click('#tools-auto');await waitFor('document.querySelector("#tools-setting-status").textContent.includes("已开启")');assert.equal(tools.autoUpdate,true);
  await click('[data-settings-category="backend"]');await waitFor('!!document.querySelector(".co-composer")');assert.equal(calls.filter(name=>name==='admin-chat-send').length,0);await click('[data-operations-tab="status"]');await waitFor('document.querySelector(".cs-content #admin-state").textContent === "运行正常"');assert.equal(await run('document.querySelectorAll("#admin-page").length'),1);
  await click('[data-settings-category="connections"]');await waitFor('!!document.querySelector("[data-channel-select=qq]")');
  assert.equal(await run('document.querySelector("#crew-settings-title").textContent'),'消息渠道');
  assert.equal(await run('document.querySelectorAll("[data-channel-select]").length'),2);
  assert(await run('!!document.querySelector("[data-channel-select=lark]")'));
  await screenshot('connections-new-user-dark.png');
  // Exercise the actual form through failed and successful saves. No credentials come back in snapshots.
  await connectionField('app_id','qq-fixture-app');await connectionField('app_secret','qq-fixture-secret');connectionSaveFailure=true;
  await click('[data-connection-action=save]');await waitFor('document.querySelector(".cs-content").textContent.includes("隔离连接保存失败")');
  assert.equal(await run('document.querySelector("[data-connection-field=app_id]").value'),'qq-fixture-app');
  assert.equal(await run('document.querySelector("[data-connection-field=app_secret]").value'),'qq-fixture-secret');
  await click('[data-connection-action=save]');await waitFor('document.querySelector("[data-connection-field=app_secret]").value===""');
  assert.equal(connectionSaves.length,1);assert.equal(connectionSaves[0].channel,'qq');
  assert.match(await run('document.querySelector("[data-connection-state]").textContent'),/连接中|正在连接/);
  assert.equal(await run('document.querySelector(".cs-content").innerHTML.includes("qq-fixture-secret")'),false);
  Object.assign(channels[0],{state:'unbound',detail:'连接已建立，请发送绑定码'});
  await click('[data-connection-action=refresh]');await waitFor('document.querySelector("[data-connection-state]").textContent.includes("绑定")');
  assert.match(await run('document.querySelector("[data-binding-command]").textContent'),/QQ-123456/);
  assert.match(await run('document.querySelector("[data-connection-steps] [aria-current=step]").textContent'),/私聊绑定/);
  assert.equal(await run('document.querySelector("[data-connection-action=test]").disabled'),true);
  await screenshot('connections-qq-binding-dark.png');
  await run('document.querySelector("[data-connection-form]").closest("section").scrollIntoView({block:"start",behavior:"instant"})');await screenshot('connections-qq-credentials-binding-dark.png');
  const connectionTheme=await run('document.documentElement.dataset.theme');await run('(()=>{document.documentElement.dataset.theme="light";document.querySelector("[data-connection-form]").closest("section").scrollIntoView({block:"start",behavior:"instant"});})()');await screenshot('connections-qq-credentials-binding-light.png');await run(`document.documentElement.dataset.theme=${JSON.stringify(connectionTheme)}`);assert.equal(await run('document.documentElement.dataset.theme'),'dark');await run('document.querySelector(".cs-body").scrollTo(0,0)');
  // The panel's live reads must pick up external binding without dropping a draft or creating a send.
  const readsBefore=calls.filter(name=>name==='settings-connection').length;
  Object.assign(channels[0],{state:'connected',bound:true,binding_code:null,detail:'隔离用户已绑定'});
  await waitFor('!document.querySelector("[data-connection-action=test]").disabled',420);
  assert(calls.filter(name=>name==='settings-connection').length>readsBefore,'binding status should poll while panel is mounted');
  await click('[data-connection-action=test]');await waitFor('!document.querySelector("[data-connection-action=test]").disabled');
  assert.equal(connectionActions.filter(item=>item.channel==='qq'&&item.action==='test').length,1);
  await click('[data-connection-action=disconnect]');await waitFor('document.querySelector("[data-connection-state]").textContent.includes("停用")||document.querySelector("[data-connection-state]").textContent.includes("断开")');
  assert.equal(channels[0].enabled,false);
  await click('[data-connection-action=reconnect]');await waitFor('document.querySelector("[data-connection-state]").textContent.includes("连接中")');
  assert.equal(channels[0].enabled,true);
  // Lark uses its own saved app/domain/binding and leaves the QQ route untouched.
  await click('[data-channel-select=lark]');await connectionField('app_id','cli_lark-fixture-app');await connectionField('app_secret','lark-fixture-secret');await connectionField('domain','lark');
  await click('[data-connection-action=save]');await waitFor('document.querySelector("[data-connection-field=app_secret]").value===""');
  assert.equal(connectionSaves.length,2);assert.equal(connectionSaves[1].domain,'lark');assert.equal(channels[0].app_id,'qq-fixture-app');
  assert.equal(await run('document.querySelector(".cs-content").innerHTML.includes("lark-fixture-secret")'),false);
  Object.assign(channels[1],{state:'connected',bound:true,binding_code:null,detail:'隔离 Lark 用户已绑定'});
  await click('[data-connection-action=refresh]');await waitFor('!document.querySelector("[data-connection-action=test]").disabled');
  await click('[data-connection-action=test]');await waitFor('!document.querySelector("[data-connection-action=test]").disabled');
  assert.equal(connectionActions.filter(item=>item.channel==='lark'&&item.action==='test').length,1);
  const connectionBounds=await run('(()=>{const body=document.querySelector(".cs-body");return {width:body.clientWidth,scrollWidth:body.scrollWidth,documentWidth:document.documentElement.clientWidth,documentScroll:document.documentElement.scrollWidth};})()');
  assert(connectionBounds.scrollWidth<=connectionBounds.width+1,'connections must fit the narrow settings body');
  assert(connectionBounds.documentScroll<=connectionBounds.documentWidth+1,'connections must not create horizontal page scrolling');
  await screenshot('connections-lark-connected-dark-narrow.png');
  await run('document.querySelector("[data-connection-form]").closest("section").scrollIntoView({block:"start",behavior:"instant"})');await screenshot('connections-lark-credentials-dark-narrow.png');await run('document.querySelector(".cs-body").scrollTo(0,0)');
  const resetCount=connectionActions.filter(item=>item.action==='reset-binding').length;
  await click('[data-connection-action=reset-binding]');await click('[data-connection-action=cancel-binding]');
  assert.equal(connectionActions.filter(item=>item.action==='reset-binding').length,resetCount,'cancel must preserve current binding');
  await click('[data-connection-action=reset-binding]');await click('[data-connection-action=confirm-binding]');
  await waitFor('!!document.querySelector("[data-binding-command]")');assert.match(await run('document.querySelector("[data-binding-command]").textContent'),/LARK-NEW-4321/);
  assert.equal(connectionActions.filter(item=>item.channel==='lark'&&item.action==='reset-binding').length,1);
  await click('[data-away]');await waitFor('document.querySelector("[data-away]")?.textContent.includes("回到电脑前")');assert.equal(notification.manual_away,true);
  await click('#nav-workspaces');assert.equal(await run('document.querySelector("#settings-page").hidden'),true);assert.equal(await run('document.querySelector("#tools-page").parentNode.tagName'),'MAIN');
  const hiddenReads=calls.filter(name=>name==='settings-connection').length;await new Promise(resolve=>setTimeout(resolve,3300));assert.equal(calls.filter(name=>name==='settings-connection').length,hiddenReads,'hidden settings must stop connection polling');
  assert.deepEqual(rendererErrors,[]);await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify({ok:true,saves,ai,execution,calls,bounds,connectionBounds,connectionSaves,connectionActions,rendererErrors,isolatedUserData:temporary,noAIProcesses:true,noRealRemoteMessages:true},null,2));
  await fs.rm(path.join(output,'failure.txt'),{force:true});console.log('Settings renderer smoke passed:',output);win.destroy();app.exit(0);
})().catch(async error=>{console.error(error);await fs.mkdir(output,{recursive:true});await fs.writeFile(path.join(output,'failure.txt'),error.stack||String(error));win?.destroy();app.exit(1);});
