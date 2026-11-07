/* Real Chrome + HTTP/SQLite, with a controllable fake native carrier. */
const {chromium}=require('playwright');
const {spawn,execFileSync}=require('node:child_process');
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..'),old=process.argv.includes('--old-ui');
const out=path.join(root,'Temp/native-handoff-error',old?'old-ui':'fixed');
(async()=>{
  await fs.mkdir(out,{recursive:true});
  const env={...process.env,PYTHONIOENCODING:'utf-8'};
  for(const key of ['CREW_SESSION_ID','CLAUDE_CODE_SESSION_ID','CREW_EXECUTION_ID','CREW_GOAL_ID'])delete env[key];
  const fixture=spawn('python',['plugin/tests/native_handoff_ui_fixture.py'],{cwd:root,env,stdio:['pipe','pipe','pipe']});
  let pending='',stderr='';const messages=[],waiters=[];
  fixture.stderr.on('data',chunk=>stderr+=chunk);
  fixture.stdout.on('data',chunk=>{pending+=chunk;let index;while((index=pending.indexOf('\n'))>=0){const line=pending.slice(0,index);pending=pending.slice(index+1);if(!line.trim())continue;const value=JSON.parse(line);const next=waiters.shift();if(next)next(value);else messages.push(value);}});
  const next=()=>messages.length?Promise.resolve(messages.shift()):new Promise((resolve,reject)=>{const timeout=setTimeout(()=>reject(Error('Fixture timeout: '+stderr)),15000);waiters.push(value=>{clearTimeout(timeout);resolve(value);});});
  let browser,page;const evidence={mode:old?'old-ui':'fixed',checks:[],page_errors:[],console_errors:[],expected_http_errors:[],posts:0};
  try{
    const info=await next();
    browser=await chromium.launch({headless:true,channel:'chrome'});page=await browser.newPage({viewport:{width:1400,height:1000}});
    page.on('pageerror',error=>evidence.page_errors.push(error.message));
    page.on('console',message=>{if(message.type()==='error')(/409 \(Conflict\)/.test(message.text())?evidence.expected_http_errors:evidence.console_errors).push(message.text());});
    page.on('request',request=>{if(request.method()==='POST'&&new URL(request.url()).pathname==='/api/session/native-return')evidence.posts++;});
    if(old){const source=execFileSync('git',['show','e69b64d:plugin/crew/web/native-handoff-ui.js'],{cwd:root,encoding:'utf8'});await page.route('**/native-handoff-ui.js*',route=>route.fulfill({contentType:'application/javascript',body:source}));}
    await page.goto(info.url+'/?project=demo#leader='+info.leader);
    await page.locator('.native-handoff-error').filter({hasText:'previous active writer conflict'}).waitFor();
    await page.screenshot({path:path.join(out,'01-old-error.png'),fullPage:true});
    const failed=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/session/native-return'&&response.request().method()==='POST');
    await page.locator('[data-act="native-return"][data-id="'+info.leader+'"]').click();
    const response=await failed;assert.equal(response.status(),409);const body=await response.json();assert.match(body.error,/session is archived/);
    await page.locator('#toast-root .toast').filter({hasText:'session is archived'}).waitFor({timeout:5000});
    // The action's error has been presented: its saved state must already be
    // refreshed, rather than eventually corrected by the unrelated 3s poll.
    assert.match((await page.locator('.native-handoff-error').allTextContents()).join(' '),/session is archived/);
    assert.equal(await page.locator('.native-handoff-error').filter({hasText:'previous active writer'}).count(),0);
    assert.equal(evidence.posts,1);evidence.checks.push('failed return shows fresh native reason with one POST');
    await page.screenshot({path:path.join(out,'02-archived-error.png'),fullPage:true});
    fixture.stdin.write('allow\n');assert.equal((await next()).allowed,true);
    const returned=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/session/native-return'&&response.request().method()==='POST');
    await page.locator('[data-act="native-return"][data-id="'+info.leader+'"]').click();
    assert.equal((await returned).status(),200);
    await page.locator('[data-act="native-handoff"][data-id="'+info.leader+'"]').waitFor();
    assert.equal(await page.locator('.native-handoff-error').count(),0);
    assert.equal(evidence.posts,2);evidence.checks.push('explicit successful return clears error with one more POST');
    await page.screenshot({path:path.join(out,'03-returned.png'),fullPage:true});
    assert.deepEqual(evidence.page_errors,[]);assert.deepEqual(evidence.console_errors,[]);
  }catch(error){evidence.failure=String(error);if(page)await page.screenshot({path:path.join(out,'failure.png'),fullPage:true});throw error;}
  finally{
    if(browser)await browser.close();
    fixture.stdin.write('shutdown\n');
    try{evidence.native=await next();}catch(error){evidence.fixture_error=String(error);}
    fixture.stdin.end();await new Promise(resolve=>fixture.once('exit',resolve));
    evidence.stderr=stderr;await fs.writeFile(path.join(out,'evidence.json'),JSON.stringify(evidence,null,2));
  }
  console.log(JSON.stringify({checks:evidence.checks.length,posts:evidence.posts,page_errors:evidence.page_errors.length,unexpected_console_errors:evidence.console_errors.length}));
})().catch(error=>{console.error(error);process.exitCode=1;});
