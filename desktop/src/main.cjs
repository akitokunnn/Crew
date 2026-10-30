'use strict';
const { app, BrowserWindow, WebContentsView, ipcMain, dialog, Menu, Tray, nativeImage, shell, clipboard } = require('electron');
const fs = require('node:fs/promises');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const core = require('./core.cjs');
const storage = require('./storage.cjs');
const chrome = require('./chrome.cjs');
const { WorkspaceTabs } = require('./tabs.cjs');
const { ActivityPreview } = require('./activity-preview.cjs');
const { installPermissions } = require('./permissions.cjs');
const { ToolUpdates } = require('./tool-updates.cjs');
const { CrewUpdates } = require('./crew-updates.cjs');
const { Administrator } = require('./admin.cjs');
const { OperationsClient } = require('./operations.cjs');
const { LaunchSettings, relaunchArgs } = require('./launch-settings.cjs');
const product = require('./product.cjs');
const config = core.options(process.argv.slice(1),{launchSettingsPath:path.join(app.getPath('appData'),product.settingsDirectory,'launch-settings.json')});
config.productGeneration = 2;
try { require('./backend-product.cjs').requireHomeGenerationSync(config.home,config.productGeneration); }
catch(error) {
  // Reject a V1 HOME before Electron profiles, administrator files or bundled
  // backend materialization can create anything inside the selected directory.
  console.error(error);
  if(!config.smoke)dialog.showErrorBox('Crew V2 数据目录不兼容',error.message+'\n请通过 --home <独立的新目录> 启动 Crew V2，或在 Crew V2 启动设置中选择独立目录。');
  app.exit(1);
  return;
}
const administrator = new Administrator(config.home);
const operations = new OperationsClient(config);
app.setName(config.instanceName);
if (process.platform === 'win32') app.setAppUserModelId(product.appId);
config.bundledBackendDir = app.isPackaged ? path.join(process.resourcesPath, 'backend') : path.resolve(__dirname, '../../plugin');
config.backendDir = config.backendDir || config.bundledBackendDir;
config.helperBackendDir = config.bundledBackendDir;
config.codexDesktopProxy = app.isPackaged
  ? path.join(process.resourcesPath, 'app.asar.unpacked', 'assets', 'CrewCodexBridge.exe')
  : path.resolve(__dirname, '../assets/CrewCodexBridge.exe');
require('node:fs').mkdirSync(config.userData,{recursive:true});
app.setPath('userData', config.userData);
const homePage = pathToFileURL(path.join(__dirname, 'home.html')).href;
const errorPage = pathToFileURL(path.join(__dirname, 'error.html')).href;
let lastPublishedTab=null;
let window, tray, backend, tabs, activityPreview, tools, crewUpdates, launchSettings, windowMenu, quitting = false, finishedQuit = false, parentDirectory = null, smokeStarted = false, pendingAction = null, pendingWorkspace = null, homeBootstrapped = false;
let startupWork = null, cleanupPromise = null;

function liveWindow(target = window) {
  return !quitting && target === window && !!target && !target.isDestroyed() && !target.webContents.isDestroyed();
}
function showWindow() {
  if (!liveWindow()) return;
  window.show(); window.focus();
}
async function startupStep(work) {
  startupWork = Promise.resolve(work);
  try { return await startupWork; }
  finally { startupWork = null; }
}
async function cleanupResources() {
  if (cleanupPromise) return cleanupPromise;
  cleanupPromise = (async () => {
    const finish = async (name, action) => {
      try { await action(); }
      catch (error) { console.error('Crew shutdown failed (' + name + '):', error); }
    };
    // Let resources already being constructed finish before closing them. The
    // continuation checks quitting before it can construct the next resource.
    if (startupWork) await finish('startup', () => startupWork);
    await Promise.all([
      finish('connection change', () => launchSettings?.pending),
      finish('Crew update', () => crewUpdates?.close()),
      finish('tool updates', () => tools?.close())
    ]);
    await Promise.all([...workspaceDataJobs].map(job=>finish('workspace data',()=>job)));
    await finish('backend', () => backend?.stop());
  })();
  return cleanupPromise;
}
async function injectAssets(directory, files) {
  const target = window;
  const assets = await Promise.all(files.map(async ([method, name]) => [method, await fs.readFile(path.join(directory, name), 'utf8')]));
  for (const [method, source] of assets) {
    if (!liveWindow(target)) return {ok:false};
    try { await target.webContents[method](source); }
    catch (error) { if (!liveWindow(target)) return {ok:false}; throw error; }
  }
  return {ok:liveWindow(target)};
}
const preparedWorkspaces=new Set(), workspaceDataJobs=new Set();
function workspaceDataRequest(base, route, body, timeoutMs=120000) {
  if(quitting)return Promise.reject(new Error('Crew 正在退出，未开始新的工作区数据操作'));
  const pending=core.request(base,route,body,timeoutMs);
  workspaceDataJobs.add(pending);
  return pending.finally(()=>workspaceDataJobs.delete(pending));
}

