"use strict";
// Run with Electron. Real Chromium DOM, repository styles/fonts and fake local
// APIs only; no Crew service, native model or user HOME is contacted.
const {app, BrowserWindow} = require("electron");
const fs = require("node:fs/promises"), sync = require("node:fs"), path = require("node:path"), os = require("node:os"), assert = require("node:assert/strict");
const {pathToFileURL} = require("node:url");
const root = path.resolve(__dirname, "../.."), web = path.join(root, "plugin/crew/web"), output = path.join(root, ".cache/storage-ui-smoke");
const temporary = sync.mkdtempSync(path.join(os.tmpdir(), "crew-storage-ui-"));
app.setPath("userData", temporary); app.setPath("sessionData", path.join(temporary, "session"));
let win; const failures = [], checks = [];
const run = async expression => { const result = await win.webContents.executeJavaScript(`(async()=>{try{return {value:await (${expression})};}catch(error){return {error:error.stack||String(error)};}})()`); if (result.error) throw Error(result.error + "\n" + expression); return result.value; };
const click = action => run(`document.querySelector('[data-storage-action="${action}"]').click()`);
async function wait(expression) { for (let index = 0; index < 180; index++) { if (await run(expression)) return; await new Promise(resolve => setTimeout(resolve, 20)); } throw Error("等待超时：" + expression); }
async function screenshot(name) { await run("document.fonts.ready"); await run("new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))"); await new Promise(resolve => setTimeout(resolve, 80)); await fs.writeFile(path.join(output, name), (await win.webContents.capturePage(undefined, {stayHidden:true, stayAwake:true})).toPNG()); }
async function noOverflow(label) {
  const bounds = await run(`(()=>{const panel=document.querySelector('.cs-body'),storage=document.querySelector('.crew-storage');return {window:innerWidth,page:document.documentElement.scrollWidth,panel:panel.clientWidth,scroll:panel.scrollWidth,storage:storage.getBoundingClientRect().width};})()`);
  assert(bounds.page <= bounds.window + 1 && bounds.scroll <= bounds.panel + 1, `${label}: ${JSON.stringify(bounds)}`); checks.push({label, bounds});
}
(async () => {
  await app.whenReady(); await fs.mkdir(output, {recursive:true});
  const html = path.join(temporary, "storage.html");
  await fs.writeFile(html, `<!doctype html><html lang="zh-CN" data-theme="light"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"><title>存储隔离交互</title></head><body><section class="crew-settings"><aside class="cs-sidebar"><div class="cs-brand"><strong>Crew</strong></div><nav><button>AI 与模型</button><button class="active">存储与清理</button></nav></aside><main class="cs-body"><header class="cs-header"><div><div class="cs-eyebrow">Crew 设置</div><h1>存储与清理</h1><p class="cs-subtitle">查看本机占用，按需整理可回收内容。</p></div></header><div class="cs-content" id="host"></div></main></section></body></html>`);
  win = new BrowserWindow({width:1120, height:900, show:false, webPreferences:{contextIsolation:true, sandbox:true, nodeIntegration:false, backgroundThrottling:false}});
  win.webContents.on("console-message", (_event, ...args) => { const entry = typeof args[0] === "object" ? args[0].message : args[1]; if (/Uncaught|Refused|violat.*Content Security Policy|violat.*directive/i.test(String(entry))) failures.push(entry); });
  await win.loadFile(html);
  const [base, settings, storage, script] = await Promise.all(["style.css", "settings-ui.css", "storage-ui.css", "storage-ui.js"].map(name => fs.readFile(path.join(web, name), "utf8")));
  const font = pathToFileURL(path.join(root, "desktop/assets/fonts/HarmonyOS_Sans_SC_Regular.woff2")).href;
  await win.webContents.insertCSS(base + "\n" + settings + "\n" + storage + `\n@font-face{font-family:"HarmonyOS Sans SC";src:url("${font}") format("woff2");font-weight:400}html,body{height:100%;margin:0;overflow:hidden}.crew-settings{height:100dvh}`);
  await win.webContents.executeJavaScript(script);
  await run(String.raw`(()=>{
    const MB=1024*1024;
    const category=(id,label,description,bytes,count,free=0,recycle=0,defaults=false)=>({id,label,description,bytes,file_count:count,candidate_count:free||recycle?2:0,reclaimable_bytes:free,recyclable_bytes:recycle,default_selected:defaults});
    window.fixture={home:'C:\\Users\\隔离用户\\Crew Data',scanned_at:'2026-10-26T09:00:00Z',total_bytes:533*MB,reclaimable_bytes:3*MB,recyclable_bytes:140*MB,trash_bytes:12*MB,
      categories:[category('logs','日志','超过保留期的运行日志；当前服务日志保留。',13*MB,24,3*MB,0,true),category('exports','导出临时包','超过保留期的临时包，仅在你确认后移入回收站。',40*MB,8,0,40*MB),category('backups','旧备份','至少保留最近 3 份。其他备份先移入回收站。',150*MB,9,0,90*MB),category('backend_cache','后台缓存','后台缓存可能被外部命令入口引用，目前全部保留。',80*MB,124),category('attachments','未引用附件','经聊天记录核对的未引用图片，移入回收站后仍可恢复。',32*MB,14,0,10*MB),category('history','工作历史','聊天、目标与交付记录保留，不参与磁盘清理。',200*MB,80),category('runtime','运行与桌面数据','服务登记、设置、锁和桌面浏览数据保留。',5*MB,6),category('other','其他文件','未列入清理白名单的文件保留。',0,0),category('trash','回收站','文件移入后保留 7 天。到期后释放空间。',12*MB,3),category('management','清理记录','有上限的预览、策略与恢复清单。',MB,2)],
      workspaces:[{id:'ws',name:'产品开发',bytes:200*MB,file_count:80}],policy:{automatic_enabled:true,retention_days:30,keep_backups:3,recycle_days:7},warnings:[],last_maintenance:null,
      history:[{batch_id:'old',created_at:'2026-10-25T09:00:00Z',status:'completed',freed_bytes:3*MB,recycled_bytes:12*MB,file_count:3,restorable_count:3,trash_bytes:12*MB,expires_at:'2026-11-01T09:00:00Z',errors:[]}]};
    window.calls=[];window.control={failRead:false,failSave:false,holdRead:false,holdClean:false};
    const clone=value=>JSON.parse(JSON.stringify(value));
    window.api={
      read:async()=>{calls.push({action:'read'});if(control.holdRead)return new Promise(resolve=>window.finishRead=()=>resolve(clone(fixture)));if(control.failRead)throw Error('隔离磁盘暂时不可访问');return clone(fixture);},
      preview:async body=>{calls.push({action:'preview',body});const selected=fixture.categories.filter(item=>body.categories.includes(item.id));return {preview_id:'p1',expires_at:'2099-01-01T01:00:00Z',freed_bytes:selected.reduce((sum,item)=>sum+item.reclaimable_bytes,0),recycled_bytes:selected.reduce((sum,item)=>sum+item.recyclable_bytes,0),file_count:6,categories:body.categories,protected:['正在运行的后台、最近三份备份、聊天与项目文件会保留。'],samples:[{label:'日志',path:'C:\\Users\\隔离用户\\Crew Data\\logs\\'+'很长的日志路径'.repeat(16)+'\\startup.log',bytes:3*MB,action:'delete'}]};},
      clean:async body=>{calls.push({action:'clean',body});if(control.holdClean)await new Promise(resolve=>window.finishClean=resolve);return {batch_id:'new',status:'partial',freed_bytes:3*MB,recycled_bytes:0,file_count:2,errors:[{path:'C:\\旧备份',error:'文件内容已变化，已保留'}]};},
      restore:async body=>{calls.push({action:'restore',body});return {status:'partial',errors:[{path:'C:\\已经存在.png',error:'恢复位置已有文件，未覆盖'}]};},
      emptyTrash:async body=>{calls.push({action:'emptyTrash',body});fixture.total_bytes-=fixture.trash_bytes;fixture.trash_bytes=0;fixture.categories.find(item=>item.id==='trash').bytes=0;fixture.history[0].restorable_count=0;return {status:'emptied',freed_bytes:12*MB,file_count:3,errors:[]};},
      savePolicy:async body=>{calls.push({action:'savePolicy',body});if(control.failSave)throw Error('隔离策略保存失败');fixture.policy=clone(body);return clone(body);}
    };
    window.disposeStorage=CrewStorage.mount(document.querySelector('#host'),api);
  })()`);
  await wait("!!document.querySelector('[data-storage-category=logs]')");
  assert.deepEqual(await run("[...document.querySelectorAll('[data-storage-category]')].filter(x=>x.checked).map(x=>x.dataset.storageCategory)"), ["logs"]);
  assert.equal(await run("document.querySelector('[data-storage-category=backend_cache]').disabled"), true);
  assert.equal(await run("fixture.categories.length"), 10);
  assert.equal(await run("document.querySelector('[data-storage-category=other]')"), null);
  assert.equal(await run("document.querySelector('[data-storage-details=retained]').open"), false);
  assert.equal(await run("document.querySelectorAll('[data-storage-actionable] .st-category').length"), 4);
  assert.equal(await run("document.querySelectorAll('.st-legend [role=listitem]').length"), 9);
  assert(await run("Number(getComputedStyle(document.querySelector('.st-usage span')).flexGrow)>0"), "CSP permits the programmatic usage-bar style properties");
  assert(await run("document.querySelector('[data-storage-action=preview]').getBoundingClientRect().top < document.querySelector('[data-storage-details=retained]').getBoundingClientRect().top"));
  checks.push({label:"ten-categories-actionable-first-retained-folded-zero-omitted", passed:true});
  assert.equal(await run("getComputedStyle(document.querySelector('.crew-storage')).fontFamily.includes('HarmonyOS Sans SC')"), true);
  await noOverflow("wide-light"); await screenshot("storage-light.png");
  await run("(()=>{const box=document.querySelector('[data-storage-category=backups]');box.focus();box.click();})()");
  await click("preview"); await wait("!!document.querySelector('#storage-preview-title')");
  assert.equal(await run("document.activeElement.id"), "storage-preview-title");
  await run("document.querySelector('[data-storage-details=samples]').open=true"); await screenshot("storage-preview.png");
  await click("cancel-preview"); assert.equal(await run("document.querySelector('[data-storage-category=backups]').checked"), true);
  assert.equal(await run("calls.filter(x=>x.action==='clean').length"), 0);
  await run("control.holdClean=true"); await click("preview"); await wait("!!document.querySelector('#storage-preview-title')"); await click("confirm-clean"); await click("confirm-clean");
  assert.equal(await run("calls.filter(x=>x.action==='clean').length"), 1);
  await run("finishClean()"); await wait("!!document.querySelector('#storage-result-title')");
  assert.equal(await run("document.querySelector('#storage-result-title').textContent"), "清理部分完成");
  assert(await run("document.querySelector('.crew-storage').textContent.includes('文件内容已变化，已保留')"));
  await click("restore"); await wait("document.querySelector('#storage-result-title').textContent==='恢复未全部完成'");
  assert(await run("document.querySelector('.crew-storage').textContent.includes('恢复位置已有文件，未覆盖')"));
  await run("control.failRead=true"); await click("refresh"); await wait("document.querySelector('[data-storage-note]').textContent.includes('隔离磁盘暂时不可访问')");
  await run("control.failRead=false"); await click("refresh"); await wait("!document.querySelector('[data-storage-action=refresh]').disabled");
  await run("(()=>{const select=document.querySelector('[data-storage-policy=retention_days]');select.value='90';select.dispatchEvent(new Event('change',{bubbles:true}));control.failSave=true;})()");
  await click("save-policy"); await wait("document.querySelector('[data-storage-note]').textContent.includes('隔离策略保存失败')");
  assert.equal(await run("document.querySelector('[data-storage-policy=retention_days]').value"), "90");
  await run("control.failSave=false"); await click("save-policy"); await wait("document.querySelector('[data-storage-note]').textContent.includes('维护策略已保存')");
  assert.equal(await run("document.querySelector('[data-storage-action=save-policy]').disabled"), true);
  checks.push({label:"preview-cancel-single-flight-partial-restore-read-policy", passed:true});
  await click("empty-trash"); assert.equal(await run("calls.filter(x=>x.action==='emptyTrash').length"), 0); await click("cancel-empty");
  await click("empty-trash"); await click("confirm-empty"); await wait("document.querySelector('#storage-result-title').textContent==='回收站已清空'"); assert.equal(await run("calls.filter(x=>x.action==='emptyTrash').length"), 1);
  await run("(()=>{fixture.categories.forEach(item=>{if(item.id!=='logs'){item.candidate_count=0;item.reclaimable_bytes=0;item.recyclable_bytes=0;}});fixture.reclaimable_bytes=3*1024*1024;fixture.recyclable_bytes=0;})()");
  await click("refresh"); await wait("!document.querySelector('[data-storage-action=refresh]').disabled");
  await run("document.querySelector('.cs-body').scrollTo(0,0)");
  const firstScreen = await run("(()=>{const button=document.querySelector('[data-storage-action=preview]').getBoundingClientRect();return {bottom:button.bottom,height:innerHeight,rows:document.querySelectorAll('[data-storage-actionable] .st-category').length};})()");
  assert(firstScreen.bottom < firstScreen.height, JSON.stringify(firstScreen)); checks.push({label:"ten-categories-few-candidates-first-screen-action", bounds:firstScreen});
  await screenshot("storage-ten-categories-light.png");
  win.setSize(480, 820);
  await run("(()=>{document.documentElement.style.setProperty('--fs','1.3');document.querySelector('.cs-body').scrollTo(0,0);})()"); await noOverflow("narrow-large-font-light"); await screenshot("storage-narrow-light.png");
  await run("document.documentElement.dataset.theme='dark'"); await noOverflow("narrow-large-font-dark"); await screenshot("storage-narrow-dark.png");
  await click("preview"); await wait("!!document.querySelector('#storage-preview-title')"); await run("document.querySelector('[data-storage-details=samples]').open=true"); await noOverflow("narrow-preview-long-path"); await screenshot("storage-narrow-preview.png");
  await click("cancel-preview"); await run("control.holdRead=true"); await click("refresh"); await run("(()=>{disposeStorage();document.querySelector('#host').textContent='新的设置页面';finishRead();})()");
  await new Promise(resolve => setTimeout(resolve, 50)); assert.equal(await run("document.querySelector('#host').textContent"), "新的设置页面");
  checks.push({label:"unmount-ignores-pending-read", passed:true});
  assert.deepEqual(failures, []);
  await fs.writeFile(path.join(output, "evidence.json"), JSON.stringify({checks, rendererErrors:failures, modelTurnsStarted:0, scope:"real Electron renderer with fake API and isolated userData"}, null, 2));
  console.log(JSON.stringify({passed:true, checks:checks.length, output}));
})().catch(async failure => { console.error(failure.stack || failure); process.exitCode = 1; await fs.mkdir(output, {recursive:true}); await fs.writeFile(path.join(output, "failure.txt"), String(failure.stack || failure)); }).finally(() => { if (win) win.destroy(); app.quit(); });
