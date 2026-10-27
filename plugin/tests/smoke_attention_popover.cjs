'use strict';
// Real Electron, animated side panels and Chinese IME against a fixture-only HTTP API.
const electron=require('electron');
if(typeof electron==='string'){
  const {spawn}=require('node:child_process'),env={...process.env};
  for(const key of ['ELECTRON_RUN_AS_NODE','CREW_SESSION_ID','CLAUDE_CODE_SESSION_ID'])delete env[key];
  const child=spawn(electron,[__filename],{env,windowsHide:true,stdio:'inherit'});
  child.on('error',error=>{console.error(error);process.exitCode=1;});child.on('exit',code=>{process.exitCode=code??1;});
}else{
  const {app,BrowserWindow,nativeTheme}=electron,fs=require('node:fs/promises'),path=require('node:path'),os=require('node:os'),http=require('node:http'),assert=require('node:assert/strict');
  const root=path.resolve(__dirname,'../..'),plugin=path.resolve(process.env.CREW_TEST_PLUGIN_ROOT||path.join(root,'plugin')),web=path.join(plugin,'crew/web'),output=path.join(root,'desktop/smoke-output/attention-popover');
  // Use the production allowlist: a file existing on disk must not mask an HTTP 404.
  const staticAssets=JSON.parse(require('node:child_process').execFileSync('python',['-c','import json; from pathlib import Path; from crew.web_assets import AssetCatalog; print(json.dumps(AssetCatalog(Path("crew/web")).routes))'],{cwd:plugin,encoding:'utf8',windowsHide:true}));
  const temporary=require('node:fs').mkdtempSync(path.join(os.tmpdir(),'crew-attention-'));app.setPath('userData',temporary);app.setPath('sessionData',temporary);app.commandLine.appendSwitch('force-device-scale-factor','1');
  const evidence={temporary,plugin_root:plugin,checks:[],screenshots:[],errors:[],modelTurnsStarted:0},calls=[],streams=[];
  let server,win,origin,polls=0,failResolve=true,runtimeDelay=0,commentsDelay=0,failComment=true,failRename=true;
  const comments=new Map([['leader',[{id:1,text:'保留录音背景',author:'用户',created_at:new Date().toISOString()}]],['member',[]]]);
  const person=(id,name,runtime='claude')=>({session_id:id,name,runtime,model:null,effort:null,status:'idle',where:'offline',busy:false,running:false,web_chat:false,last_seq:1,read_seq:1,last_reply_id:'reply',unread:0,pending:0,priority:null,goal:'独立推进，保留对话空间',context:null,activity:null,codex:[],codex_older:0,member_ids:[],applied:null,trust:{enabled:false},runtime_switch:{allowed:true},scope:null,task:null,done_when:null,progress:[],leases:[],claims:[],branch:null});
  const theme={id:'green',name:'竹青',light:{},dark:{}},stamp=new Date().toISOString();
  const state={project:{name:'浮卡验收',root:'C:/fixture',theme:'green'},projects:[],themes:[theme],leaders:[person('leader','声音负责人'),person('other','另一负责人','codex')],members:[{...person('member','测量员','codex'),leader_session_id:'leader',group:'声音负责人'}],goals:[],
    todos:[1,2].map(id=>({id,title:id===1?'确认录音测试环境':'查看 .127 镜头前的实物验收',goal_id:id===2?'goal':null,steps:['打开已有测试页面','保留实际结果'],verification:'结果可核对',status:'open',version:1,session_id:'member',leader_session_id:'leader',proposer_name:'测量员',group:'声音负责人',created_at:stamp})),
    decisions:[{id:3,session_id:'leader',leader_session_id:'leader',asker:'声音负责人',question:'选择本轮复测范围',options:['短句与混说','仅短句'],recommendation:'A',presentation_stage:'ready',held:false,brief:{title:'选择本轮复测范围',why_user:'需要你确认验收范围',context:'保留上一轮证据。',recommendation_reason:'先覆盖已知失败场景',impact:'决定本轮使用的录音样本',options:[{label:'短句与混说',consequence:'复测完整场景'},{label:'仅短句',consequence:'混说留到下一轮'}]}},{id:4,session_id:'leader',leader_session_id:'leader',asker:'声音负责人',question:'挂起的旧问题',options:[],presentation_stage:'notice',held:true}],
    native_approvals:[{id:'permission',session_id:'member',goal_id:'goal',kind:'command',command:'git status --short',cwd:'C:/fixture',reason:'核对本次命令权限',paths:[],created_at:stamp,status:'pending'}],attention:[],runtimes:[],ai:{default:null},candidates:{running:[],others:[]},limits:{claude:{seven_day:null,five_hour:null},codex:null},channel:{unread:0,latest_id:0},notification:{manual_away:false,away:false,error:null,channel:{state:'unconfigured',detail:null}}};
  state.goals=[{id:'goal',project:state.project.name,leader_session_id:'leader',title:'录音评测任务',request:'核对同一录音',status:'running',version:1,current_round:1,requirement_version:1,priority:0,updated_at:stamp,created_at:stamp,acceptance:[],constraints:[]}];
  state.leaders[0].context={used:84000,max:200000};
  const json=(res,value,status=200)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(value));};
  async function route(req,res){
    const url=new URL(req.url,origin),name=url.pathname;
    if(name==='/api/state'){polls++;return json(res,state);}
    if(name==='/api/runtimes'){if(runtimeDelay)await pause(runtimeDelay);return json(res,{runtimes:state.runtimes});}
    if(name==='/api/comments'&&req.method==='GET'){if(commentsDelay)await pause(commentsDelay);return json(res,{comments:comments.get(url.searchParams.get('session_id'))||[]});}
    if(name==='/api/usage')return json(res,{tree:[{leader_session_id:'leader',name:'声音负责人',tokens:1000,cost:0.1,share:100,codex_runs:1,codex_tokens:400,self:{tokens:1000,cost:0.1,codex_runs:1,codex_tokens:400},members:[]}],daily:[{date:'2026-10-27',display_name:'声音负责人',total_tokens:1000,cost_usd:0.1}],models:[]});
    if(name==='/api/goal')return json(res,{goal:state.goals[0],executions:[{id:'execution',session_id:'leader',runtime:'claude',model:null,effort:null,round:1,requirement_version:1,generation:1,status:'idle'}],reports:[],documents:[],context:{items:[],manifest:{}}});
    if(name==='/api/chat/history')return json(res,{...(url.searchParams.has('goal_id')?{execution_id:'execution'}:{}),events:[{seq:1,type:'text',id:'reply',text:'声音评测继续推进。旁边的待办不会占用这段对话。'}],busy:false,running:false,read_seq:1});
    if(name==='/api/chat/events'){res.fixtureSession=url.searchParams.get('session_id');res.writeHead(200,{'Content-Type':'text/event-stream'});res.write(': fixture\n\n');streams.push(res);return;}
    if(req.method==='POST'){
      let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);calls.push({name,body});
      if(name==='/api/chat/read')return json(res,{read_seq:body.seq});
      if(name==='/api/session/compact')return json(res,{queued:false,message:'正在压缩上下文'});
      if(name==='/api/comments'){
        if(failComment){failComment=false;return json(res,{error:'隔离评论保存失败'},409);}
        const list=comments.get(body.session_id)||[],entry={id:list.length+1,text:body.text,author:'用户',created_at:new Date().toISOString()};
        comments.set(body.session_id,[...list,entry]);return json(res,{id:entry.id});
      }
      if(name==='/api/leader'){
        state.leaders.push({...person('created-leader',body.name,body.runtime),model:body.model,effort:body.effort});return json(res,{session_id:'created-leader'});
      }
      if(name==='/api/session/rename'){
        if(failRename){failRename=false;return json(res,{error:'隔离改名失败'},409);}
        state.leaders.concat(state.members).find(item=>item.session_id===body.session_id).name=body.name;return json(res,{native_warnings:[]});
      }
      if(name==='/api/open')return json(res,{needs_window_ready:true,needs_confirmation:true,cwd:'C:/fixture'});
      if(name==='/api/open/continue')return json(res,{ok:true});
      if(name==='/api/leaders/priority'){state.leaders.forEach(item=>item.priority=body.ranks.includes(item.session_id)?body.ranks.indexOf(item.session_id)+1:null);return json(res,{ok:true});}
      if(name==='/api/leaders/order'){state.leaders.sort((a,b)=>body.order.indexOf(a.session_id)-body.order.indexOf(b.session_id));return json(res,{ok:true});}
      if(name==='/api/todo/resolve'){if(failResolve){failResolve=false;return json(res,{error:'隔离保存失败，请重试'},409);}state.todos=state.todos.filter(item=>item.id!==body.id);return json(res,{ok:true,before:{},version:2});}
      if(name==='/api/answer'){state.decisions=state.decisions.filter(item=>item.id!==body.id);return json(res,{ok:true});}
      if(name==='/api/native-approval'){state.native_approvals=[];return json(res,{ok:true});}
      throw Error('Unexpected mutation '+name);
    }
    if(name.startsWith('/api/'))throw Error('Unexpected read '+name);
    const asset=staticAssets[name];if(!asset){res.writeHead(404);res.end();return;}
    const file=path.resolve(web,asset[0]);assert(file.startsWith(web+path.sep));
    try{const bytes=await fs.readFile(file);res.writeHead(200,{'Content-Type':asset[1]});res.end(bytes);}catch(error){if(error.code!=='ENOENT')throw error;res.writeHead(404);res.end();}
  }
  const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms)),js=code=>win.webContents.executeJavaScript(code,true);
  async function until(check,label){const deadline=Date.now()+12000;while(Date.now()<deadline){if(await check())return;await pause(30);}throw Error('Timed out: '+label);}
  const wait=code=>until(()=>js(code),code),click=selector=>js(`document.querySelector(${JSON.stringify(selector)}).click();true`),opened='(document.querySelector("#attention-slot").dataset.open==="true")';
  async function check(label,code){assert(await js(code),label);evidence.checks.push(label);}
  async function capture(name){await js('document.fonts.ready.then(()=>true)');await pause(80);const shot=await win.webContents.debugger.sendCommand('Page.captureScreenshot',{format:'png'});const p=path.join(output,name+'.png');await fs.writeFile(p,Buffer.from(shot.data,'base64'));evidence.screenshots.push(p);}
  async function open(){await click('[data-act="attention"]');await wait(opened);await pause(420);}
  async function mouse(selector){const point=await js(`(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return{x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)}})()`);for(const type of ['mouseMoved','mousePressed','mouseReleased'])await win.webContents.debugger.sendCommand('Input.dispatchMouseEvent',{type,...point,...(type==='mouseMoved'?{}:{button:'left',clickCount:1})});}
  async function featureJourneys(){
    const catalog=[{id:'claude',name:'Claude Code',available:true,models:[{id:'opus-fixture',name:'Opus fixture',efforts:['high']}],efforts:['high']},
      {id:'codex',name:'Codex',available:true,models:[{id:'gpt-fixture',name:'GPT fixture',efforts:['medium','high']}],efforts:['medium','high']}];
    state.runtimes=catalog;runtimeDelay=240;
    await click('#chat-head [data-act="model-menu"]');await wait('document.querySelector("#menu").classList.contains("show")');
    await mouse('#chat-host textarea');await pause(350);
    await check('a late native catalog response cannot reopen a model menu dismissed by the user','!document.querySelector("#menu").classList.contains("show")');
    await click('#chat-head [data-act="model-menu"]');await wait('!!document.querySelector("#menu [data-model=opus-fixture]")');
    await check('model discovery repaints the exact open menu with native carrier choices','!!document.querySelector("#menu [data-model=opus-fixture]")&&!document.querySelector("#menu [data-model=gpt-fixture]")');
    await mouse('#chat-host textarea');runtimeDelay=0;

    const compactBefore=calls.filter(call=>call.name==='/api/session/compact').length;
    await click('#chat-head [data-act="session-menu"]');await click('#menu [data-act="compact"]');
    await wait('!!document.querySelector("#backdrop.show [data-act=compact-confirm]")');await click('[data-act="compact-confirm"]');
    await until(()=>calls.filter(call=>call.name==='/api/session/compact').length===compactBefore+1,'confirmed compaction');
    await check('menu confirmation and ring share one pending compaction and one busy state','document.querySelector("#chat-head [data-act=context-compact]").disabled&&!!document.querySelector("#sidebar .is-compacting")');
    await click('#chat-head [data-act="context-compact"]');await pause(60);
    assert.equal(calls.filter(call=>call.name==='/api/session/compact').length,compactBefore+1);
    const compactions=calls.filter(call=>call.name==='/api/session/compact');assert.equal(compactions[0].body.after_turn,true);assert(!Object.hasOwn(compactions.at(-1).body,'after_turn'));
    for(const stream of streams.filter(stream=>stream.fixtureSession==='leader'))stream.write('data: '+JSON.stringify({seq:4,type:'compact',pre_tokens:18000,post_tokens:17000})+'\n\n');
    await wait('!document.querySelector(".is-compacting")');evidence.checks.push('ring requests after-turn compaction while explicit menu confirmation requests immediate compaction');

    await js('document.querySelector("#chat-head [data-act=session-menu]").focus();true');await click('#chat-head [data-act="session-menu"]');await click('#menu [data-act="comments"]');
    await wait('document.querySelector(".comments-overlay.show .comment-entry")?.textContent.includes("保留录音背景")');
    await js('window.commentNode=document.querySelector("#comment-text");commentNode.focus();window.commentEnds=0;commentNode.addEventListener("compositionend",()=>commentEnds++);true');
    await win.webContents.debugger.sendCommand('Input.imeSetComposition',{text:'bu chong',selectionStart:8,selectionEnd:8});
    commentsDelay=180;comments.set('leader',[...comments.get('leader'),{id:2,text:'异步新增评论'}]);await click('[data-act="comments-refresh"]');await pause(280);
    await check('comments keep the connected native Chinese composition while their own asynchronous list refreshes','commentNode===document.querySelector("#comment-text")&&document.activeElement===commentNode&&commentEnds===0');
    await win.webContents.debugger.sendCommand('Input.insertText',{text:'补充录音说明'});await wait('document.querySelector("#comment-text").value==="补充录音说明"&&document.querySelector(".comments-list").textContent.includes("异步新增评论")');
    commentsDelay=0;await click('#comments-form [type="submit"]');await wait('document.querySelector(".comments-overlay .error-line")?.textContent.includes("隔离评论保存失败")');
    await check('a failed comment retains its exact Chinese draft with an explicit retry','document.querySelector("#comment-text").value==="补充录音说明"&&!document.querySelector("#comments-form [type=submit]").disabled');
    await click('#comments-form [type="submit"]');await wait('document.querySelector("#comment-text").value===""&&document.querySelector(".comments-list").textContent.includes("补充录音说明")');
    await capture('06-comments-portal');
    assert.deepEqual(calls.filter(call=>call.name==='/api/comments').map(call=>call.body),[{session_id:'leader',text:'补充录音说明'},{session_id:'leader',text:'补充录音说明'}]);
    await js('const text=document.querySelector("#comment-text");text.value="未发送的评论草稿";text.dispatchEvent(new Event("input",{bubbles:true}));true');
    for(const stream of streams.filter(stream=>stream.fixtureSession==='leader'))stream.write('data: '+JSON.stringify({seq:5,type:'text',id:'portal-unread',text:'评论遮挡时的新回复'})+'\n\n');
    await wait('document.querySelector("#chat-host .chat-inner").textContent.includes("评论遮挡时的新回复")');await pause(1100);
    assert(!calls.some(call=>call.name==='/api/chat/read'&&call.body.seq===5));evidence.checks.push('the independent comments portal blocks actual delayed chat read receipts');
    win.webContents.sendInputEvent({type:'keyDown',keyCode:'Escape'});win.webContents.sendInputEvent({type:'keyUp',keyCode:'Escape'});await wait('!document.querySelector(".comments-overlay").classList.contains("show")');
    await check('Escape closes comments and returns focus to the surviving conversation menu control','document.activeElement.matches("#chat-head [data-act=session-menu]")');
    await click('#chat-head [data-act="session-menu"]');await click('#menu [data-act="comments"]');await wait('document.querySelector("#comment-text")?.value==="未发送的评论草稿"');
    await js('document.querySelector(".comments-overlay").dispatchEvent(new MouseEvent("click",{bubbles:true}));true');
    await check('mask close preserves the unsent comment draft for a later open','!document.querySelector(".comments-overlay").classList.contains("show")');
    await mouse('#chat-host textarea');await js('document.querySelector("#chat-host .chat-scroll").dispatchEvent(new Event("scroll"));true');
    await until(()=>calls.some(call=>call.name==='/api/chat/read'&&call.body.seq===5),'read receipt after comments close');

    await click('#sidebar [data-act="modal-leader"]');await wait('!!document.querySelector(".leader-create-overlay.show #leader-name")');
    await js('window.leaderNameNode=document.querySelector("#leader-name");window.leaderTaskNode=document.querySelector("#leader-task");for(const [node,value]of [[leaderNameNode,"界面测试 leader"],[leaderTaskNode,"只创建隔离夹具身份"]]){node.value=value;node.dispatchEvent(new Event("input",{bubbles:true}));}true');
    await click('[data-act="leader-runtime"][data-runtime="codex"]');await click('[data-act="leader-model"][data-model="gpt-fixture"]');await click('[data-act="leader-effort"][data-effort="high"]');
    runtimeDelay=180;await click('[data-act="leader-refresh"]');await pause(260);
    await check('native discovery updates only leader brain controls and preserves both actual text field nodes','leaderNameNode===document.querySelector("#leader-name")&&leaderTaskNode===document.querySelector("#leader-task")&&leaderNameNode.value==="界面测试 leader"&&leaderTaskNode.value==="只创建隔离夹具身份"&&document.querySelector("#leader-form").elements.runtime.value==="codex"&&document.querySelector("#leader-form").elements.model.value==="gpt-fixture"&&document.querySelector("#leader-form").elements.effort.value==="high"');
    await capture('07-leader-create');
    runtimeDelay=0;await click('#leader-form [type="submit"]');await wait('document.querySelector("#chat-head .chat-name")?.textContent==="界面测试 leader"');
    assert.deepEqual(calls.filter(call=>call.name==='/api/leader').map(call=>call.body),[{name:'界面测试 leader',task:'只创建隔离夹具身份',runtime:'codex',model:'gpt-fixture',effort:'high'}]);
    await check('accepted leader creation closes its own portal and navigates exactly once to the returned identity','!document.querySelector(".leader-create-overlay").classList.contains("show")&&location.hash.includes("created-leader")');
    await click('[data-act="leader"][data-id="leader"]');await wait('document.querySelector("#chat-head .chat-name")?.textContent==="声音负责人"');
  }
  async function personnelJourneys(){
    state.members[0].runtime='claude';const before=polls;await until(()=>polls>before,'member carrier refresh');
    await click('[data-act="right-open"]');await pause(350);await mouse('#right-panel [data-act="card"][data-id="member"]');
    await wait('document.querySelector("#card").classList.contains("show")');
    await check('person inspector retains the full member details and stable action identity','document.querySelector("#card .card-head").textContent.includes("测量员")&&document.querySelector("#card [data-act=open-vscode]").dataset.id==="member"&&document.querySelector("#card .card-body").textContent.includes("设备和文件")');
    await js('document.querySelector("#card [data-act=rename]").focus();true');
    win.webContents.sendInputEvent({type:'keyDown',keyCode:'K',modifiers:['control']});win.webContents.sendInputEvent({type:'keyUp',keyCode:'K',modifiers:['control']});
    await wait('document.querySelector(".command-palette-overlay").classList.contains("show")');
    win.webContents.sendInputEvent({type:'keyDown',keyCode:'Escape'});win.webContents.sendInputEvent({type:'keyUp',keyCode:'Escape'});
    await wait('!document.querySelector(".command-palette-overlay").classList.contains("show")');
    await check('quick find over a person card closes by Escape and returns focus to a visibly open card control','(()=>{const b=document.activeElement;return document.querySelector("#card").classList.contains("show")&&b.matches("#card [data-act=rename]")&&b.getClientRects().length>0&&getComputedStyle(b).visibility==="visible"})()');
    await click('#card [data-act="rename"]');await wait('!!document.querySelector("#session-rename-form")');
    await js('(()=>{const input=document.querySelector("#session-new-name");input.value="测量员新名字";input.dispatchEvent(new Event("input",{bubbles:true}));return true})()');
    await click('#session-rename-form [type="submit"]');await wait('document.querySelector("#rename-error")?.textContent==="隔离改名失败"');
    await check('person rename failure preserves its editable draft without closing the card','document.querySelector("#session-new-name").value==="测量员新名字"&&!document.querySelector("#session-rename-form [type=submit]").disabled&&document.querySelector("#card").classList.contains("show")');
    await click('#session-rename-form [type="submit"]');await wait('!document.querySelector("#backdrop").classList.contains("show")&&document.querySelector("#card .card-head").textContent.includes("测量员新名字")');
    assert.deepEqual(calls.filter(call=>call.name==='/api/session/rename').map(call=>call.body),[{session_id:'member',name:'测量员新名字'},{session_id:'member',name:'测量员新名字'}]);
    await click('#card [data-act="open-vscode"]');await wait('!!document.querySelector("#card .step [data-act=handoff-go]")');
    await click('#card [data-act="handoff-go"]');await wait('!document.querySelector("#card .step")');
    assert.deepEqual(calls.find(call=>call.name==='/api/open/continue').body,{session_id:'member',confirm:true});
    evidence.checks.push('native handoff uses its captured member identity and confirmation exactly once');
    await capture('08-person-card');await click('#card [data-act="card-close"]');await click('[data-act="right-close"]');

    await click('#chat-head [data-act="session-menu"]');await js('[...document.querySelectorAll("#menu [data-act=submenu]")].find(b=>b.textContent.includes("设为优先")).click();true');
    await click('#menu [data-act="set-priority"][data-rank="1"]');await wait('!!document.querySelector("#sidebar .priority-zone [data-id=leader]")');
    await check('priority menu transactions update the same sidebar ordering owner','document.querySelector("#sidebar .priority-zone [data-id=leader]").textContent.includes("优先 1")');
    await click('#chat-head [data-act="session-menu"]');await js('[...document.querySelectorAll("#menu [data-act=submenu]")].find(b=>b.textContent.includes("设为优先")).click();true');
    await click('#menu [data-act="set-priority"][data-rank="0"]');await wait('!document.querySelector("#sidebar .priority-zone [data-id=leader]")');

    await click('#sidebar [data-view="usage"]');await wait('!!document.querySelector(".usage-page .utable")');await click('[data-act="usage-tab"][data-tab="daily"]');
    await wait('document.querySelector(".usage-page .utable").textContent.includes("2026-10-27")');await click('#sidebar [data-act="leader"][data-id="leader"]');
    await click('#sidebar [data-view="usage"]');await wait('document.querySelector("[data-act=usage-tab][data-tab=daily]").classList.contains("on")');
    await check('usage remount retains the selected view and cached table while the shell owns navigation','document.querySelector(".usage-page .utable").textContent.includes("2026-10-27")&&!document.querySelector(".usage-page .usage-error")');
    await click('#sidebar [data-act="leader"][data-id="leader"]');
    await click('#sidebar [data-act="theme"]');await check('appearance actions persist their own local preference','localStorage.getItem("crew.theme")==="light"');await click('#sidebar [data-act="theme"]');await click('#sidebar [data-act="theme"]');
    state.members[0].name='测量员';state.members[0].runtime='codex';const restored=polls;await until(()=>polls>restored,'original member restored');
  }
  (async()=>{
    try{
      await app.whenReady();await fs.mkdir(output,{recursive:true});server=http.createServer((req,res)=>route(req,res).catch(error=>{evidence.errors.push(error.stack);json(res,{error:error.message},500);}));await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin='http://127.0.0.1:'+server.address().port;
      win=new BrowserWindow({show:false,width:1680,height:960,webPreferences:{offscreen:true,backgroundThrottling:false,contextIsolation:true,nodeIntegration:false,sandbox:true}});win.webContents.debugger.attach('1.3');
      win.webContents.on('console-message',(_event,...args)=>{const message=typeof args[0]==='object'?args[0].message:args[1];if(String(message).includes('Uncaught'))evidence.errors.push(message);});
      await win.loadURL(origin+'/?project=fixture#leader=leader');await wait('!!document.querySelector("#chat-host textarea")&&!document.querySelector("#chat-host textarea").disabled');
      await capture('00-context-before-compaction');
      await mouse('#chat-head [data-act="context-compact"]');await wait('!!document.querySelector("#chat-head .is-compacting")');
      await until(()=>calls.some(call=>call.name==='/api/session/compact'),'compact request');await pause(100);
      state.leaders[0].context={used:18000,max:200000};
      for(const stream of streams.filter(stream=>stream.fixtureSession==='leader'))stream.write('data: '+JSON.stringify({seq:2,type:'compact',pre_tokens:84000,post_tokens:18000})+'\n\n');
      await wait('document.querySelector("#chat-head .context-pct")?.textContent==="9%"&&!document.querySelector("#chat-head .is-compacting")');
      evidence.compactIdle=await js('(()=>{const b=document.querySelector("#chat-head .context-ring"),s=getComputedStyle(b);return{hover:b.matches(":hover"),focus:b.matches(":focus-visible"),shadow:s.boxShadow,background:s.backgroundColor,busy:b.getAttribute("aria-busy"),disabled:b.disabled}})()');
      await capture('00-context-after-compaction');
      await check('completed compaction has no persistent pressed border while the pointer stays over the gauge','(()=>{const b=document.querySelector("#chat-head .context-ring");return b.matches(":hover")&&!b.disabled&&!b.hasAttribute("aria-busy")&&!b.classList.contains("is-compacting")&&getComputedStyle(b).boxShadow==="none"})()');
      await win.webContents.debugger.sendCommand('Input.dispatchMouseEvent',{type:'mouseMoved',x:800,y:400});
      await js('document.querySelector("#chat-head [data-act=model-menu]").focus()');
      win.webContents.sendInputEvent({type:'keyDown',keyCode:'Tab'});win.webContents.sendInputEvent({type:'keyUp',keyCode:'Tab'});await pause(50);
      await check('keyboard Tab still exposes a visible focus outline on the context gauge','document.activeElement.matches("#chat-head .context-ring:focus-visible")&&getComputedStyle(document.activeElement).outlineStyle!=="none"');
      state.leaders[0].runtime='codex';state.leaders[0].model='gpt-6.1-sol';state.leaders[0].effort='medium';state.leaders[0].context={used:84000,max:200000};
      state.runtimes=[{id:'codex',name:'Codex',available:true,models:[{id:'gpt-6.1-sol',name:'GPT-6.1-Sol',efforts:['medium']}],efforts:['medium']}];
      await wait('document.querySelector("#chat-head .model-label")?.textContent.includes("GPT-6.1-Sol")&&document.querySelector("#chat-head .context-pct")?.textContent==="42%"');
      await mouse('#sidebar [data-act="context-compact"][data-id="leader"]');await wait('!!document.querySelector("#sidebar .is-compacting")');
      await until(()=>calls.filter(call=>call.name==='/api/session/compact').length===2,'Codex compact request');await pause(100);state.leaders[0].context={used:18000,max:200000};
      for(const stream of streams.filter(stream=>stream.fixtureSession==='leader'))stream.write('data: '+JSON.stringify({seq:3,type:'compact',pre_tokens:84000,post_tokens:18000})+'\n\n');
      await wait('document.querySelector("#sidebar .context-pct")?.textContent==="9%"&&!document.querySelector(".is-compacting")');await pause(200);
      await check('Codex sidebar compaction also clears its busy appearance while hovered','(()=>{const b=document.querySelector("#sidebar .context-ring");return b.matches(":hover")&&!b.disabled&&!b.hasAttribute("aria-busy")&&getComputedStyle(b).boxShadow==="none"})()');
      await win.webContents.debugger.sendCommand('Input.dispatchMouseEvent',{type:'mouseMoved',x:800,y:400});
      const toolbarFits='(()=>{const h=document.querySelector("#chat-head").getBoundingClientRect(),m=document.querySelector("#chat-head .heading-meta").getBoundingClientRect(),buttons=[...document.querySelectorAll("#chat-head .right button")].filter(b=>b.getClientRects().length),rects=buttons.map(b=>b.getBoundingClientRect());return document.querySelector("#chat-head .chat-name").getBoundingClientRect().width>50&&rects.every((r,i)=>Math.abs(r.height-32)<1&&r.left>=m.right&&r.right<=h.right&&(!i||r.left>=rects[i-1].right))&&buttons.at(-1).dataset.act==="session-menu"&&document.documentElement.scrollWidth<=innerWidth})()';
      state.leaders[0].name='声音与多模态协作负责人（完整名称保留）';
      await wait('document.querySelector("#chat-head .chat-name")?.textContent.includes("完整名称保留")');
      for(const width of [1680,1280,1000,820]){win.setSize(width,960);await pause(160);await check('toolbar keeps aligned controls and a readable identity at '+width+'px',toolbarFits);await capture('00-toolbar-'+width);}
      win.setSize(1000,960);await js('document.documentElement.style.setProperty("--fs","1.4")');await pause(160);await check('large text keeps the toolbar inside its available conversation column',toolbarFits);await capture('00-toolbar-large-text');
      win.setSize(1680,960);await js('document.documentElement.style.setProperty("--fs","1")');state.leaders[0].name='声音负责人';state.leaders[0].runtime='claude';state.leaders[0].model=null;state.leaders[0].effort=null;state.runtimes=[];
      await wait('document.querySelector("#chat-head .chat-name")?.textContent==="声音负责人"');
      await featureJourneys();
      await personnelJourneys();
      await pause(280);await js('window.chatTop=document.querySelector("#chat-host").getBoundingClientRect().top;const input=document.querySelector("#chat-host textarea");input.value="对话草稿保留";input.dispatchEvent(new Event("input",{bubbles:true}));');
      await check('pending todos, decisions and member permissions are combined without a top tray','document.querySelector(".attention-count").textContent==="4"&&document.querySelector("#attention-scope").textContent.includes("全部任务与总对话")&&!'+opened+'&&!document.querySelector("#main #decision-tray")&&!document.querySelector("#main #native-approval-tray")');
      await check('the trigger is a keyboard-focusable native button','(()=>{const b=document.querySelector("[data-act=attention]");return b.tagName==="BUTTON"&&b.type==="button"&&b.tabIndex===0})()');await click('[data-act="right-open"]');await pause(420);
      await check('architecture alone fills the right column with original rich member cards','(()=>{const p=document.querySelector("#chat-attention").getBoundingClientRect(),r=document.querySelector("#right-panel").getBoundingClientRect();return Math.abs(p.top-r.top)<1&&Math.abs(p.bottom-r.bottom)<1&&!!document.querySelector("#right-panel .leader-node .now")&&!!document.querySelector("#right-panel .node .now")&&document.querySelector("#side-task-section").hidden})()');
      await capture('00-architecture-only');await js('window.archTop=document.querySelector("#right-panel").getBoundingClientRect().top;window.chatWidth=document.querySelector("#chat-host").getBoundingClientRect().width');
      await click('[data-act="attention"]');await pause(70);await js('window.middleTop=document.querySelector("#right-panel").getBoundingClientRect().top');await pause(420);
      await check('pending grows downward and pushes architecture through real intermediate frames without changing chat width','(()=>{const r=document.querySelector("#right-panel").getBoundingClientRect();return middleTop>archTop&&middleTop<r.top&&document.querySelector("#chat-host").getBoundingClientRect().width===chatWidth&&r.top>document.querySelector("#side-attention-section").getBoundingClientRect().bottom})()');
      await check('side panels preserve chat position and draft and keep the trigger visibly selected','Math.abs(document.querySelector("#chat-host").getBoundingClientRect().top-chatTop)<.5&&document.querySelector("#chat-host textarea").value==="对话草稿保留"&&document.querySelector("[data-act=attention]").getAttribute("aria-expanded")==="true"');await capture('01-light-floating');
      await js('document.querySelector(".attention-scroll").scrollTop=160;window.panelScroll=document.querySelector(".attention-scroll").scrollTop');
      await js('window.downTop=document.querySelector("#right-panel").getBoundingClientRect().top');win.webContents.sendInputEvent({type:'keyDown',keyCode:'Escape'});await wait('!'+opened);await pause(70);await js('window.upMiddle=document.querySelector("#right-panel").getBoundingClientRect().top');await pause(420);await check('closing reverses the movement and architecture returns to the full-height position','(()=>{const top=document.querySelector("#right-panel").getBoundingClientRect().top;return upMiddle<downTop&&upMiddle>top&&Math.abs(top-archTop)<.5})()');await wait('!'+opened);await check('Escape closes pending while keeping architecture and returns keyboard focus to its trigger','document.activeElement.matches("[data-act=attention]")');await open();await check('closing and reopening retains panel reading position','document.querySelector(".attention-scroll").scrollTop===panelScroll');
      await click('[data-todo-id="1"] [data-act="todo-toggle"]');await check('compact todo expands before its completion controls become visible','document.querySelector(".todo-card.open .todo-card-actions").getClientRects().length>0');await click('[data-todo-id="1"] [data-act="todo-action"]');await wait('!!document.querySelector("#todo-note-1")');await js('window.originalNote=document.querySelector("#todo-note-1");originalNote.focus();originalNote.scrollIntoView({block:"center"});window.ends=0;originalNote.addEventListener("compositionend",()=>ends++);');
      await win.webContents.debugger.sendCommand('Input.imeSetComposition',{text:'yi shou quan',selectionStart:12,selectionEnd:12});const mark=polls;state.todos[0].title='录音测试环境已补充说明';await until(()=>polls>mark,'state poll during composition');await pause(100);
      await check('real state polling does not commit pinyin, replace or blur an active Chinese composition','originalNote===document.querySelector("#todo-note-1")&&document.activeElement===originalNote&&ends===0');await win.webContents.debugger.sendCommand('Input.insertText',{text:'已授权'});await wait('document.querySelector("#todo-note-1").value==="已授权"');
      await click('[data-todo-form="1"] button[type="submit"]');await wait('document.querySelector("#todo-note-1").closest("form").querySelector("[role=alert]")?.textContent.includes("隔离保存失败")');await check('failed save retains the exact Chinese note and remains retryable','document.querySelector("#todo-note-1").value==="已授权"&&!document.querySelector("#todo-note-1").closest("form").querySelector("button[type=submit]").disabled');
      await click('[data-todo-form="1"] button[type="submit"]');await wait('document.querySelector(".attention-count").textContent==="3"');assert.deepEqual(calls.filter(call=>call.name==='/api/todo/resolve').map(call=>call.body),[{id:1,status:'done',note:'已授权',expected_version:1},{id:1,status:'done',note:'已授权',expected_version:1}]);
      await click('[data-act="member-chat"][data-id="member"]');await wait('!'+opened);await open();await check('member conversations retain aligned toolbar controls',toolbarFits);await check('a member sees only their native permissions, not the leaders decision and todo controls','document.querySelector(".attention-count").textContent==="1"&&!document.querySelector("#chat-attention [data-todo-id]")&&!document.querySelector("#chat-attention [data-dc]")');
      await click('[data-act="leader"][data-id="leader"]');await wait('!!document.querySelector("[data-goal-act=list]")');await click('[data-goal-act="list"]');await wait('!!document.querySelector("[data-goal-act=open]")');await click('[data-goal-act="open"][data-goal-id="goal"]');await wait('!!document.querySelector("#chat-host textarea")');await open();await check('a task popover keeps only its own todos and permissions without mixing general decisions','document.querySelector(".attention-count").textContent==="2"&&!!document.querySelector("#chat-attention [data-todo-id]")&&!document.querySelector("#chat-attention [data-dc]")&&document.querySelector("#attention-scope").textContent.includes("录音评测任务")');await check('task conversations retain aligned toolbar controls',toolbarFits);await check('entering a task leaves one compact top line while actions and materials live in the right panel','document.querySelector("#goal-head").getBoundingClientRect().height<=55&&!document.querySelector("#goal-head [data-action]")&&!document.querySelector("#goal-head .goal-materials")&&!!document.querySelector("#side-task-body [data-action=pause]")&&!!document.querySelector("#side-task-body [data-kind=requirements]")');await click('[data-act="task-panel"]');await check('task materials occupy their own higher layer while pending remains visible','!document.querySelector("#side-task-section").hidden&&'+opened+'&&!document.querySelector("#chat-attention #side-task-body")');await capture('04-task-scope');await click('[data-act="side-section-close"][data-section="tasks"]');
      await click('[data-goal-act="general"]');await wait('document.querySelector("#chat-host textarea")?.value==="对话草稿保留"');await open();
      await click('[data-act="leader"][data-id="other"]');await wait('!'+opened);await open();await check('switching leaders closes the previous panel and never leaks the other groups pending work','!document.querySelector(".attention-count")&&!document.querySelector("#attention-empty").hidden&&!document.querySelector("#chat-attention [data-todo-id]")');
      await click('[data-act="leader"][data-id="leader"]');await wait('document.querySelector(".attention-count")?.textContent==="3"');await open();nativeTheme.themeSource='dark';win.setSize(850,760);await pause(100);await capture('02-dark-narrow');
      await check('narrow dark panels remain inside the viewport','(()=>{const r=document.querySelector("#chat-attention").getBoundingClientRect(),s=document.querySelector(".attention-scroll");return r.left>=0&&r.right<=innerWidth&&r.bottom<=innerHeight&&document.documentElement.scrollWidth<=innerWidth})()');
      const mutationsBeforeOutside=calls.filter(call=>call.name!=='/api/chat/read').length;
      const point={x:4,y:4};win.webContents.sendInputEvent({type:'mouseDown',button:'left',clickCount:1,...point});win.webContents.sendInputEvent({type:'mouseUp',button:'left',clickCount:1,...point});await check('clicking the conversation leaves independently controlled panels open',opened);
      assert.equal(calls.filter(call=>call.name!=='/api/chat/read').length,mutationsBeforeOutside,'outside click sends no answer or approval');
      await click('[data-side-decision] > summary');await click('[data-act="answer-option"][data-option="0"]');await wait('document.querySelector(".attention-count")?.textContent==="2"');await click('[data-native-approval-id] > summary');await click('[data-native-approval-act="allow"]');await wait('document.querySelector(".attention-count")?.textContent==="1"');
      assert.deepEqual(calls.filter(call=>call.name==='/api/answer').map(call=>call.body),[{id:3,text:'短句与混说'}]);assert.deepEqual(calls.filter(call=>call.name==='/api/native-approval').map(call=>call.body),[{id:'permission',allow:true}]);evidence.checks.push('explicit decision and permission choices retain their original API identity and update the badge');
      state.todos=[];state.decisions=[];state.native_approvals=[];const last=polls;await until(()=>polls>last,'empty refresh');await wait('!document.querySelector("#attention-empty").hidden');await check('resolved work leaves an honest empty state with no stale counter or automatic send','!document.querySelector(".attention-count")&&document.querySelector("#chat-host textarea").value==="对话草稿保留"');await capture('03-empty-state');
      state.todos=Array.from({length:18},(_,index)=>({id:100+index,title:'需要核对的长事项 '+(index+1)+'：保留实际录音和验收证据',steps:'核对样本',verification:'有可复现结果',status:'open',version:1,goal_id:'goal',session_id:'member',leader_session_id:'leader',proposer_name:'测量员',group:'声音负责人',created_at:stamp}));
      state.members.push(...Array.from({length:12},(_,index)=>({...person('extra-'+index,'协作成员 '+(index+1),index%2?'claude':'codex'),leader_session_id:'leader',group:'声音负责人'})));state.goals[0].title='很长的录音评测任务名称：'.repeat(12);state.goals[0].version++;
      const stressPoll=polls;await until(()=>polls>stressPoll,'long content refresh');await click('[data-goal-act="list"]');await wait('!!document.querySelector("[data-goal-act=open]")');await click('[data-goal-act="open"][data-goal-id="goal"]');await wait('!!document.querySelector("#chat-host textarea")');await open();await click('[data-act="right-open"]');await pause(420);
      await check('long task names, eighteen todos and thirteen members stay bounded with independent scroll areas','(()=>{const t=document.querySelector(".attention-scroll"),r=document.querySelector("#right-panel .tree"),g=document.querySelector("#goal-head");return t.scrollHeight>t.clientHeight&&r.scrollHeight>r.clientHeight&&g.getBoundingClientRect().height<=55&&document.documentElement.scrollWidth<=innerWidth})()');
      await js('document.querySelector(".attention-scroll").scrollTop=90;window.todoRead=document.querySelector(".attention-scroll").scrollTop;document.querySelector("#right-panel .tree").scrollTop=70;window.treeRead=document.querySelector("#right-panel .tree").scrollTop');await click('[data-act="task-panel"]');await click('[data-act="side-section-close"][data-section="tasks"]');
      await check('section toggles retain the other sections exact reading positions','document.querySelector(".attention-scroll").scrollTop===todoRead&&document.querySelector("#right-panel .tree").scrollTop===treeRead');await capture('05-long-task-inspector');
      await click('[data-goal-act="drawer"][data-kind="requirements"]');await wait('!document.querySelector("#goal-drawer").hidden');
      await check('task requirements cover auxiliary panels and keep the original task request',opened+'&&document.querySelector("#goal-drawer").textContent.includes("核对同一录音")');
      await check('materials actually cover the auxiliary cards at the pointer layer','(()=>{const r=document.querySelector("#goal-drawer").getBoundingClientRect();return !!document.elementFromPoint(r.left+20,r.top+100)?.closest("#goal-drawer")})()');win.webContents.sendInputEvent({type:'keyDown',keyCode:'Escape'});await wait('!document.querySelector("#side-task-section").hidden');await check('Escape from task materials restores its visible entry and keyboard focus','document.querySelector("#goal-drawer").hidden&&document.activeElement.matches("[data-goal-act=drawer][data-kind=requirements]")');
      await click('[data-action="cancel"]');await wait('!!document.querySelector("[data-goal-confirm=cancel]")');await check('task confirmation appears above auxiliary panels without executing an action',opened);
      await click('[data-goal-act="close-create"]');await wait(opened);await check('cancelling confirmation restores the original task control','document.activeElement.matches("[data-action=cancel]")');
      assert(!calls.some(call=>call.name==='/api/goals/action'));await click('[data-act="side-section-close"][data-section="tasks"]');await js('document.querySelector("#right-panel .node[data-id=member]").dispatchEvent(new MouseEvent("contextmenu",{bubbles:true,clientX:600,clientY:420}));true');await wait('document.querySelector("#menu").classList.contains("show")');await check('member operations stay visible above auxiliary panels with their exact identity',opened+'&&document.querySelector("#menu [data-act=card]").dataset.id==="member"&&document.querySelector("#menu").getBoundingClientRect().bottom<=innerHeight');
      await js('document.querySelector("#menu").classList.remove("show")');
      for(let i=0;i<6;i++)await click('[data-act="attention"]');await pause(420);
      await check('rapid toggles settle at the final expanded height without hiding architecture','document.querySelector("#attention-slot").dataset.open==="true"&&!document.querySelector("#right-panel").hidden&&Math.abs(document.querySelector("#attention-slot").getBoundingClientRect().height-document.querySelector("#side-attention-section").getBoundingClientRect().height-12)<1');
      await win.webContents.debugger.sendCommand('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
      await check('reduced motion disables panel movement','getComputedStyle(document.querySelector("#attention-slot")).transitionDuration==="0s"');
      win.setSize(820,600);await js('document.documentElement.style.setProperty("--fs","1.4")');await pause(150);
      await check('minimum desktop size and large type keep both cards within the viewport','(()=>{const a=document.querySelector("#side-attention-section").getBoundingClientRect(),r=document.querySelector("#right-panel").getBoundingClientRect();return a.bottom<=r.top+1&&r.bottom<=innerHeight&&document.documentElement.scrollWidth<=innerWidth})()');
      state.members[0].leader_session_id=null;state.members[0].group=null;state.native_approvals=[{id:'orphan-permission',session_id:'member',goal_id:null,kind:'command',command:'git status',cwd:'C:/fixture',reason:'未分组成员的单次权限',paths:[],created_at:stamp,status:'pending'}];
      const orphanPoll=polls;await until(()=>polls>orphanPoll,'unassigned member refresh');await js('location.hash="history=member"');await wait('document.querySelector("#chat-head .chat-name")?.textContent==="测量员"');await open();
      await check('unassigned members retain their own permission panel without exposing group controls','!document.querySelector("#chat-attention").hidden&&document.querySelector(".attention-count").textContent==="1"&&!!document.querySelector("[data-native-approval-id=orphan-permission]")&&!document.querySelector("#chat-head [data-act=right-open]")');
      assert.equal(evidence.errors.length,0,JSON.stringify(evidence.errors));assert(!calls.some(call=>call.name==='/api/chat/send'));evidence.ok=true;await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));console.log(JSON.stringify({ok:true,checks:evidence.checks.length,evidence:path.join(output,'evidence.json'),temporary}));
    }catch(error){evidence.errors.push(error.stack);evidence.geometry=await js('({top:document.querySelector("#chat-host")?.getBoundingClientRect().top,original:window.chatTop,archTop:window.archTop,downTop:window.downTop,upMiddle:window.upMiddle,archNow:document.querySelector("#right-panel")?.getBoundingClientRect().top,draft:document.querySelector("textarea")?.value,selected:document.querySelector("[data-act=attention]")?.getAttribute("aria-expanded"),height:document.querySelector("#attention-slot")?.getBoundingClientRect().height})').catch(()=>null);evidence.body=await js('document.body.innerText').catch(()=>null);evidence.calls=calls;await fs.mkdir(output,{recursive:true});await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));console.error(error);process.exitCode=1;}
    finally{win?.destroy();for(const stream of streams)stream.end();server?.closeAllConnections();server?.close();app.exit(process.exitCode||0);}
  })();
}
