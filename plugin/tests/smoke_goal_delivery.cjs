'use strict';
// Delivery reading journey in the real Electron renderer with an in-memory loopback API.
// No model, production HOME or Crew service is used; userData is a fresh temporary directory.
const {chromium}=require('playwright'),fs=require('node:fs/promises'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..'),web=path.join(root,'plugin/crew/web'),output=path.join(root,'Temp/ui-align/batch3/goal-delivery');
const calls=[],streams=[],errors=[],steps=[],shots=[];let browser,page,server,origin;
const clone=value=>JSON.parse(JSON.stringify(value)),catalog=[{id:'codex',name:'Codex',available:true,models:[],efforts:[]},{id:'claude',name:'Claude Code',available:false,models:[],efforts:[]}];
const theme={id:'green',name:'竹青',light:{'--accent':'#3E7C74','--on-accent':'#F7FBFA','--accent-text':'#2E6760'},dark:{'--accent':'#83B9A8','--on-accent':'#182A24','--accent-text':'#A8D6C7'}};
const now=Date.now(),at=minutes=>new Date(now-minutes*60000).toISOString();
function person(id,name,goal,extra={}){return {session_id:id,name,runtime:'codex',model:null,effort:null,status:'offline',where:'offline',busy:false,running:false,web_chat:false,last_seq:0,read_seq:0,last_reply_id:null,unread:0,pending:0,priority:null,goal,task:goal,context:null,activity:null,codex:[],codex_older:0,member_ids:[],applied:null,trust:{enabled:false},runtime_switch:{allowed:true},...extra};}
const voice=person('voice-leader','录音质量','负责短句换人识别与交付验证',{active_goal_id:'goal-b'}),board=person('board-leader','风险看板','维护每周风险摘要');
const state={project:{name:'交付阅读',root:'C:/fixture/project',theme:'green'},projects:[{name:'交付阅读',theme:'green'}],themes:[theme],runtimes:catalog,ai:{default:{runtime:'codex',model:null,effort:null},digest:null,git:null,operations:null},git_assistant:null,leaders:[voice,board],members:[],goals:[],decisions:[],todos:[],attention:[],candidates:{running:[],others:[]},limits:{claude:{seven_day:null,five_hour:null},codex:null},channel:{unread:0,latest_id:0,digesting:false,digest_error:null,digest_id:null},notification:{manual_away:false,away:false,error:null,channels:[{id:'qq',name:'QQ',state:'connected',detail:null},{id:'lark',name:'Lark',state:'connected',detail:null}]},vscode_error:null};
const longSummary='同一段录音上复测了 42 条短句：B 紧接短句接话的识别已改善，A 停顿后 B 接话 3 条里仍有 1 条把 B 并入 A。\n混说场景只做了示意对照，没有真机麦克风复测，因此不能宣称全部达标。'+'详细对照、失败样本和复测命令见完整报告。'.repeat(6);
const executions=(id,leader,round,version,status='idle')=>[{id,session_id:leader,runtime:'codex',model:null,effort:null,round,requirement_version:version,generation:1,status}];
const reportEntry=(goal,id,round,version,current,status,summary,counts,unresolved,minutes)=>({id,round,requirement_version:version,version:1,outcome:status,status,created_at:at(minutes),title:goal.title,summary,acceptance_counts:counts,unresolved,current,url:`/api/goals/report?goal_id=${goal.id}&report_id=${id}`});
const details=new Map(),journals=new Map();
function addGoal(goal,detail){const value={assignments:[],work_summary:{total:0,done:0},decision_mode:{mode:"human"},now_doing:[],pending_decision:null,working_count:0,last_activity_at:new Date().toISOString(),project:state.project.name,request:goal.request,acceptance:[{id:'a1',title:'同一录音前后对照'},{id:'a2',title:'报告带可复现 example'}],constraints:[],version:3,current_round:1,requirement_version:1,priority:0,updated_at:at(goal.minutes),supplements_after_report:[],report_available:detail.reports.length>0,...goal};delete value.minutes;const reports=detail.reports.map(make=>make(value));value.acceptance_brief=value.status==='awaiting_acceptance'?{report_id:reports[0].id,summary:reports[0].summary,user_test:[],created_at:reports[0].created_at,supplements_after_report:value.supplements_after_report}:null;state.goals.push(value);details.set(value.id,{goal:value,context:{items:[],manifest:{}},documents:[],acceptance_review:null,...detail,reports});}
addGoal({id:'goal-a',leader_session_id:voice.session_id,title:'改善短句换人的识别表现',request:'使用同一段录音对照前后结果，保留原始输入；不要在未验证时宣称成功。',status:'awaiting_acceptance',requirement_version:3,supplements_after_report:[{version:2,text:'请补充手机上打开报告的验收步骤。',actor:'user',at:at(10)},{version:3,text:'复核补充：同一录音的原始结果需要保留。',actor:voice.session_id,at:at(9)}],minutes:12},
  {executions:executions('exec-a',voice.session_id,1,1),reports:[goal=>reportEntry(goal,'report-a1',1,1,true,'awaiting_acceptance',longSummary,{passed:4,failed:0,unverified:1,simulated:0},['实物场景尚缺真人展示证据','校准评估只覆盖给定样本','部分旧类别仍有回退风险','部署来源需要另行核对','仓库根目录仍有中间产物待整理'],12)]});
addGoal({id:'goal-b',leader_session_id:voice.session_id,title:'交付可以直接打开的风险预警看板',request:'看板要给出当前风险、判断理由，并明确哪些结论尚未验证。',status:'running',current_round:2,requirement_version:2,minutes:20},
  {executions:executions('exec-b2',voice.session_id,2,2,'running'),reports:[goal=>reportEntry(goal,'report-b1',1,1,false,'completed','第一轮看板已能打开，风险判断理由齐全。',{passed:2,failed:0,unverified:0,simulated:0},[],300)]});
addGoal({id:'goal-c',leader_session_id:voice.session_id,title:'确认本周数据的使用范围',request:'待确认输入覆盖范围后继续统计，当前产出已保留。',status:'waiting_user',minutes:40},{executions:executions('exec-c',voice.session_id,1,1),reports:[]});
addGoal({id:'goal-e',leader_session_id:voice.session_id,title:'整理录音样本目录',request:'按说话人和场景整理已有样本，不删除任何原始文件。',status:'queued',minutes:60},{executions:[],reports:[]});
addGoal({id:'goal-d',leader_session_id:board.session_id,title:'每周风险摘要邮件',request:'每周一汇总上周风险变化并标出未验证结论。',status:'completed',minutes:90},
  {executions:[],reports:[goal=>reportEntry(goal,'report-d1',1,1,true,'completed','三项验收标准均有实测证据：摘要按时生成、风险变化可追溯、未验证结论单独标出。',{passed:3,failed:0,unverified:0,simulated:0},[],90)]});
function journal(sid){if(!journals.has(sid))journals.set(sid,{events:[],busy:false,read_seq:0});return journals.get(sid);}
for(const owner of [voice,board])for(let i=0;i<6;i++)journal(owner.session_id).events.push({seq:i+1,type:'text',id:owner.session_id+'-'+i,text:'总会话第 '+(i+1)+' 条记录：保留原始录音与核验结果。'.repeat(6)});
function json(response,value,status=200){response.writeHead(status,{'Content-Type':'application/json'});response.end(JSON.stringify(value));}
async function route(request,response){
  const url=new URL(request.url,origin),name=url.pathname;let body;
  if(request.method==='POST'){let raw='';for await(const part of request)raw+=part;body=JSON.parse(raw||'{}');}
  if(!name.startsWith('/api/')){const file=path.resolve(web,'.'+(name==='/'?'/index.html':name));if(!file.startsWith(web+path.sep)){response.writeHead(403);return response.end();}try{const content=await fs.readFile(file);response.writeHead(200,{'Content-Type':({'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml','.woff2':'font/woff2'})[path.extname(file)]||'application/octet-stream'});return response.end(content);}catch(_){response.writeHead(404);return response.end();}}
  calls.push({name,method:request.method,body,query:Object.fromEntries(url.searchParams),at:Date.now()});
  const sid=body?.session_id||url.searchParams.get('session_id'),gid=body?.goal_id||url.searchParams.get('goal_id');
  if(name==='/api/access/context')return json(response,{remote:false,local_controls:true,local_desktop:true,authenticated:true});
  if(name==='/api/state')return json(response,{...clone(state),project:url.searchParams.has('project')?clone(state.project):null});
  if(name==='/api/runtimes')return json(response,{runtimes:catalog});
  if(name==='/api/usage')return json(response,{tree:[{leader_session_id:voice.session_id,name:voice.name,tokens:14400,cost:0.051,share:100,codex_runs:3,codex_tokens:8500,
    self:{tokens:3600,cost:0.01275,codex_runs:1,codex_tokens:500},members:[
      {session_id:'cc-member',name:'Claude组员',tokens:10800,cost:0.03825,share:75,codex_runs:0,codex_tokens:0},
      {session_id:'cx-member',name:'Codex组员',tokens:0,cost:0,share:0,codex_runs:2,codex_tokens:8000}]}],
    daily:[{date:'2026-10-27',display_name:'Claude组员',total_tokens:10800,cost_usd:0.03825}],models:[]});
  if(name==='/api/goal'){const id=url.searchParams.get('id');return json(response,details.get(id));}
  if(name==='/api/documents'){assert(gid,'document reads must name their task');return json(response,{documents:details.get(gid)?.documents||[]});}
  if(name==='/api/chat/history')return json(response,journal(sid));
  if(name==='/api/chat/events'){response.writeHead(200,{'Content-Type':'text/event-stream','X-Crew-Execution-Id':'','X-Crew-Latest-Seq':String(journal(sid).events.at(-1)?.seq||0)});response.write(': connected\n\n');streams.push(response);return;}
  if(name==='/api/chat/read'){journal(sid).read_seq=body.seq;return json(response,{read_seq:body.seq});}
  if(name==='/api/goals/report'){
    const goal=state.goals.find(item=>item.id===gid),report=details.get(gid)?.reports.find(item=>item.id===url.searchParams.get('report_id'));
    if(!goal||!report){response.writeHead(404);return response.end();}
    response.writeHead(200,{'Content-Type':'text/html'});
    return response.end(`<!doctype html><h1>${goal.title}</h1><p>报告 ${report.id} · 第 ${report.round} 轮 · 需求 v${report.requirement_version}</p><h2>example</h2><table><tr><th>输入</th><td>A 停顿后 B 接短句</td></tr><tr><th>实际</th><td>识别为 B</td></tr></table>${Array.from({length:60},(_,index)=>`<p>复测记录第 ${index+1} 段：同一录音逐条对照。</p>`).join('')}`);
  }
  if(name==='/api/comments')return json(response,{comments:[]});
  if(name==='/api/goals/action'&&body?.action==='reject'){
    const detail=details.get(body.goal_id);
    assert.equal(detail.goal.status,'awaiting_acceptance');assert(body.note.trim());
    detail.goal.status='failed';detail.goal.version++;
    detail.acceptance_review={round:detail.goal.current_round,accepted:false,note:body.note,report_id:detail.reports[0].id};
    return json(response,{goal:detail.goal,acceptance_review:detail.acceptance_review});
  }
  if(name==='/api/goals/action'&&body?.action==='pause'){
    const detail=details.get(body.goal_id);detail.goal.status='paused';detail.goal.version++;
    for(const execution of detail.executions)execution.status='paused';
    return json(response,{goal:detail.goal});
  }
  if(name==='/api/chat/send'&&!gid){
    const value=journal(sid);value.busy=true;
    value.events.push({seq:value.events.length+1,type:'message',id:body.message_id,text:body.text,source:'human',at:new Date().toISOString()});
    return json(response,{ok:true,accepted:true,message_id:body.message_id});
  }
  if(name==='/api/chat/send'&&gid){
    const detail=details.get(gid),goal=detail.goal;
    assert(['completed','failed','awaiting_acceptance'].includes(goal.status));
    goal.current_round++;goal.requirement_version++;goal.version++;goal.status='running';
    goal.request+='\n'+body.text;
    detail.previous_acceptance_review=detail.acceptance_review;detail.acceptance_review=null;
    for(const report of detail.reports)report.current=false;
    detail.executions.push(...executions('followup-'+gid,sid,goal.current_round,goal.requirement_version,'running'));
    const value=journal(sid);value.busy=true;
    value.events.push({seq:1,type:'message',id:body.message_id,text:body.text,source:'human',at:new Date().toISOString()});
    return json(response,{ok:true,accepted:true,message_id:body.message_id,goal_id:gid,execution_id:value.execution_id});
  }
  return json(response,{error:'未实现 fixture API: '+name},404);
}
const wait=source=>page.waitForFunction(source,null,{timeout:15000}),click=async selector=>{if(selector==='#chat-host .send'&&await page.locator('[data-act=tasks-toggle]').getAttribute('aria-expanded')==='true')await page.locator('[data-act=tasks-toggle]').click();return page.locator(selector).click();},text=selector=>page.locator(selector).innerText();
async function shot(name){await page.waitForTimeout(350);await page.screenshot({path:path.join(output,name+'.png')});shots.push(name+'.png');}
async function openTask(id){await page.goto(origin+'/?project='+encodeURIComponent(state.project.name)+'#leader='+details.get(id).goal.leader_session_id+'&goal='+id);await page.reload();await page.locator(`[data-goal-tools="${id}"]`).waitFor();}
(async()=>{
  try{
    await fs.mkdir(output,{recursive:true});server=http.createServer((request,response)=>route(request,response).catch(error=>{errors.push(error.stack);json(response,{error:error.message},500);}));await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin='http://127.0.0.1:'+server.address().port;
    browser=await chromium.launch({channel:'chrome',headless:true});page=await browser.newPage({viewport:{width:1440,height:900}});page.on('pageerror',error=>errors.push(error.stack));page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
    await page.goto(origin+'/?project='+encodeURIComponent(state.project.name));await page.locator('.wh-review .goal-supplements').waitFor();assert.equal(await page.locator('.wh-review .goal-supplements li').count(),0);await shot('00-home-supplements-collapsed');await click('.wh-review [data-goal-act=supplements-toggle]');await page.locator('.wh-review .goal-supplements li').first().waitFor();assert.equal(await page.locator('.wh-review .goal-supplements li').count(),2);assert((await text('.wh-review .goal-supplements')).includes('录音质量'));await shot('00a-home-supplements-expanded');steps.push('主页待验收紧邻摘要显示补充，默认折叠，展开保留原文、版本顺序和会话名');
    await openTask('goal-a');await shot('00b-task-supplements-collapsed');assert.equal(await page.locator('#side-task-body .goal-supplements li').count(),0);await click('#side-task-body [data-goal-act=supplements-toggle]');await page.locator('#side-task-body .goal-supplements li').first().waitFor();assert.equal(await page.locator('#side-task-body .goal-supplements li').count(),2);const times=await page.locator('#side-task-body .goal-supplements time').evaluateAll(nodes=>nodes.map(node=>node.dateTime));assert.deepEqual(times,details.get('goal-a').goal.supplements_after_report.map(item=>item.at));await shot('00c-task-supplements-expanded');await click('#side-task-body [data-goal-act=supplements-toggle]');steps.push('任务验收补充默认折叠；展开按需求版本顺序显示原文与时间；旧需求执行仍列在本轮进度');
    await page.locator('#chat-host textarea').fill('总会话里尚未发送的草稿');const input=page.locator('#chat-host textarea');await input.evaluate(node=>node.dataset.smoke='same');
    assert.equal(await page.locator('#side-task-body textarea').count(),0);assert.equal(await page.locator('#chat-head [role=tab]').count(),0);assert(await page.locator('.task-delivery [data-action=accept]').isVisible());assert(await page.locator('.task-delivery [data-action=reject]').isVisible());
    const result=await text('.goal-delivery');for(const part of ['本次结果','共 5 项','通过 4','未验证 1','尚存问题 · 5 条','查看完整报告'])assert(result.includes(part),part);assert(!result.includes('模拟验证'));
    await click('[data-goal-act=conclusion-toggle]');await click('[data-goal-act=report-notes-toggle]');assert.equal(await page.locator('.goal-delivery-unresolved-list li').count(),5);assert((await text('.goal-delivery-summary')).includes('详细对照、失败样本和复测命令见完整报告。'));await shot('01-delivery');steps.push('交付在右栏，AI 验证与尚存问题分列；长报告全文和问题清单分别展开');
    await click('#side-task-body [data-goal-act=delivery]');await page.locator('.goal-reader-frame iframe').waitFor();const head=await text('.goal-report-reader .head');for(const part of ['改善短句换人的识别表现','当前版本','需求 v1','等待用户验收','返回任务'])assert(head.includes(part),part);assert.equal(await page.locator('.goal-reader-frame iframe').getAttribute('sandbox'),'allow-scripts');
    assert(await page.locator('#main').evaluate(node=>[...node.children].every(child=>child.classList.contains('goal-report-reader')?!child.inert:child.inert)));assert.equal(await page.locator('.task-delivery [data-action=accept]').evaluate(node=>{node.focus();return document.activeElement===node;}),false);
    await page.locator('.goal-reader-frame iframe').evaluate(node=>{node.dataset.smoke='same';window.reportLoads=0;node.addEventListener('load',()=>window.reportLoads++);});const srcdoc=await page.locator('.goal-reader-frame iframe').getAttribute('srcdoc');
    const detail=details.get('goal-a');detail.goal.version++;detail.reports.unshift({...detail.reports[0],id:'report-a2',version:2});await wait('document.querySelector(".goal-report-reader .head").textContent.includes("已有更新版本")');assert.equal(await page.locator('.goal-reader-frame iframe').getAttribute('srcdoc'),srcdoc);assert.equal(await page.locator('.goal-reader-frame iframe').getAttribute('data-smoke'),'same');assert.equal(await page.evaluate(()=>window.reportLoads),0);await shot('02-reader-updated');
    await page.keyboard.press('Escape');await page.locator('.goal-report-reader').waitFor({state:'detached'});assert.equal(await input.inputValue(),'总会话里尚未发送的草稿');assert.equal(await input.getAttribute('data-smoke'),'same');assert.equal(await page.evaluate(()=>document.activeElement.dataset.goalAct),'delivery');steps.push('阅读层隔离验收按钮；新稿到达时只更新标题，同一 iframe 不重载；Esc 回到原入口且总会话草稿和节点保留');
    await click('#side-task-body [data-goal-act=delivery]');await page.locator('.goal-reader-frame iframe').waitFor();assert((await text('.goal-report-reader .head')).includes('当前版本'));assert((await text('.goal-report-reader .head')).includes('第 2 稿'));await page.keyboard.press('Escape');assert.equal(await page.locator('#goal-drawer,[data-goal-act=versions]').count(),0);assert.equal(await page.evaluate(()=>document.activeElement.dataset.goalAct),'delivery');steps.push('直接交付入口读取最新稿，Esc 返回任务且旧抽屉和成果版本入口不存在');
    if(await page.locator('#chat-head [data-act=tasks-toggle]').getAttribute('aria-expanded')==='false')await click('#chat-head [data-act=tasks-toggle]');await click('#task-list [data-goal-act=open][data-goal-id=goal-b]');assert.equal(await input.inputValue(),'总会话里尚未发送的草稿');await page.locator('.goal-delivery-gap').waitFor();assert((await text('.goal-delivery-gap')).includes('当前修改尚无新交付'));await click('#side-task-body [data-goal-act=delivery]');await page.locator('.goal-reader-frame iframe').waitFor();assert((await text('.goal-report-reader .head')).includes('历史报告'));await page.keyboard.press('Escape');steps.push('切任务仍是同一总会话；新一轮无报告时明确显示缺口，旧轮次只作为历史报告阅读');
    if(await page.locator('#leader-entry').getAttribute('aria-expanded')==='true')await page.locator('#leader-entry').click();await page.setViewportSize({width:390,height:844});await page.waitForTimeout(250);await shot('03-mobile-task');assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert(await input.isVisible());assert(await input.evaluate(node=>node.getBoundingClientRect().width>200));await click('#side-task-body [data-goal-act=delivery]');await page.locator('.goal-reader-frame iframe').waitFor();await shot('04-mobile-report');await page.keyboard.press('Escape');steps.push('390px 任务右栏和报告阅读无横向溢出，输入框保持可用');await page.setViewportSize({width:1440,height:900});
    for(const initialStatus of ['awaiting_acceptance','completed']){
      const id='followup-'+initialStatus;addGoal({id,leader_session_id:voice.session_id,title:'报告收尾修改',request:'完成交付并保留证据',status:initialStatus,minutes:0},{documents:[{id:initialStatus==='completed'?802:801,title:'仅此任务资料-'+initialStatus,path:'docs/'+id+'.md',kind:'report',important:false}],executions:executions('old-'+id,voice.session_id,1,1,'completed'),reports:[goal=>reportEntry(goal,'old-report-'+id,1,1,true,initialStatus,'原始报告结论',{passed:2,failed:0,unverified:0,simulated:0},[],0)]});
      await openTask(id);if(initialStatus==='awaiting_acceptance'){await click('[data-action=reject]');await page.locator('[data-goal-confirm=reject] textarea').fill('报告收尾缺少复测证据');await click('[data-goal-confirm=reject] button[type=submit]');await page.locator('#side-task-body .goal-status[data-status=failed]').waitFor();assert((await text('.goal-review')).includes('报告收尾缺少复测证据'));}
      const pin=page.locator('.chat-task-binding');assert.equal(await pin.getAttribute('aria-pressed'),'false');await pin.click();await page.locator('#chat-host textarea').fill('请继续修改报告结尾');const sent=page.waitForResponse(response=>response.url().includes('/api/chat/send')&&response.request().method()==='POST');await click('#chat-host .send');assert((await sent).ok());await page.locator('#side-task-body .goal-status[data-status=running]').waitFor();assert.equal(details.get(id).goal.current_round,2);assert(await page.locator('.chat-task-label').filter({hasText:'→ 报告收尾修改'}).count());assert((await text('.goal-delivery-gap')).includes('历史版本'));await page.locator('#side-task-body').getByText('仅此任务资料-'+initialStatus,{exact:true}).waitFor();assert(!(await text('#side-task-body')).includes('仅此任务资料-'+(initialStatus==='completed'?'awaiting_acceptance':'completed')));await shot('05-bound-followup-'+initialStatus);await page.keyboard.press('Escape');
    }
    steps.push('退回或已结束任务由点亮的小框继续，仍用 leader session + goal_id；旧报告和退回理由保留，文档严格归属当前任务');
    assert(calls.filter(call=>call.name==='/api/chat/history'||call.name==='/api/chat/events'||call.name==='/api/chat/read').every(call=>!call.query.goal_id&&!call.body?.goal_id));assert(!calls.some(call=>call.name==='/api/chat/interrupt'));
    const mark=calls.length;await page.goto(origin+'/?project='+encodeURIComponent(state.project.name)+'#usage');await page.reload();await page.locator('.utable tr').filter({hasText:'Claude组员'}).waitFor();for(const part of ['Claude组员','Codex组员','Codex 8.5K','Claude 折合'])assert((await text('.utable')).includes(part),part);await click('[data-act=usage-fold]');assert(!(await text('.utable')).includes('Claude组员'));assert((await text('.utable')).includes('Codex 8.5K'));await click('[data-act=usage-fold]');await click('[data-act=usage-tab][data-tab=daily]');assert((await text('.utable')).includes('Claude组员'));assert(calls.slice(mark).every(call=>call.method==='GET'||call.name==='/api/chat/read'));steps.push('完整用量页保留按成员身份、折叠合计和按天统计，读取不唤醒模型');
    assert.deepEqual(errors,[]);assert(calls.filter(call=>call.name==='/api/documents').every(call=>call.query.goal_id));await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify({ok:true,steps,shots,calls,errors},null,2));console.log(JSON.stringify({ok:true,checks:steps.length,evidence:path.join(output,'evidence.json')}));
  }catch(error){console.error(error);process.exitCode=1;await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify({ok:false,steps,shots,calls,errors,failure:error.stack},null,2));if(page)await shot('failure');}
  finally{await browser?.close();for(const stream of streams)stream.end();server?.closeAllConnections();server?.close();}
})();
