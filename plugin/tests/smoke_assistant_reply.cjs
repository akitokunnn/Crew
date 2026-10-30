/* Real Chrome + HTTP + SQLite. No production HOME or live AI is touched. */
const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..'),old=process.argv.includes('--old-contract');
const output=path.join(root,'Temp/assistant-reply',old?'old-contract':'fixed');
const evidence={checks:[],errors:[],screenshots:[]};let fixture,browser,page;
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function shot(name){await page.screenshot({path:path.join(output,name+'.png'),fullPage:true});evidence.screenshots.push(name+'.png');}
async function main(){
  await fs.mkdir(output,{recursive:true});
  const env={...process.env,PYTHONIOENCODING:'utf-8'};for(const key of ['CREW_SESSION_ID','CLAUDE_CODE_SESSION_ID','CREW_EXECUTION_ID','CREW_GOAL_ID'])delete env[key];
  fixture=spawn('python',['-u',path.join(__dirname,'assistant_ui_fixture.py')],{cwd:path.join(root,'plugin'),env,windowsHide:true,stdio:['pipe','pipe','pipe']});
  let buffer='',info;fixture.stdout.on('data',value=>{buffer+=value;let cut;while((cut=buffer.indexOf('\n'))>=0){try{const item=JSON.parse(buffer.slice(0,cut));if(item.url)info=item;}catch{}buffer=buffer.slice(cut+1);}});fixture.stderr.on('data',value=>process.stderr.write(value));
  const deadline=Date.now()+15000;while(!info&&Date.now()<deadline)await pause(50);assert(info,'isolated service must start');
  browser=await chromium.launch({headless:true,channel:'chrome'});page=await browser.newPage({viewport:{width:1280,height:900}});
  page.on('pageerror',error=>evidence.errors.push(error.stack||String(error)));page.on('console',message=>{if(message.type()==='error')evidence.errors.push(message.text());});
  if(old)await page.route('**/api/assistant/state',async route=>{const response=await route.fetch(),state=await response.json();delete state.assistant.name;for(const member of state.members)delete member.name;await route.fulfill({response,json:state});});
  await page.goto(info.url+'/assistant.html');const input=page.locator('#assistant-chat textarea');await input.waitFor();await shot('01-open');
  await input.fill('你好，请回复这条测试消息。');await page.getByRole('button',{name:'发送',exact:true}).click();await page.getByText('你好，请回复这条测试消息。',{exact:true}).waitFor();await shot('02-sent');
  fixture.stdin.write('reply\n');await pause(700);await shot('03-reply');
  assert.equal(evidence.errors.length,0,evidence.errors.join('\n'));await page.getByText('你好，我是跨工作区的助手。已收到这条消息。',{exact:true}).waitFor({timeout:5000});evidence.checks.push('accepted input displays the assistant reply');
  assert.equal(await page.locator('.m-lead .m-meta').first().innerText().then(text=>text.includes('助手')),true);evidence.checks.push('assistant has a visible name');
  await input.fill('切换页面后仍保留的草稿');await page.goto(info.url+'/?project=demo');await page.locator('.wh-head').waitFor();await page.goto(info.url+'/assistant.html');await page.locator('#assistant-chat textarea').waitFor();
  assert.equal(await page.locator('#assistant-chat textarea').inputValue(),'切换页面后仍保留的草稿');await page.getByText('你好，我是跨工作区的助手。已收到这条消息。',{exact:true}).waitFor();await shot('04-return-with-history');evidence.checks.push('returning keeps the same reply and unsent draft');
  assert.equal(evidence.errors.length,0,evidence.errors.join('\n'));
}
main().catch(async error=>{evidence.failure=String(error.stack||error);if(page)await shot('failure').catch(()=>{});process.exitCode=1;}).finally(async()=>{await browser?.close();fixture?.stdin.write('shutdown\n');if(fixture)await new Promise(resolve=>{fixture.once('exit',resolve);setTimeout(()=>{fixture.kill();resolve();},10000).unref();});await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));console.log(JSON.stringify(evidence));});
