/* Real Chrome + HTTP + SQLite. No production HOME or live AI is touched. */
const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..'),old=process.argv.includes('--old-contract');
const output=path.join(root,'Temp/assistant-reply',old?'old-contract':'fixed');
const evidence={checks:[],errors:[],screenshots:[]};let fixture,browser,page;
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function shot(name){await page.screenshot({path:path.join(output,name+'.png'),fullPage:true});evidence.screenshots.push(name+'.png');}
function seconds(text){const parts=Array.from(text.matchAll(/(\d+)(小时|分|秒)/g));assert(parts.length,'elapsed time has readable units');return parts.reduce((sum,part)=>sum+Number(part[1])*({小时:3600,分:60,秒:1})[part[2]],0);}
async function main(){
  await fs.mkdir(output,{recursive:true});
  const env={...process.env,PYTHONIOENCODING:'utf-8'};for(const key of ['CREW_SESSION_ID','CLAUDE_CODE_SESSION_ID','CREW_EXECUTION_ID','CREW_GOAL_ID'])delete env[key];
  fixture=spawn('python',['-u',path.join(__dirname,'assistant_ui_fixture.py')],{cwd:path.join(root,'plugin'),env,windowsHide:true,stdio:['pipe','pipe','pipe']});
  let buffer='',info;fixture.stdout.on('data',value=>{buffer+=value;let cut;while((cut=buffer.indexOf('\n'))>=0){try{const item=JSON.parse(buffer.slice(0,cut));if(item.url)info=item;}catch{}buffer=buffer.slice(cut+1);}});fixture.stderr.on('data',value=>process.stderr.write(value));
  const deadline=Date.now()+15000;while(!info&&Date.now()<deadline)await pause(50);assert(info,'isolated service must start');
  evidence.isolatedHome=info.home;
  browser=await chromium.launch({headless:true,channel:'chrome'});page=await browser.newPage({viewport:{width:1280,height:900},reducedMotion:'no-preference'});
  page.on('pageerror',error=>evidence.errors.push(error.stack||String(error)));page.on('console',message=>{if(message.type()==='error')evidence.errors.push(message.text());});
  if(old)await page.route('**/api/assistant/state',async route=>{const response=await route.fetch(),state=await response.json();delete state.assistant.name;for(const member of state.members)delete member.name;await route.fulfill({response,json:state});});
  await page.goto(info.url+'/assistant.html');const input=page.locator('#assistant-chat textarea');await input.waitFor();await shot('01-open');
  await input.fill('你好，请回复这条测试消息。');await page.getByRole('button',{name:'发送',exact:true}).click();await page.getByText('你好，请回复这条测试消息。',{exact:true}).waitFor();
  const elapsed=page.locator('.chat-activity [data-activity-elapsed]'),tokenCount=page.locator('.chat-activity [data-activity-tokens]');
  await elapsed.waitFor();const firstClock=await elapsed.innerText();
  await page.waitForFunction(before=>{const text=document.querySelector('.chat-activity [data-activity-elapsed]')?.textContent||'';const value=Array.from(text.matchAll(/(\d+)(小时|分|秒)/g)).reduce((sum,part)=>sum+Number(part[1])*({小时:3600,分:60,秒:1})[part[2]],0);return value>before;},seconds(firstClock),{timeout:3500});
  assert(seconds(await elapsed.innerText())>seconds(firstClock));evidence.checks.push('waiting clock advances every second before the model replies');await shot('02-sent-waiting');
  fixture.stdin.write('tokens 120\n');await tokenCount.getByText('↓ 120 tokens',{exact:true}).waitFor({timeout:10000});
  evidence.checks.push('assistant shows native token usage in its own conversation page');await shot('03-native-usage');
  fixture.stdin.write('tokens 380\n');
  await page.waitForFunction(()=>{const value=document.querySelector('.chat-activity [data-activity-tokens]')?.textContent.match(/↓ (\d+) tokens/);return value&&Number(value[1])>120&&Number(value[1])<380;},null,{timeout:8000});
  await tokenCount.getByText('↓ 380 tokens',{exact:true}).waitFor({timeout:5000});evidence.checks.push('new native usage updates the visible token count smoothly');await shot('04-updated-usage');
  const beforeRefresh=seconds(await elapsed.innerText()),turnStartedAt=await page.evaluate(async()=>{const value=await(await fetch('/api/assistant/state',{headers:{'X-Crew':'1'}})).json();return value.assistant.activity.turn_started_at;});
  await page.reload();await elapsed.waitFor();
  assert(seconds(await elapsed.innerText())>=beforeRefresh);await tokenCount.getByText('↓ 380 tokens',{exact:true}).waitFor({timeout:5000});
  assert.equal(await page.evaluate(async()=>{const value=await(await fetch('/api/assistant/state',{headers:{'X-Crew':'1'}})).json();return value.assistant.activity.turn_started_at;}),turnStartedAt);
  evidence.checks.push('refreshing a busy assistant preserves the turn start and usage');await shot('05-busy-after-refresh');
  await page.setViewportSize({width:390,height:844});await elapsed.waitFor();
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile page must not scroll horizontally');
  assert(await tokenCount.isVisible());evidence.checks.push('390px mobile assistant keeps clock and tokens visible without overflow');await shot('06-mobile-waiting');
  await page.setViewportSize({width:1280,height:900});
  fixture.stdin.write('reply\n');await page.locator('.chat-activity').waitFor({state:'detached',timeout:5000});await shot('07-reply-complete');
  assert.equal(evidence.errors.length,0,evidence.errors.join('\n'));await page.getByText('你好，我是跨工作区的助手。已收到这条消息。',{exact:true}).waitFor({timeout:5000});evidence.checks.push('accepted input displays the assistant reply');
  assert.equal(await page.locator('.m-lead .m-meta').first().innerText().then(text=>text.includes('助手')),true);evidence.checks.push('assistant has a visible name');
  evidence.checks.push('completed response removes its waiting status');
  await input.fill('请演示一次模型失败。');await page.getByRole('button',{name:'发送',exact:true}).click();await elapsed.waitFor();await shot('08-second-turn-waiting');
  fixture.stdin.write('fail\n');await page.getByText('测试模型响应失败：连接已断开',{exact:true}).waitFor({timeout:5000});await page.locator('.chat-activity').waitFor({state:'detached',timeout:5000});
  evidence.checks.push('failed response removes the clock and keeps the real error');await shot('09-failure-settled');
  await input.fill('切换页面后仍保留的草稿');await page.goto(info.url+'/?project=demo');await page.locator('.wh-head').waitFor();await page.goto(info.url+'/assistant.html');await page.locator('#assistant-chat textarea').waitFor();
  assert.equal(await page.locator('#assistant-chat textarea').inputValue(),'切换页面后仍保留的草稿');await page.getByText('你好，我是跨工作区的助手。已收到这条消息。',{exact:true}).waitFor();await shot('10-return-with-history');evidence.checks.push('returning keeps the same reply and unsent draft');
  assert.equal(evidence.errors.length,0,evidence.errors.join('\n'));
}
main().catch(async error=>{evidence.failure=String(error.stack||error);if(page)await shot('failure').catch(()=>{});process.exitCode=1;}).finally(async()=>{await browser?.close();fixture?.stdin.write('shutdown\n');if(fixture)await new Promise(resolve=>{fixture.once('exit',resolve);setTimeout(()=>{fixture.kill();resolve();},10000).unref();});await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));console.log(JSON.stringify(evidence));});
