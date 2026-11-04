'use strict';
// Real renderer + in-memory loopback API. No Crew HOME, CLI or model is used.
const {chromium}=require('playwright'),fs=require('node:fs/promises'),path=require('node:path'),os=require('node:os'),http=require('node:http'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..'),web=path.join(root,'plugin/crew/web'),output=path.join(root,'Temp/ui-align/batch2/git-settings');
const calls=[],errors=[],expectedHttpErrors=[],checks=[],shots=[],waiting=[];
let win,browser,page,server,origin,home,graph='ready',failSave=false,staleSave=false;
const leader={session_id:'git-chief',name:'Git 管理',runtime:'codex',model:null,effort:null,status:'offline',where:'offline',busy:false,running:false,web_chat:false,last_seq:0,read_seq:0,last_reply_id:null,unread:0,pending:0,priority:null,goal:'协调项目提交与合并',task:'协调项目提交与合并',context:null,activity:null,codex:[],codex_older:0,member_ids:[],applied:null,trust:{enabled:false},runtime_switch:{allowed:true}};
Object.assign(leader,{name:'Git 助手',builtin:true,base:'develop',stable:'master',requests:{active:4,waiting_user:1,merging:1,queued:2},attention:3,pending:99});
const catalog=[{id:'codex',name:'Codex',available:true,models:[],efforts:[]},{id:'claude',name:'Claude Code',available:true,models:[],efforts:[]}];
const project={name:'MIB fixture',display_name:'MIB 协作',root:'C:/fixture/MIB',theme:'green'};
const state={project,projects:[project],themes:[{id:'green',name:'青绿',light:{'--accent':'#3E7C74','--on-accent':'#F7FBFA','--accent-text':'#2E6760'},dark:{'--accent':'#83B9A8','--on-accent':'#182A24','--accent-text':'#A8D6C7'}}],runtimes:catalog,ai:{default:{runtime:'codex',model:null,effort:null},digest:null,git:null,operations:null},git_assistant:null,leaders:[],members:[],goals:[],decisions:[],todos:[],attention:[],candidates:{running:[],others:[]},limits:{claude:{seven_day:null,five_hour:null},codex:null},channel:{unread:0,latest_id:0,digesting:false,digest_error:null,digest_id:null},notification:{manual_away:false,away:false,error:null,channels:[{id:'qq',name:'QQ',state:'connected',detail:null},{id:'lark',name:'Lark',state:'connected',detail:null}]},vscode_error:null};
let settings={project,revision:'initial',config:null,available:true,choices:{branches:['develop','master','feature/long-name'],base:'develop',stable:'master'},leaders:[{session_id:leader.session_id,name:leader.name,cwd:'C:/fixture/MIB/Temp/wt/git',runtime:'codex',model:null,effort:null}],remotes:[{name:'origin',url:'https://github.com/example/project.git',direction:'fetch'},{name:'origin',url:'https://github.com/example/project.git',direction:'push'}]};
function graphSnapshot(){
  const commits=Array.from({length:28},(_,index)=>({sha:String(index+1).padStart(40,'0'),parents:index<27?[String(index+2).padStart(40,'0')]:[],refs:index===0?['develop']:[],subject:'缓存验收提交 '+(index+1),body:'',author:'Fixture',time:new Date(Date.now()-index*60000).toISOString(),pull_requests:[],parent_branches:[]}));
  const base={name:'develop',latest:commits[0],origin_relation:'',sessions:[],worktrees:[],ahead:0,behind:0,conflicts:[],unique_commits:[],merged:true};
  const stable={...base,name:settings.config.stable},requests=['merging','waiting_user','open','open'].map((status,index)=>({id:7+index,session_id:leader.session_id,requester_name:leader.name,blocking:index===0,status,branch:'topic-'+index,description:['合入后验证集成','请确认发布范围','补充验收步骤','整理合并记录'][index],note:null,pr_number:null,created_at:new Date().toISOString()}));
  return {enabled:true,assistant:leader,repository:'MIB fixture',choices:settings.choices,config:settings.config,base,stable,branches:[base,stable],remote_only:[],recent_merge:null,ref_unique_commits:{develop:[]},requests,commits,next_offset:null,github:null,github_reason:'fixture offline',last_fetch_at:null};
}
function json(response,value,status=200){response.writeHead(status,{'Content-Type':'application/json'});response.end(JSON.stringify(value));}
async function route(request,response){
  const url=new URL(request.url,origin),name=url.pathname;let body;
  if(request.method==='POST'){let raw='';for await(const part of request)raw+=part;body=JSON.parse(raw||'{}');}
  if(!name.startsWith('/api/')){
    const file=path.resolve(web,'.'+(name==='/'?'/index.html':name));
    if(!file.startsWith(web+path.sep)){response.writeHead(403);return response.end();}
    try{const content=await fs.readFile(file);response.writeHead(200,{'Content-Type':({'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml','.woff2':'font/woff2'})[path.extname(file)]||'application/octet-stream'});return response.end(content);}catch(_){response.writeHead(404);return response.end();}
  }
  calls.push({name,method:request.method,body,query:Object.fromEntries(url.searchParams)});
  if(name==='/api/access/context')return json(response,{remote:false,local_controls:true,local_desktop:true,authenticated:true});
  if(name==='/api/chat/history')return json(response,{events:[{seq:1,type:'text',id:'reply',text:'主干 develop → master，合并前核对测试证据。'}],busy:false,read_seq:0});
  if(name==='/api/chat/events'){response.writeHead(200,{'Content-Type':'text/event-stream','X-Crew-Execution-Id':'','X-Crew-Latest-Seq':'1'});response.write(': connected\n\n');waiting.push(response);return;}
  if(name==='/api/chat/read')return json(response,{read_seq:body.seq});
  if(name==='/api/chat/send'){assert.equal(body.session_id,leader.session_id);assert(!Object.hasOwn(body,'goal_id'));return json(response,{ok:true,accepted:true,message_id:body.message_id});}
  if(name==='/api/git/enable'){assert.equal(body.runtime,'codex');graph='enabled';state.git_assistant=leader;settings={...settings,revision:'enabled',config:{base:body.base,stable:body.stable,branch_rules:'',admin_session_id:leader.session_id}};return json(response,{ok:true});}
  if(name==='/api/state')return json(response,state);
  if(name==='/api/runtimes')return json(response,{runtimes:catalog});
  if(name==='/api/git'){
    if(graph==='pending'){waiting.push(response);return;}
    if(graph==='error')return json(response,{error:'fixture Git 读取超时，请重试'},503);
    if(graph==='enabled')return json(response,graphSnapshot());
    return json(response,{enabled:false,assistant:null,repository:'MIB fixture',choices:settings.choices});
  }
  if(name==='/api/git/settings'){
    if(request.method!=='POST')return json(response,settings);
    assert(!Object.hasOwn(body,'admin_session_id'));
    if(state.git_assistant===null)return json(response,{error:'请先启用 Git 助手'},400);
    if(failSave){failSave=false;return json(response,{error:'fixture 保存失败，请保留草稿重试'},500);}
    if(staleSave){staleSave=false;settings.revision='changed-by-ai';return json(response,{error:'Git 设置已被 AI 修改，请重新读取后再保存。'},409);}
    assert.equal(body.project,project.name);assert.equal(body.revision,settings.revision);
    settings={...settings,revision:settings.revision+'-saved',config:body.enabled===false?null:{...settings.config,...body,stable:body.stable||null}};
    if(body.enabled===false){state.git_assistant=null;graph='ready';}
    return json(response,{ok:true,config:settings.config,revision:settings.revision});
  }
  return json(response,{error:'Unexpected fixture route: '+name},404);
}
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const run=expression=>page.evaluate(expression);
async function wait(expression){for(let i=0;i<200;i++){if(await run(expression))return;await sleep(25);}throw Error('Timed out: '+expression);}
async function click(selector){await page.locator(selector).first().click();}
async function fill(selector,value){await run(`(()=>{const node=document.querySelector(${JSON.stringify(selector)});node.value=${JSON.stringify(value)};node.dispatchEvent(new Event('input',{bubbles:true}));})()`);}
async function shot(name){await page.locator('#toast-root .toast').first().waitFor({state:'detached',timeout:7000});await page.waitForTimeout(300);await page.screenshot({path:path.join(output,name),fullPage:true});shots.push(name);}
async function check(name,fn){await fn();checks.push(name);}
async function main(){
  await fs.mkdir(output,{recursive:true});
  server=http.createServer((request,response)=>{route(request,response).catch(error=>{errors.push(error.stack);if(!response.headersSent)json(response,{error:error.message},500);else response.end();});});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin='http://127.0.0.1:'+server.address().port;
  browser=await chromium.launch({headless:true,channel:'chrome'});page=await browser.newPage({viewport:{width:1440,height:1000},colorScheme:'light'});
  page.on('pageerror',error=>errors.push(error.stack));page.on('console',message=>{if(message.type()!=='error')return;const expected=message.text().match(/^Failed to load resource: the server responded with a status of (400|409|500|503) \(/);if(expected)expectedHttpErrors.push({status:Number(expected[1]),message:message.text()});else errors.push(message.text());});
  await page.goto(origin+'/?project='+encodeURIComponent(project.name)+'#git');
  await check('Git 助手为 null 显示启用入口',async()=>{await page.locator('#git-enable-form').waitFor();assert.equal(await page.locator('#git-chat-host').count(),0);await shot('01-Git未启用.png');});
  await check('未启用时设置不选择管理员，后端原文 toast 并保留输入',async()=>{await click('[data-git-act="settings"]');await fill('#gits-policy','先检查证据');assert.equal(await page.locator('#gits-admin').count(),0);await click('[data-git-setting="save"]');await page.locator('#toast-root .toast').filter({hasText:'请先启用 Git 助手'}).waitFor();assert.equal(await page.locator('#gits-policy').inputValue(),'先检查证据');await click('[data-git-setting="create"]');});
  await check('启用使用原接口，Git 角标读取 attention，助手不占 leaders',async()=>{await click('[data-git-act="enable"]');await page.locator('#git-chat-host textarea').waitFor();await page.locator('#git-chat-host').getByText('主干 develop → master，合并前核对测试证据。',{exact:true}).waitFor();assert.equal(await page.locator('.rail-git .rail-badge').innerText(),'3');assert.equal(state.leaders.length,0);await shot('02-Git已启用对话.png');});
  await check('Git 使用相同助手 ID 的 history events send',async()=>{await fill('#git-chat-host textarea','请核对请求 #7');await click('#git-chat-host .send');await page.waitForFunction(()=>document.querySelector('#git-chat-host textarea').value==='');for(const name of ['/api/chat/history','/api/chat/events','/api/chat/send'])assert(calls.some(call=>call.name===name&&(call.body?.session_id||call.query.session_id)===leader.session_id));});
  await check('轮询保留输入节点，分支和 PR 折叠可读',async()=>{await fill('#git-chat-host textarea','保留 Git 草稿');const node=await page.locator('#git-chat-host textarea').elementHandle();await page.waitForResponse(response=>new URL(response.url()).pathname==='/api/state');assert(await node.evaluate(element=>element===document.querySelector('#git-chat-host textarea')));await click('.git-repository-details>summary');assert(await page.locator('.git-branch-table').isVisible());assert.match(await page.locator('.git-request-dependency').innerText(),/合并后继续/);await shot('03-Git分支与请求.png');});
  await check('Git 设置不含身份下拉，保存失败与关闭保留草稿，成功保持绑定',async()=>{await click('[data-git-act="settings"]');await click('[data-git-setting="reload"]');await click('[data-git-setting="discard"]');await page.waitForFunction(()=>document.querySelector('[data-git-setting="disable"]')!==null);await fill('#gits-policy','测试和 CI 通过后提交 PR');await shot('04-Git设置.png');failSave=true;await click('[data-git-setting="save"]');await wait(`document.querySelector('[data-git-settings-note]').textContent.includes('保存失败')`);await click('[data-git-setting="close"]');await click('[data-git-act="settings"]');assert.match(await page.locator('#gits-policy').inputValue(),/CI/);await click('[data-git-setting="save"]');await wait(`document.querySelector('[data-git-settings-note]').textContent.includes('已保存')`);assert.equal(settings.config.admin_session_id,leader.session_id);});
  await check('并发更新提示冲突，明确放弃后读取最新',async()=>{await fill('#gits-policy','不要覆盖 AI 的规则');staleSave=true;await click('[data-git-setting="save"]');await wait(`document.querySelector('[data-git-settings-note]').textContent.includes('重新读取')`);assert.equal(await page.locator('#gits-policy').inputValue(),'不要覆盖 AI 的规则');await click('[data-git-setting="reload"]');await click('[data-git-setting="discard"]');await page.waitForFunction(()=>document.querySelector('#gits-policy')?.value.includes('CI'));});
  await check('深色窄窗口 Git 设置不横向溢出',async()=>{await run(`CrewTheme.setMode('dark');CrewTheme.apply(${JSON.stringify(state.themes)},'green','dark');void 0`);await page.setViewportSize({width:540,height:800});assert(await page.locator('.git-settings-dialog').evaluate(node=>node.scrollWidth<=node.clientWidth+1));await shot('05-Git设置窄屏.png');await page.setViewportSize({width:1440,height:1000});await click('[data-git-setting="close"]');});
  await check('跨页复用快照和草稿，未采集额度无填充',async()=>{const reads=calls.filter(call=>call.name==='/api/git').length;await click('#sidebar [data-view="all"]');await click('#leader-entry');assert.equal(await page.locator('.flyout-quota progress').count(),0);assert.match(await page.locator('.flyout-quota').innerText(),/未采集/);await shot('06-Leader未采集额度.png');await page.keyboard.press('Escape');await click('#sidebar [data-view="git"]');assert.equal(calls.filter(call=>call.name==='/api/git').length,reads);assert.equal(await page.locator('#git-chat-host textarea').inputValue(),'保留 Git 草稿');});
  await check('更新失败保留助手对话和原始原因',async()=>{graph='error';await click('[data-git-act="refresh"]');await page.locator('.git-operation-error').filter({hasText:'读取超时'}).waitFor();assert.equal(await page.locator('#git-chat-host textarea').inputValue(),'保留 Git 草稿');graph='enabled';});
  await check('停用先确认，返回启用入口且不改普通 leaders',async()=>{await click('[data-git-act="settings"]');await click('[data-git-setting="disable"]');assert.notEqual(settings.config,null);await click('[data-git-setting="confirm-disable"]');await wait(`document.querySelector('[data-git-settings-note]').textContent.includes('已解除')`);await click('[data-git-setting="close"]');await page.locator('#git-enable-form').waitFor();assert.equal(state.leaders.length,0);});
  assert(calls.filter(call=>call.name==='/api/git/settings'&&call.method==='POST').every(call=>!Object.hasOwn(call.body,'admin_session_id')));assert.deepEqual(expectedHttpErrors.map(item=>item.status).sort((a,b)=>a-b),[400,409,500,503]);assert.deepEqual(errors,[]);
}
main().catch(async error=>{errors.push(error.stack);process.exitCode=1;if(page)await shot('failure.png');}).finally(async()=>{await browser?.close();for(const response of waiting)response.end();server?.closeAllConnections();if(server)await new Promise(resolve=>server.close(resolve));await fs.mkdir(output,{recursive:true});await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify({ok:errors.length===0,checks,shots,errors,expectedHttpErrors,calls},null,2));console.log(JSON.stringify({ok:errors.length===0,checks:checks.length,errors,expectedHttpErrors,output}));});
