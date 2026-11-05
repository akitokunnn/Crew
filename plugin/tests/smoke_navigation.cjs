/* Real HTTP, SQLite and Chrome. Navigation never starts model work. */
'use strict';
const {chromium}=require('playwright'),{spawn}=require('node:child_process');
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..'),out=path.join(root,'Temp/ui-align/navigation');
const evidence={checks:[],consoleErrors:[],screenshots:[],modelRequests:[]};let fixture,browser,page;
async function capture(name){await page.waitForTimeout(250);await page.screenshot({path:path.join(out,name+'.png'),fullPage:true});evidence.screenshots.push(name+'.png');}
async function main(){
  await fs.mkdir(out,{recursive:true});const env={...process.env,PYTHONIOENCODING:'utf-8',CREW_NAVIGATION_REVIEW_FIXTURE:'1'};
  for(const key of ['CREW_SESSION_ID','CLAUDE_CODE_SESSION_ID','CREW_EXECUTION_ID','CREW_GOAL_ID'])delete env[key];
  fixture=spawn('python',['-u',path.join(__dirname,'conversation_routing_fixture.py')],{cwd:path.join(root,'plugin'),env,windowsHide:true,stdio:['pipe','pipe','pipe']});
  let buffer='',info;fixture.stderr.on('data',value=>process.stderr.write(value));fixture.stdout.on('data',value=>{buffer+=value;let cut;while((cut=buffer.indexOf('\n'))>=0){const item=JSON.parse(buffer.slice(0,cut));if(item.url)info=item;buffer=buffer.slice(cut+1);}});
  const deadline=Date.now()+20000;while(!info&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,50));assert(info,'isolated fixture starts');evidence.fixture=info;
  browser=await chromium.launch({headless:true,channel:'chrome'});page=await browser.newPage({viewport:{width:1440,height:900},colorScheme:'light'});
  page.on('pageerror',error=>evidence.consoleErrors.push(error.stack));page.on('console',message=>{if(message.type()==='error')evidence.consoleErrors.push(message.text());});page.on('request',request=>{if(request.method()==='POST'&&/\/api\/(chat\/send|goals\/(action|agent))/.test(request.url()))evidence.modelRequests.push(request.url());});
  const list='#goal-head [data-goal-act=list]',task=id=>`#side-task-body [data-goal-act=open][data-goal-id="${id}"]`;
  const position=()=>page.locator(list).evaluate(node=>{const r=node.getBoundingClientRect();return {x:r.x,y:r.y};});
  async function fixed(expected){const actual=await position();assert(Math.abs(actual.x-expected.x)<2&&Math.abs(actual.y-expected.y)<2);}
  for(const width of [1440,768,390]){
    await page.setViewportSize({width,height:900});await page.goto(info.url+'/?project=demo#leader='+info.leader);await page.locator('#chat-host textarea').waitFor();
    await page.locator('#chat-host textarea').fill('总会话草稿 '+width);const original=await position();await capture(width+'-general');
    await page.locator(list).click();await page.locator(task(info.goals[0])).waitFor();await fixed(original);assert(await page.locator('#chat-host textarea').isVisible());
    await page.locator(task(info.goals[0])).click();await page.locator('#side-task-body [data-goal-tools]').waitFor();await fixed(original);assert.equal(await page.locator('#chat-host textarea').inputValue(),'总会话草稿 '+width);assert.equal(await page.locator('#goal-head [role=tab]').count(),0);await capture(width+'-task');
    await page.locator('.chat-task-binding').click();await page.locator(list).click();await page.locator(task(info.goals[1])).click();assert.equal(await page.locator('.chat-task-binding').innerText(),'📌 客户端联调');assert.equal(await page.locator('.chat-task-binding').getAttribute('aria-pressed'),'false');
    await page.locator('#side-task-section [data-section=tasks]').click();assert.equal(await page.locator('#chat-host textarea').inputValue(),'总会话草稿 '+width);assert.equal(await page.locator('.chat-task-binding').isVisible(),false);
    await page.locator('#leader-entry').focus();await page.keyboard.press('Enter');await page.locator('#leader-flyout[data-open=true]').waitFor();await capture(width+'-leaders');await page.keyboard.press('Escape');assert(await page.locator('#leader-entry').evaluate(node=>document.activeElement===node));
    assert(await page.locator('#sidebar>a[href="/assistant.html"]').isVisible());assert.equal(await page.locator('#sidebar>.rail-button[data-view=all]').count(),1);assert.equal(await page.locator('#sidebar>[data-view=todos],#sidebar>[data-view=adopt],#sidebar>[data-view=usage]').count(),0);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    evidence.checks.push(width+': stable location row, retained general draft, task binding reset, keyboard flyout, fixed utility rail and no overflow');
  }
  for(const person of info.navigation){
    for(const member of [false,true]){
      const hash=member?`history=${person.member}&leader=${person.leader}`:`leader=${person.leader}`;
      await page.goto(`${info.url}/?project=${person.project}#${hash}&goal=${person.goal}`);await page.locator('#side-task-body [data-goal-tools]').waitFor();
      const params=new URLSearchParams(new URL(page.url()).hash.slice(1));assert.equal(params.get('leader'),person.leader);assert.equal(await page.locator('#chat-head .chat-name').innerText(),person.runtime==='claude'?'协作负责人':'界面负责人');assert(await page.locator('#chat-host textarea').isVisible());
      await page.locator('#side-task-section [data-section=tasks]').click();assert.equal(new URLSearchParams(new URL(page.url()).hash.slice(1)).get('goal'),null);assert(await page.locator('#sidebar>a[href="/assistant.html"]').isVisible());evidence.checks.push(person.runtime+': '+(member?'member task link':'leader task link')+' keeps the leader total conversation and task panel');
    }
    await page.goto(`${info.url}/?project=${person.project}#history=${person.member}&leader=${person.leader}`);await page.waitForFunction(name=>document.querySelector('#chat-head .chat-name')?.textContent===name,person.runtime==='claude'?'one':'界面核查');assert.equal(await page.locator('.chat-task-binding').isVisible(),false);await page.locator('#side-task-section').waitFor({state:'hidden'});evidence.checks.push(person.runtime+': explicit member total conversation has no task binding');
  }
  await page.setViewportSize({width:390,height:640});await page.goto(info.url+'/?project=demo#all');await page.locator('.wh-head').waitFor();assert(await page.locator('#sidebar>a[href="/assistant.html"]').evaluate(node=>{const r=node.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight;}));await capture('short-mobile-rail');
  await page.locator('#leader-entry').click();await page.locator('#leader-flyout [data-view=channel]').click();await page.locator('#channel-host').waitFor();
  await page.locator('#leader-entry').click();await page.locator('#leader-flyout [data-view=usage]').click();await page.locator('#sidebar>a[href="/assistant.html"]').click();await page.waitForURL('**/assistant.html');await page.locator('#assistant-chat textarea').waitFor();await capture('assistant');evidence.checks.push('channel and full usage remain in the flyout; the assistant stays reachable on a short phone');
  assert.equal(evidence.modelRequests.length,0,'navigation does not send model work');assert.deepEqual(evidence.consoleErrors,[]);
}
main().catch(async error=>{evidence.error=error.stack;process.exitCode=1;if(page)await capture('failure');}).finally(async()=>{await browser?.close();fixture?.stdin.end('shutdown\n');if(fixture)await new Promise(resolve=>{fixture.once('exit',resolve);setTimeout(()=>{fixture.kill();resolve();},5000).unref();});await fs.writeFile(path.join(out,'evidence.json'),JSON.stringify(evidence,null,2));console.log(JSON.stringify({checks:evidence.checks.length,consoleErrors:evidence.consoleErrors,error:evidence.error,out}));});
