/* Real isolated Chrome, production goal-ui.js, native CDP; no service, AI or added dependency. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises'), path = require('node:path'), os = require('node:os');
const {spawn, execFileSync} = require('node:child_process');
const root = path.resolve(__dirname, '../..');
const old = process.argv.includes('--old-code');
const output = path.join(root, 'Temp/ui-align/batch2/goal-amend', old ? 'old-code' : 'current');

async function main() {
  const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'crew-goal-amend-chrome-'));
  await fs.mkdir(output, {recursive:true});
  const chrome = spawn('C:/Program Files/Google/Chrome/Application/chrome.exe', [
    '--headless=new', '--disable-gpu', '--in-process-gpu', '--no-sandbox', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=0', '--user-data-dir=' + profile, 'about:blank'
  ], {windowsHide:true, stdio:['ignore','ignore','pipe']});
  let socket;
  let chromeLog = '';
  chrome.stderr.on('data', data => {chromeLog += data;});
  try {
    const endpoint = await new Promise((resolve, reject) => {
      let buffer = '';
      const timer = setTimeout(() => reject(Error('Chrome DevTools startup timed out')), 15000);
      chrome.once('error', reject);
      chrome.stderr.on('data', data => {
        buffer += data;
        const match = buffer.match(/DevTools listening on (ws:\/\/[^\s]+)/);
        if (match) {clearTimeout(timer); resolve(match[1]);}
      });
    });
    socket = new WebSocket(endpoint);
    await new Promise((resolve,reject) => {socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
    let next = 0;
    const pending = new Map(), errors = [];
    socket.addEventListener('close', event => {
      for (const request of pending.values()) {clearTimeout(request.timer);request.reject(Error('Chrome CDP closed: '+event.code+' '+event.reason+' '+chromeLog));}
      pending.clear();
    });
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if (message.id) {
        const request = pending.get(message.id);
        pending.delete(message.id);clearTimeout(request.timer);
        if (message.error) request.reject(Error(JSON.stringify(message.error))); else request.resolve(message.result);
      } else if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text);
      else if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') errors.push(JSON.stringify(message.params.args));
      else if (message.method === 'Log.entryAdded' && message.params.entry.level === 'error') errors.push(message.params.entry.text);
    });
    const call = (method, params={}, sessionId) => new Promise((resolve,reject) => {
      const id = ++next, timer=setTimeout(()=>{pending.delete(id);reject(Error('CDP timed out: '+method+' '+chromeLog));},15000);
      pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({id,method,params,sessionId}));
    });
    const {targetId} = await call('Target.createTarget',{url:'about:blank'});
    const {sessionId} = await call('Target.attachToTarget',{targetId,flatten:true});
    const page = (method, params) => call(method,params,sessionId);
    for (const method of ['Runtime.enable','Page.enable','Log.enable']) await page(method);
    await page('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
    const evaluate = async expression => {
      const result = await page('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true,replMode:true});
      if (result.exceptionDetails) throw Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
      return result.result.value;
    };
    const source = old ? execFileSync('git',['show','HEAD:plugin/crew/web/goal-ui.js'],{cwd:root,encoding:'utf8',windowsHide:true})
      : await fs.readFile(path.join(root,'plugin/crew/web/goal-ui.js'),'utf8');
    const css = (await Promise.all(['style.css','goal-ui.css'].map(name=>fs.readFile(path.join(root,'plugin/crew/web',name),'utf8')))).join('\n');
    await evaluate(`document.body.innerHTML = '<aside id="side-task-section"></aside><main id="main"><div id="chat-host">原始测量会话</div></main>';
      const style=document.createElement('style');style.textContent=${JSON.stringify(css)}+'body{font:16px system-ui;margin:0;color:#222}#side-task-section{padding:24px;width:300px}#main{position:absolute;inset:0 0 0 350px}.goal-report-reader{position:absolute;inset:0;background:white;display:flex;flex-direction:column}.goal-reader-head{display:flex;gap:16px;align-items:center;flex-wrap:wrap;padding:16px 24px}.goal-reader-frame{flex:1}.goal-reader-frame iframe{height:100%;width:100%}button{cursor:pointer}';document.head.append(style);`);
    await evaluate(await fs.readFile(path.join(root,'plugin/crew/web/goal-work-ui.js'),'utf8'));
    await evaluate(source);
    await evaluate(`
      window.goal={id:'goal',leader_session_id:'leader',title:'保留压测结果',request:'完成测量并保留已有证据',status:'awaiting_acceptance',current_round:1,requirement_version:1,version:1,report_available:true,supplements_after_report:[]};
      window.reports=[{id:'report-1',title:goal.title,round:1,requirement_version:1,version:1,status:'awaiting_acceptance',outcome:'awaiting_acceptance',summary:'原始测量已交付',acceptance_counts:{passed:1,failed:0,unverified:0,simulated:0},unresolved:[],url:'/report',created_at:'2026-11-05T00:00:00Z'}];
      window.executions=[{id:'old-round',session_id:'leader',round:0,generation:99,requirement_version:1},{id:'current-execution',session_id:'leader',round:1,generation:2,requirement_version:1},{id:'old-generation',session_id:'leader',round:1,generation:1,requirement_version:1}];
      window.state={git_assistant:null,members:[],leaders:[{session_id:'leader',name:'测试负责人'}],goals:[goal]};
      window.fetch=async()=>({ok:true,text:async()=>'<h1>原始测量结果</h1><p>这一轮的成功结果保留。</p>'});
      window.client=CrewGoals.create({project:'fixture',state:()=>state,api:async()=>({goal:{...goal},executions,reports,context:{items:[],manifest:{}},documents:[]}),onChange:()=>document.getElementById('side-task-section').innerHTML=client.sideMarkup('leader')});
      client.sync('leader','goal');await client.refresh();document.getElementById('side-task-section').innerHTML=client.sideMarkup('leader');
      document.querySelector('[data-goal-act="delivery"]').click();
    `);
    const wait = async expression => {
      for (let i=0;i<100;i++) {
        if (await evaluate(expression)) return;
        await new Promise(resolve=>setTimeout(resolve,20));
      }
      throw Error('Browser condition timed out: '+expression);
    };
    const shot = async name => {
      const {data} = await page('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
      await fs.writeFile(path.join(output,name+'.png'),Buffer.from(data,'base64'));
    };
    await wait(`!!document.querySelector('.goal-report-reader iframe')?.srcdoc`);
    assert((await evaluate(`document.querySelector('.goal-reader-head').textContent`)).includes('当前版本'));
    await evaluate(`window.savedFrame=document.querySelector('.goal-report-reader iframe');window.savedFrameDocument=savedFrame.srcdoc;`);
    await shot('01-original-report');
    await evaluate(`goal.requirement_version=2;goal.version++;client.sync('leader','goal');await client.refresh();`);
    const updated = await evaluate(`document.querySelector('.goal-reader-head').textContent`);
    assert(updated.includes('当前版本'), 'amend must retain the current report');
    assert(!updated.includes('尚无新交付'));
    assert.equal(await evaluate(`client.executionFor('leader')?.id`),'current-execution');
    assert.equal(await evaluate(`document.querySelector('.goal-report-reader iframe')===savedFrame && savedFrame.srcdoc===savedFrameDocument`),true);
    await shot('02-amended-same-report');
    await evaluate(`reports.unshift({...reports[0],id:'report-2',version:2,requirement_version:2});goal.version++;client.sync('leader','goal');await client.refresh();`);
    assert((await evaluate(`document.querySelector('.goal-reader-head').textContent`)).includes('较早版本'));
    await shot('03-new-draft');
    await evaluate(`goal.current_round=2;goal.requirement_version=3;goal.version++;client.sync('leader','goal');await client.refresh();`);
    assert((await evaluate(`document.querySelector('.goal-reader-head').textContent`)).includes('历史报告'));
    await shot('04-new-round-history');
    assert.deepEqual(errors,[]);
    const evidence={checks:4,consoleErrors:0,screenshots:4,oldCode:old};
    await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));
    console.log(JSON.stringify({...evidence,output}));
    await call('Browser.close');
  } finally {
    socket?.close();
    if (chrome.exitCode === null) chrome.kill();
    await new Promise(resolve=>{if(chrome.exitCode!==null)resolve();else chrome.once('exit',resolve);});
    assert(profile.startsWith(path.join(os.tmpdir(),'crew-goal-amend-chrome-')));
    await fs.rm(profile,{recursive:true,force:true,maxRetries:20,retryDelay:100});
  }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