function trusted(event) {
  if (!liveWindow() || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame || event.senderFrame.url !== homePage) {
    throw new Error('桌面操作只允许来自 Crew 工作区首页');
  }
}
function handle(channel, callback) { ipcMain.handle(channel, async (event, ...args) => { trusted(event); return callback(...args); }); }
function trustedChrome(event) {
  if (!liveWindow() || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame ||
      ![homePage,errorPage].includes(event.senderFrame.url)) throw new Error('窗口操作只允许来自 Crew 窗口');
}
function currentWindowState() { return { maximized:window.isMaximized(), focused:window.isFocused(), canGoBack:(tabs?.activeView?.webContents||window.webContents).navigationHistory.canGoBack() }; }
function publishTabs(state=tabs?.state()) {
  if (!liveWindow()) return;
  if (state) window.webContents.send('desktop:tabs-state',state);
  if(state?.activeId!==lastPublishedTab)activityPreview?.hide();
  lastPublishedTab=state?.activeId;activityPreview?.refresh();
  window.webContents.send('desktop:window-state',currentWindowState());
  const active=state?.tabs.find(tab=>tab.id===state.activeId);
  window.setTitle(active?.project ? active.name+' — '+config.instanceName : config.instanceName);
  for (const menu of [windowMenu, Menu.getApplicationMenu()]) {
    const item = menu?.getMenuItemById('workspace-settings'); if (item) item.enabled = Boolean(active?.project&&!active.readOnly);
  }
}
ipcMain.handle('desktop:chrome-ready', async event => {
  trustedChrome(event);
  const target = window, css = await fs.readFile(path.join(__dirname, 'chrome.css'), 'utf8');
  if (!liveWindow(target)) return null;
  try { await target.webContents.insertCSS(css, { cssOrigin: 'author' }); }
  catch (error) { if (!liveWindow(target)) return null; throw error; }
  if (!liveWindow(target)) return null;
  return { ...currentWindowState(),tabs:tabs?.state() };
});
ipcMain.on('desktop:activity-preview-ready',event=>activityPreview?.markReady(event));
ipcMain.on('desktop:activity-preview-hover',(event,inside)=>{if(activityPreview?.owns(event)){if(inside)activityPreview.cancelHide();else activityPreview.scheduleHide();}});
ipcMain.on('desktop:activity-preview-open',(event,id)=>activityPreview?.open(event,id));
ipcMain.on('desktop:tabs-layout',(event,bounds)=>{
  try {
    trusted(event);
    const [width,height]=window.getContentSize();
    if (!bounds || !['x','y','width','height'].every(key=>Number.isInteger(bounds[key])) || bounds.x<0 || bounds.y<72 || bounds.width<1 || bounds.height<1 || bounds.x+bounds.width>width || bounds.y+bounds.height>height) return;
    tabs?.setBounds(bounds);
  } catch (_) { /* Child frames cannot reposition native workspace views. */ }
});
ipcMain.handle('desktop:chrome-action', async (event, command, value) => {
  trustedChrome(event);
  if (command === 'home') return showHome();
  if (command === 'assistant') return tabs.openAssistant();
  if (command === 'admin') return showHome('admin');
  if (command === 'settings') return showHome('settings');
  if (command === 'back') return tabs.back();
  if (command === 'retry') return event.senderFrame.url===errorPage ? window.loadURL(homePage) : tabs.reload();
  if (command === 'tab-preview') return activityPreview.show(value?.id,value?.anchor);
  if (command === 'tab-preview-hide') {activityPreview.scheduleHide();return;}
  if (command === 'tab-select') {activityPreview?.hide();return tabs.select(value);}
  if (command === 'tab-close') return tabs.close(value);
  if (command === 'menu') return new Promise(resolve => windowMenu.popup({ window, callback: resolve }));
  throw new Error('不支持的窗口操作');
});
function external(url) {
  try { const parsed = new URL(url); if ((['http:', 'https:'].includes(parsed.protocol) || /^codex:\/\/threads\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(url)) && !parsed.username && !parsed.password) void shell.openExternal(parsed.href).catch(error=>actionError(new Error('无法打开外部应用：'+error.message))); }
  catch (_) { /* Invalid external link: keep the application on its current page. */ }
}
async function showHome(action = null, workspace = null) {
  if (!liveWindow()) return;
  const target = window;
  pendingWorkspace = action === 'workspace-settings' ? workspace : null;
  if(action==='new')parentDirectory=null;
  if(target.webContents.getURL()!==homePage){
    homeBootstrapped=false;pendingAction=action;
    try { await target.loadURL(homePage); }
    catch (error) { if (!liveWindow(target)) return; throw error; }
  }
  if (!liveWindow(target)) return;
  tabs?.select('home');
  if(action && homeBootstrapped){window.webContents.send('desktop:home-action',{action,...(workspace ? {project:workspace} : {})});pendingWorkspace=null;}
  else if(action)pendingAction=action;
  if (!config.smoke) showWindow();
}
function actionError(error) {
  console.error('Crew action failed:',error);
  if(!config.smoke && liveWindow())void dialog.showMessageBox(window,{type:'error',title:'Crew',message:'这个操作暂时没有完成',detail:error.message,buttons:['好'],noLink:true})
    .catch(failure=>console.error('Crew error dialog failed:',failure));
}
function menuAction(action) { return ()=>{ if (!liveWindow()) return; Promise.resolve().then(()=>{if(liveWindow())return action();}).catch(actionError); }; }
async function openProject(name, hash = '') {
  if (!liveWindow()) return false;
  const target = window;
  if(new URL(backend.url).port==='0')throw new Error(backend.startupError||'后台尚未连接，请查看系统管理员');
  let project=(await core.projects(backend.url)).find(item=>item.name===name);
  if (!liveWindow(target)) return false;
  if(!project&&name.startsWith('storage:')){
    const history=await workspaceDataRequest(backend.url,'/api/workspace-data/history?workspace_id='+encodeURIComponent(name.slice(8)));
    project={name,display_name:history.workspace.name,root:'已保存的工作区历史',read_only:true,workspace_id:history.workspace.id};
  }
  if (!liveWindow(target)) return false;
  if(!project)throw new Error('工作区不存在，请刷新列表');
  if (!project.read_only) {
    const [location]=await core.projectLocations([project]);
    if (!liveWindow(target)) return false;
    if(location.directory_state.status!=='ready'){await showHome('workspace-settings',name);return false;}
  }
  const alreadyOpen=tabs.state().tabs.some(tab=>tab.project===project.name);
  const alreadyPrepared=preparedWorkspaces.delete(project.name);
  if(!alreadyOpen&&!alreadyPrepared)project=await storage.prepare(project,backend.url,chooseHistory,storageProgress,workspaceDataRequest);
  if(!project||!liveWindow(target))return false;
  await tabs.open(project, hash);return true;
}
function storageProgress(message){if(liveWindow())window.webContents.send('desktop:storage-progress',message);}
async function chooseHistory({kind,discovery}) {
  if(!liveWindow())return null;
  const target=window;
  if(kind==='history_missing'){
    const answer=await dialog.showMessageBox(window,{type:'question',title:'打开为 V2 工作区',message:'这个文件夹有旧 Crew 的工作区身份',detail:'可以使用现有源码，从全新的 V2 记录开始。旧版任务、聊天和文件夹身份会保留；新建记录不会启动 AI。需要接续原历史时，请取消并导入工作区包。',buttons:['从全新 V2 记录开始','取消'],defaultId:0,cancelId:1,noLink:true});
    return liveWindow(target)&&answer.response===0?{action:'fresh'}:null;
  }
  if(kind==='relocate'){
    const answer=await dialog.showMessageBox(window,{type:'question',title:'接续工作区',message:'发现已有工作区身份',detail:'可以将此目录设为本机工作位置，历史身份保持不变。Crew 会先停止该工作区在旧位置的执行；外部工具中的执行请自行退出。复制项目作为独立工作区时，请使用导出包的“复制为新工作区”。',buttons:['迁移目录并接续','取消'],defaultId:0,cancelId:1,noLink:true});
    return liveWindow(target)&&answer.response===0?'relocate':null;
  }
  const candidates=discovery.candidates||[];
  let selected=0;
  if(candidates.length>1){
    const answer=await dialog.showMessageBox(window,{type:'question',title:'选择工作区历史',message:'发现多份历史，请选择要接续的数据',detail:candidates.map((c,i)=>`${i+1}. ${c.project}\n${c.source_home}\n成员 ${c.counts.sessions||0} · 任务 ${c.counts.goals||0} · 报告 ${c.counts.reports||0}`).join('\n\n'),buttons:[...candidates.map((c,i)=>`${i+1}. ${c.project}`),'取消'],defaultId:0,cancelId:candidates.length,noLink:true});
    if(!liveWindow(target))return null;
    selected=answer.response;if(selected>=candidates.length)return null;
  }
  const candidate=candidates[selected];if(!candidate)return null;
  const buttons=['升级并接续','仅查看历史',...(discovery.can_start_fresh?['从全新 V2 记录开始']:[]),'取消'];
  const answer=await dialog.showMessageBox(window,{type:'question',title:'升级工作区数据',message:'发现可以接续的旧版历史',detail:`${candidate.project}\n成员 ${candidate.counts.sessions||0} · 任务 ${candidate.counts.goals||0} · 报告 ${candidate.counts.reports||0} · 资料 ${candidate.counts.documents||0}\n\n升级会建立完整历史库，保留旧数据并校验关联和产出文件。查看、升级均不会启动 AI。`,buttons,defaultId:0,cancelId:buttons.length-1,noLink:true});
  return !liveWindow(target)||answer.response===buttons.length-1?null:answer.response===2?{action:'fresh'}:{action:answer.response===0?'upgrade':'view',candidate_id:candidate.id};
}
async function exportWorkspace(name){
  const answer=await dialog.showSaveDialog(window,{title:'导出完整工作区',defaultPath:(name.replace(/[<>:"/\\|?*]/g,'_')||'workspace')+'.crewpack',filters:[{name:'Crew 工作区',extensions:['crewpack']}]});
  if(answer.canceled||!liveWindow())return null;
  const result=await workspaceDataRequest(backend.url,'/api/workspace-data/export',{project:name,destination:answer.filePath},120000);
  if(!liveWindow())return result;
  await dialog.showMessageBox(window,{type:'info',title:'工作区已导出',message:'完整工作区包已保存',detail:answer.filePath+'\n包含历史记录及已保存的产出文件。'+(result.cached_files?.length?`\n${result.cached_files.length} 项资料原件暂不可用，已携带校验通过的保存版本。`:''),buttons:['好'],noLink:true});
  return result;
}
async function importWorkspace(){
  const answer=await dialog.showOpenDialog(window,{title:'导入工作区',filters:[{name:'Crew 工作区',extensions:['crewpack']}],properties:['openFile']});
  if(answer.canceled||!liveWindow())return null;
  const path=answer.filePaths[0],preview=await workspaceDataRequest(backend.url,'/api/workspace-data/preview',{path},120000);
  if(!liveWindow())return null;
  const manifest=preview.manifest||preview;
  const choice=await dialog.showMessageBox(window,{type:'question',title:'导入工作区',message:'工作区包已通过校验',detail:`${preview.workspace?.name||manifest.workspace?.name||manifest.workspace_name||'工作区历史'}\n\n恢复历史保留原工作区身份；复制会建立独立的新工作区。导入不会接管原电脑的执行权限，也不会启动 AI。`,buttons:['恢复历史（保留身份）','复制为新工作区','取消'],defaultId:0,cancelId:2,noLink:true});
  if(choice.response===2||!liveWindow())return null;
  const result=await workspaceDataRequest(backend.url,'/api/workspace-data/import',{path,mode:choice.response===0?'restore_readonly':'fork'},120000);
  if(!liveWindow())return result;
  if(choice.response===1){
    const next=await dialog.showMessageBox(window,{type:'question',title:'工作区副本已导入',message:'要在这台电脑上接续工作吗？',detail:'选择已有代码目录后，Crew 会恢复本机工作区界面，保留历史与任务。所有执行保持停止；只有你明确继续或发送消息时，才会建立新的原生会话。',buttons:['选择目录并接续','先查看历史'],defaultId:0,cancelId:1,noLink:true});
    if(!liveWindow())return result;
    if(next.response===0){const active=await activateWorkspace(result.workspace_id);if(active)return active;}
  }
  if(liveWindow())await tabs.open(result.project);return result;
}
async function activateWorkspace(workspaceId){
  const directory=await dialog.showOpenDialog(window,{title:'选择新工作区副本的本机代码目录',buttonLabel:'绑定副本并接续',properties:['openDirectory']});
  if(directory.canceled||!liveWindow())return null;
  storageProgress('正在恢复本机工作区界面，执行保持停止…');
  try{
    const result=await workspaceDataRequest(backend.url,'/api/workspace-data/activate',{workspace_id:workspaceId,root:directory.filePaths[0],replace_source_marker:true},120000);
    if(liveWindow())await tabs.open(result.project);return result;
  }finally{storageProgress('');}
}
async function viewHistory(name){
  const project=(await core.projects(backend.url)).find(p=>p.name===name);
  const workspaceId=project?.workspace_id||(name.startsWith('storage:')?name.slice(8):null);
  if(!workspaceId)throw Error('请先打开工作区并完成历史升级');
  return openProject('storage:'+workspaceId);
}
async function exportHandoff(name){
  const answer=await dialog.showOpenDialog(window,{title:'保存 Codex / VS Code 交接材料',buttonLabel:'保存到此文件夹',properties:['openDirectory']});
  if(answer.canceled||!liveWindow())return null;
  const destination=path.join(answer.filePaths[0],'Crew-handoff-'+new Date().toISOString().replace(/[:.]/g,'-'));
  const result=await workspaceDataRequest(backend.url,'/api/workspace-data/handoff',{project:name,destination},120000);
  if(!liveWindow())return result;
  const choice=await dialog.showMessageBox(window,{type:'info',title:'交接材料已生成',message:'可以在 Codex 或 VS Code 中接续工作',detail:result.markdown+'\n\n材料包含原始要求、当前进度、必要上下文和报告来源。它会建立可读的接续上下文；原生会话能否直接恢复取决于目标工具。开始修改前，请先暂停 Crew 中对应的执行。',buttons:['复制接续提示','查看文件夹','好'],defaultId:0,cancelId:2,noLink:true});
  if(!liveWindow())return result;
  if(choice.response===0)clipboard.writeText('请先阅读 '+result.markdown+'，并按其中引用读取 handoff.json 的完整要求与约束。核对当前项目文件和已有产出后继续；这份交接不授予运行权限，也不代表原生会话已经恢复。');
  if(choice.response===1)shell.showItemInFolder(result.markdown);
  return result;
}
function keyboard(event,input){
  if(input.type!=='keyDown')return;
  const key=input.key.toLowerCase(),ctrl=input.control||input.meta;
  let action;
  if(ctrl&&!input.alt&&key==='tab')action=()=>tabs.cycle(input.shift?-1:1);
  else if(ctrl&&!input.alt&&!input.shift&&key==='t')action=()=>showHome();
  else if(ctrl&&!input.alt&&!input.shift&&key==='w')action=()=>tabs.close(tabs.state().activeId);
  else if((ctrl&&!input.alt&&key==='r')||key==='f5')action=()=>tabs.reload();
  else if(input.alt&&!ctrl&&key==='arrowleft')action=()=>tabs.back();
  if(action){event.preventDefault();menuAction(action)();}
}
function aboutCrew() {
  return dialog.showMessageBox(window, { type:'info', title:'关于 Crew', message:'Crew',
    detail:'版本 ' + app.getVersion() + '\n让不同的 AI 围绕同一个目标协作。',
    icon:nativeImage.createFromPath(path.join(__dirname,'../assets/crew.png')), buttons:['好'], noLink:true });
}
async function showLogs() {
  const target = window;
  if (!liveWindow(target)) return;
  const directory = path.join(config.home,'desktop','logs');
  try { await fs.access(directory); if (liveWindow(target)) await shell.openPath(directory); }
  catch (_) { if (liveWindow(target)) await dialog.showMessageBox(target,{ type:'info',title:'Crew 日志',message:'还没有可查看的日志',detail:'出现启动问题时，会在这里保留最近的记录。',buttons:['好'],noLink:true }); }
}
function workspaceMenu() {
  return [
    { id:'workspace-home', label:'工作区首页', accelerator:'CmdOrCtrl+Shift+O', click:menuAction(()=>showHome()) },
    { id:'workspace-new-tab', label:'打开工作区标签', accelerator:'CmdOrCtrl+T', click:menuAction(()=>showHome()) },
    { id:'workspace-new', label:'新建工作区…', accelerator:'CmdOrCtrl+N', click:menuAction(()=>showHome('new')) },
    { id:'workspace-open', label:'打开已有项目…', accelerator:'CmdOrCtrl+O', click:menuAction(()=>showHome('open')) },
    { id:'workspace-import', label:'导入工作区包…', click:menuAction(importWorkspace) },
    { id:'workspace-export', label:'导出当前工作区…', click:menuAction(()=>{const active=tabs?.state().tabs.find(t=>t.id===tabs.state().activeId);if(active?.project)return exportWorkspace(active.project);return dialog.showMessageBox(window,{message:'请先打开要导出的工作区',buttons:['好']});}) },
    { id:'workspace-handoff', label:'生成 Codex / VS Code 交接材料…', click:menuAction(()=>{const active=tabs?.state().tabs.find(t=>t.id===tabs.state().activeId);if(active?.project)return exportHandoff(active.project);return dialog.showMessageBox(window,{message:'请先打开要交接的工作区',buttons:['好']});}) },
    { id:'workspace-history', label:'查看已保存的完整历史', click:menuAction(()=>{const active=tabs?.state().tabs.find(t=>t.id===tabs.state().activeId);if(active?.project)return viewHistory(active.project);}) },
    { id:'workspace-settings', label:'当前工作区设置…', enabled:Boolean(tabs?.state().tabs.find(tab=>tab.id===tabs.state().activeId)?.project), click:menuAction(()=>{
      const active=tabs.state().tabs.find(tab=>tab.id===tabs.state().activeId);
      if(active?.project)return showHome('workspace-settings',active.project);
    }) },
    { id:'system-administrator',label:'系统管理员…',click:menuAction(()=>showHome('admin')) },
    { id:'tools-updates',label:'工具与更新…',click:menuAction(()=>showHome('tools')) },
    { id:'app-settings',label:'设置…',accelerator:'CmdOrCtrl+,',click:menuAction(()=>showHome('settings')) },
    { type:'separator' },
    { id:'window-back', label:'返回上一页', accelerator:'Alt+Left', click:menuAction(()=>tabs.back()) },
    { id:'window-reload', label:'刷新当前标签',accelerator:'CmdOrCtrl+R',click:menuAction(()=>tabs.reload()) },
    { id:'tab-next',label:'下一个标签',accelerator:'Ctrl+Tab',click:menuAction(()=>tabs.cycle(1)) },
    { id:'tab-previous',label:'上一个标签',accelerator:'Ctrl+Shift+Tab',click:menuAction(()=>tabs.cycle(-1)) },
    { type:'separator' },
    { id:'tab-close', label:'关闭当前标签', accelerator:'CmdOrCtrl+W', click:menuAction(()=>tabs.close(tabs.state().activeId)) },
    { id:'window-close',label:'收起到托盘',click:()=>{if(liveWindow())window.close();} },
    { id:'app-quit', label:'退出 Crew', accelerator:'CmdOrCtrl+Q', click:()=>app.quit() }
  ];
}
async function settled() {
  await new Promise(resolve => setTimeout(resolve, 350));
  await window.webContents.executeJavaScript('(async()=>{ await document.fonts.ready; await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))); return true; })()');
}
async function chromeEvidence() {
  return window.webContents.executeJavaScript(`(() => {
    const bar=document.getElementById('crew-desktop-titlebar'), content=document.getElementById('crew-desktop-content');
    const overlay=navigator.windowControlsOverlay;
    return { ready:document.documentElement.dataset.crewDesktopChromeReady==='true', page:document.documentElement.dataset.crewDesktopPage,
      title:document.querySelector('.crew-chrome-title')?.textContent, height:bar?.getBoundingClientRect().height,
      content:content?.getBoundingClientRect().toJSON(), borderRadius:content&&getComputedStyle(content).borderRadius,
      draggable:bar&&getComputedStyle(bar).getPropertyValue('-webkit-app-region'),
      buttons:[...document.querySelectorAll('#crew-desktop-titlebar button')].map(button=>button.getAttribute('aria-label')),
      overlayVisible:overlay?.visible, overlayRect:overlay?.getTitlebarAreaRect().toJSON(),
      nodeAvailable:typeof require!=='undefined', viewport:{width:innerWidth,height:innerHeight} };
  })()`);
}
async function waitForChrome(page) {
  for (let attempt = 0; attempt < 200; attempt++) {
    try {
      if (await window.webContents.executeJavaScript('document.documentElement.dataset.crewDesktopChromeReady === "true"' + (page ? ' && document.documentElement.dataset.crewDesktopPage === ' + JSON.stringify(page) : ''))) return;
    } catch (_) { /* The old document is being replaced by the recovery page. */ }
    await new Promise(resolve => setTimeout(resolve, 25));
  }
  throw new Error('窗口外壳没有完成加载');
}
async function visualEvidence(target, evidence) {
  evidence.visual = {};
  await window.webContents.executeJavaScript('document.getElementById("new").click(); document.getElementById("name").value="新的工作区"');
  await settled();
  const form = target.replace(/\.json$/i, '') + '-new.png';
  await fs.writeFile(form, (await window.webContents.capturePage()).toPNG());
  evidence.visual.createForm = form;
  const project = (await core.projects(backend.url))[0];
  if (!project) throw new Error('视觉 smoke 需要隔离示例工作区');
  await tabs.open(project);
  await settled();
  const view=tabs.activeView;
  const workspace = target.replace(/\.json$/i, '') + '-workspace.png';
  await view.webContents.executeJavaScript('document.fonts.ready');
  try { await fs.writeFile(workspace,(await view.webContents.capturePage(undefined,{stayHidden:true})).toPNG()); }
  catch(error) {
    // Hidden native child views need a compositor frame before capturePage.
    // DevTools requests that actual renderer frame without exposing a window.
    view.webContents.debugger.attach('1.3');
    try {
      const shot=await view.webContents.debugger.sendCommand('Page.captureScreenshot',{format:'png',fromSurface:true,captureBeyondViewport:false});
      await fs.writeFile(workspace,Buffer.from(shot.data,'base64'));evidence.visual.workspaceCapture='renderer-devtools';
    } finally {view.webContents.debugger.detach();}
  }
  evidence.visual.workspace = workspace;
  const preferences=view.webContents.getLastWebPreferences();
  evidence.visual.workspaceChrome={...(await chromeEvidence()),activeProject:project.name,tabs:tabs.state(),bounds:view.getBounds(),
    childURL:view.webContents.getURL(),nodeAvailable:await view.webContents.executeJavaScript('typeof require!=="undefined"'),
    bridgeAvailable:await view.webContents.executeJavaScript('typeof crewDesktop!=="undefined"'),preload:preferences.preload||null,
    contextIsolation:preferences.contextIsolation,sandbox:preferences.sandbox,nodeIntegration:preferences.nodeIntegration};
  if(!evidence.visual.workspaceChrome.ready||evidence.visual.workspaceChrome.nodeAvailable||evidence.visual.workspaceChrome.bridgeAvailable||preferences.preload)throw new Error('功能页外壳验收失败');
  const shellShot=target.replace(/\.json$/i,'')+'-tabs.png';
  await fs.writeFile(shellShot,(await window.webContents.capturePage()).toPNG());evidence.visual.tabsShell=shellShot;
  await window.webContents.executeJavaScript('document.querySelector(".crew-chrome-brand").click()');
  for(let attempt=0;attempt<100&&tabs.state().activeId!=='home';attempt++)await new Promise(resolve=>setTimeout(resolve,25));
  if(window.webContents.getURL()!==homePage||tabs.state().activeId!=='home')throw new Error('标题栏不能返回工作区首页');
  evidence.visual.homeNavigation = true;
  if (backend.owned) {
    await backend.stop();
    await tabs.open(project);tabs.reload();
    for(let attempt=0;attempt<200&&!tabs.state().tabs.find(tab=>tab.project===project.name)?.error;attempt++)await new Promise(resolve=>setTimeout(resolve,25));
    if(!tabs.state().tabs.find(tab=>tab.project===project.name)?.error)throw new Error('工作区连接失败没有显示恢复操作');
    await settled();
    const recovery=target.replace(/\.json$/i,'')+'-recovery.png';
    await fs.writeFile(recovery,(await window.webContents.capturePage()).toPNG());
    evidence.visual.recovery=recovery; evidence.visual.recoveryChrome=await chromeEvidence();
    await window.webContents.executeJavaScript('document.querySelector("#crew-tab-recovery button:last-child").click()');
    for(let attempt=0;attempt<100&&tabs.state().activeId!=='home';attempt++)await new Promise(resolve=>setTimeout(resolve,25));
    if(tabs.state().activeId!=='home')throw new Error('工作区恢复页不能回首页');
    evidence.visual.recoveryHomeNavigation=true;
  }
}

handle('desktop:bootstrap', async () => {
  const target = window;
  if(new URL(backend.url).port==='0')throw new Error(backend.startupError||'后台尚未连接，请查看系统管理员');
  const projects = await core.projectLocations(await core.projects(backend.url)), action = pendingAction, workspace = pendingWorkspace; pendingAction = null; pendingWorkspace = null;
  if (!liveWindow(target)) return null;
  for(const project of projects)tabs.updateProject(project);
  homeBootstrapped=true;
  return { projects, service: { url:backend.url, owned:backend.owned }, pendingAction:action, ...(workspace ? {pendingWorkspace:workspace} : {}) };
});
handle('desktop:admin-status',async()=>{
  const status=await administrator.snapshot();
  if (!liveWindow()) return status;
  if(backend?.startupError&&['healthy','degraded'].includes(status.state)&&status.url){
    const recovered=core.loopback(status.url);
    if(tabs&&tabs.state().tabs.every(tab=>tab.project===null)){
      try { await backend.validateService(recovered); await core.projects(recovered); }
      catch(error) {
        // Keep the file-backed health page usable while HTTP is still down.
        // An old or not-yet-ready URL must never replace the offline origin.
        return {...status,incidents:[...status.incidents,{at:new Date().toISOString(),level:'warning',message:'后台尚不能读取工作区：'+error.message}]};
      }
      if(liveWindow()&&backend.startupError&&tabs.state().tabs.every(tab=>tab.project===null)){
        tabs.reconnect(recovered);backend.url=recovered;backend.startupError=null;
      }
    }
  }
  return status;
});
handle('desktop:home-ready',()=>{
  homeBootstrapped=true;
  if(pendingAction){const action=pendingAction,project=pendingWorkspace;pendingAction=null;pendingWorkspace=null;window.webContents.send('desktop:home-action',{action,...(project?{project}:{})});}
  return {ok:true};
});
handle('desktop:admin-check',()=>administrator.request('check'));
handle('desktop:admin-restart',()=>administrator.request('recover'));
handle('desktop:admin-chat-snapshot',()=>operations.request('snapshot'));
function operationsMutation(action,body){
  if(launchSettings?.pending)throw Error('后台连接正在切换，完成后再发送消息或修改设置。');
  return operations.request(action,body);
}
handle('desktop:admin-chat-send',body=>operationsMutation('send',body));
handle('desktop:admin-chat-interrupt',()=>operations.request('interrupt'));
handle('desktop:admin-config-get',()=>operations.request('config-get'));
handle('desktop:admin-config-save',body=>operationsMutation('config-save',body));
handle('desktop:existing', async () => {
  const result = await dialog.showOpenDialog(window, { title: '选择工作区文件夹', buttonLabel: '打开工作区', properties: ['openDirectory'] });
  if (result.canceled) return null;
  const project=await storage.openDirectory(result.filePaths[0], backend.url, chooseHistory,storageProgress,workspaceDataRequest);
  if(project?.name)preparedWorkspaces.add(project.name);
  return project;
});
handle('desktop:existing-new',directory=>core.existingWorkspace(directory,backend.url,{newProject:true}));
handle('desktop:storage-import',importWorkspace);
handle('desktop:storage-export',name=>exportWorkspace(name));
handle('desktop:storage-activate',workspaceId=>activateWorkspace(workspaceId));
handle('desktop:parent', async () => {
  const result = await dialog.showOpenDialog(window, { title: '选择新工作区的保存位置', buttonLabel: '选择此位置', properties: ['openDirectory'] });
  if (result.canceled) return null;
  parentDirectory = result.filePaths[0];
  return parentDirectory;
});
handle('desktop:create', async name => {
  if (!parentDirectory) throw new Error('请先选择工作区的保存位置');
  const result=await core.newWorkspace(parentDirectory, name, backend.url);parentDirectory=null;return result;
});
handle('desktop:open', async name => {
  return {ok:await openProject(name)};
});
handle('desktop:tools-snapshot',()=>tools.snapshot());
handle('desktop:tools-check',()=>tools.check());
handle('desktop:tools-update',id=>tools.update(id));
handle('desktop:tools-cancel',id=>tools.cancel(id));
handle('desktop:tools-settings',settings=>tools.settings(settings));
handle('desktop:crew-update-snapshot',()=>crewUpdates.snapshot());
handle('desktop:crew-update-check',()=>crewUpdates.check());
handle('desktop:crew-update-apply',()=>{if(launchSettings?.pending)throw Error('正在切换后台连接，请等待完成。');return crewUpdates.update();});
handle('desktop:crew-update-cancel',()=>crewUpdates.cancel());
handle('desktop:crew-update-open',async kind=>{
  if(!['release','main'].includes(kind))throw new Error('不支持的版本链接');
  const online=crewUpdates.snapshot().online;
  if(kind==='release'&&!online?.release?.url)throw new Error('V2 尚未登记独立发布包，请查看 rebuild/v2 分支。');
  const target=kind==='release'?online.release.url:(online?.main?.url||'https://github.com/akitokunnn/Crew/tree/rebuild/v2');
  const url=new URL(target);
  if(url.protocol!=='https:'||url.hostname!=='github.com'||url.username||url.password||!(url.pathname==='/akitokunnn/Crew'||url.pathname.startsWith('/akitokunnn/Crew/')))throw new Error('版本链接不属于 Crew 官方仓库');
  await shell.openExternal(url.href);return {ok:true};
});
function settingsRequest(route, body, timeoutMs) {
  if (!backend?.url || new URL(backend.url).port === '0') throw Error(backend?.startupError || '后台尚未连接，请查看后台信息。');
  return core.request(backend.url, route, body, timeoutMs);
}
handle('desktop:prompts-get',query=>settingsRequest('/api/behavior-prompts?'+new URLSearchParams(query || {})));
handle('desktop:prompts-save',body=>settingsRequest('/api/behavior-prompts',body));
handle('desktop:settings-get',()=>operations.request('settings-get'));
handle('desktop:settings-save',body=>operationsMutation('settings-save',body));
handle('desktop:launch-settings-get',()=>launchSettings.get());
handle('desktop:launch-settings-validate',body=>launchSettings.validate(body));
handle('desktop:launch-settings-apply',body=>launchSettings.apply(body));
handle('desktop:launch-settings-pick',async body=>{
  if(!body || !['home','backend','python'].includes(body.kind) || typeof body.current!=='string')throw Error('请选择有效的目录或 Python 程序。');
  const file=body.kind==='python';
  const result=await dialog.showOpenDialog(window,{title:file?'选择 Python 程序':body.kind==='home'?'选择 Crew 数据目录':'选择后台目录（包含 crew_supervisor.py）',buttonLabel:'选择',
    ...(body.current&&path.isAbsolute(body.current)?{defaultPath:body.current}:{}),properties:[file?'openFile':'openDirectory']});
  return {path:result.canceled?null:result.filePaths[0]};
});
handle('desktop:settings-connection',()=>settingsRequest('/api/connections'));
handle('desktop:settings-connection-save',body=>settingsRequest('/api/connections/configure',body));
handle('desktop:settings-connection-action',body=>settingsRequest('/api/connections/action',body));
handle('desktop:settings-connection-preferences',body=>settingsRequest('/api/connections/preferences',body));
handle('desktop:settings-away',away=>{
  if (typeof away !== 'boolean') throw Error('离开状态必须是 true 或 false');
  return settingsRequest('/api/notifications/away', {away});
});
handle('desktop:storage-read',()=>settingsRequest('/api/storage',undefined,180000));
handle('desktop:storage-preview',body=>settingsRequest('/api/storage/preview',body,180000));
handle('desktop:storage-clean',body=>settingsRequest('/api/storage/clean',body,180000));
handle('desktop:storage-restore',body=>settingsRequest('/api/storage/restore',body,180000));
handle('desktop:storage-empty-trash',body=>settingsRequest('/api/storage/trash/empty',body,180000));
handle('desktop:storage-policy',body=>settingsRequest('/api/storage/policy',body));
handle('desktop:settings-ui',async()=>{
  // Only trusted home can load these fixed, packaged application assets.
  const directory = path.join(config.helperBackendDir, 'crew', 'web');
  return injectAssets(directory, [
    ['executeJavaScript','launch-settings-ui.js'], ['insertCSS','operations-ui.css'],
    ['executeJavaScript','operations-ui.js'], ['insertCSS','settings-ui.css'],
    ['insertCSS','behavior-prompts-ui.css'], ['executeJavaScript','behavior-prompts-ui.js'],
    ['executeJavaScript','settings-ui.js'], ['executeJavaScript','connections-ui.js'],
    ['insertCSS','storage-ui.css'], ['executeJavaScript','storage-ui.js']
  ]);
});
handle('desktop:workspace-ui',async()=>{
  const directory=path.join(config.helperBackendDir,'crew','web');
  return injectAssets(directory, [
    ['insertCSS','workspace-settings-ui.css'], ['executeJavaScript','workspace-settings-ui.js'],
    ['insertCSS','maintenance-ui.css'], ['executeJavaScript','maintenance-ui.js']
  ]);
});
// Workspace cleanup from the settings dialog: only these maintenance routes are reachable.
handle('desktop:workspace-maintenance',async request=>{
  const route=request?.route, body=request?.body;
  const reads=['/api/maintenance/jobs?','/api/maintenance/archive?'], writes=['/api/maintenance/cleanup/preview','/api/maintenance/cleanup'];
  if(typeof route!=='string'||!(body===undefined?reads.some(prefix=>route.startsWith(prefix)):writes.includes(route)))throw Error('不支持的工作区维护请求。');
  return settingsRequest(route,body,180000);
});
handle('desktop:workspace-get',name=>{
  if(typeof name!=='string'||!name.trim())throw Error('请选择工作区。');
  return settingsRequest('/api/project/settings?project='+encodeURIComponent(name));
});
handle('desktop:workspace-update',async body=>{
  const result=await settingsRequest('/api/project/update',body,180000);
  if(liveWindow())tabs.updateProject(result.project); return result;
});
handle('desktop:workspace-remove',async body=>{
  const result=await settingsRequest('/api/project/remove',body,180000);
  if(result.removed&&liveWindow())tabs.closeProject(result.project); return result;
});
handle('desktop:workspace-directory',async current=>{
  if(typeof current!=='string')throw Error('工作目录必须是路径文本。');
  const result=await dialog.showOpenDialog(window,{title:'选择工作目录',buttonLabel:'选择此目录',defaultPath:current,properties:['openDirectory']});
  return result.canceled ? null : result.filePaths[0];
});
handle('desktop:ready', async info => {
  if (!config.smoke || smokeStarted) return;
  const rendererReadyAt = Date.now();
  smokeStarted = true;
  try {
    const target = path.resolve(config.smokeOutput);
    await fs.mkdir(path.dirname(target), { recursive: true });
    const screenshot = target.replace(/\.json$/i, '') + '.png';
    let screenshotError = null;
    for (let attempt = 0; attempt < 3; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 150 * (attempt + 1)));
      try { const capture = await window.webContents.capturePage(); await fs.writeFile(screenshot, capture.toPNG()); screenshotError = null; break; }
      catch (error) { screenshotError = error.message || String(error); }
    }
    const preferences = window.webContents.getLastWebPreferences();
    const evidence = { window: !window.isDestroyed(), url: window.webContents.getURL(), service: { url: backend.url, owned: backend.owned },
      timing:{process_created_at:process.getCreationTime?.() ?? null,renderer_ready_at:rendererReadyAt},
      desktopVersion:app.getVersion(),
      contextIsolation: preferences.contextIsolation, nodeIntegration: preferences.nodeIntegration, sandbox: preferences.sandbox,
      renderer: info, chrome: await chromeEvidence(), menuBarVisible: window.isMenuBarVisible(), windowOptions: chrome.windowOptions(),
      screenshot: screenshotError ? null : screenshot, screenshotError, electron: process.versions.electron, home: config.home };
    if (config.smokeVisual) await visualEvidence(target, evidence);
    await backend.stop();
    evidence.ownedServiceExited = !backend.owned || backend.child.exitCode !== null;
    if (!backend.owned) evidence.borrowedServiceStillAvailable = Array.isArray(await core.projects(backend.url));
    await fs.writeFile(target, JSON.stringify(evidence, null, 2));
    app.quit();
  } catch (error) { console.error(error); app.exit(1); }
});

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', showWindow);
  app.whenReady().then(async () => {
    if (quitting) return;
    try {
      if (app.isPackaged) {
        config.helperBackendDir=await startupStep(core.materializeBackend(config.bundledBackendDir,config.home));
        if (quitting) return;
        if(config.backendSource==='bundled')config.backendDir=config.helperBackendDir;
      }
      backend = new core.Backend(config);
      try { await startupStep(backend.start()); }
      catch (error) {
        if (quitting) return;
        if(config.smoke)throw error;
        // The administrator must remain reachable even when no workspace can
        // connect. Keep the shell and its file-backed health page available.
        console.error('Crew backend unavailable:',error);
        backend.startupError=error.message;
        let candidate=config.serviceUrl;
        if(!candidate)try{candidate=JSON.parse(await fs.readFile(path.join(config.home,'web.json'),'utf8')).url;}catch(_){}
        try{backend.url=error.code==='CREW_PRODUCT_GENERATION'?'http://127.0.0.1:0':core.loopback(candidate);}catch(_){backend.url='http://127.0.0.1:0';}
        pendingAction='admin';
      }
      if (quitting) return;
      tools=new ToolUpdates(config,{onChange:state=>{if(liveWindow())window.webContents.send('desktop:tools-state',state);}});
      config.desktopVersion=app.getVersion();
      crewUpdates=new CrewUpdates(config,backend,{onChange:state=>{if(liveWindow())window.webContents.send('desktop:crew-update-state',state);},onRestart:async health=>{
        backend.startupError=null;
        if(tabs&&tabs.origin!==backend.url)throw new Error('更新后服务地址变化；工作区标签已保留，请重新打开 Crew。');
        publishTabs();
      }});
      launchSettings=new LaunchSettings(config,{backend,updates:crewUpdates,operations,bundledBackend:config.bundledBackendDir,onApplied:async()=>{
        if (quitting) return;
        const portable=process.env.PORTABLE_EXECUTABLE_FILE;
        try{
          if(portable && !(await fs.stat(portable)).isFile())throw Error('便携程序文件不可用。');
          if (quitting) return;
          app.relaunch({args:relaunchArgs(process.argv.slice(1),config.launchSettingsPath),...(portable?{execPath:portable}:{})});
        }
        catch(error){throw Error('启动设置已保存，但重新启动失败：'+error.message+'；请手动重新打开 Crew。');}
        setTimeout(()=>app.quit(),250);
      }});
      await startupStep(tools.initialize());
      if (quitting) return;
      if(!config.smoke&&!config.isolated&&!backend.startupError)tools.start();
      window = new BrowserWindow({ width: 1220, height: 850, minWidth: 820, minHeight: 600,
        show: false, title: config.instanceName, icon: path.join(__dirname, '../assets/crew.png'), ...chrome.windowOptions(),
        webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true,
          additionalArguments: ['--crew-desktop-config=' + encodeURIComponent(JSON.stringify({ home: homePage, origin: backend.url, error:errorPage, instanceName:config.instanceName }))] } });
      tabs=new WorkspaceTabs(window,{origin:backend.url,
        createView:()=>new WebContentsView({webPreferences:{contextIsolation:true,nodeIntegration:false,sandbox:true,webSecurity:true,...(config.smoke?{backgroundThrottling:false}:{})}}),
        onChange:state=>publishTabs(state),onHome:()=>{},external,openProject,workspaceSettings:name=>{void showHome('workspace-settings',name).catch(actionError);},
        onTitleChange:name=>{void core.projects(backend.url).then(items=>{if(!liveWindow())return;const item=items.find(project=>project.name===name);if(item)tabs.updateProject(item);}).catch(error=>console.error('Crew workspace metadata refresh failed:',error));},
        readActivity:names=>core.request(backend.url,'/api/workspaces/activity?'+new URLSearchParams(names.map(name=>['project',name])).toString(),undefined,4000),
        onKeyboard:keyboard,onNavigationError:actionError});
      activityPreview=new ActivityPreview(window,{
        createView:()=>new WebContentsView({webPreferences:{preload:path.join(__dirname,'activity-preview-preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true,webSecurity:true,backgroundThrottling:false}}),
        getTab:id=>tabs.state().tabs.find(tab=>tab.id===id&&tab.project),onSelect:id=>tabs.select(id)});
      window.on('blur',()=>activityPreview.hide());window.on('resize',()=>activityPreview.hide());
      tabs.setBounds({x:7,y:75,width:1206,height:768});
      window.once('ready-to-show', () => { if (!config.smoke && liveWindow()) window.show(); });
      for (const event of ['maximize', 'unmaximize', 'focus', 'blur']) window.on(event, () => {
        if (liveWindow()) window.webContents.send('desktop:window-state', currentWindowState());
      });
      installPermissions(window.webContents.session, {homeContents:window.webContents, homePage,
        workspaceContents:contents=>[...tabs.entries.values()].some(tab=>tab.view.webContents===contents),
        workspaceOrigin:()=>new URL(tabs.origin).origin,
        requestPermission:async label=>{
          const result=await dialog.showMessageBox(window,{type:'question',title:'Crew 权限申请',message:'允许 Crew 使用'+label+'？',
            detail:'由你正在使用的 Crew 功能发起。允许后对当前页面生效，关闭应用后重新申请。',
            buttons:['允许','暂不允许'],defaultId:0,cancelId:1,noLink:true});
          return result.response===0;
        }});
      window.webContents.on('will-attach-webview', event => event.preventDefault());
      window.webContents.on('will-navigate', (event, url) => {
        if ([homePage,errorPage].includes(url)) return;
        event.preventDefault(); external(url);
      });
      window.webContents.on('will-redirect',(event)=>event.preventDefault());
      window.webContents.on('before-input-event',keyboard);
      window.webContents.on('did-start-loading',()=>{homeBootstrapped=false;});
      window.webContents.on('did-fail-load', (_event, code, _description, url, mainFrame) => {
        if (!liveWindow() || !mainFrame || code === -3 || url!==homePage) return;
        void window.loadURL(errorPage).catch(error => console.error('Crew recovery page failed:',error));
      });
      window.webContents.setWindowOpenHandler(({ url }) => { external(url); return { action: 'deny' }; });
      window.webContents.on('page-title-updated', event => {
        event.preventDefault();
        publishTabs();
      });
      window.on('close', event => { if (!quitting) { event.preventDefault(); window.hide(); } });
      window.on('closed', () => { window = null; });
      Menu.setApplicationMenu(Menu.buildFromTemplate([
        { label:'工作区', submenu:workspaceMenu() },
        { label:'编辑', submenu:[{ role:'undo',label:'撤销' },{ role:'redo',label:'重做' },{ type:'separator' },{ role:'cut',label:'剪切' },{ role:'copy',label:'复制' },{ role:'paste',label:'粘贴' },{ role:'selectAll',label:'全选' }] },
        { label:'帮助', submenu:[{ id:'app-about',label:'关于 Crew',click:menuAction(aboutCrew) },{ id:'app-logs',label:'查看日志文件夹',click:menuAction(showLogs) }] }
      ]));
      window.setAutoHideMenuBar(true); window.setMenuBarVisibility(false);
      windowMenu = Menu.buildFromTemplate([...workspaceMenu(),{ type:'separator' },{ label:'关于 Crew',click:menuAction(aboutCrew) },{ label:'查看日志文件夹',click:menuAction(showLogs) }]);
      if (!config.smoke) {
        const icon = nativeImage.createFromPath(path.join(__dirname, '../assets/crew-tray.png'));
        tray = new Tray(icon);
        tray.setToolTip(config.instanceName+' · 关闭窗口后继续运行');
        tray.setContextMenu(Menu.buildFromTemplate([{ label: '打开 Crew', click: showWindow },
          { label: '工作区首页', click:menuAction(()=>showHome()) },{ label:'新建工作区…',click:menuAction(()=>showHome('new')) },{ label:'打开已有项目…',click:menuAction(()=>showHome('open')) }, { type: 'separator' }, { label:'关于 Crew',click:menuAction(aboutCrew) },{ label: '退出 Crew', click: () => app.quit() }]));
        tray.on('double-click', showWindow);
      }
      await window.loadURL(homePage);
    } catch (error) {
      if (quitting) return;
      console.error(error);
      quitting = true;
      await cleanupResources();
      if (!config.smoke) {
        const directory=path.join(config.home,'desktop','logs');
        try { await fs.mkdir(directory,{recursive:true}); await fs.writeFile(path.join(directory,'app.log'),new Date().toISOString()+'\n'+(error.stack||String(error))); } catch (_) { /* A log failure must not hide the recovery action. */ }
        const result=await dialog.showMessageBox({type:'error',title:'Crew',message:'暂时无法打开 Crew',
          detail:'请确认 Crew 可以连接，且本机已安装 Python 3，然后重试。可在工作区的日志目录查看详细记录。',
          buttons:['重试','关闭'],defaultId:0,cancelId:1,noLink:true});
        if(result.response===0)app.relaunch();
      }
      app.exit(1);
    }
  });
  app.on('before-quit', event => {
    if (finishedQuit) return;
    event.preventDefault();
    if (quitting) return;
    quitting = true;
    for (const [name, resource] of [['preview',activityPreview],['tabs',tabs]]) {
      try { resource?.dispose(); }
      catch (error) { console.error('Crew shutdown failed (' + name + '):', error); }
    }
    void cleanupResources().then(() => {
      finishedQuit = true;
      if (tray && !tray.isDestroyed()) tray.destroy();
      tray = null;
      app.quit();
    }).catch(error => { console.error('Crew shutdown failed:', error); app.exit(1); });
  });
}
