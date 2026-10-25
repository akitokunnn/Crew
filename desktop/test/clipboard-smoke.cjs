'use strict';
// Run with Electron. Queries real native permissions; copy delivery is stubbed
// so this regression check never reads or overwrites the user's clipboard.
const {app,BrowserWindow}=require('electron');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),http=require('node:http');
const {pathToFileURL}=require('node:url');
const {installPermissions}=require('../src/permissions.cjs');
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'crew-clipboard-test-'));
app.setPath('userData',temporary);app.setPath('sessionData',temporary);
const file=path.join(temporary,'home.html');fs.writeFileSync(file,'<div id="host"></div>');
const homePage=pathToFileURL(file).href;
const server=http.createServer((_req,res)=>res.end('<div id="host"></div>'));
const script=fs.readFileSync(path.resolve(__dirname,'../../plugin/crew/web/connections-ui.js'),'utf8');
let windows=[];
(async()=>{
  await app.whenReady();await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin=`http://127.0.0.1:${server.address().port}`;
  const options={show:false,webPreferences:{offscreen:true,sandbox:true,contextIsolation:true,nodeIntegration:false,backgroundThrottling:false}};
  const home=new BrowserWindow(options),workspace=new BrowserWindow(options);windows=[home,workspace];
  await home.loadURL(homePage);await workspace.loadURL(origin+'/?project=test');
  home.webContents.session.setPermissionCheckHandler(()=>false);
  assert.equal(await home.webContents.executeJavaScript('navigator.permissions.query({name:"clipboard-write"}).then(p=>p.state)'),'denied');
  const requests=[];
  installPermissions(home.webContents.session,{homeContents:home.webContents,homePage,workspaceContents:wc=>wc===workspace.webContents,workspaceOrigin:()=>origin,
    requestPermission:async label=>{requests.push(label);return true;}});
  for(const win of windows){
    const run=code=>win.webContents.executeJavaScript(code);
    assert.equal(await run('navigator.permissions.query({name:"clipboard-write"}).then(p=>p.state)'),'granted');
    assert.equal(await run('navigator.permissions.query({name:"clipboard-read"}).then(p=>p.state)'),'denied');
    assert.equal(await run('Notification.requestPermission()'),'granted');
    await run(script+";void 0;");
    await run(`window.fixture={channels:[{id:'qq',state:'unbound',enabled:true,secret_configured:true,bound:false,binding_code:'CREW_FAKE_BINDING'}],notification:{}};
      window.host=document.querySelector('#host');window.reads=0;
      window.dispose=window.CrewConnections.mount(host,{read:async()=>{reads++;return fixture;}});void 0;`);
    await run(`new Promise(resolve=>setTimeout(resolve,0))`);
    await run(`Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.copied=text;}}});host.querySelector('[data-connection-action="copy-binding"]').click();`);
    await run(`new Promise(resolve=>setTimeout(resolve,0))`);
    assert.equal(await run('window.copied'),'/bind CREW_FAKE_BINDING');
    assert.match(await run('host.querySelector("[data-connection-note]").textContent'),/已复制/);
    await run(`navigator.clipboard.writeText=async()=>{throw new Error('Write permission denied.');};host.querySelector('[data-connection-action="copy-binding"]').click();`);
    await run(`new Promise(resolve=>setTimeout(resolve,0))`);
    assert.match(await run('host.querySelector("[data-connection-note]").textContent'),/Write permission denied.*Ctrl\+C/);
    assert.equal(await run(`(()=>{const input=host.querySelector('[data-binding-command]');window.commandInput=input;return input.readOnly&&input.selectionStart===0&&input.selectionEnd===input.value.length;})()`),true);
    await run(`host.querySelector('[data-connection-action="refresh"]').click();`);
    await run(`new Promise(resolve=>setTimeout(resolve,0))`);
    assert.equal(await run(`commandInput===host.querySelector('[data-binding-command]')&&commandInput.selectionEnd===commandInput.value.length`),true);
    await run(`Object.defineProperty(navigator,'clipboard',{value:undefined});host.querySelector('[data-connection-action="copy-binding"]').click();`);
    await run(`new Promise(resolve=>setTimeout(resolve,0))`);
    assert.match(await run('host.querySelector("[data-connection-note]").textContent'),/当前页面无法使用剪贴板.*Ctrl\+C/);
    await run('dispose();void 0;');
  }
  assert.deepEqual(requests,['桌面通知','桌面通知']);
  console.log('PASS: native home/workspace clipboard permissions, copy delivery, denial/manual selection, refresh, missing API');
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>{for(const win of windows)win.destroy();server.close();app.exit(process.exitCode||0);});
