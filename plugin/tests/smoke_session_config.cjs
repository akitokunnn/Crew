'use strict';
// Real isolated Electron/HTTP/database QA. Never sends or wakes a model.
// Pixel QA uses smoke_goal_delivery.cjs offscreen windows; hidden BrowserViews do not reliably produce screenshots.
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const {spawn,spawnSync}=require('node:child_process');
const root=path.resolve(__dirname,'../..'),desktop=path.join(root,'desktop'),output=path.join(desktop,'smoke-output/session-config');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
if(!process.versions.electron){
  (async()=>{
    const home=await fs.mkdtemp(path.join(desktop,'.cache/session-config-smoke-')),env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
    const child=spawn(require(require.resolve('electron',{paths:[desktop]})),['--require',__filename,desktop,'--smoke-test','--isolated-sources','--home',home,'--smoke-output',path.join(output,'unused.json')],{cwd:desktop,env,windowsHide:true,stdio:['ignore','pipe','pipe']});
    child.stdout.pipe(process.stdout);child.stderr.pipe(process.stderr);
    const timer=setTimeout(()=>{child.kill();process.exitCode=1;},100000);
    child.once('error',error=>{clearTimeout(timer);console.error(error);process.exitCode=1;});
    child.once('exit',async code=>{
      clearTimeout(timer);child.stdout.destroy();child.stderr.destroy();
      try{
        const result=JSON.parse(await fs.readFile(path.join(output,'evidence.json'),'utf8'));
        assert.equal(code,0);assert.equal(result.home,home);assert.equal(result.ok,true);assert.equal(result.errors.length,0);
        try{await fs.access(path.join(home,'web.json'));throw Error('Owned service registry remains');}catch(error){if(error.code!=='ENOENT')throw error;}
        console.log(JSON.stringify({ok:true,checks:result.checks.length,evidence:path.join(output,'evidence.json'),ownedServiceExited:true}));
      }catch(error){console.error(error);process.exitCode=1;}
    });
  })().catch(error=>{console.error(error);process.exitCode=1;});
}else{
  const {app,ipcMain,webContents}=require('electron'),core=require(path.join(desktop,'src/core.cjs'));
  const evidence={checks:[],errors:[],expectedErrors:[],scope:'real isolated renderer, API and database; no send/wake/compact or model turns'};
  app.once('browser-window-created',(_event,win)=>{
    win.webContents.setBackgroundThrottling(false);
    ipcMain.removeHandler('desktop:ready');ipcMain.handle('desktop:ready',()=>{});
    win.webContents.once('did-finish-load',async()=>{
      let contents,home;
      const js=source=>contents.executeJavaScript(source,true);
      async function until(fn,label){const end=Date.now()+15000;while(Date.now()<end){if(await fn())return;await pause(40);}throw Error('Timed out: '+label);}
      const wait=(source,label)=>until(()=>js(source),label),click=selector=>js(`document.querySelector(${JSON.stringify(selector)}).click()`);
      async function check(label,source){assert(await js(source),label);evidence.checks.push(label);}
      function python(source,...args){const result=spawnSync('python',['-',path.join(root,'plugin'),home,...args],{input:'import sys,json\nfrom pathlib import Path\nfrom contextlib import closing\nsys.path.insert(0,sys.argv[1])\nfrom crew import board,store,trust_mode,runtimes\nhome=Path(sys.argv[2])\n'+source,encoding:'utf8',windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'}});assert.equal(result.status,0,result.stderr);return result.stdout.trim();}
      const edit=(selector,value)=>js(`{const field=document.querySelector(${JSON.stringify(selector)});field.value=${JSON.stringify(value)};field.dispatchEvent(new Event('input',{bubbles:true}));}`);
      try{
        await fs.mkdir(output,{recursive:true});await until(()=>win.webContents.executeJavaScript('Boolean(window.crewDesktop)&&document.querySelector("#service").textContent==="已连接"'),'home bootstrap');
        const bootstrap=await win.webContents.executeJavaScript('window.crewDesktop.bootstrap()');assert.notEqual(new URL(bootstrap.service.url).port,'47811');evidence.service=bootstrap.service;
        home=process.argv[process.argv.indexOf('--home')+1];evidence.home=home;
        const parent=path.join(home,'projects');await fs.mkdir(parent,{recursive:true});await core.newWorkspace(parent,'配置验收',bootstrap.service.url);
        const projects=await core.projects(bootstrap.service.url);
        python("projects=json.loads(sys.argv[3]);p=next(p for p in projects if p['name']=='配置验收')\nwith closing(store.connect(home)) as conn:\n for sid,name,role,group in [('config-lead-a','交付','leader',None),('config-lead-b','调查','leader',None),('config-member','实施','member','交付')]:\n  board.join(conn,sid,p['name'],name,p['root'],p['root'],'main',role=role,group_name=group)\n  (home/'chat').mkdir(exist_ok=True);(home/'chat'/(sid+'.jsonl')).touch()\n board.set_task(conn,'config-member','继续接入；未发送任何模型请求')\n board.set_card(conn,'config-member','完成跨载体交接','保留任务卡、组与旧历史')\n trust_mode.set(conn,p['name'],True,'B组独有目标',leader_session_id='config-lead-b')\n",JSON.stringify(projects));
        await win.webContents.executeJavaScript('window.crewDesktop.openProject("配置验收")');
        contents=webContents.getAllWebContents().find(item=>item.getURL().includes(encodeURIComponent('配置验收')));assert(contents);contents.setBackgroundThrottling(false);
        contents.on('console-message',event=>{if(event.level==='error'){if(event.message.includes('409'))evidence.expectedErrors.push(event.message);else evidence.errors.push(event.message);}});
        await wait('Boolean(document.querySelector("[data-act=leader][data-id=config-lead-a]"))','leaders loaded');
        await js(`window.__configRequests=[];const originalFetch=window.fetch;window.fetch=async(...args)=>{const result=await originalFetch(...args);if(String(args[0]).includes('/api/trust')||String(args[0]).includes('/api/session/runtime/')||String(args[0]).match(/\\/api\\/(wake|chat\\/send|session\\/compact)/)){const row={url:String(args[0]),body:args[1]?.body?JSON.parse(args[1].body):null,status:result.status};try{row.result=await result.clone().json();}catch(_){}window.__configRequests.push(row);}return result;};void 0;`);
        await click('[data-act="leader"][data-id="config-lead-a"]');await wait('Boolean(document.querySelector(".composer textarea"))','leader composer');
        await edit('.composer textarea','保存各组设置时保留这条草稿');
        await check('workspace sidebar and generic settings no longer expose project-level trust','!document.querySelector("#sidebar [data-act=trust-settings]")&&!document.querySelector("#settings .trust-settings")&&document.querySelector("[data-act=leader-trust]").dataset.id==="config-lead-a"');
        await click('#chat-head [data-act="leader-trust"]');
        await check('new leader defaults to off with an empty scoped goal','!document.querySelector("#leader-trust-form [name=enabled]").checked&&document.querySelector("#leader-trust-goal").value===""');
        await click('#leader-trust-form [name="enabled"]');await edit('#leader-trust-goal','A组：完成界面验收，授权范围内自主判断');

        await js('document.querySelector("#leader-trust-form").requestSubmit()');await wait('document.querySelector(".leader-trust-dialog")===null||document.querySelector(".leader-trust-dialog").parentElement.hidden','trust saved');
        await wait('document.querySelector("#chat-head .leader-trust-indicator")?.textContent.includes("开")','updated indicator');
        await check('save shows the exact deferred-message wording and preserves the current draft','document.querySelector("#toast-root").textContent.includes("已保存，下次对话会告知它")&&document.querySelector(".composer textarea").value==="保存各组设置时保留这条草稿"&&document.querySelector("#chat-head [data-act=leader-trust]").title.includes("A组：完成界面验收")');
        await click('#chat-head [data-act="leader-trust"]');await click('[data-lt-act="report"]');await wait('Boolean(document.querySelector(".leader-trust-report"))','scoped report');
        await check('report and download URL are scoped to the selected leader','document.querySelector(".leader-trust-report").textContent.includes("A组：完成界面验收")&&!document.querySelector(".leader-trust-report").textContent.includes("B组独有目标")&&document.querySelector(".leader-trust-dialog a[download]").href.includes("leader_session_id=config-lead-a")');
        await click('[data-lt-act="close"]');
        await click('[data-act="leader"][data-id="config-lead-b"]');await wait('location.hash.includes("config-lead-b")','other leader');await click('#chat-head [data-act="leader-trust"]');
        await check('the second leader retains its own goal rather than inheriting the first','document.querySelector("#leader-trust-goal").value==="B组独有目标"&&document.querySelector("#leader-trust-form [name=enabled]").checked');
        await click('#leader-trust-form [name="enabled"]');await js('document.querySelector("#leader-trust-form").requestSubmit()');await wait('document.querySelector(".leader-trust-dialog").parentElement.hidden','second leader off saved');
        await click('[data-act="leader"][data-id="config-lead-a"]');await wait('Boolean(document.querySelector(".composer textarea"))','back first leader');
        await check('leader navigation preserves the original unsent chat draft','document.querySelector(".composer textarea").value==="保存各组设置时保留这条草稿"');
        await click('[data-act="card"][data-id="config-member"]');await wait('Boolean(document.querySelector("#card [data-act=runtime-switch]"))','runtime action on member card');
        await click('#card [data-act="runtime-switch"]');
        await check('target options start with null defaults rather than source runtime options','document.querySelector("#runtime-switch-runtime").value==="codex"&&document.querySelector("#runtime-switch-model").value===""&&document.querySelector("#runtime-switch-effort").value===""&&document.querySelector("#runtime-switch-effort").disabled');
        await click('[data-rs-act="prepare"]');await wait('Boolean(document.querySelector("#runtime-switch-handoff"))&&!document.querySelector("[data-rs-act=prepare]").disabled','real handoff preview');
        await check('preview includes actual task facts, character count and a concrete context replay notice','document.querySelector("#runtime-switch-handoff").value.includes("继续接入")&&document.querySelector("#runtime-switch-handoff").value.includes("保留任务卡")&&document.querySelector(".runtime-switch-chars").textContent.includes("个字符")&&document.querySelector(".runtime-switch-cost").textContent.includes("可能增加用量")&&!document.querySelector("[data-rs-act=confirm]").disabled');
        const originalPreview=await js('window.__configRequests.filter(row=>row.url.includes("runtime/prepare")).at(-1).result.preview_id');
        await edit('#runtime-switch-handoff','人工审阅后的交接：继续接入；保留原历史、任务卡和已知限制。');
        await check('editing handoff invalidates confirmation until another bound preview is reviewed','document.querySelector("[data-rs-act=confirm]").disabled&&document.querySelector(".runtime-switch-chars").textContent.includes("重新生成")');
        await click('[data-rs-act="prepare"]');await wait('!document.querySelector("[data-rs-act=confirm]").disabled','edited preview ready');
        assert.notEqual(await js('window.__configRequests.filter(row=>row.url.includes("runtime/prepare")).at(-1).result.preview_id'),originalPreview);evidence.checks.push('edited handoff generates a new server-bound preview ID');
        python("with closing(store.connect(home)) as conn: board.set_task(conn,'config-member','任务已更新，需重新检查')\n");
        await click('[data-rs-act="confirm"]');await wait('document.querySelector(".runtime-switch-dialog .error-line").textContent.includes("变化")','stale confirmation conflict');
        await check('a real 409 never reports success and requires review again while keeping edited text','document.querySelector("[data-rs-act=confirm]").disabled&&document.querySelector("#runtime-switch-handoff").value.includes("人工审阅后的交接")&&!document.querySelector("#toast-root").textContent.includes("已更换")');
        await click('[data-rs-act="prepare"]');await wait('!document.querySelector("[data-rs-act=confirm]").disabled','fresh preview after conflict');
        await click('[data-rs-act="confirm"]');await wait('document.querySelector(".runtime-switch-dialog").parentElement.hidden','configured switch');
        await check('confirm configures Codex without claiming a model has run','document.querySelector("#toast-root").textContent.includes("已更换，下次发消息或唤醒时继续")&&document.querySelector("#card [data-runtime=codex]")&&document.querySelector(".member-runtime-switch").textContent.includes("交接已保存")');

        await click('#card [data-act="runtime-switch"]');await check('switching back offers only Claude models and keeps defaults independent','document.querySelector("#runtime-switch-runtime").value==="claude"&&document.querySelector("#runtime-switch-model").value===""&&Array.from(document.querySelectorAll("#runtime-switch-model option")).some(item=>item.value.includes("claude"))');
        await js('document.querySelector("#runtime-switch-model").value="claude-haiku-4-5-20251001";document.querySelector("#runtime-switch-model").dispatchEvent(new Event("change",{bubbles:true}))');
        await check('the target model controls its own effort support','document.querySelector("#runtime-switch-effort").disabled&&document.querySelector("#runtime-switch-effort").value===""');
        await js('const select=document.querySelector("#runtime-switch-model");select.value=Array.from(select.options).find(item=>item.value&&item.value!=="claude-haiku-4-5-20251001").value;select.dispatchEvent(new Event("change",{bubbles:true}));document.querySelector("#runtime-switch-effort").value="medium";document.querySelector("#runtime-switch-effort").dispatchEvent(new Event("change",{bubbles:true}));void 0;');
        await click('[data-rs-act="prepare"]');await wait('!document.querySelector("[data-rs-act=confirm]").disabled','return preview');
        await check('explicit target model and supported effort reach the fresh preview body','window.__configRequests.filter(row=>row.url.includes("runtime/prepare")).at(-1).body.model.startsWith("claude")&&window.__configRequests.filter(row=>row.url.includes("runtime/prepare")).at(-1).body.effort==="medium"');await click('[data-rs-act="confirm"]');await wait('document.querySelector(".runtime-switch-dialog").parentElement.hidden','return configured');
        await check('switching back preserves the Crew member, group and original card','document.querySelector("#card [data-runtime=claude]")&&document.querySelector("#card").textContent.includes("归 交付")&&document.querySelector("#card").textContent.includes("完成跨载体交接")');
        const requests=await js('window.__configRequests');assert(!requests.some(row=>/\/api\/(wake|chat\/send|session\/compact)/.test(row.url)));assert(requests.filter(row=>row.url.includes('runtime/switch')&&row.status===200).every(row=>row.result.starts_turn===false));evidence.requests=requests;evidence.checks.push('all actual save/preview/switch requests avoid send, wake and compact; success receipts explicitly start no turn');
        const persisted=JSON.parse(python("with closing(store.connect(home)) as conn:\n a=trust_mode.get(conn,'配置验收','config-lead-a');b=trust_mode.get(conn,'配置验收','config-lead-b');mapping=runtimes.get(conn,'config-member');switches=[dict(row) for row in conn.execute(\"SELECT status,handoff FROM runtime_switches WHERE session_id='config-member' AND status='configured'\")];notices=conn.execute('SELECT COUNT(*) FROM leader_notices').fetchone()[0]\n print(json.dumps({'a':a,'b':b,'runtime':mapping,'switches':switches,'notices':notices},ensure_ascii=False))\n"));
        assert.equal(persisted.a.enabled,true);assert.equal(persisted.b.enabled,false);assert.equal(persisted.b.goal,'B组独有目标');assert.equal(persisted.runtime.runtime,'claude');assert.equal(persisted.switches.length,2);assert.equal(persisted.notices,0);evidence.persisted=persisted;
        for(const sid of ['config-lead-a','config-lead-b'])assert.equal((await fs.stat(path.join(home,'chat',sid+'.jsonl'))).size,0);
        const memberEvents=(await fs.readFile(path.join(home,'chat','config-member.jsonl'),'utf8')).trim().split('\n').filter(Boolean).map(row=>JSON.parse(row));assert(memberEvents.every(event=>event.type==='runtime_switch'));evidence.memberEvents=memberEvents;evidence.checks.push('database settings remain independent, no leader notices or model message journals appear, and only runtime-switch audit events are written');
        assert.equal(evidence.errors.length,0,JSON.stringify(evidence.errors));evidence.ok=true;await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));app.quit();
      }catch(error){evidence.errors.push(error.stack||String(error));if(contents)try{evidence.body=await js('document.body.innerText');}catch(_){}await fs.mkdir(output,{recursive:true});await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));console.error(error);process.exitCode=1;app.quit();}
    });
  });
}
