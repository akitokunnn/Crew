'use strict';
// Real renderer, isolated userData, fake HTTP API. Never launches an AI or reads production HOME.
const {app,BrowserWindow}=require('electron');
const fs=require('node:fs/promises'),path=require('node:path'),os=require('node:os'),http=require('node:http'),assert=require('node:assert/strict');
const web=path.resolve(__dirname,'../crew/web'),output=path.resolve(__dirname,'../../desktop/smoke-output/members');
const calls=[],streams=[],errors=[];
let win,server,origin,failSpawn=true,failRename=true;
const brain={runtime:'codex',model:'test-code',effort:'high'};
const catalog=[{id:'codex',name:'Codex',available:true,models:[{id:'test-code',name:'测试代码模型',efforts:['low','high']}],efforts:['low','high']},{id:'claude',name:'Claude Code',available:true,models:[{id:'test-review',name:'测试审查模型',efforts:['medium','high']}],efforts:['medium','high']}];
function person(id,name,extra={}){return {session_id:id,name,...brain,status:'offline',where:'offline',busy:false,running:false,web_chat:true,last_seq:0,read_seq:0,last_reply_id:null,unread:0,pending:0,priority:null,goal:'交互隔离测试',task:'交互隔离测试',context:null,activity:null,scope:null,done_when:null,progress:[],leases:[],claims:[],branch:null,cwd:'C:/fixture/project',codex:[],codex_older:0,member_ids:[],applied:null,trust:{enabled:false},runtime_switch:{allowed:true},...extra};}
const state={project:{name:'交互验收',root:'C:/fixture/project',theme:'green'},projects:[{name:'交互验收',theme:'green'}],themes:[{id:'green',name:'竹青',light:{'--accent':'#3E7C74','--on-accent':'#F7FBFA'},dark:{'--accent':'#83B9A8','--on-accent':'#182A24'}}],runtimes:catalog,ai:{default:brain,leader:null,member:null,digest:null,git:null,operations:null},leaders:[person('chief','产品体验')],members:[person('member','界面实现',{leader_session_id:'chief',group:'产品体验'})],goals:[],decisions:[],todos:[],attention:[],candidates:{running:[],others:[]},limits:{claude:{seven_day:null,five_hour:null},codex:null},channel:{unread:0,latest_id:0,digesting:false},notification:{manual_away:false,away:false,error:null,channel:{state:'unbound'}},vscode_error:null};
function json(res,value,status=200){res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(value));}
async function route(req,res){
  const url=new URL(req.url,origin),name=url.pathname;let body;
  if(req.method==='POST'){let raw='';for await(const part of req)raw+=part;body=JSON.parse(raw||'{}');}
  if(!name.startsWith('/api/')){
    const file=path.resolve(web,'.'+(name==='/'?'/index.html':name));
    if(!file.startsWith(web+path.sep)){res.writeHead(403);return res.end();}
    try{const content=await fs.readFile(file);res.writeHead(200,{'Content-Type':({'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml','.woff2':'font/woff2'})[path.extname(file)]||'application/octet-stream'});return res.end(content);}
    catch(error){res.writeHead(404);return res.end();}
  }
  calls.push({name,body});
  if(name==='/api/state')return json(res,state);
  if(name==='/api/runtimes')return json(res,{runtimes:catalog});
  if(name==='/api/chat/history')return json(res,{events:[],busy:false,read_seq:0});
  if(name==='/api/chat/events'){res.writeHead(200,{'Content-Type':'text/event-stream'});res.write(': connected\n\n');streams.push(res);return;}
  if(name==='/api/chat/read')return json(res,{read_seq:body.seq});
  if(name==='/api/comments')return json(res,{comments:[]});
  if(name==='/api/member-defaults'){state.leaders[0].member_defaults=body.selection;return json(res,{selection:body.selection});}
  if(name==='/api/spawn'){
    await new Promise(resolve=>setTimeout(resolve,80));
    if(failSpawn){failSpawn=false;return json(res,{error:'隔离创建失败：请保留任务后重试'},409);}
    state.members.push(person('created',body.name,{...body,leader_session_id:'chief',group:state.leaders[0].name,session_id:'created'}));
    return json(res,{session_id:'created'});
  }
  if(name==='/api/session/rename'){
    await new Promise(resolve=>setTimeout(resolve,80));
    if(failRename){failRename=false;return json(res,{error:'隔离改名失败：名字已被使用'},409);}
    const item=[...state.leaders,...state.members].find(item=>item.session_id===body.session_id);item.name=body.name;
    if(item.session_id==='chief')for(const member of state.members)member.group=item.name;
    return json(res,{ok:true});
  }
  throw Error('未实现测试 API：'+name);
}
const run=code=>win.webContents.executeJavaScript(`(${code})`);
async function wait(code){for(let i=0;i<160;i++){if(await run(code))return;await new Promise(resolve=>setTimeout(resolve,35));}throw Error('等待超时：'+code);}
const click=selector=>run(`document.querySelector(${JSON.stringify(selector)}).click()`);
const fill=(selector,value)=>run(`(()=>{const item=document.querySelector(${JSON.stringify(selector)});item.value=${JSON.stringify(value)};item.dispatchEvent(new Event('input',{bubbles:true}));})()`);
const choose=(field,value)=>run(`(()=>{const item=document.querySelector('.member-dialog [data-ai-field="${field}"]');item.value=${JSON.stringify(value)};item.dispatchEvent(new Event('change',{bubbles:true}));})()`);
async function shot(name){await new Promise(resolve=>setTimeout(resolve,120));await fs.writeFile(path.join(output,name),(await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true})).toPNG());}
async function openMenu(){await click('[data-act=member-add-menu]');await wait('document.querySelector("#menu").textContent.includes("新建组员")');}
(async()=>{
  const temporary=await fs.mkdtemp(path.join(os.tmpdir(),'crew-members-'));app.setPath('userData',temporary);await app.whenReady();await fs.mkdir(output,{recursive:true});
  server=http.createServer((req,res)=>route(req,res).catch(error=>{errors.push(error.stack);json(res,{error:error.message},500);}));
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin='http://127.0.0.1:'+server.address().port;
  win=new BrowserWindow({width:1380,height:960,show:false,webPreferences:{nodeIntegration:false,contextIsolation:true,sandbox:true,offscreen:true,backgroundThrottling:false}});
  win.webContents.on('console-message',(_event,...args)=>{const message=typeof args[0]==='object'?args[0].message:args[1];if(String(message).includes('Uncaught'))errors.push(message);});
  await win.loadURL(origin+'/?project='+encodeURIComponent(state.project.name)+'#leader=chief');
  await wait('!!document.querySelector("#chat-host textarea")');
  if(await run('!!document.querySelector("[data-act=right-open]")'))await click('[data-act=right-open]');
  await wait('document.querySelector("[data-act=member-defaults]").checkVisibility()');
  await openMenu();assert(await run('document.querySelector("#menu").textContent.includes("收编已有会话")'));await click('#menu [data-act=member-create]');
  await wait('document.activeElement.id==="member-name"');assert(await run('document.querySelector(".member-dialog [type=submit]").disabled'));await click('.member-dialog [data-member-action=close]');
  assert(!calls.some(item=>['/api/spawn','/api/member-defaults','/api/chat/send'].includes(item.name)));
  await click('[data-act=member-defaults]');await wait('!!document.querySelector(".member-dialog input[name=inherit]")');await click('.member-dialog input[name=inherit]');await choose('runtime','claude');await choose('model','test-review');await choose('effort','high');await shot('member-defaults.png');
  await click('.member-dialog [type=submit]');await wait('document.querySelector(".member-dialog").parentElement.hidden');
  assert.deepEqual(state.leaders[0].member_defaults,{runtime:'claude',model:'test-review',effort:'high'});assert(!calls.some(item=>item.name==='/api/spawn'||item.name==='/api/chat/send'));
  await openMenu();await click('#menu [data-act=member-create]');assert.equal(await run('document.querySelector(".member-dialog [data-ai-field=runtime]").value'),'claude');
  await fill('#member-name','独立验证');await fill('#member-task','对同一输入做前后对照，给出复现步骤。');await shot('member-create.png');await click('.member-dialog [type=submit]');
  await wait('document.querySelector("[data-member-error]")?.textContent.includes("隔离创建失败")');assert.equal(await run('document.querySelector("#member-name").value'),'独立验证');assert.equal(await run('document.querySelector("#member-task").value'),'对同一输入做前后对照，给出复现步骤。');
  await click('.member-dialog [type=submit]');await wait('document.querySelector(".member-dialog").parentElement.hidden && document.querySelector("#chat-head").textContent.includes("独立验证")');
  const spawns=calls.filter(item=>item.name==='/api/spawn');assert.equal(spawns.length,2);assert.deepEqual(spawns[0].body,spawns[1].body);assert.equal(spawns[1].body.leader_session_id,'chief');assert.equal(spawns[1].body.runtime,'claude');
  // Card and context-menu rename use the same styled dialog and preserve failed input.
  await click('[data-act=card][data-id=member]');await wait('document.activeElement.dataset.act==="card-close"');await click('#card [data-act=rename]');
  await wait('document.activeElement.id==="session-new-name"');assert(await run('document.querySelector("#session-rename-form [type=submit]").disabled'));assert.equal(await run('document.querySelector("#rename-title").textContent'),'给组员改名');
  const bounds=await run('(()=>{const box=document.querySelector(".rename-modal").getBoundingClientRect(),field=document.querySelector("#session-new-name").getBoundingClientRect();return {width:box.width,input:field.width,height:field.height,scroll:document.documentElement.scrollWidth,viewport:innerWidth};})()');assert(bounds.width<=460);assert(bounds.input>300);assert(bounds.height>=40);assert(bounds.scroll<=bounds.viewport);
  await shot('rename-member.png');await fill('#session-new-name','  ');assert(await run('document.querySelector("#session-rename-form [type=submit]").disabled'));await fill('#session-new-name','界面细节验收');await click('#session-rename-form [type=submit]');await wait('document.querySelector("#rename-error")?.textContent.includes("名字已被使用")');assert.equal(await run('document.querySelector("#session-new-name").value'),'界面细节验收');await shot('rename-error.png');
  await click('#session-rename-form [type=submit]');await wait('!document.querySelector("#session-rename-form")');assert.equal(state.members[0].name,'界面细节验收');
  await click('#card [data-act=card-close]');await run('document.querySelector("#sidebar [data-act=leader][data-id=chief]").dispatchEvent(new MouseEvent("contextmenu",{bubbles:true,clientX:210,clientY:200}))');await click('#menu [data-act=session-rename]');await wait('document.querySelector("#rename-title").textContent.includes("leader")');await shot('rename-leader.png');
  const count=calls.filter(item=>item.name==='/api/session/rename').length;await run('document.querySelector("#session-new-name").dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true}))');await wait('!document.querySelector("#session-rename-form")');assert.equal(calls.filter(item=>item.name==='/api/session/rename').length,count);assert(await run('document.activeElement.dataset.id==="chief"'));
  // A fresh narrow window avoids OS-dependent resizing of a hidden offscreen window.
  const old=win;win=new BrowserWindow({width:860,height:760,show:false,webPreferences:{nodeIntegration:false,contextIsolation:true,sandbox:true,offscreen:true,backgroundThrottling:false}});old.destroy();
  await win.loadURL(origin+'/?project='+encodeURIComponent(state.project.name)+'#leader=chief');await wait('!!document.querySelector("#chat-host textarea")');
  await run('document.documentElement.dataset.theme="dark"');
  await run('document.querySelector("#sidebar [data-act=leader][data-id=chief]").dispatchEvent(new MouseEvent("contextmenu",{bubbles:true,clientX:210,clientY:200}))');await click('#menu [data-act=session-rename]');
  assert(await run('(()=>{const rect=document.querySelector(".rename-modal").getBoundingClientRect();return rect.left>=0&&rect.right<=innerWidth&&rect.bottom<=innerHeight;})()'));await shot('rename-dark-narrow.png');
  await run('document.querySelector("#session-new-name").dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true}))');
  await click('[data-act=right-open]');await openMenu();await click('#menu [data-act=member-create]');
  assert(await run('(()=>{const rect=document.querySelector(".member-dialog").getBoundingClientRect();return rect.left>=0&&rect.right<=innerWidth&&rect.bottom<=innerHeight;})()'));await shot('member-create-dark-narrow.png');
  await click('.member-dialog [data-member-action=close]');
  assert.deepEqual(errors,[]);await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify({ok:true,bounds,calls,noModels:true,isolatedUserData:temporary},null,2));console.log('Member and rename smoke passed:',output);
  win.destroy();for(const stream of streams)stream.end();server.close();app.exit(0);
})().catch(async error=>{console.error(error);console.error(JSON.stringify({errors,calls}));if(win){console.error(await run('document.body.innerText'));await shot('failure.png');}await fs.mkdir(output,{recursive:true});await fs.writeFile(path.join(output,'failure.txt'),error.stack);win?.destroy();for(const stream of streams)stream.end();server?.close();app.exit(1);});
