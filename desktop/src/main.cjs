'use strict';
const { app, BrowserWindow, WebContentsView, ipcMain, dialog, Menu, Tray, nativeImage, shell } = require('electron');
const fs = require('node:fs/promises');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const core = require('./core.cjs');
const chrome = require('./chrome.cjs');
const { WorkspaceTabs } = require('./tabs.cjs');
const { ToolUpdates } = require('./tool-updates.cjs');
const { CrewUpdates } = require('./crew-updates.cjs');
const { Administrator } = require('./admin.cjs');
const { OperationsClient } = require('./operations.cjs');
const { LaunchSettings, relaunchArgs } = require('./launch-settings.cjs');
const config = core.options(process.argv.slice(1),{launchSettingsPath:path.join(app.getPath('appData'),'Crew','launch-settings.json')});
const administrator = new Administrator(config.home);
const operations = new OperationsClient(config);
app.setName('Crew');
if (process.platform === 'win32') app.setAppUserModelId('local.crew.desktop');
config.bundledBackendDir = app.isPackaged ? path.join(process.resourcesPath, 'backend') : path.resolve(__dirname, '../../plugin');
config.backendDir = config.backendDir || config.bundledBackendDir;
config.helperBackendDir = config.bundledBackendDir;
require('node:fs').mkdirSync(config.userData,{recursive:true});
app.setPath('userData', config.userData);
const homePage = pathToFileURL(path.join(__dirname, 'home.html')).href;
const errorPage = pathToFileURL(path.join(__dirname, 'error.html')).href;
let window, tray, backend, tabs, tools, crewUpdates, launchSettings, windowMenu, quitting = false, finishedQuit = false, parentDirectory = null, smokeStarted = false, pendingAction = null, pendingWorkspace = null, homeBootstrapped = false;

