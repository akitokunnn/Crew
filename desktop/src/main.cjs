'use strict';
const { app, BrowserWindow, ipcMain, dialog, Menu, Tray, nativeImage, shell } = require('electron');
const fs = require('node:fs/promises');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const core = require('./core.cjs');
const config = core.options(process.argv.slice(1));
config.backendDir = config.backendDir || (app.isPackaged ? path.join(process.resourcesPath, 'backend') : path.resolve(__dirname, '../../plugin'));
app.setPath('userData', path.join(config.home, 'desktop'));
const homePage = pathToFileURL(path.join(__dirname, 'home.html')).href;
let window, tray, backend, quitting = false, finishedQuit = false, parentDirectory = null, smokeStarted = false;

function trusted(event) {
  if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame || event.senderFrame.url !== homePage) {
    throw new Error('桌面操作只允许来自 Crew 工作区首页');
  }
}
function handle(channel, callback) { ipcMain.handle(channel, async (event, ...args) => { trusted(event); return callback(...args); }); }
function external(url) {
  try { const parsed = new URL(url); if (['http:', 'https:'].includes(parsed.protocol) && !parsed.username && !parsed.password) void shell.openExternal(parsed.href); }
  catch (_) { /* Invalid external link: keep the application on its current page. */ }
}
function showHome() { if (!window) return; void window.loadURL(homePage); window.show(); window.focus(); }

handle('desktop:bootstrap', async () => ({ projects: await core.projects(backend.url), service: { url: backend.url, owned: backend.owned } }));
handle('desktop:existing', async () => {
  const result = await dialog.showOpenDialog(window, { title: '选择已有 Git 工作区', buttonLabel: '打开工作区', properties: ['openDirectory'] });
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
  return core.newWorkspace(parentDirectory, name, backend.url);
});
handle('desktop:open', async name => {
  const project = (await core.projects(backend.url)).find(item => item.name === name);
  if (!project) throw new Error('工作区不存在，请刷新列表');
  await window.loadURL(backend.url + '/?project=' + encodeURIComponent(project.name));
  return { ok: true };
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
      renderer: info, screenshot: screenshotError ? null : screenshot, screenshotError, electron: process.versions.electron, home: config.home };
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
      if (app.isPackaged) config.backendDir = await core.materializeBackend(config.backendDir, config.home);
      backend = new core.Backend(config);
      await backend.start();
      window = new BrowserWindow({ width: 1220, height: 850, minWidth: 820, minHeight: 600,
        show: !config.smoke, title: 'Crew', backgroundColor: '#f4f3ef',
        webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true } });
      window.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
      window.webContents.session.setPermissionCheckHandler(() => false);
      window.webContents.on('will-attach-webview', event => event.preventDefault());
      window.webContents.on('will-navigate', (event, url) => {
        if (url === homePage || new URL(url).origin === backend.url) return;
        event.preventDefault(); external(url);
      });
      window.webContents.setWindowOpenHandler(({ url }) => { external(url); return { action: 'deny' }; });
      window.on('close', event => { if (!quitting) { event.preventDefault(); window.hide(); } });
      Menu.setApplicationMenu(Menu.buildFromTemplate([{ label: 'Crew', submenu: [
        { label: '工作区首页', accelerator: 'CmdOrCtrl+Shift+O', click: showHome },
        { role: 'reload', label: '刷新' }, { type: 'separator' },
        { label: '退出 Crew', accelerator: 'CmdOrCtrl+Q', click: () => app.quit() } ] },
        { label: '编辑', submenu: [{ role: 'undo' }, { role: 'redo' }, { type: 'separator' }, { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' }] }]));
      if (!config.smoke) {
        const icon = nativeImage.createFromPath(path.join(__dirname, '../assets/crew.png'));
        tray = new Tray(icon);
        tray.setToolTip('Crew · 关闭窗口后继续运行');
        tray.setContextMenu(Menu.buildFromTemplate([{ label: '打开 Crew', click: () => { window.show(); window.focus(); } },
          { label: '工作区首页', click: showHome }, { type: 'separator' }, { label: '退出 Crew', click: () => app.quit() }]));
        tray.on('double-click', () => { window.show(); window.focus(); });
      }
      await window.loadURL(homePage);
    } catch (error) {
      console.error(error);
      if (!config.smoke) dialog.showErrorBox('Crew 启动失败', error.message + '\n请确认已安装 Python 3 和 Git。');
      if (backend) await backend.stop();
      app.exit(1);
    }
  });
  app.on('before-quit', event => {
    if (finishedQuit) return;
    event.preventDefault();
    if (quitting) return;
    quitting = true;
    Promise.resolve(backend?.stop()).finally(() => { finishedQuit = true; tray?.destroy(); app.quit(); });
  });
}
