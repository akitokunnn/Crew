'use strict';
// Run with Node. The same file is required by an isolated real Electron app.
const fs = require('node:fs/promises'), path = require('node:path'), assert = require('node:assert/strict');
const { spawn, spawnSync } = require('node:child_process');
const root = path.resolve(__dirname, '../..'), desktop = path.join(root, 'desktop');
const output = path.join(desktop, 'smoke-output/documents');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

if (!process.versions.electron) {
  (async () => {
    const launchedAt = Date.now();
    const home = await fs.mkdtemp(path.join(desktop, '.cache/documents-smoke-'));
    const env = {...process.env}; delete env.ELECTRON_RUN_AS_NODE;
    const child = spawn(require(require.resolve('electron', {paths:[desktop]})), ['--require', __filename, desktop, '--smoke-test', '--isolated-sources', '--home', home, '--smoke-output', path.join(output, 'unused.json')], {cwd:desktop, env, windowsHide:true, stdio:['ignore','pipe','pipe']});
    child.stdout.pipe(process.stdout); child.stderr.pipe(process.stderr);
    const timer = setTimeout(() => { child.kill(); process.exitCode = 1; }, 120000);
    child.once('error', error => { clearTimeout(timer); console.error(error); process.exitCode = 1; });
    child.once('exit', async (code, signal) => {
      const exitedAt = Date.now();
      clearTimeout(timer); child.stdout.destroy(); child.stderr.destroy();
      try {
        const result = JSON.parse(await fs.readFile(path.join(output,'evidence.json'),'utf8'));
        assert.equal(result.home,home, 'Evidence belongs to a prior run');
        result.timing = {...result.timing, launched_at:launchedAt, process_exit_at:exitedAt, total_ms:exitedAt-launchedAt, quit_to_exit_ms:result.timing?.quit_requested_at ? exitedAt-result.timing.quit_requested_at : null, exit_code:code, signal};
        await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(result,null,2));
        console.log(JSON.stringify({timing:result.timing}));
        assert.equal(code,0); assert.equal(result.home,home); assert.equal(result.ok,true); assert.equal(result.errors.length,0);
        try { await fs.access(path.join(home,'web.json')); throw Error('Owned service registry remains'); } catch (error) { if(error.code!=='ENOENT')throw error; }
        console.log(JSON.stringify({ok:true,checks:result.checks.length,evidence:path.join(output,'evidence.json'),ownedServiceExited:true}));
      } catch(error) { console.error(error); process.exitCode=1; }
    });
  })().catch(error => {console.error(error);process.exitCode=1;});
} else {
  const {app,ipcMain,webContents} = require('electron');
  const core = require(path.join(desktop,'src/core.cjs'));
  const evidence={checks:[],errors:[],screenshots:[],modelTurnsStarted:0,timing:{hook_loaded_at:Date.now(),phases:[]},scope:'real isolated HTTP, database, document files, renderer, pin and preview; external editor/open actions covered by transport/backend tests'};
  const flushTiming=()=>{try{require('node:fs').writeFileSync(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));}catch(_){}};
  const stop=core.Backend.prototype.stop;
  core.Backend.prototype.stop=async function(...args){evidence.timing.backend_stop_started_at=Date.now();try{return await stop.apply(this,args);}finally{evidence.timing.backend_stop_finished_at=Date.now();evidence.timing.backend_exit_code=this.child?.exitCode;flushTiming();}};
  app.on('before-quit',()=>{evidence.timing.before_quit_at??=Date.now();flushTiming();});
  app.on('will-quit',()=>{evidence.timing.will_quit_at=Date.now();flushTiming();});
  process.once('exit',()=>{evidence.timing.electron_exit_at=Date.now();flushTiming();});
  app.once('browser-window-created',(_event,win)=>{
    evidence.timing.window_created_at=Date.now();
    win.webContents.setBackgroundThrottling(false);
    ipcMain.removeHandler('desktop:ready');ipcMain.handle('desktop:ready',()=>{});
    win.webContents.once('did-finish-load',async()=>{
      evidence.timing.home_loaded_at=Date.now();
      let contents;
      const js=source=>contents.executeJavaScript(source,true);
      async function until(callback,label){const end=Date.now()+15000;while(Date.now()<end){if(await callback())return;await pause(40);}throw Error('Timed out: '+label);}
      const wait=(source,label)=>until(()=>js(source),label);
      const click=selector=>js(`document.querySelector(${JSON.stringify(selector)}).click()`);
      async function check(label,source){assert(await js(source),label);evidence.checks.push(label);evidence.timing.phases.push({label,at:Date.now()});}
      async function capture(name){
        await js('document.fonts.ready.then(()=>true)');await pause(400);
        let timer;contents.debugger.attach('1.3');
        try{
          const result=await Promise.race([contents.debugger.sendCommand('Page.captureScreenshot',{format:'png',fromSurface:true,captureBeyondViewport:false}),new Promise((_resolve,reject)=>{timer=setTimeout(()=>reject(Error('Screenshot timeout')),8000);})]);
          const file=path.join(output,name+'.png');await fs.writeFile(file,Buffer.from(result.data,'base64'));evidence.screenshots.push(file);
        }finally{clearTimeout(timer);contents.debugger.detach();}
      }
      try{
        await fs.mkdir(output,{recursive:true});
        await until(()=>win.webContents.executeJavaScript('Boolean(window.crewDesktop)&&document.querySelector("#service").textContent==="已连接"'),'home bootstrap');
        evidence.timing.home_ready_at=Date.now();
        const state=await win.webContents.executeJavaScript('window.crewDesktop.bootstrap()');evidence.service=state.service;
        assert.notEqual(new URL(state.service.url).port,'47811');
        const home=process.argv[process.argv.indexOf('--home')+1];evidence.home=home;
        const parent=path.join(home,'projects');await fs.mkdir(parent,{recursive:true});
        await core.newWorkspace(parent,'文档验收',state.service.url);await core.newWorkspace(parent,'另一个项目',state.service.url);
        const projects=await core.projects(state.service.url);evidence.timing.projects_created_at=Date.now();
        const seed=`import json,sys\nfrom pathlib import Path\nfrom contextlib import closing\nsys.path.insert(0,sys.argv[1])\nfrom crew import board,store,documents\nhome=Path(sys.argv[2]); projects={p['name']:p for p in json.loads(sys.argv[3])}\nwith closing(store.connect(home)) as conn:\n for sid,project,name in [('docs-leader-a','文档验收','交付'),('docs-leader-b','文档验收','测试'),('docs-leader-foreign','另一个项目','外部')]:\n  p=projects[project];board.join(conn,sid,project,name,p['root'],p['root'],'main',role='leader')\n  (home/'chat').mkdir(exist_ok=True);(home/'chat'/(sid+'.jsonl')).touch()\n p=Path(projects['文档验收']['root'])/'reports';p.mkdir()\n (p/'child.txt').write_text('嵌套文件正常',encoding='utf8')\n rows=[('summary.md','交付总览','本轮交付结果与后续安排。','summary',True,'docs-leader-a','manual'),('report.md','测试报告','测试结果和待确认项目。','report',True,'docs-leader-b','manual'),('guide.md','使用指南','工作区操作和文档预览说明。','guide',False,'docs-leader-a','manual'),('legacy.md','旧说明',None,'other',False,'docs-leader-a','journal'),('missing.md','已归档文档','用于检查文件移动后的提示。','report',False,'docs-leader-a','manual'),('escaped.md','<script>window.__docXss=1</script>','标题中的标记只作为文字显示。','other',False,'docs-leader-a','manual')]\n result=[]\n for filename,title,summary,kind,important,sid,source in rows:\n  target=p/filename;target.write_text('# 文件预览测试\\n\\n这是隔离 QA 文档。\\n\\n[子文件](./child.txt)',encoding='utf8')\n  result.append(documents.register(conn,sid,str(target),title=title,summary=summary,kind=kind,important=important,source=source))\n (p/'missing.md').unlink()\n print(json.dumps(result,ensure_ascii=False))\n`;
        const seeded=spawnSync('python',['-',path.join(root,'plugin'),home,JSON.stringify(projects)],{input:seed,encoding:'utf8',windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
        assert.equal(seeded.status,0,seeded.stderr);const docs=JSON.parse(seeded.stdout);evidence.documents=docs.map(doc=>({id:doc.id,title:doc.title,kind:doc.kind,important:doc.important}));
        const summaryId=docs.find(doc=>doc.kind==='summary').id;
        evidence.timing.fixture_seeded_at=Date.now();
        await win.webContents.executeJavaScript('window.crewDesktop.openProject("文档验收")');
        contents=webContents.getAllWebContents().find(item=>item.getURL().includes(encodeURIComponent('文档验收')));assert(contents);
        contents.setBackgroundThrottling(false);contents.on('console-message',event=>{if(event.level==='error')evidence.errors.push(event.message);});
        await wait('Boolean(document.querySelector("[data-act=leader][data-id=docs-leader-a]"))','leader UI');
        await click('[data-act="leader"][data-id="docs-leader-a"]');
        await wait('Boolean(document.querySelector(".composer textarea")) && document.querySelectorAll(".doc-rail-card").length===5','group documents rail');
        await js('document.querySelector(".composer textarea").value="保留这条未发送草稿";document.querySelector(".composer textarea").dispatchEvent(new Event("input",{bubbles:true}))');
        win.setSize(1640,1000);await wait('innerWidth>=1360','wide native view resize');
        await check('wide chat has a 220px group document rail with important summary first','Math.abs(document.querySelector("#documents-rail").getBoundingClientRect().width-220)<2 && document.querySelector(".doc-rail-title").textContent==="交付总览"');
        await capture('01-wide-chat-documents');
        await click('[data-view="documents"]');await wait('document.querySelectorAll(".document-card").length===6','document center');
        await check('document center has all indexed files, source metadata and important summaries first','document.querySelector(".doc-title").textContent==="交付总览" && document.querySelector("[data-view=documents] .count").textContent==="6" && document.querySelector(".doc-metadata").textContent.includes("交付")');
        await check('historical missing summaries and moved files are explicit without inventing content','document.querySelector("#documents-results").textContent.includes("未填写摘要") && document.querySelector("#documents-results").textContent.includes("文件已不在原位置") && typeof window.__docXss==="undefined"');
        await capture('02-document-center');
        await js('document.querySelector("#documents-search").value="指南";document.querySelector("#documents-search").dispatchEvent(new Event("input",{bubbles:true}))');
        await check('search filters by the actual document text','document.querySelectorAll(".document-card").length===1 && document.querySelector(".doc-title").textContent==="使用指南"');
        await js('document.querySelector("#documents-search").value="";document.querySelector("#documents-search").dispatchEvent(new Event("input",{bubbles:true}));document.querySelector("#documents-group").value="docs-leader-b";document.querySelector("#documents-group").dispatchEvent(new Event("change",{bubbles:true}))');
        await check('group filter isolates the other leader document','document.querySelectorAll(".document-card").length===1 && document.querySelector(".doc-title").textContent==="测试报告"');
        await js('document.querySelector("#documents-group").value="";document.querySelector("#documents-group").dispatchEvent(new Event("change",{bubbles:true}));document.querySelector("#documents-kind").value="guide";document.querySelector("#documents-kind").dispatchEvent(new Event("change",{bubbles:true}))');
        await check('kind filter shows only the matching guide','document.querySelectorAll(".document-card").length===1 && document.querySelector(".doc-kind").textContent==="指南"');
        await js('document.querySelector("#documents-kind").value="";document.querySelector("#documents-kind").dispatchEvent(new Event("change",{bubbles:true}));document.querySelector("#documents-important").click()');
        await check('important-only filter keeps the two marked documents','document.querySelectorAll(".document-card").length===2');
        await click(`[data-document-card="${summaryId}"] [data-doc-act=pin]`);await wait('document.querySelectorAll(".document-card").length===1','persist unpin');
        await click('#documents-important');
        await click(`[data-document-card="${summaryId}"] [data-doc-act=reset-pin]`);
        await wait(`document.querySelector('[data-document-card="${summaryId}"] [data-doc-act=pin]').getAttribute('aria-pressed')==='true'`,'restore automatic important marker');
        evidence.checks.push('pin and restore automatic marking persist through the real HTTP/database contract');
        await click(`[data-document-card="${summaryId}"] [data-document-id]`);
        await wait('document.querySelector("#file-preview").classList.contains("show") && document.querySelector("#file-preview .markdown h1")?.textContent==="文件预览测试"','ID markdown preview');
        await check('document ID preview uses trusted submitter identity for nested links','document.querySelector("#file-preview [data-file-path]").dataset.fileSession==="docs-leader-a"');
        await capture('03-id-markdown-preview');
        await click('#file-preview [data-file-path]');
        await wait('document.querySelector("#file-preview .file-text")?.textContent.includes("嵌套文件正常")','nested text preview');
        evidence.checks.push('a relative resource inside Markdown resolves within the trusted submitter workspace');
        await click('#file-preview .file-close');
        await click(`[data-document-card="${summaryId}"] [data-doc-act=source]`);
        await wait('Boolean(document.querySelector(".composer textarea"))','source leader chat');
        await check('source navigation returns to its leader with the original unsent draft','location.hash.includes("leader=docs-leader-a") && document.querySelector(".composer textarea").value==="保留这条未发送草稿"');
        win.setSize(1100,850);await wait('innerWidth<1360','narrow native view resize');
        await check('narrow chat hides the fixed rail and keeps its document header button','getComputedStyle(document.querySelector("#documents-rail")).visibility==="hidden" && Boolean(document.querySelector("[data-act=documents-open]"))');
        const composerWidth=await js('document.querySelector(".composer").getBoundingClientRect().width');
        await click('[data-act="documents-open"]');await wait('document.querySelector("#documents-rail").classList.contains("open")','document drawer');
        assert.equal(await js('document.querySelector(".composer").getBoundingClientRect().width'),composerWidth);
        await capture('04-narrow-document-drawer');evidence.checks.push('narrow document drawer overlays without reducing composer width');
        await click('[data-doc-act="close-rail"]');await click('[data-act="right-open"]');
        await check('architecture drawer and document drawer are mutually exclusive','document.querySelector("#right-panel").classList.contains("show") && document.querySelector("#documents-rail").hidden && document.querySelector("[data-act=right-open]").getAttribute("aria-expanded")==="true"');
        await capture('05-architecture-drawer');await click('[data-act="right-close"]');
        await check('opening and closing side panels preserves the chat draft','document.querySelector(".composer textarea").value==="保留这条未发送草稿" && document.documentElement.scrollWidth<=innerWidth');
        await click('[data-view="documents"]');await wait('document.querySelectorAll(".document-card").length===6','return center');
        await click('#documents-refresh');await wait('!document.querySelector("#documents-refresh").disabled','explicit document refresh');
        evidence.checks.push('explicit refresh rebuilds the index while preserving the usable document center');
        for(const sid of ['docs-leader-a','docs-leader-b','docs-leader-foreign'])assert.equal((await fs.stat(path.join(home,'chat',sid+'.jsonl'))).size,0);
        assert.equal(evidence.errors.length,0,JSON.stringify(evidence.errors));evidence.ok=true;
        evidence.timing.fixture_cleanup_started_at=Date.now();
        evidence.timing.debuggers_before_cleanup=webContents.getAllWebContents().filter(item=>!item.isDestroyed()).map(item=>({id:item.id,type:item.getType(),attached:item.debugger.isAttached()}));
        for(const item of webContents.getAllWebContents())if(!item.isDestroyed()&&item.debugger.isAttached())item.debugger.detach();
        evidence.timing.fixture_cleanup_finished_at=Date.now();
        evidence.timing.quit_requested_at=Date.now();evidence.timing.ui_elapsed_ms=evidence.timing.quit_requested_at-evidence.timing.hook_loaded_at;
        await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));app.quit();
      }catch(error){
        evidence.errors.push(error.stack||String(error));
        if(contents)try{evidence.body=await js('document.body.innerText');await capture('failure');}catch(_){}
        await fs.mkdir(output,{recursive:true});await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));console.error(error);process.exitCode=1;app.quit();
      }
    });
  });
}
