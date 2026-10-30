/* Real Chromium + real HTTP/SQLite, with only AI carriers replaced by the isolated fixture. */
const {chromium}=require('playwright');
const {spawn,execFileSync}=require('node:child_process');
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..'),old=process.argv.includes('--old-ui'),missingDialog=process.argv.includes('--missing-dialog-css');
const out=path.join(root,'Temp/v2-ui-review',old?'old-ui':missingDialog?'missing-dialog-css':'current');
const evidence={oldUI:old,checks:[],failures:[],consoleErrors:[],screenshots:[]};
let fixture,browser;
async function main(){
  await fs.mkdir(out,{recursive:true});
  const env={...process.env,PYTHONIOENCODING:'utf-8',CREW_UI_REVIEW_FIXTURE:'1'};for(const k of ['CREW_SESSION_ID','CLAUDE_CODE_SESSION_ID','CREW_EXECUTION_ID','CREW_GOAL_ID'])delete env[k];
  fixture=spawn('python',['-u',path.join(__dirname,'conversation_routing_fixture.py')],{cwd:path.join(root,'plugin'),env,windowsHide:true,stdio:['pipe','pipe','pipe']});
  let buffer='',info;fixture.stderr.on('data',d=>process.stderr.write(d));
  fixture.stdout.on('data',d=>{buffer+=d;let n;while((n=buffer.indexOf('\n'))>=0){try{const v=JSON.parse(buffer.slice(0,n));if(v.url)info=v;}catch{}buffer=buffer.slice(n+1);}});
  const deadline=Date.now()+20000;while(!info&&Date.now()<deadline)await new Promise(r=>setTimeout(r,50));assert(info,'fixture bootstrap');evidence.fixture=info;
  browser=await chromium.launch({headless:true,channel:'chrome'});const page=await browser.newPage({viewport:{width:1440,height:1000},colorScheme:'light'});
  page.on('pageerror',error=>evidence.consoleErrors.push(error.stack||String(error)));page.on('console',msg=>{if(msg.type()==='error')evidence.consoleErrors.push(msg.text());});
  if(old){for(const name of ['app.js','chat.js','style.css','goal-ui.js','goal-ui.css','right-panels.css','index.html','leader-trust-ui.js','leader-trust-ui.css']){
    const body=execFileSync('git',['show',`a31ca95:plugin/crew/web/${name}`],{cwd:root,encoding:'utf8'});
    await page.route(name==='index.html'?'**/?project=demo*':`**/${name}`,route=>route.fulfill({status:200,contentType:name.endsWith('.css')?'text/css':name.endsWith('.js')?'text/javascript':'text/html',body}));
  }}
  if(missingDialog){const css=await fs.readFile(path.join(root,'plugin/crew/web/style.css'),'utf8');await page.route('**/style.css',route=>route.fulfill({status:200,contentType:'text/css',body:css.split('\n').filter(line=>!line.startsWith('.session-config-')&&!line.startsWith('@media(max-width:760px){.session-config-backdrop')).join('\n')}));}
  async function capture(name){await page.screenshot({path:path.join(out,name+'.png'),fullPage:true,animations:'disabled'});evidence.screenshots.push(name+'.png');}
  async function check(label,test){const pass=await test();evidence.checks.push({label,pass});if(!pass)evidence.failures.push(label);}
  async function dialogFits(selector){return page.locator(selector).evaluate(form=>{const box=form.getBoundingClientRect();return getComputedStyle(form.parentElement).position==='fixed'&&box.top>=0&&box.bottom<=innerHeight&&box.left>=0&&box.right<=innerWidth;});}
  await page.goto(info.url+'/?project=demo#leader='+info.leader);await page.locator('#goal-chat-hint').filter({hasText:'2 个任务'}).waitFor();await capture('01-general');
  await check('General conversation has no task-materials or trust shortcut',async()=>await page.locator('#chat-head [data-act="task-panel"],#chat-head [data-act="leader-trust"]').count()===0);
  await page.locator('#chat-head [data-act="right-open"]').click();await page.locator('#right-panel .node').first().waitFor();await capture('02-architecture');
  await check('Architecture has no comment affordance',async()=>await page.locator('#right-panel [data-act="comments"]').count()===0);
  await check('Relationship cards bound active dispatches and do not accumulate historical uncertainty',async()=>await page.locator('#right-panel .cx.codex-status-running').count()===2&&await page.locator('#right-panel .codex-status-unknown').count()===0&&await page.locator('#right-panel .cx-summary').count()===1);
  await check('Long dispatch descriptions remain one-line readable titles',()=>page.locator('#right-panel .cx .n').evaluateAll(nodes=>nodes.every(node=>node.textContent.length<=48&&node.getBoundingClientRect().height<30)));
  await page.locator('#chat-head [data-act="attention"]').click();await page.locator('#side-attention-section').waitFor();await page.waitForTimeout(450);await capture('03-pending-and-architecture');
  await check('Pending card does not clip a large shadow into a rectangle',()=>page.evaluate(()=>{const p=document.querySelector('#side-attention-section'),slot=document.querySelector('#attention-slot');return getComputedStyle(p).boxShadow==='none'||getComputedStyle(slot).overflow==='visible';}));
  await check('Pending and architecture fit without overlap',()=>page.evaluate(()=>{const a=document.querySelector('#side-attention-section').getBoundingClientRect(),b=document.querySelector('#right-panel').getBoundingClientRect();return a.top>=0&&a.right<=innerWidth&&b.top>=a.bottom&&b.bottom<=innerHeight;}));
  await page.locator('#chat-head [data-act="attention"]').click();await page.locator('#chat-head [data-act="right-open"]').click();
  await page.locator('#chat-host textarea').fill('总会话草稿');await page.locator('#goal-head [data-goal-act="list"]').click();await capture('04-task-list');
  await page.locator(`#goal-head [data-goal-act="open"][data-goal-id="${info.goals[0]}"]`).click();await page.locator('#chat-host textarea').waitFor();await page.locator('#chat-host textarea').fill('任务草稿');await capture('05-task');
  await check('Task has an explicit visible return to the task list',async()=>/返回任务清单/.test(await page.locator('#goal-head [data-goal-act="list"]').innerText()));
  await page.locator('#chat-head [data-act="task-panel"]').click();await page.locator('[data-goal-tools]').waitFor();await capture('06-task-materials');
  await check('Task materials retain real evidence and a complete report path',async()=>await page.locator('[data-goal-tools]').getByText('尚未证明效果达标',{exact:false}).count()>0&&await page.locator('[data-goal-tools] [data-goal-act="delivery"]').count()>0);
  await page.locator('[data-goal-tools] [data-goal-act="delivery"]').click();await page.locator('.goal-reader-frame iframe').waitFor();await capture('07-report');
  const report=page.frameLocator('.goal-reader-frame iframe');
  await check('Evidence appendix is collapsed and the authored report stays primary',async()=>await report.locator('.crew-evidence').getAttribute('open')===null);
  await report.locator('.crew-evidence>summary').click();await capture('07a-verification');
  await check('Verification retains unverified evidence without repeating the delivery narrative',async()=>await report.locator('.crew-evidence-body').getByText('未验证',{exact:true}).count()>0&&await report.locator('.crew-evidence-body h1').count()===0);
  await report.locator('.verification-item>summary').first().click();await check('Expanding a criterion exposes its original evidence reference',async()=>await report.locator('.crew-evidence-body').getByText('Measurement：sample.json',{exact:true}).isVisible());await capture('07b-evidence-reference');
  await page.locator('[data-goal-act="close-report"]').click();await page.locator('#side-task-section [data-act="side-section-close"]').click();
  await page.locator('#goal-head [data-goal-act="list"]').click();await page.locator(`#goal-head [data-goal-act="open"][data-goal-id="${info.goals[0]}"]`).click();
  await check('Returning through the list preserves the task draft',async()=>await page.locator('#chat-host textarea').inputValue()==='任务草稿');
  await page.locator('#goal-head [data-goal-act="general"]').click();await check('General draft remains independent',async()=>await page.locator('#chat-host textarea').inputValue()==='总会话草稿');await capture('08-general-return');
  await page.locator('#chat-head [data-act="session-menu"]').click();await capture('09-session-menu');
  await check('Session menu has no removed comments or group trust entry',async()=>await page.locator('#menu [data-act="comments"],#menu [data-act^="leader-trust"]').count()===0);
  if(await page.locator('#menu [data-act="dispatch-history"]').count()){
    await page.locator('#menu [data-act="dispatch-history"]').click();
    await check('Unknown dispatch outcomes remain available in history',async()=>await page.locator('.dispatch-history .codex-status-unknown').count()>0);
    await capture('09a-dispatch-history');await page.keyboard.press('Escape');
  }
  await page.keyboard.press('Escape');await page.setViewportSize({width:900,height:800});await page.locator('#chat-head [data-act="attention"]').click();await page.waitForTimeout(450);await capture('10-narrow-pending');
  await check('Narrow pending remains in viewport and composer stays reachable',()=>page.evaluate(()=>{const r=document.querySelector('#side-attention-section').getBoundingClientRect();return r.right<=innerWidth&&r.bottom<=innerHeight&&!document.querySelector('#chat-host textarea').disabled;}));
  await page.locator('#chat-head [data-act="attention"]').click();await page.setViewportSize({width:1440,height:1000});
  await page.locator('#chat-head [data-act="session-menu"]').click();
  await check('Independent active tasks do not disable general compaction',async()=>await page.locator('#menu [data-act="compact"]').isEnabled());
  if(await page.locator('#menu [data-act="compact"]').isEnabled()){
    await page.locator('#menu [data-act="compact"]').click();await capture('11-general-compact-confirm');
    const sent=page.waitForResponse(r=>r.url().includes('/api/session/compact')&&r.request().method()==='POST');
    await page.locator('[data-act="compact-confirm"]').click();const response=await sent;
    evidence.generalCompact={request:response.request().postDataJSON(),status:response.status(),response:await response.json()};
    await check('General compaction reaches only the selected general conversation',async()=>response.ok()&&evidence.generalCompact.request.session_id===info.leader&&!evidence.generalCompact.request.goal_id);
    fixture.stdin.write('compact-finish:general\n');await page.locator('.context-divider').filter({hasText:'上下文已压缩'}).waitFor();await capture('12-general-compact-complete');
    await page.locator('#goal-head [data-goal-act="list"]').click();await page.locator(`#goal-head [data-goal-act="open"][data-goal-id="${info.goals[0]}"]`).click();
    await page.locator('#chat-head [data-act="session-menu"]').click();await page.locator('#menu [data-act="compact"]').click();await capture('13-task-compact-confirm');
    const scoped=page.waitForResponse(r=>r.url().includes('/api/session/compact')&&r.request().method()==='POST');await page.locator('[data-act="compact-confirm"]').click();const taskResponse=await scoped;
    evidence.taskCompact={request:taskResponse.request().postDataJSON(),status:taskResponse.status(),response:await taskResponse.json()};
    await check('Task compaction sends a goal scope and queues behind its active reply',async()=>taskResponse.ok()&&evidence.taskCompact.request.goal_id===info.goals[0]&&evidence.taskCompact.response.queued===true);
    fixture.stdin.write('compact-finish:'+info.goals[0]+'\n');await page.locator('.context-divider').filter({hasText:'上下文已压缩'}).waitFor();
    await page.waitForFunction(()=>!document.querySelector('.context-compact-state'));await capture('14-task-compact-complete');
    await check('Task compaction finishes without losing the draft',async()=>await page.locator('#chat-host textarea').inputValue()==='任务草稿');
    await check('Missing post-compaction token counts remain unknown rather than becoming zero',async()=>await page.locator('.context-divider').filter({hasText:'上下文已压缩'}).innerText()==='上下文已压缩：5.0 万 → 未知');
  }
  await page.goto(info.url+'/?project=demo#leader='+info.delivered.leader+'&goal='+info.delivered.goal_id);
  await page.locator('#goal-head .goal-status[data-status="completed"]').waitFor();
  const before=await (await page.request.get(info.url+'/api/goal?project=demo&id='+info.delivered.goal_id)).json();
  await capture('15-delivered-task');await page.locator('#chat-host textarea').fill('请解释一下测量结果，不用修改。');
  const discussionHistory=page.waitForResponse(async response=>{
    const url=new URL(response.url());
    if(url.pathname!=='/api/chat/history'||url.searchParams.get('goal_id')!==info.delivered.goal_id||!response.ok())return false;
    const history=await response.json();return history.response_mode==='discussion'&&history.busy;
  });
  const followup=page.waitForResponse(r=>r.url().includes('/api/chat/send')&&r.request().method()==='POST');
  await page.locator('#chat-host .send').click();const followed=await followup;assert(followed.ok());const reply=await followed.json();
  const responding=await (await discussionHistory).json();
  evidence.replyHistory={execution_id:responding.execution_id,response_mode:responding.response_mode,busy:responding.busy};
  assert.equal(responding.execution_id,reply.execution_id);
  // Sending has ended before the replacement history and animation frame settle.
  // Wait for the actual new-execution UI; the original assertion still fails on the old UI.
  try{await page.waitForFunction(()=>document.querySelector('#chat-host .chat-activity')?.textContent==='回复中',null,{timeout:5000});}
  catch(error){if(error.name!=='TimeoutError')throw error;}
  evidence.replyActivity=await page.locator('#chat-host .chat-activity').innerText();
  await check('A delivered-task explanation shows replying activity',async()=>await page.locator('#chat-host .chat-activity').innerText()==='回复中');
  const after=await (await page.request.get(info.url+'/api/goal?project=demo&id='+info.delivered.goal_id)).json();
  await check('An explanation preserves the completed task and its acceptance',async()=>await page.locator('#goal-head .goal-status[data-status="completed"]').count()===1&&before.goal.status===after.goal.status&&before.goal.version===after.goal.version&&JSON.stringify(before.acceptance_review)===JSON.stringify(after.acceptance_review));
  await capture('16-delivered-task-replying');
  const changeText='把结论改成更易懂的中文。';await page.locator('#chat-host textarea').fill(changeText);
  const changeSent=page.waitForResponse(r=>r.url().includes('/api/chat/send')&&r.request().method()==='POST');await page.locator('#chat-host .send').click();assert((await changeSent).ok());
  const amendment=await page.request.post(info.url+'/api/goals/agent?project=demo',{headers:{'X-Crew':'1'},data:{actor_session_id:info.delivered.leader,execution_id:reply.execution_id,action:'amend',data:{goal_id:info.delivered.goal_id,text:changeText,expected_version:after.goal.version}}});assert(amendment.ok(),await amendment.text());
  await page.locator('#goal-head .goal-status[data-status="running"]').waitFor();
  await page.waitForFunction(()=>document.querySelector('#chat-host .chat-activity')?.textContent==='等模型回应');
  const changed=await amendment.json(),history=await (await page.request.get(info.url+'/api/chat/history?project=demo&session_id='+info.delivered.leader+'&goal_id='+info.delivered.goal_id)).json();
  await check('Explicit amendment updates the same task execution from discussion to work',async()=>history.execution_id===reply.execution_id&&history.response_mode==='work'&&changed.current_round===before.goal.current_round);
  await capture('17-task-amendment');
  await page.locator('#chat-head [data-act="task-panel"]').click();await page.locator('[data-goal-tools] [data-action="cancel"]').click();await capture('18-cancel-confirm');
  const cancelLayout=await page.locator('[data-goal-confirm="cancel"]').evaluate(form=>{const box=form.getBoundingClientRect();return {position:getComputedStyle(form.parentElement).position,left:box.left,top:box.top,right:box.right,bottom:box.bottom,width:innerWidth,height:innerHeight};});evidence.cancelLayout=cancelLayout;
  const cancellation=page.waitForResponse(r=>r.url().includes('/api/goals/action')&&r.request().method()==='POST');
  await page.locator('[data-goal-confirm="cancel"] [type="submit"]').click();assert((await cancellation).ok());
  fixture.stdin.write('finish-task:'+info.delivered.goal_id+'\n');await page.locator('#goal-head .goal-status[data-status="cancelled"]').waitFor();
  await page.locator('[data-goal-confirm="cancel"]').waitFor({state:'detached'});
  const cancelledHint=await page.locator('#chat-host .readonly-note').innerText();evidence.cancelledHint=cancelledHint;
  if(await page.locator('#side-task-section [data-act="side-section-close"]').isVisible())await page.locator('#side-task-section [data-act="side-section-close"]').click();
  await capture('19-cancelled-task');await page.locator('#goal-head [data-goal-act="general"]').click();await page.locator('#chat-host textarea').waitFor();
  await check('Cancellation dialog fits the viewport and returns to general conversation',async()=>cancelLayout.position==='fixed'&&cancelLayout.top>=0&&cancelLayout.bottom<=cancelLayout.height&&cancelLayout.left>=0&&cancelLayout.right<=cancelLayout.width&&cancelledHint==='此任务已取消，仅供查阅。可返回总会话讨论后续安排。'&&await page.locator('#chat-host textarea').isEnabled());
  await capture('20-cancelled-return-general');
  await page.locator('#goal-head [data-goal-act="create"]').click();await page.locator('[data-goal-create]').waitFor();await capture('21-goal-create-dialog');
  const goalFormFits=await dialogFits('[data-goal-create]');await page.locator('[data-goal-create] [data-goal-act="close-create"]').first().click();
  await check('Goal creation dialog fits the viewport and closing restores the composer',async()=>goalFormFits&&await page.locator('#chat-host textarea').isEnabled());
  await page.goto(info.url+'/?project=demo#leader='+info.leader);await page.locator('#chat-head [data-act="right-open"]').click();
  await page.locator('#right-panel [data-act="member-add-menu"]').click();await page.locator('#menu [data-act="member-create"]').click();await page.locator('.member-dialog').waitFor();await capture('22-member-create-dialog');
  const memberFormFits=await dialogFits('.member-dialog');await page.locator('.member-dialog [data-member-action="close"]').first().click();
  await check('Member creation dialog fits the viewport and closing restores the composer',async()=>memberFormFits&&await page.locator('#chat-host textarea').isEnabled());
  await page.locator('#right-panel [data-act="card"]').first().click();await page.locator('#card .card-head').click({button:'right'});await page.locator('#menu [data-act="runtime-switch"]').click();await page.locator('.runtime-switch-dialog').waitFor();await capture('23-runtime-switch-dialog');
  const runtimeFormFits=await dialogFits('.runtime-switch-dialog');await page.locator('.runtime-switch-dialog [data-rs-act="close"]').first().click();await page.locator('#card [data-act="card-close"]').click();
  await check('Runtime switch dialog fits the viewport and closing restores the composer',async()=>runtimeFormFits&&await page.locator('#chat-host textarea').isEnabled());
  assert.equal(evidence.consoleErrors.length,0,JSON.stringify(evidence.consoleErrors));assert.equal(evidence.failures.length,0,evidence.failures.join('\n'));
}
main().catch(error=>{evidence.error=error.stack||String(error);process.exitCode=1;}).finally(async()=>{await browser?.close();if(fixture){fixture.stdin.end('shutdown\n');await new Promise(resolve=>{fixture.once('exit',resolve);setTimeout(()=>{fixture.kill();resolve();},5000).unref();});}await fs.writeFile(path.join(out,'evidence.json'),JSON.stringify(evidence,null,2));console.log(JSON.stringify({out,checks:evidence.checks.length,failures:evidence.failures,consoleErrors:evidence.consoleErrors,error:evidence.error}));});
