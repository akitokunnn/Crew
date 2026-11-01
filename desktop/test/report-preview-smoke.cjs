'use strict';
// New synthetic reports only. Real Electron, HTTP and SQLite; no live AI or user HOME.
const fs = require('node:fs/promises'), path = require('node:path'), os = require('node:os');
const assert = require('node:assert/strict'), {spawn} = require('node:child_process');

if (!process.versions.electron) {
  const env = {...process.env};
  delete env.ELECTRON_RUN_AS_NODE;
  for (const key of ['CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'CREW_EXECUTION_ID', 'CREW_GOAL_ID']) delete env[key];
  env.CREW_REPORT_PREVIEW_RUN_ID = require('node:crypto').randomUUID();
  const child = spawn(require('electron'), [__filename], {env, windowsHide:true, stdio:'inherit'});
  const deadline=setTimeout(()=>{child.kill();console.error('Report preview smoke exceeded 90 seconds');process.exitCode=1;},90000);
  child.once('exit', async code => {
    clearTimeout(deadline);
    try {
      const result=JSON.parse(await fs.readFile(path.resolve(__dirname,'../smoke-output/report-preview/evidence.json'),'utf8'));
      assert.equal(code,0);assert.equal(result.run_id,env.CREW_REPORT_PREVIEW_RUN_ID);assert.equal(result.ok,true);
    }catch(error){console.error(error);process.exitCode=1;}
  });
  child.once('error', error => { console.error(error); process.exitCode = 1; });
} else {
  const {app, BrowserWindow, nativeImage} = require('electron');
  const root = path.resolve(__dirname, '../..'), output = path.join(root, 'desktop/smoke-output/report-preview');
  const isolated = require('node:fs').mkdtempSync(path.join(os.tmpdir(), 'crew-report-preview-'));
  app.setPath('userData', isolated);
  app.commandLine.appendSwitch('force-device-scale-factor', '1');
  app.commandLine.appendSwitch('force-color-profile', 'srgb');
  // Each report window is temporary; keep the fixture's desktop host alive.
  app.on('window-all-closed', () => {});
  const evidence = {checks:[], screenshots:[], errors:[], isolatedUserData:isolated,run_id:process.env.CREW_REPORT_PREVIEW_RUN_ID,
    modelProcess:'No model process; real CLI and host-bound Codex tool calls', syntheticReportsOnly:true};
  let fixture, client, renderer, trap;
  const pending = new Map();
  let next = 0, fixtureInfo, fixtureClosed = false, trapRequests = 0, fixtureStderr = '';
  const pause = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
  async function until(condition, label, timeout=15000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) { if (await condition()) return; await pause(25); }
    throw Error('Timed out: ' + label);
  }
  function check(label, condition) { assert(condition, label); evidence.checks.push(label); }
  function command(action, values={}) {
    const id = ++next;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { pending.delete(id); reject(Error('Fixture command timed out: '+action)); }, 45000);
      pending.set(id, {resolve:value => {clearTimeout(timer); resolve(value);}, reject});
      fixture.stdin.write(JSON.stringify({id,action,...values})+'\n');
    });
  }
  function report(marker, color, extra='') {
    return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>
      html,body{margin:0;min-height:100%;background:white;font:20px/1.6 system-ui,sans-serif;color:#16332b}
      main{padding:48px 32px;max-width:900px;margin:auto}h1{font-size:36px;line-height:1.2}
      p{font-size:20px}small{display:block;font-size:14px}@media(max-width:500px){main{padding:28px 20px}h1{font-size:28px}}
      </style></head><body><main><h1>报告预览 · ${marker}</h1><p id="result">等待内联脚本</p>
      <small id="environment"></small><p>这是隔离测试生成的报告，不读取真实工作区。</p></main><script>
      document.body.style.background='${color}';document.documentElement.style.background='${color}';
      document.getElementById('result').textContent='内联脚本已完成 · ${marker}';
      document.getElementById('environment').textContent='Node: '+typeof require;
      if(typeof require!=='undefined')document.body.style.background='#ff0000';
      ${extra}</script></body></html>`;
  }
  async function image(preview, name, width, height, color) {
    assert(preview && typeof preview.path === 'string', 'capture returns a readable artifact path');
    const source = path.resolve(preview.path), home = path.resolve(fixtureInfo.home);
    check(name+': screenshot remains in the isolated HOME', source.startsWith(home+path.sep));
    const png = await fs.readFile(source);
    assert.equal(png.subarray(0,8).toString('hex'), '89504e470d0a1a0a');
    const actual = nativeImage.createFromBuffer(png), size = actual.getSize();
    check(name+': PNG has the requested viewport', size.width===width && size.height===height);
    assert.equal(preview.width, width); assert.equal(preview.height, height);
    if (color) {
      const bitmap = actual.toBitmap(), index = (10*width+10)*4;
      const rgb = [bitmap[index+2],bitmap[index+1],bitmap[index]];
      const expected = color.match(/[0-9a-f]{2}/gi).map(value => parseInt(value,16));
      check(name+': pixels come from the authored inline script', rgb.every((value,i)=>Math.abs(value-expected[i])<=3));
    }
    check(name+': capture does not claim a visual verdict', preview.visual_verification==='not_performed');
    const target = path.join(output,name+'.png'); await fs.writeFile(target,png); evidence.screenshots.push(target);
    return png;
  }
  async function capture(html, viewport='desktop') {
    const core = require('../src/core.cjs');
    return core.request(fixtureInfo.url,'/api/goals/agent?project=demo',{
      actor_session_id:fixtureInfo.leader_session_id,action:'report.preview',
      data:{goal_id:fixtureInfo.goal_id,document_html:html,viewport}
    },40000);
  }
  async function main() {
    await fs.mkdir(output,{recursive:true});
    const env={...process.env,PYTHONIOENCODING:'utf-8'};
    for(const key of ['CREW_SESSION_ID','CLAUDE_CODE_SESSION_ID','CREW_EXECUTION_ID','CREW_GOAL_ID'])delete env[key];
    fixture=spawn('python',['-u',path.join(root,'plugin/tests/report_preview_fixture.py')],{
      cwd:path.join(root,'plugin'),env,windowsHide:true,stdio:['pipe','pipe','pipe']});
    let buffer='';
    fixture.stdout.on('data',chunk=>{
      buffer+=chunk;
      let cut;
      while((cut=buffer.indexOf('\n'))>=0){
        const line=buffer.slice(0,cut);buffer=buffer.slice(cut+1);
        try{const value=JSON.parse(line);if(value.url)fixtureInfo=value;if(value.closed)fixtureClosed=true;
          if(value.id&&pending.has(value.id)){const item=pending.get(value.id);pending.delete(value.id);
            if(value.error)item.reject(Error(value.error));else item.resolve(value.result);}}
        catch(error){evidence.errors.push('Unexpected fixture output: '+line+' '+error.message);}
      }
    });
    fixture.stderr.on('data',chunk=>{fixtureStderr+=chunk;process.stderr.write(chunk);});
    fixture.once('exit',code=>{for(const item of pending.values())item.reject(Error('Fixture exited: '+code));pending.clear();});
    await until(()=>fixtureInfo,'isolated HTTP fixture');evidence.fixture=fixtureInfo;
    check('real HTTP service uses a non-production ephemeral port', ![47811,7209].includes(Number(new URL(fixtureInfo.url).port)));
    const core=require('../src/core.cjs');
    const {ReportPreviewRenderer,ReportPreviewClient}=require('../src/report-preview.cjs');
    renderer=new ReportPreviewRenderer({BrowserWindow,timeoutMs:15000});
    client=new ReportPreviewClient({base:fixtureInfo.url,request:core.request,renderer,
      onError:error=>evidence.errors.push(error.stack||String(error))});
    await client.start();

    const cli=await command('cli',{html:report('CLI 桌面','#dcfce7'),viewport:'desktop'});
    check('Claude CLI reaches the shared live preview host', cli.code===0 && cli.value?.ok===true);
    assert.equal(cli.error,'');
    await image(cli.value.preview,'01-cli-desktop',1280,900,'#dcfce7');
    assert.deepEqual(cli.value.preview.console_errors,[]);

    const codex=await command('codex',{html:report('Codex 手机','#dbeafe'),viewport:'mobile'});
    check('bound Codex member receives the shared preview result', codex.success===true);
    const text=codex.contentItems.find(item=>item.type==='inputText');assert(text);
    const native=JSON.parse(text.text);
    check('leader and member previews use separate session image directories',
      path.dirname(cli.value.preview.path)!==path.dirname(native.preview.path));
    await image(native.preview,'02-codex-mobile',390,844,'#dbeafe');
    const nativePicture=codex.contentItems.find(item=>item.type==='inputImage');
    check('Codex receives the actual PNG as an image tool result', nativePicture && /^data:image\/png;base64,/.test(nativePicture.imageUrl));
    assert.deepEqual(native.preview.console_errors,[]);

    const [alpha,beta]=await Promise.all([
      capture(report('并发 A','#fef3c7')),capture(report('并发 B','#fce7f3'))]);
    check('concurrent previews keep distinct identities and artifact paths', alpha.preview.id!==beta.preview.id && alpha.preview.path!==beta.preview.path);
    await image(alpha.preview,'03-concurrent-a',1280,900,'#fef3c7');
    await image(beta.preview,'04-concurrent-b',1280,900,'#fce7f3');

    const errorResult=await capture(report('真实脚本错误','#fee2e2',
      "console.error('report fixture expected console error');throw Error('report fixture expected script error');"));
    await image(errorResult.preview,'05-script-error',1280,900,'#fee2e2');
    check('real console and script errors remain in the result',
      errorResult.preview.console_errors.some(value=>value.includes('expected console error')) &&
      errorResult.preview.console_errors.some(value=>value.includes('expected script error')));

    trap=require('node:http').createServer((request,response)=>{trapRequests++;response.end('unexpected report request');});
    await new Promise(resolve=>trap.listen(0,'127.0.0.1',resolve));
    const target=`http://127.0.0.1:${trap.address().port}/must-not-load`;
    const guarded=await capture(report('隔离边界','#ccfbf1',
      `fetch(${JSON.stringify(target)}).catch(()=>{});const image=new Image();image.src=${JSON.stringify(target)};
      try{top.location.href=${JSON.stringify(target)};}catch(error){console.error('navigation blocked: '+error.message);}`));
    await image(guarded.preview,'06-isolated-report',1280,900,'#ccfbf1');
    check('authored reports cannot make network requests or navigate the host',trapRequests===0);
    check('blocked resource and navigation errors are retained',guarded.preview.console_errors.length>0);

    const observed=await command('inspect');
    check('previewing does not start any model or task',observed.model_launches===0 && observed.goal_status==='queued' && observed.member_goal_status==='queued');
    await client.close();client=null;
    const before=Date.now();
    const unavailable=await capture(report('桌面已关闭','#ffffff'));
    check('closed desktop is reported explicitly instead of timing out or silently retrying',
      unavailable.ok===false && /桌面|预览|不可用|关闭/.test(unavailable.error||'') && Date.now()-before<5000);
    assert.deepEqual(evidence.errors,[]);
    evidence.ok=true;
  }
  app.whenReady().then(main).catch(error=>{evidence.errors.push(error.stack||String(error));console.error(error);process.exitCode=1;})
    .finally(async()=>{
      try{await client?.close();await renderer?.close();}catch(error){evidence.errors.push(String(error));process.exitCode=1;}
      if(trap)await new Promise(resolve=>trap.close(resolve));
      fixture?.stdin.end(JSON.stringify({action:'shutdown'})+'\n');
      if(fixture)await until(()=>fixtureClosed,'fixture cleanup',10000).catch(error=>{fixture.kill();evidence.errors.push(String(error));process.exitCode=1;});
      if(fixtureStderr.includes('Traceback (most recent call last)')){evidence.errors.push('Unexpected backend traceback during preview or shutdown');process.exitCode=1;}
      evidence.ok=Boolean(evidence.ok)&&evidence.errors.length===0;
      await fs.mkdir(output,{recursive:true});await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));
      console.log(JSON.stringify({ok:Boolean(evidence.ok)&&evidence.errors.length===0,checks:evidence.checks.length,errors:evidence.errors,evidence:path.join(output,'evidence.json')}));
      app.exit(process.exitCode||0);
    });
}
