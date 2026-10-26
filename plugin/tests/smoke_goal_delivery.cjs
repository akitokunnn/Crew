'use strict';
// Delivery reading journey in the real Electron renderer with an in-memory loopback API.
// No model, production HOME or Crew service is used; userData is a fresh temporary directory.
const {app,BrowserWindow}=require('electron'),fs=require('node:fs/promises'),path=require('node:path'),os=require('node:os'),http=require('node:http'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..'),web=path.join(root,'plugin/crew/web'),output=path.join(root,'desktop/smoke-output/goal-delivery');
const temporary=require('node:fs').mkdtempSync(path.join(os.tmpdir(),'crew-goal-delivery-'));
app.setPath('userData',temporary);app.setPath('sessionData',temporary);
// A fixed device scale keeps the requested window size equal to the CSS viewport on any display.
app.commandLine.appendSwitch('force-device-scale-factor','1');
const calls=[],streams=[],errors=[],steps=[],shots=[];let win,server,origin;
const delayReport=new Map(),failReportOnce=new Set(),failDetailOnce=new Set();
const clone=value=>JSON.parse(JSON.stringify(value)),catalog=[{id:'codex',name:'Codex',available:true,models:[],efforts:[]},{id:'claude',name:'Claude Code',available:false,models:[],efforts:[]}];
const theme={id:'green',name:'竹青',light:{'--accent':'#3E7C74','--on-accent':'#F7FBFA','--accent-text':'#2E6760'},dark:{'--accent':'#83B9A8','--on-accent':'#182A24','--accent-text':'#A8D6C7'}};
const now=Date.now(),at=minutes=>new Date(now-minutes*60000).toISOString();
function person(id,name,goal,extra={}){return {session_id:id,name,runtime:'codex',model:null,effort:null,status:'offline',where:'offline',busy:false,running:false,web_chat:false,last_seq:0,read_seq:0,last_reply_id:null,unread:0,pending:0,priority:null,goal,task:goal,context:null,activity:null,codex:[],codex_older:0,member_ids:[],applied:null,trust:{enabled:false},runtime_switch:{allowed:true},...extra};}
const voice=person('voice-leader','录音质量','负责短句换人识别与交付验证',{active_goal_id:'goal-b'}),board=person('board-leader','风险看板','维护每周风险摘要');
const state={project:{name:'交付阅读',root:'C:/fixture/project',theme:'green'},projects:[{name:'交付阅读',theme:'green'}],themes:[theme],runtimes:catalog,ai:{default:{runtime:'codex',model:null,effort:null},digest:null,git:null,operations:null},leaders:[voice,board],members:[],goals:[],decisions:[],todos:[],attention:[],candidates:{running:[],others:[]},limits:{claude:{seven_day:null,five_hour:null},codex:null},channel:{unread:0,latest_id:0,digesting:false,digest_error:null,digest_id:null},notification:{manual_away:false,away:false,error:null,channel:{state:'connected',detail:'隔离连接'}},vscode_error:null};
const longSummary='同一段录音上复测了 42 条短句：B 紧接短句接话的识别已改善，A 停顿后 B 接话 3 条里仍有 1 条把 B 并入 A。\n混说场景只做了示意对照，没有真机麦克风复测，因此不能宣称全部达标。'+'详细对照、失败样本和复测命令见完整报告。'.repeat(6);
const executions=(id,leader,round,version,status='idle')=>[{id,session_id:leader,runtime:'codex',model:null,effort:null,round,requirement_version:version,generation:1,status}];
const reportEntry=(goal,id,round,version,current,status,summary,counts,unresolved,minutes)=>({id,round,requirement_version:version,version:1,outcome:status,status,created_at:at(minutes),title:goal.title,summary,acceptance_counts:counts,unresolved,current,url:`/api/goals/report?goal_id=${goal.id}&report_id=${id}`});
const details=new Map(),journals=new Map();
function addGoal(goal,detail){const value={project:state.project.name,request:goal.request,acceptance:[{id:'a1',title:'同一录音前后对照'},{id:'a2',title:'报告带可复现 example'}],constraints:[],version:3,current_round:1,requirement_version:1,priority:0,updated_at:at(goal.minutes),report_available:detail.reports.length>0,...goal};delete value.minutes;state.goals.push(value);details.set(value.id,{goal:value,context:{items:[],manifest:{}},documents:[],acceptance_review:null,...detail,reports:detail.reports.map(make=>make(value))});}
addGoal({id:'goal-a',leader_session_id:voice.session_id,title:'改善短句换人的识别表现',request:'使用同一段录音对照前后结果，保留原始输入；不要在未验证时宣称成功。',status:'awaiting_acceptance',minutes:12},
  {executions:executions('exec-a',voice.session_id,1,1),reports:[goal=>reportEntry(goal,'report-a1',1,1,true,'awaiting_acceptance',longSummary,{passed:4,failed:0,unverified:1,simulated:0},['实物场景尚缺真人展示证据','校准评估只覆盖给定样本','部分旧类别仍有回退风险','部署来源需要另行核对','仓库根目录仍有中间产物待整理'],12)]});
addGoal({id:'goal-b',leader_session_id:voice.session_id,title:'交付可以直接打开的风险预警看板',request:'看板要给出当前风险、判断理由，并明确哪些结论尚未验证。',status:'running',current_round:2,requirement_version:2,minutes:20},
  {executions:executions('exec-b2',voice.session_id,2,2,'running'),reports:[goal=>reportEntry(goal,'report-b1',1,1,false,'completed','第一轮看板已能打开，风险判断理由齐全。',{passed:2,failed:0,unverified:0,simulated:0},[],300)]});
addGoal({id:'goal-c',leader_session_id:voice.session_id,title:'确认本周数据的使用范围',request:'待确认输入覆盖范围后继续统计，当前产出已保留。',status:'waiting_user',minutes:40},{executions:executions('exec-c',voice.session_id,1,1),reports:[]});
addGoal({id:'goal-e',leader_session_id:voice.session_id,title:'整理录音样本目录',request:'按说话人和场景整理已有样本，不删除任何原始文件。',status:'queued',minutes:60},{executions:[],reports:[]});
addGoal({id:'goal-d',leader_session_id:board.session_id,title:'每周风险摘要邮件',request:'每周一汇总上周风险变化并标出未验证结论。',status:'completed',minutes:90},
  {executions:[],reports:[goal=>reportEntry(goal,'report-d1',1,1,true,'completed','三项验收标准均有实测证据：摘要按时生成、风险变化可追溯、未验证结论单独标出。',{passed:3,failed:0,unverified:0,simulated:0},[],90)]});
function journal(sid,gid){const goal=state.goals.find(item=>item.id===gid),execution=gid?details.get(gid)?.executions.find(item=>item.session_id===sid&&item.round===goal.current_round):null,key=execution?.id||`${gid||'general'}:${sid}`;if(!journals.has(key))journals.set(key,{events:[],busy:false,read_seq:0,execution_id:execution?.id??null});return {key,value:journals.get(key)};}
function seed(sid,gid,count,prefix){const {value}=journal(sid,gid);for(let index=0;index<count;index++)value.events.push({seq:value.events.length+1,type:'text',id:`${prefix}-${index}`,text:`${prefix}第 ${index+1} 条记录：保留原始录音，先测量误判位置，再按同一输入前后对照。`.repeat(2)});value.read_seq=value.events.length;}
seed(voice.session_id,null,6,'总会话');seed(voice.session_id,'goal-b',40,'看板推进');seed(board.session_id,null,3,'看板总会话');
function json(response,value,status=200){response.writeHead(status,{'Content-Type':'application/json'});response.end(JSON.stringify(value));}
async function route(request,response){
  const url=new URL(request.url,origin),name=url.pathname;let body;
  if(request.method==='POST'){let raw='';for await(const part of request)raw+=part;body=JSON.parse(raw||'{}');}
  if(!name.startsWith('/api/')){const file=path.resolve(web,'.'+(name==='/'?'/index.html':name));if(!file.startsWith(web+path.sep)){response.writeHead(403);return response.end();}try{const content=await fs.readFile(file);response.writeHead(200,{'Content-Type':({'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml','.woff2':'font/woff2'})[path.extname(file)]||'application/octet-stream'});return response.end(content);}catch(_){response.writeHead(404);return response.end();}}
  calls.push({name,method:request.method,body,query:Object.fromEntries(url.searchParams),at:Date.now()});
  const sid=body?.session_id||url.searchParams.get('session_id'),gid=body?.goal_id||url.searchParams.get('goal_id');
  if(name==='/api/state')return json(response,{...clone(state),project:url.searchParams.has('project')?clone(state.project):null});
  if(name==='/api/runtimes')return json(response,{runtimes:catalog});
  if(name==='/api/goal'){const id=url.searchParams.get('id');if(failDetailOnce.delete(id))return json(response,{error:'隔离详情读取失败，可重试'},503);return json(response,details.get(id));}
  if(name==='/api/documents')return json(response,{documents:details.get(gid)?.documents||[]});
  if(name==='/api/chat/history')return json(response,journal(sid,gid).value);
  if(name==='/api/chat/events'){const {key}=journal(sid,gid);response.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache'});response.write(': connected\n\n');const stream={response,key,closed:false};streams.push(stream);response.on('close',()=>{stream.closed=true;});return;}
  if(name==='/api/chat/read'){const {value}=journal(sid,gid);value.read_seq=body.seq;return json(response,{read_seq:body.seq});}
  if(name==='/api/goals/report'){
    const goal=state.goals.find(item=>item.id===gid),report=details.get(gid)?.reports.find(item=>item.id===url.searchParams.get('report_id'));
    if(!goal||!report){response.writeHead(404);return response.end();}
    if(delayReport.has(gid))await new Promise(resolve=>setTimeout(resolve,delayReport.get(gid)));
    if(failReportOnce.delete(gid)){response.writeHead(503,{'Content-Type':'text/plain'});return response.end('fixture failure');}
    response.writeHead(200,{'Content-Type':'text/html'});
    return response.end(`<!doctype html><h1>${goal.title}</h1><p>报告 ${report.id} · 第 ${report.round} 轮 · 需求 v${report.requirement_version}</p><h2>example</h2><table><tr><th>输入</th><td>A 停顿后 B 接短句</td></tr><tr><th>实际</th><td>识别为 B</td></tr></table>${Array.from({length:60},(_,index)=>`<p>复测记录第 ${index+1} 段：同一录音逐条对照。</p>`).join('')}`);
  }
  if(name==='/api/comments')return json(response,{comments:[]});
  return json(response,{error:'未实现 fixture API: '+name},404);
}
const run=async code=>{const result=await win.webContents.executeJavaScript(`(async()=>{try{return{value:await (${code})};}catch(error){return{error:error.stack};}})()`);if(result.error)throw Error(result.error);return result.value;};
async function wait(expression,label=expression){for(let i=0;i<200;i++){if(await run(expression))return;await new Promise(resolve=>setTimeout(resolve,30));}throw Error('等待超时 '+label);}
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function click(selector){await run(`(()=>{const node=document.querySelector(${JSON.stringify(selector)});if(!node)throw Error("找不到 "+${JSON.stringify(selector)});node.click();})()`);}
async function fill(selector,value){await run(`(()=>{const node=document.querySelector(${JSON.stringify(selector)});node.value=${JSON.stringify(value)};node.dispatchEvent(new Event('input',{bubbles:true}));})()`);}
const text=selector=>run(`document.querySelector(${JSON.stringify(selector)})?.textContent ?? null`);
async function shot(name){await sleep(160);await fs.writeFile(path.join(output,name),(await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true})).toPNG());shots.push(name);}
const since=mark=>calls.filter(call=>call.at>=mark);
const reportCalls=list=>list.filter(call=>call.name==='/api/goals/report').map(call=>`${call.query.goal_id}/${call.query.report_id}`);
async function open(width,height,hash){
  // Create the next window before destroying the previous one; closing the last window would quit Electron.
  const previous=win;win=new BrowserWindow({width,height,useContentSize:true,show:false,webPreferences:{nodeIntegration:false,contextIsolation:true,sandbox:true,offscreen:true,backgroundThrottling:false}});previous?.destroy();
  win.webContents.on('console-message',(_event,...args)=>{const message=typeof args[0]==='object'?args[0].message:args[1];if(String(message).includes('Uncaught'))errors.push(message);});
  await win.loadURL(origin+'/?project='+encodeURIComponent(state.project.name)+'#'+hash);
  const viewport=await run('({width:innerWidth,height:innerHeight,dpr:devicePixelRatio})');assert.deepEqual(viewport,{width,height,dpr:1},'predictable viewport');return viewport;
}
const listCards=`[...document.querySelectorAll('#goal-head .goal-card')].map(card=>({open:card.querySelector('[data-goal-act=open]').dataset.goalId,delivery:card.querySelector('[data-goal-act=delivery]')?.dataset.goalId??null}))`;
(async()=>{
  await app.whenReady();await fs.rm(output,{recursive:true,force:true});await fs.mkdir(output,{recursive:true});
  server=http.createServer((request,response)=>{route(request,response).catch(error=>{errors.push(error.stack);json(response,{error:error.message},500);});});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin='http://127.0.0.1:'+server.address().port;
  const viewports=[await open(1440,900,'leader='+voice.session_id)];
  await wait('!!document.querySelector("#chat-host textarea") && document.querySelector(".chat-inner").textContent.includes("总会话第 6 条")');
  await fill('#chat-host textarea','总会话里还没发出的草稿：看完交付再追问数据口径');

  await click('[data-goal-act=list]');await wait('document.querySelector("#chat-host").hidden && !!document.querySelector(".goal-card")');
  const cards=await run(listCards);assert.deepEqual(cards,[{open:'goal-a',delivery:'goal-a'},{open:'goal-b',delivery:'goal-b'},{open:'goal-c',delivery:null},{open:'goal-e',delivery:null}]);
  assert.equal(await run('document.querySelectorAll("button button").length'),0,'no nested buttons');
  assert(await run('[...document.querySelectorAll(".goal-card")].every(card=>card.textContent.includes("第 ") && card.textContent.includes("录音质量"))'));
  await shot('01-任务清单-宽.png');steps.push('任务卡把「进入任务」与「查看交付报告」分成并列按钮；只有有报告的任务出现查看按钮');

  let mark=Date.now();await click('[data-goal-act=open][data-goal-id=goal-c]');await wait('!!document.querySelector("[data-goal-detail=goal-c]") && !!document.querySelector("#chat-host .chat-inner")');await sleep(300);
  assert.equal(await run('document.querySelector(".goal-delivery, .goal-report-reader, #goal-drawer:not([hidden])")'),null);assert.deepEqual(reportCalls(since(mark)),[]);assert(!since(mark).some(call=>call.name==='/api/documents'));
  steps.push('普通点击没有报告的任务：正常进入，不加空面板、不展开资料、不请求 HTML');

  await click('[data-goal-act=list]');await wait('!!document.querySelector(".goal-card")');
  mark=Date.now();await click('[data-goal-act=delivery][data-goal-id=goal-a]');await wait('!!document.querySelector(".goal-report-reader iframe")');
  const head=await text('.goal-report-reader .head');for(const part of ['改善短句换人的识别表现 · 交付报告','本轮报告','第 1 轮 · 需求 v1','AI 报告结果：等待用户验收','返回任务'])assert(head.includes(part),part);assert(!head.includes('暂无新交付'));
  assert.equal(await run('document.querySelector(".goal-report-reader iframe").getAttribute("sandbox")'),'allow-scripts');assert(await run(`document.querySelector(".goal-report-reader iframe").srcdoc.includes("default-src 'none'") && document.querySelector(".goal-report-reader iframe").srcdoc.includes("report-a1")`));
  assert.deepEqual(reportCalls(since(mark)),['goal-a/report-a1']);assert.equal(since(mark).filter(call=>call.name==='/api/goal').length,1);
  assert(await run('location.hash.includes("goal=goal-a")'));await shot('02-直接阅读-本轮报告-宽.png');
  steps.push('从清单一次点击「查看交付报告」：进入对应目标，读一次详情，直接在隔离阅读层打开本轮报告并标明轮次、结果和本轮关系');

  await click('[data-goal-act=close-report]');await wait('!document.querySelector(".goal-report-reader") && !!document.querySelector("[data-goal-detail=goal-a] .goal-delivery")');
  const panel=await text('.goal-delivery');for(const part of ['AI 提交的结论','第 1 轮报告 · 报告结果：等待用户验收','报告验收记录（AI 填写）','共 5 项','通过 4','未验证 1','报告补充说明 · 5 条','查看完整报告'])assert(panel.includes(part),part);assert(!panel.includes('模拟验证'),'zero counts are not listed');assert(!panel.includes('仍未解决 5 项'));
  assert(!(await text('.goal-delivery-meta')).includes('补充说明'),'report notes are outside acceptance counts');
  assert.equal(await run('document.activeElement.textContent'),'查看完整报告');assert.equal(await run('document.querySelector("#goal-drawer").hidden'),true);
  const compact=await run('({head:document.querySelector("#goal-head").getBoundingClientRect().height,panel:document.querySelector(".goal-delivery").getBoundingClientRect().height,chat:document.querySelector("#chat-host").getBoundingClientRect().height})');
  // Report notes now have their own row; preserve the existing chat-space requirement.
  assert(compact.panel<150,'collapsed conclusion and separate notes stay compact: '+compact.panel);assert(compact.chat>380,'chat keeps its space: '+compact.chat);
  await shot('03-返回任务-结论面板-宽.png');
  await click('[data-goal-act=conclusion-toggle]');await wait('document.querySelector(".goal-delivery-summary").classList.contains("open")');
  assert.equal(await run('document.querySelector(".goal-delivery-unresolved-list")'),null,'conclusion and notes expand independently');
  await click('[data-goal-act=report-notes-toggle]');await wait('document.querySelectorAll(".goal-delivery-unresolved-list li").length===5');
  assert(await run('document.querySelector(".goal-delivery-summary").textContent.includes("详细对照、失败样本和复测命令见完整报告。详细对照")'));await shot('04-长结论展开-宽.png');
  await click('[data-goal-act=report-notes-toggle]');await click('[data-goal-act=conclusion-toggle]');await wait('!document.querySelector(".goal-delivery-summary").classList.contains("open")');
  steps.push('「返回任务」显示共 5 项验收、通过 4 / 未验证 1；5 条报告补充说明单独展开，原文完整保留，不作为额外验收项，且与结论展开互不干扰');

  await click('[data-goal-act=list]');await click('[data-goal-act=open][data-goal-id=goal-b]');
  await wait('!!document.querySelector("[data-goal-detail=goal-b] .goal-delivery-gap") && !!document.querySelector("#chat-host textarea") && document.querySelector(".chat-inner").textContent.includes("看板推进第 40 条")');
  assert((await text('.goal-delivery-gap')).includes('当前第 2 轮暂无新交付'));assert(!(await text('#goal-head')).includes('AI 提交的结论'));
  await fill('#chat-host textarea','任务 B 里未发送的草稿');await run('(()=>{const scroll=document.querySelector(".chat-scroll");scroll.scrollTop=260;scroll.dispatchEvent(new Event("scroll"));document.querySelector("#chat-host textarea").dataset.smokeMark="kept";})()');
  const scrollBefore=await run('document.querySelector(".chat-scroll").scrollTop');
  mark=Date.now();await click('#goal-head [data-goal-act=delivery]');await wait('!!document.querySelector(".goal-report-reader iframe")');
  const historyHead=await text('.goal-report-reader .head');for(const part of ['历史报告','第 1 轮 · 需求 v1','AI 报告结果：已达标','当前第 2 轮暂无新交付'])assert(historyHead.includes(part),part);assert(!historyHead.includes('本轮报告'));
  assert.deepEqual(reportCalls(since(mark)),['goal-b/report-b1']);await shot('05-历史报告-宽.png');
  await run('document.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true}))');await wait('!document.querySelector(".goal-report-reader")');
  assert.deepEqual(await run('({draft:document.querySelector("#chat-host textarea").value,mark:document.querySelector("#chat-host textarea").dataset.smokeMark,scroll:document.querySelector(".chat-scroll").scrollTop})'),{draft:'任务 B 里未发送的草稿',mark:'kept',scroll:scrollBefore});
  steps.push('重开后的目标只提示「当前第 2 轮暂无新交付」；可读第 1 轮历史报告且明确标为历史；Esc 返回后同一聊天节点、草稿与阅读位置保留');

  await click('[data-goal-act=list]');delayReport.set('goal-a',900);mark=Date.now();await click('[data-goal-act=delivery][data-goal-id=goal-a]');await sleep(60);
  await click(`[data-act=leader][data-id="${board.session_id}"]`);await wait(`location.hash==="#leader=${board.session_id}" && !!document.querySelector("#chat-host textarea")`);await sleep(1300);
  assert.equal(await run('document.querySelector(".goal-report-reader")'),null,'late report HTML does not open on another group');assert.deepEqual(reportCalls(since(mark)),['goal-a/report-a1']);
  steps.push('A 的报告晚到时已切到另一组：不会在 B 组突然打开旧报告');

  await click(`[data-act=leader][data-id="${voice.session_id}"]`);await wait('!!document.querySelector("#chat-host textarea")');await click('[data-goal-act=list]');await wait('!!document.querySelector(".goal-card")');
  await click('[data-goal-act=delivery][data-goal-id=goal-a]');await wait('document.querySelector(".goal-report-reader")?.textContent.includes("正在读取报告")');await click('[data-goal-act=close-report]');await sleep(1300);
  assert.equal(await run('document.querySelector(".goal-report-reader")'),null,'closed reader stays closed');delayReport.delete('goal-a');
  steps.push('报告读取中点「返回任务」：晚到的 HTML 不会让阅读层复活');

  failReportOnce.add('goal-a');await click('#goal-head [data-goal-act=delivery]');await wait('!!document.querySelector(".goal-reader-status .error-line")');
  assert((await text('.goal-report-reader')).includes('报告暂时无法读取，请重试。'));await shot('06-报告读取失败-宽.png');
  await click('[data-goal-act=retry-report]');await wait('!!document.querySelector(".goal-report-reader iframe")');await click('[data-goal-act=close-report]');await wait('!document.querySelector(".goal-report-reader")');
  failDetailOnce.add('goal-a');await click('#goal-head [data-goal-act=delivery]');await wait('document.querySelector("#goal-head [role=alert]")?.textContent.includes("交付报告未能打开")');
  assert((await text('#goal-head [role=alert]')).includes('隔离详情读取失败，可重试'));assert.equal(await run('document.querySelectorAll("#goal-head [role=alert]").length'),1,'the failure is shown once');await click('#goal-head [role=alert] [data-goal-act=delivery]');await wait('!!document.querySelector(".goal-report-reader iframe")');
  await click('[data-goal-act=close-report]');await wait('!document.querySelector(".goal-report-reader")');
  steps.push('报告 HTML 与目标详情读取失败都如实显示错误，可在原位重试成功');

  await click('[data-view=all]');await click('[data-act=all-tab][data-tab=goals]');await wait('document.querySelectorAll("[data-goal-act=delivery]").length===3');
  mark=Date.now();await click('[data-goal-act=delivery][data-goal-id=goal-d]');await wait(`location.hash.includes("leader=${board.session_id}") && location.hash.includes("goal=goal-d") && !!document.querySelector(".goal-report-reader iframe")`);
  assert((await text('.goal-report-reader .head')).includes('本轮报告'));assert.deepEqual(reportCalls(since(mark)),['goal-d/report-d1']);
  await click('[data-goal-act=close-report]');await wait('!document.querySelector(".goal-report-reader") && !!document.querySelector("[data-goal-detail=goal-d] .goal-delivery")');
  const closedHead=await text('#chat-head'),closedHint=await text('#goal-chat-hint');
  assert(!closedHead.includes('本目标尚未启动'),closedHead);assert(!closedHint.includes('在此发送'),closedHint);assert(!closedHint.includes('开始本任务'),closedHint);
  assert((await text('.readonly-note')).includes('本轮已结束'));await shot('07-已结束目标无矛盾提示-宽.png');
  steps.push('从「全部 → 目标」跨组查看交付；已结束且无执行元数据的目标不再显示「本目标尚未启动」「在此发送会开始任务」');

  await click(`[data-act=leader][data-id="${voice.session_id}"]`);await click('[data-goal-act=general]');await wait('!!document.querySelector("#chat-host textarea")');
  assert.equal(await run('document.querySelector("#chat-host textarea").value'),'总会话里还没发出的草稿：看完交付再追问数据口径');
  steps.push('整个阅读旅程后回到总会话，未发送草稿仍在');

  // While reading, keyboard and pointer cannot reach the covered task area; the sidebar and group tree stay usable.
  const key=async(keyCode,modifiers=[])=>{win.webContents.sendInputEvent({type:'keyDown',keyCode,modifiers});if(keyCode==='Enter')win.webContents.sendInputEvent({type:'char',keyCode:'\r',modifiers});win.webContents.sendInputEvent({type:'keyUp',keyCode,modifiers});await sleep(70);};
  const where='(()=>{const node=document.activeElement;return {tag:node.tagName,label:String(node.dataset.goalAct||node.dataset.act||node.dataset.action||node.textContent).trim().slice(0,24),reader:!!node.closest(".goal-report-reader"),behind:!!node.closest("#main")&&!node.closest(".goal-report-reader"),sidebar:!!node.closest("#sidebar"),tree:!!node.closest("#right-panel")};})()';
  const controls='JSON.stringify([...document.querySelectorAll("#main button,#main textarea,#main input")].filter(node=>!node.closest(".goal-report-reader")).map(node=>[node.dataset.goalAct||node.dataset.act||node.dataset.action||node.tagName,node.disabled,node.hidden,node.readOnly??null,node.getAttribute("tabindex")]))';
  const coveredOnly='[...document.querySelector("#main").children].every(node=>node.classList.contains("goal-report-reader")?!node.inert:node.inert)';
  const focusClose='document.querySelector(".goal-report-reader [data-goal-act=close-report]").focus()';
  await click(`[data-act=leader][data-id="${voice.session_id}"]`);await click('[data-goal-act=list]');await wait('!!document.querySelector(".goal-card")');await click('[data-goal-act=open][data-goal-id=goal-a]');
  await wait('!!document.querySelector("[data-goal-detail=goal-a] [data-action=accept]") && !!document.querySelector("#chat-host .chat-inner")');
  const controlsBefore=await run(controls);mark=Date.now();
  await click('#goal-head [data-goal-act=delivery]');await wait('!!document.querySelector(".goal-report-reader iframe")');
  assert(await run(coveredOnly),'every task-area part behind the reader is inert, the reader is not');
  await run(focusClose);const backwards=[];for(let i=0;i<20;i++){await key('Tab',['shift']);backwards.push(await run(where));}
  await run(focusClose);const forwards=[];for(let i=0;i<20;i++){await key('Tab');forwards.push(await run(where));}
  assert.deepEqual([...backwards,...forwards].filter(item=>item.behind),[],'Tab and Shift+Tab never reach the covered task area');
  assert(backwards[0].sidebar,'Shift+Tab from 返回任务 moves to the sidebar: '+JSON.stringify(backwards[0]));assert(forwards.some(item=>item.tree),'the group tree stays reachable');
  const blocked=await run('(()=>{const result={};for(const action of ["accept","reject"]){const button=document.querySelector("#goal-head [data-action="+action+"]");button.focus();result[action]=document.activeElement===button;}const box=document.querySelector("#goal-head [data-action=accept]").getBoundingClientRect();const x=Math.round(box.left+box.width/2),y=Math.round(box.top+box.height/2),hit=document.elementFromPoint(x,y);return {...result,x,y,hitReader:!!hit.closest(".goal-report-reader"),hitButton:!!hit.closest("button")};})()');
  assert.equal(blocked.accept,false,'验收通过 cannot take focus');assert.equal(blocked.reject,false,'需要修改 cannot take focus');assert.deepEqual([blocked.hitReader,blocked.hitButton],[true,false],'the pointer lands on the reader, not on one of its buttons');
  for(const type of ['mouseDown','mouseUp'])win.webContents.sendInputEvent({type,x:blocked.x,y:blocked.y,button:'left',clickCount:1});await sleep(250);
  assert(await run('!!document.querySelector(".goal-report-reader")'),'a press over the covered task actions leaves the reader open');
  assert.deepEqual(since(mark).filter(call=>call.method==='POST'&&call.name!=='/api/chat/read'),[],'nothing behind the reader was triggered');
  await run(focusClose);await key('Escape');await wait('!document.querySelector(".goal-report-reader")');
  assert.equal(await run('document.querySelectorAll("#main [inert]").length'),0);assert.equal(await run(controls),controlsBefore,'disabled, hidden and read-only states are unchanged');
  assert.equal(await run('document.activeElement.dataset.goalAct'),'delivery','focus returns to the task');
  assert(await run('(()=>{const accept=document.querySelector("#goal-head [data-action=accept]");accept.focus();const ok=document.activeElement===accept;document.querySelector("#goal-head [data-goal-act=delivery]").focus();return ok;})()'),'task actions are focusable again after closing');
  const keyboard={backwards,forwards,blocked};
  steps.push('阅读层打开时：Tab/Shift+Tab 只在侧栏、阅读层与关系树之间移动，背后的「验收通过/需要修改」既不能聚焦也点不到；Esc 关闭后焦点回到任务，按钮的 disabled/hidden/只读状态不变');

  // The same goal changes in the background while its report is open: the header follows, the document is never reloaded.
  const goalA=state.goals.find(item=>item.id==='goal-a'),detailA=details.get('goal-a');
  async function background(change,reports=[]){
    const count=calls.filter(call=>call.name==='/api/goal').length;
    Object.assign(goalA,change,{version:goalA.version+1,updated_at:new Date().toISOString()});detailA.reports.unshift(...reports.map(make=>make(goalA)));
    for(const item of detailA.reports)item.current=item.round===goalA.current_round&&item.requirement_version===goalA.requirement_version;
    for(let i=0;i<250&&calls.filter(call=>call.name==='/api/goal').length===count;i++)await sleep(30);
    assert(calls.filter(call=>call.name==='/api/goal').length>count,'goal detail refreshed after the background change');await sleep(400);
  }
  const frameBox=()=>run('(()=>{const box=document.querySelector(".goal-report-reader iframe").getBoundingClientRect();return {x:Math.round(box.left),y:Math.round(box.top),width:600,height:300};})()');
  const frameView=async()=>(await win.webContents.capturePage(await frameBox(),{stayHidden:true,stayAwake:true})).toBitmap();
  const readerHead=()=>text('.goal-report-reader .head');
  const preserved=async(label,kept)=>{const result=await run('({marker:document.querySelector(".goal-report-reader iframe")?.dataset.smoke,loads:window.__reportLoads,srcdoc:document.querySelector(".goal-report-reader iframe").srcdoc,covered:'+coveredOnly+'})');assert.deepEqual(result,{marker:'same',loads:0,srcdoc:kept.srcdoc,covered:true},label);assert((await frameView()).equals(kept.view),label+': the rendered report view is unchanged');};
  async function readWithMarker(selector){
    await click(selector);await wait('!!document.querySelector(".goal-report-reader iframe")');await sleep(400);
    await run('(()=>{const frame=document.querySelector(".goal-report-reader iframe");frame.dataset.smoke="same";window.__reportLoads=0;frame.addEventListener("load",()=>{window.__reportLoads++;});})()');
    // Synthetic wheel/key input does not scroll the isolated sandboxed frame in a hidden window, and its document runs no
    // script; so the evidence is that the same frame node never loads again and its rendered view stays identical.
    return {view:await frameView(),srcdoc:await run('document.querySelector(".goal-report-reader iframe").srcdoc')};
  }
  let view=await readWithMarker('#goal-head [data-goal-act=delivery]');const backgroundHeads=[];
  await run('document.querySelector("#chat-head").dataset.smokeOld="1"');
  await background({},[goal=>({...reportEntry(goal,'report-a1b',1,1,true,'awaiting_acceptance','第二稿：补充了混说场景的同一录音复测。',{passed:3,failed:0,unverified:1,simulated:0},[],1),version:2})]);
  let readingHead=await readerHead();backgroundHeads.push(readingHead);for(const part of ['本轮较早报告','已有更新版本（第 2 稿）','第 1 轮 · 需求 v1 · 第 1 稿'])assert(readingHead.includes(part),part);await preserved('a newer draft in the same round',view);await shot('11-阅读中出现新稿-宽.png');
  await background({status:'running',requirement_version:2});
  readingHead=await readerHead();backgroundHeads.push(readingHead);for(const part of ['历史报告','需求已更新到 v2，本轮暂无新交付'])assert(readingHead.includes(part),part);
  assert.equal(await run('document.querySelector("#chat-head").dataset.smokeOld'),undefined,'the chat scaffold was rebuilt behind the reader');await preserved('requirement updated, task area rebuilt',view);
  await background({status:'queued',current_round:2,requirement_version:3});
  readingHead=await readerHead();backgroundHeads.push(readingHead);assert(readingHead.includes('当前第 2 轮暂无新交付'),readingHead);await preserved('reopened into a new round',view);await shot('12-重开后仍在阅读历史报告-宽.png');
  await background({status:'awaiting_acceptance'},[goal=>({...reportEntry(goal,'report-a2',2,3,true,'awaiting_acceptance','第二轮报告：混说场景已在真机复测。',{passed:2,failed:0,unverified:0,simulated:0},[],0),version:1})]);
  readingHead=await readerHead();backgroundHeads.push(readingHead);for(const part of ['历史报告','本轮已有新报告'])assert(readingHead.includes(part),part);await preserved('a new round report arrived',view);
  await key('Escape');await wait('!document.querySelector(".goal-report-reader") && (document.querySelector("[data-goal-detail=goal-a] .goal-delivery")?.textContent||"").includes("第二轮报告")');
  assert.equal(await run('document.querySelectorAll("#main [inert]").length'),0);assert.equal(await run('document.activeElement.dataset.goalAct'),'delivery');assert.equal(await run('document.querySelector("#goal-drawer").hidden'),true);
  steps.push('直接阅读中同一目标在后台出现新稿、需求更新（主区重建）、重开换轮、新轮报告：阅读头部依次改为「本轮较早报告/历史报告」并说明原因，同一个 iframe 节点不再触发 load、srcdoc 与渲染画面不变（文档未重载），主区始终不可操作；返回任务后显示新一轮结论');

  await click('[data-goal-act=drawer][data-kind=reports]');await wait('!!document.querySelector(".goal-report-card[data-report-id=report-a2]")');
  await click('.goal-report-card[data-report-id=report-a2]');await wait('!!document.querySelector("#goal-drawer iframe") && !document.querySelector("[data-goal-act=expand-report]").disabled');
  view=await readWithMarker('[data-goal-act=expand-report]');
  await background({},[goal=>({...reportEntry(goal,'report-a2b',2,3,true,'awaiting_acceptance','第二轮第二稿。',{passed:2,failed:0,unverified:0,simulated:0},[],0),version:2})]);
  readingHead=await readerHead();backgroundHeads.push(readingHead);for(const part of ['本轮较早报告','已有更新版本（第 2 稿）','第 2 轮 · 需求 v3 · 第 1 稿','返回交付资料'])assert(readingHead.includes(part),part);await preserved('manual reader, newer draft',view);await shot('13-手动阅读较早稿-宽.png');
  await key('Escape');await wait('!document.querySelector(".goal-report-reader") && !document.querySelector("#goal-drawer").hidden');
  const drawerList=await text('#goal-drawer .goal-report-list');for(const part of ['第 2 轮 · 第 2 稿 · 本轮报告','第 2 轮 · 第 1 稿 · 本轮较早报告','第 1 轮 · 第 2 稿 · 历史报告'])assert(drawerList.includes(part),part);
  assert.equal(await run('document.querySelectorAll("#main [inert]").length'),0);await click('[data-goal-act=close-drawer]');
  steps.push('手动「交付与文档 → 展开」阅读时出现同轮新稿：头部标为「本轮较早报告 · 已有更新版本」，文档不重载；Esc 与直接阅读一样关闭阅读层并解除遮挡，回到交付资料，列表按稿次标明本轮/较早/历史');

  mark=Date.now();await click('#goal-head [data-goal-act=delivery]');await wait('!!document.querySelector(".goal-report-reader iframe")');
  assert(await run(`(()=>{const node=document.querySelector('#sidebar [data-act=leader][data-id="${board.session_id}"]');node.focus();return document.activeElement===node;})()`),'the sidebar takes focus while reading');
  await key('Enter');await wait(`location.hash==="#leader=${board.session_id}" && !document.querySelector(".goal-report-reader") && !!document.querySelector("#chat-host textarea")`);
  assert.equal(await run('document.querySelectorAll("#main [inert]").length'),0);assert.deepEqual(reportCalls(since(mark)),['goal-a/report-a2b'],'viewing delivery reads the newest draft');
  steps.push('阅读中用键盘在侧栏切到另一组：阅读层关闭、主区恢复可操作；查看交付读取最高稿次报告');

  viewports.push(await open(860,760,`leader=${voice.session_id}`));await wait('!!document.querySelector("#chat-host textarea")');
  await click('[data-goal-act=list]');await wait('!!document.querySelector(".goal-card")');await shot('08-任务清单-窄.png');
  await click('[data-goal-act=open][data-goal-id=goal-a]');await wait('!!document.querySelector("[data-goal-detail=goal-a] .goal-delivery")');
  const narrow=await run('({width:innerWidth,scroll:document.documentElement.scrollWidth,head:document.querySelector("#goal-head").getBoundingClientRect().height,chat:document.querySelector("#chat-host").getBoundingClientRect().height})');
  assert(narrow.scroll<=narrow.width,'no horizontal overflow');assert(narrow.chat>250,'narrow chat keeps space: '+narrow.chat);await shot('09-结论面板-窄.png');
  await click('#goal-head [data-goal-act=delivery]');await wait('!!document.querySelector(".goal-report-reader iframe")');assert(await run('document.documentElement.scrollWidth<=innerWidth'));await shot('10-直接阅读-窄.png');
  await click('[data-goal-act=close-report]');await wait('!document.querySelector(".goal-report-reader")');
  steps.push('窄窗口：卡片、结论面板和阅读层无横向溢出，聊天区仍保留空间');

  // Reproduce the production handoff ordering, with no native tool result to reconcile.
  const former=journal(board.session_id,null).value;
  for(const event of [
    {type:'assistant',id:'start-tool',content:[{type:'tool_use',id:'start-call',name:'crew_goal',input:{action:'start',goal_id:'goal-d'}}]},
    {type:'identity_handoff',target_execution:'retired-execution',text:'当前 Crew 身份已转交执行；保留此段历史，旧原生进程停止。'},
    {type:'turn_result',status:'interrupted',success:false},{type:'status',status:'offline'}
  ])former.events.push({...event,seq:former.events.length+1,at:new Date().toISOString()});
  former.read_seq=former.events.length;
  viewports.push(await open(1100,800,`leader=${board.session_id}`));
  await wait('document.querySelector(".tool-state")?.textContent.includes("已转交")');
  assert.equal(await run('document.querySelectorAll(".tool.live,.tool .spin").length'),0);
  await click('[data-chat-fold="tool:start-call"]');
  assert(await run('document.querySelector(".tool-steps").checkVisibility() && document.querySelector(".tool-steps").textContent.includes("未返回结果")'));
  assert(!(await text('.tool-steps')).includes('执行失败'));
  await run('document.querySelector("[data-chat-goal=goal-d]").scrollIntoView({block:"nearest"})');await shot('14-总会话工具已转交.png');
  await click('[data-chat-goal=goal-d]');
  await wait('location.hash.includes("goal=goal-d") && !!document.querySelector("[data-goal-detail=goal-d]")');
  assert((await text('#goal-head')).includes('每周风险摘要邮件'));await shot('15-查看已完成任务.png');
  steps.push('旧总会话的启动工具在转交、原生中断后显示已转交；无回执时保留结果未知，直接查看已完成任务，读取不会启动或恢复 AI');

  // Long descriptions and enabled trust must not become a second permanent header card.
  board.name='股市掌控者 · 大盘与持仓风险分析';board.goal='主控：已将临时并行工作交接给三名正式 Crew 成员，固定美日大盘方案并独立审查解除风险。'.repeat(6);
  board.trust={enabled:true,goal:'moomoo 持仓卖出与风险提示 App 原型\n'+('授权范围：按用户目标自主协调、验证和交付；需要人工操作时登记待办。\n'.repeat(6))};
  board.model='fixture-model-with-a-long-discovered-name';
  addGoal({id:'compact-goal',leader_session_id:board.session_id,title:'大盘顶部预警：图示拆解、因果信号与历史验证，以及完整的边界条件与实际输入样本对照',request:'保留原始输入，分别验证美日大盘；说明未验证部分。',constraints:['不重复启动真实任务'],status:'running',minutes:0},
    {executions:executions('compact-execution',board.session_id,1,1).map(item=>({...item,model:'fixture-model-with-a-long-discovered-name'})),reports:[]});
  const compactHeaders=[];
  await run('localStorage.setItem("crew.fontScale","1.35")');
  for(const width of [1440,1100,860]){
    await open(width,800,`leader=${board.session_id}&goal=compact-goal`);await wait('!!document.querySelector("[data-goal-detail=compact-goal]") && !!document.querySelector("#chat-host textarea")');
    const size=await run('(()=>{const main=document.querySelector("#main").getBoundingClientRect(),chat=document.querySelector("#chat-host").getBoundingClientRect(),scope=document.querySelector(".chat-scope");return {width:innerWidth,top:chat.top-main.top,chat:chat.height,header:document.querySelector("#chat-head").getBoundingClientRect().height,scroll:document.documentElement.scrollWidth,scope:scope.getBoundingClientRect().height,scopeOverflow:scope.scrollWidth>scope.clientWidth};})()');
    assert(size.top<235,'compact header at 135% scale: '+JSON.stringify(size));assert(size.header<85);assert(size.scope<30);assert(size.scopeOverflow);assert(size.scroll<=size.width);assert(size.chat>500);compactHeaders.push(size);
    assert.equal(await run('document.querySelectorAll("#leader-trust-note,.leader-trust-note").length'),0);
    await shot(`16-紧凑顶部-${width}.png`);
  }
  await open(1440,900,`leader=${board.session_id}&goal=compact-goal`);await wait('!!document.querySelector("[data-goal-detail=compact-goal]")');
  await click('[data-goal-act=drawer][data-kind=context]');await wait('!document.querySelector("#goal-drawer").hidden');
  const selected=await run('(()=>{const active=document.querySelector("[data-kind=context][data-goal-act=drawer]"),idle=document.querySelector("[data-kind=reports][data-goal-act=drawer]");return {expanded:active.getAttribute("aria-expanded"),idle:idle.getAttribute("aria-expanded"),color:getComputedStyle(active).color,idleColor:getComputedStyle(idle).color,marker:getComputedStyle(active).boxShadow};})()');
  assert.equal(selected.expanded,'true');assert.equal(selected.idle,'false');assert.notEqual(selected.color,selected.idleColor);assert.notEqual(selected.marker,'none');await shot('17-上下文选中反馈.png');
  await click('[data-goal-act=drawer][data-kind=requirements]');await wait('document.querySelector("#goal-drawer h2").textContent==="需求与验收"');
  assert.equal(await run('document.querySelectorAll(".goal-materials [aria-expanded=true]").length'),1);assert.equal(await run('document.querySelector("[data-kind=context][data-goal-act=drawer]").getAttribute("aria-expanded")'),'false');
  assert((await text('#goal-drawer')).includes('保留原始输入'));assert((await text('#goal-drawer')).includes('不重复启动真实任务'));assert.equal(await run('document.querySelectorAll("#goal-head .goal-requirements").length'),0);await shot('18-需求侧栏选中.png');
  await click('[data-goal-act=drawer][data-kind=reports]');await wait('document.querySelector("#goal-drawer h2").textContent==="交付与文档"');assert.equal(await run('document.querySelectorAll(".goal-materials [aria-expanded=true]").length'),1);
  await key('Escape');assert.equal(await run('document.querySelectorAll(".goal-materials [aria-expanded=true]").length'),0);
  await click('#chat-head [data-act=leader-trust]');await wait('!!document.querySelector("#leader-trust-goal")');assert.equal(await run('document.querySelector("#leader-trust-goal").value'),board.trust.goal);await click('[data-lt-act=close]');
  await run('document.documentElement.dataset.theme="dark"');await click('[data-goal-act=drawer][data-kind=context]');await shot('19-深色选中反馈.png');await key('Escape');
  steps.push('135% 字号和长职责/长任务名下，信任开启不增加常驻卡片；三种资料共用侧栏并即时同步选中反馈，Esc 清除状态，信任授权全文可读且未保存或执行');

  const forbidden=calls.filter(call=>['/api/chat/send','/api/goals/action','/api/goals/agent','/api/goals/create','/api/spawn','/api/chat/interrupt','/api/session/stop'].includes(call.name)||/wake|spawn|start/.test(call.name));
  assert.deepEqual(forbidden,[]);const posts=[...new Set(calls.filter(call=>call.method==='POST').map(call=>call.name))];assert(posts.every(name=>name==='/api/chat/read'),posts.join(','));
  assert(calls.filter(call=>call.name==='/api/goals/report').every(call=>details.get(call.query.goal_id)?.reports.some(item=>item.id===call.query.report_id)));
  assert.equal(errors.length,0,errors.join('\n'));
  const evidence={ok:true,scope:'real Electron renderer with in-memory loopback fixtures; no model, production HOME, Crew service or user data',viewports,steps,shots,compact,narrow,keyboard,backgroundHeads,compactHeaders,selected,forbiddenCalls:forbidden,postEndpoints:posts,
    endpoints:Object.entries(calls.reduce((count,call)=>({...count,[call.name]:(count[call.name]||0)+1}),{})),reportReads:reportCalls(calls),calls,errors,isolatedUserData:temporary};
  await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));console.log('Goal delivery smoke passed:',output);
  win.destroy();for(const stream of streams)stream.response.end();server.close();await fs.rm(temporary,{recursive:true,force:true});app.exit(0);
})().catch(async error=>{console.error(error);await fs.mkdir(output,{recursive:true});await fs.writeFile(path.join(output,'failure.txt'),error.stack||String(error));await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify({ok:false,steps,shots,calls,errors,failure:error.stack||String(error)},null,2));win?.destroy();for(const stream of streams)stream.response.end();server?.close();app.exit(1);});