function trusted(event) {
  if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame || event.senderFrame.url !== homePage) {
    throw new Error('桌面操作只允许来自 Crew 工作区首页');
  }
}
function handle(channel, callback) { ipcMain.handle(channel, async (event, ...args) => { trusted(event); return callback(...args); }); }
function trustedChrome(event) {
  if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame ||
      ![homePage,errorPage].includes(event.senderFrame.url)) throw new Error('窗口操作只允许来自 Crew 窗口');
}
function currentWindowState() { return { maximized:window.isMaximized(), focused:window.isFocused(), canGoBack:(tabs?.activeView?.webContents||window.webContents).navigationHistory.canGoBack() }; }
function publishTabs(state=tabs?.state()) {
  if (!window || window.webContents.isDestroyed()) return;
  if (state) window.webContents.send('desktop:tabs-state',state);
  window.webContents.send('desktop:window-state',currentWindowState());
  const active=state?.tabs.find(tab=>tab.id===state.activeId);
  window.setTitle(active?.project ? active.name+' — Crew' : 'Crew');
  for (const menu of [windowMenu, Menu.getApplicationMenu()]) {
    const item = menu?.getMenuItemById('workspace-settings'); if (item) item.enabled = Boolean(active?.project);
  }
}
ipcMain.handle('desktop:chrome-ready', async event => {
  trustedChrome(event);
  await window.webContents.insertCSS(await fs.readFile(path.join(__dirname, 'chrome.css'), 'utf8'), { cssOrigin: 'author' });
  return { ...currentWindowState(),tabs:tabs?.state() };
});
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
  if (command === 'admin') return showHome('admin');
  if (command === 'settings') return showHome('settings');
  if (command === 'back') return tabs.back();
  if (command === 'retry') return event.senderFrame.url===errorPage ? window.loadURL(homePage) : tabs.reload();
  if (command === 'tab-select') return tabs.select(value);
  if (command === 'tab-close') return tabs.close(value);
  if (command === 'menu') return new Promise(resolve => windowMenu.popup({ window, callback: resolve }));
  throw new Error('不支持的窗口操作');
});
function external(url) {
  try { const parsed = new URL(url); if (['http:', 'https:'].includes(parsed.protocol) && !parsed.username && !parsed.password) void shell.openExternal(parsed.href); }
  catch (_) { /* Invalid external link: keep the application on its current page. */ }
}
async function showHome(action = null, workspace = null) {
  if (!window) return;
  pendingWorkspace = action === 'workspace-settings' ? workspace : null;
  if(action==='new')parentDirectory=null;
  if(window.webContents.getURL()!==homePage){homeBootstrapped=false;pendingAction=action;await window.loadURL(homePage);}
  tabs?.select('home');
  if(action && homeBootstrapped){window.webContents.send('desktop:home-action',{action,...(workspace ? {project:workspace} : {})});pendingWorkspace=null;}
  else if(action)pendingAction=action;
  if (!config.smoke) { window.show(); window.focus(); }
}
function actionError(error) {
  console.error('Crew action failed:',error);
  if(!config.smoke)void dialog.showMessageBox(window,{type:'error',title:'Crew',message:'这个操作暂时没有完成',detail:error.message,buttons:['好'],noLink:true})
    .catch(failure=>console.error('Crew error dialog failed:',failure));
}
function menuAction(action) { return ()=>{ Promise.resolve().then(action).catch(actionError); }; }
async function openProject(name) {
  if(new URL(backend.url).port==='0')throw new Error(backend.startupError||'后台尚未连接，请查看系统管理员');
  const project=(await core.projects(backend.url)).find(item=>item.name===name);
  if(!project)throw new Error('工作区不存在，请刷新列表');
  return tabs.open(project);
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
  const directory = path.join(config.home,'desktop','logs');
  try { await fs.access(directory); await shell.openPath(directory); }
  catch (_) { await dialog.showMessageBox(window,{ type:'info',title:'Crew 日志',message:'还没有可查看的日志',detail:'出现启动问题时，会在这里保留最近的记录。',buttons:['好'],noLink:true }); }
}
function workspaceMenu() {
  return [
    { id:'workspace-home', label:'工作区首页', accelerator:'CmdOrCtrl+Shift+O', click:menuAction(()=>showHome()) },
    { id:'workspace-new-tab', label:'打开工作区标签', accelerator:'CmdOrCtrl+T', click:menuAction(()=>showHome()) },
    { id:'workspace-new', label:'新建工作区…', accelerator:'CmdOrCtrl+N', click:menuAction(()=>showHome('new')) },
    { id:'workspace-open', label:'打开已有项目…', accelerator:'CmdOrCtrl+O', click:menuAction(()=>showHome('open')) },
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
    { id:'window-close',label:'收起到托盘',click:()=>window.close() },
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
  if(new URL(backend.url).port==='0')throw new Error(backend.startupError||'后台尚未连接，请查看系统管理员');
  const projects = await core.projects(backend.url), action = pendingAction, workspace = pendingWorkspace; pendingAction = null; pendingWorkspace = null;
  homeBootstrapped=true;
  return { projects, service: { url:backend.url, owned:backend.owned }, pendingAction:action, ...(workspace ? {pendingWorkspace:workspace} : {}) };
});
handle('desktop:admin-status',async()=>{
  const status=await administrator.snapshot();
  if(backend?.startupError&&['healthy','degraded'].includes(status.state)&&status.url){
    const recovered=core.loopback(status.url);
    if(tabs&&tabs.state().tabs.every(tab=>tab.project===null)){
      try { await core.projects(recovered); }
      catch(error) {
        // Keep the file-backed health page usable while HTTP is still down.
        // An old or not-yet-ready URL must never replace the offline origin.
        return {...status,incidents:[...status.incidents,{at:new Date().toISOString(),level:'warning',message:'后台尚不能读取工作区：'+error.message}]};
      }
      if(backend.startupError&&tabs.state().tabs.every(tab=>tab.project===null)){
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
  return core.existingWorkspace(result.filePaths[0], backend.url);
});
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
  await openProject(name);
  return { ok: true };
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
  const target=kind==='release'?(online?.release?.url||'https://github.com/akitokunnn/Crew/releases'):(online?.main?.url||'https://github.com/akitokunnn/Crew');
  const url=new URL(target);
  if(url.protocol!=='https:'||url.hostname!=='github.com'||url.username||url.password||!(url.pathname==='/akitokunnn/Crew'||url.pathname.startsWith('/akitokunnn/Crew/')))throw new Error('版本链接不属于 Crew 官方仓库');
  await shell.openExternal(url.href);return {ok:true};
});
function settingsRequest(route, body, timeoutMs) {
  if (!backend?.url || new URL(backend.url).port === '0') throw Error(backend?.startupError || '后台尚未连接，请查看后台信息。');
  return core.request(backend.url, route, body, timeoutMs);
}
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
handle('desktop:settings-away',away=>{
  if (typeof away !== 'boolean') throw Error('离开状态必须是 true 或 false');
  return settingsRequest('/api/notifications/away', {away});
});
handle('desktop:settings-ui',async()=>{
  // Only trusted home can load these fixed, packaged application assets.
  const directory = path.join(config.helperBackendDir, 'crew', 'web');
  await window.webContents.executeJavaScript(await fs.readFile(path.join(directory, 'launch-settings-ui.js'),'utf8'));
  await window.webContents.insertCSS(await fs.readFile(path.join(directory, 'operations-ui.css'),'utf8'));
  await window.webContents.executeJavaScript(await fs.readFile(path.join(directory, 'operations-ui.js'),'utf8'));
  await window.webContents.insertCSS(await fs.readFile(path.join(directory, 'settings-ui.css'),'utf8'));
  await window.webContents.executeJavaScript(await fs.readFile(path.join(directory, 'settings-ui.js'),'utf8'));
  await window.webContents.executeJavaScript(await fs.readFile(path.join(directory, 'connections-ui.js'),'utf8'));
  return {ok:true};
});
handle('desktop:workspace-ui',async()=>{
  const directory=path.join(config.helperBackendDir,'crew','web');
  await window.webContents.insertCSS(await fs.readFile(path.join(directory,'workspace-settings-ui.css'),'utf8'));
  await window.webContents.executeJavaScript(await fs.readFile(path.join(directory,'workspace-settings-ui.js'),'utf8'));
  await window.webContents.insertCSS(await fs.readFile(path.join(directory,'maintenance-ui.css'),'utf8'));
  await window.webContents.executeJavaScript(await fs.readFile(path.join(directory,'maintenance-ui.js'),'utf8'));
  return {ok:true};
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
  tabs.updateProject(result.project); return result;
});
handle('desktop:workspace-remove',async body=>{
  const result=await settingsRequest('/api/project/remove',body,180000);
  if(result.removed)tabs.closeProject(result.project); return result;
});
handle('desktop:workspace-directory',async current=>{
  if(typeof current!=='string')throw Error('工作目录必须是路径文本。');
  const result=await dialog.showOpenDialog(window,{title:'选择工作目录',buttonLabel:'选择此目录',defaultPath:current,properties:['openDirectory']});
  return result.canceled ? null : result.filePaths[0];
});
handle('desktop:ready', async info => {
  if (!config.smoke || smokeStarted) return;
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
  app.on('second-instance', () => { if (window) { window.show(); window.focus(); } });
  app.whenReady().then(async () => {
    try {
      if (app.isPackaged) {
        config.helperBackendDir=await core.materializeBackend(config.bundledBackendDir,config.home);
        if(config.backendSource==='bundled')config.backendDir=config.helperBackendDir;
      }
      backend = new core.Backend(config);
      try { await backend.start(); }
      catch (error) {
        if(config.smoke)throw error;
        // The administrator must remain reachable even when no workspace can
        // connect. Keep the shell and its file-backed health page available.
        console.error('Crew backend unavailable:',error);
        backend.startupError=error.message;
        let candidate=config.serviceUrl;
        if(!candidate)try{candidate=JSON.parse(await fs.readFile(path.join(config.home,'web.json'),'utf8')).url;}catch(_){}
        try{backend.url=core.loopback(candidate);}catch(_){backend.url='http://127.0.0.1:0';}
        pendingAction='admin';
      }
      tools=new ToolUpdates(config,{onChange:state=>{if(window&&!window.webContents.isDestroyed())window.webContents.send('desktop:tools-state',state);}});
      config.desktopVersion=app.getVersion();
      crewUpdates=new CrewUpdates(config,backend,{onChange:state=>{if(window&&!window.webContents.isDestroyed())window.webContents.send('desktop:crew-update-state',state);},onRestart:async health=>{
        backend.startupError=null;
        if(tabs&&tabs.origin!==backend.url)throw new Error('更新后服务地址变化；工作区标签已保留，请重新打开 Crew。');
        publishTabs();
      }});
      launchSettings=new LaunchSettings(config,{backend,updates:crewUpdates,operations,bundledBackend:config.bundledBackendDir,onApplied:async()=>{
        const portable=process.env.PORTABLE_EXECUTABLE_FILE;
        try{
          if(portable && !(await fs.stat(portable)).isFile())throw Error('便携程序文件不可用。');
          app.relaunch({args:relaunchArgs(process.argv.slice(1),config.launchSettingsPath),...(portable?{execPath:portable}:{})});
        }
        catch(error){throw Error('启动设置已保存，但重新启动失败：'+error.message+'；请手动重新打开 Crew。');}
        setTimeout(()=>app.quit(),250);
      }});
      await tools.initialize();
      if(!config.smoke&&!config.isolated&&!backend.startupError)tools.start();
      window = new BrowserWindow({ width: 1220, height: 850, minWidth: 820, minHeight: 600,
        show: false, title: 'Crew', icon: path.join(__dirname, '../assets/crew.png'), ...chrome.windowOptions(),
        webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true,
          additionalArguments: ['--crew-desktop-config=' + encodeURIComponent(JSON.stringify({ home: homePage, origin: backend.url, error:errorPage }))] } });
      tabs=new WorkspaceTabs(window,{origin:backend.url,
        createView:()=>new WebContentsView({webPreferences:{contextIsolation:true,nodeIntegration:false,sandbox:true,webSecurity:true,...(config.smoke?{backgroundThrottling:false}:{})}}),
        onChange:state=>publishTabs(state),onHome:()=>{},external,openProject,workspaceSettings:name=>{void showHome('workspace-settings',name).catch(actionError);},onKeyboard:keyboard,onNavigationError:actionError});
      tabs.setBounds({x:7,y:75,width:1206,height:768});
      window.once('ready-to-show', () => { if (!config.smoke) window.show(); });
      for (const event of ['maximize', 'unmaximize', 'focus', 'blur']) window.on(event, () => {
        if (!window.webContents.isDestroyed()) window.webContents.send('desktop:window-state', currentWindowState());
      });
      window.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
      window.webContents.session.setPermissionCheckHandler(() => false);
      window.webContents.on('will-attach-webview', event => event.preventDefault());
      window.webContents.on('will-navigate', (event, url) => {
        if ([homePage,errorPage].includes(url)) return;
        event.preventDefault(); external(url);
      });
      window.webContents.on('will-redirect',(event)=>event.preventDefault());
      window.webContents.on('before-input-event',keyboard);
      window.webContents.on('did-start-loading',()=>{homeBootstrapped=false;});
      window.webContents.on('did-fail-load', (_event, code, _description, url, mainFrame) => {
        if (!mainFrame || code === -3 || url!==homePage) return;
        void window.loadURL(errorPage).catch(error => console.error('Crew recovery page failed:',error));
      });
      window.webContents.setWindowOpenHandler(({ url }) => { external(url); return { action: 'deny' }; });
      window.webContents.on('page-title-updated', event => {
        event.preventDefault();
        publishTabs();
      });
      window.on('close', event => { if (!quitting) { event.preventDefault(); window.hide(); } });
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
        tray.setToolTip('Crew · 关闭窗口后继续运行');
        tray.setContextMenu(Menu.buildFromTemplate([{ label: '打开 Crew', click: () => { window.show(); window.focus(); } },
          { label: '工作区首页', click:menuAction(()=>showHome()) },{ label:'新建工作区…',click:menuAction(()=>showHome('new')) },{ label:'打开已有项目…',click:menuAction(()=>showHome('open')) }, { type: 'separator' }, { label:'关于 Crew',click:menuAction(aboutCrew) },{ label: '退出 Crew', click: () => app.quit() }]));
        tray.on('double-click', () => { window.show(); window.focus(); });
      }
      await window.loadURL(homePage);
    } catch (error) {
      console.error(error);
      if (backend) await backend.stop();
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
    tabs?.dispose();
    Promise.resolve(crewUpdates?.close()).then(()=>tools?.close()).then(()=>backend?.stop()).finally(() => { finishedQuit = true; tray?.destroy(); app.quit(); });
  });
}
