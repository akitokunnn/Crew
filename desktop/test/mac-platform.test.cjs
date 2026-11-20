'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');
const os = require('node:os');
const chrome = require('../src/chrome.cjs');
const { applicationMenuTemplate, backAccelerator, isBackShortcut } = require('../src/menu.cjs');
const { bundlePaths } = require('../scripts/bundle-paths.cjs');
const { pathCommand, parseShellPath, readLoginShellPath } = require('../src/login-shell.cjs');
const { options, Backend } = require('../src/core.cjs');
const { checkPythonVersion } = require('../src/python.cjs');
const { samePath } = require('../src/paths.cjs');

test('Mac uses the native app, edit and window menus, retaining the existing Windows structure', () => {
  const workspace = [{id:'workspace-new'}], help = [{id:'app-about'}];
  const mac = applicationMenuTemplate('darwin',workspace,help);
  const windows = applicationMenuTemplate('win32',workspace,help);
  assert.deepEqual(mac.map(item=>item.role || item.label),['appMenu','工作区','editMenu','windowMenu','帮助']);
  assert.deepEqual(windows.map(item=>item.label),['工作区','编辑','帮助']);
  assert.deepEqual(windows[1].submenu.filter(item=>item.role).map(item=>item.role),['undo','redo','cut','copy','paste','selectAll']);
  assert.equal(mac[1].submenu,workspace); assert.equal(windows[0].submenu,workspace);
  assert.equal(backAccelerator('darwin'),'Cmd+['); assert.equal(backAccelerator('win32'),'Alt+Left');
  const macBack = {key:'[',meta:true}, windowsBack = {key:'ArrowLeft',alt:true};
  assert.equal(isBackShortcut('darwin',macBack),true);
  assert.equal(isBackShortcut('win32',windowsBack),true);
  assert.equal(isBackShortcut('darwin',windowsBack),false);
  assert.equal(isBackShortcut('win32',macBack),false);
});

test('Mac centers native window controls inside Crew’s titlebar', () => {
  const mac = chrome.windowOptions('darwin');
  assert.equal(mac.titleBarStyle,'hidden');
  assert.deepEqual(mac.trafficLightPosition,{x:12,y:12});
  assert.equal(mac.titleBarOverlay,undefined);
  assert.equal(chrome.windowOptions('win32').titleBarOverlay.height,36);
});

test('login shell PATH parsing keeps the exact marked value and rejects absent or incomplete output', () => {
  const value = '/opt/homebrew/bin:/Applications/With Spaces:/usr/bin';
  assert.equal(pathCommand(),`printf '__CREW_PATH_BEGIN__%s__CREW_PATH_END__' "$PATH"`);
  assert.equal(parseShellPath(`__CREW_PATH_BEGIN__${value}__CREW_PATH_END__`),value);
  assert.equal(parseShellPath(`rc banner\n__CREW_PATH_BEGIN__${value}__CREW_PATH_END__\nrc noise`),value);
  assert.throws(()=>parseShellPath('rc banner'),/缺少完整/);
  assert.throws(()=>parseShellPath('__CREW_PATH_BEGIN__/usr/bin'),/缺少完整/);
  assert.throws(()=>parseShellPath('__CREW_PATH_BEGIN____CREW_PATH_END__'),/为空/);
});

test('shell stderr explains a missing PATH or timeout and a nonzero exit can still supply PATH', async () => {
  const shell = '/bin/zsh', marked = '__CREW_PATH_BEGIN__/usr/bin__CREW_PATH_END__';
  const run = (stdout,stderr,code) => async (file,args,options) => {
    assert.equal(file,shell); assert.deepEqual(args,['-ilc',pathCommand()]); assert.equal(options.timeout,10000);
    throw Object.assign(new Error('fixture shell problem'),{stdout,stderr,code});
  };
  assert.equal(await readLoginShellPath(shell,run(marked,'rc problem',3)),'/usr/bin');
  await assert.rejects(readLoginShellPath(shell,run('','broken rc line',3)),/\/bin\/zsh -ilc.*退出码 3.*broken rc line/);
  await assert.rejects(readLoginShellPath(shell,run('','fixture ENOENT','ENOENT')),/fixture ENOENT/);
  await assert.rejects(readLoginShellPath(shell,async () => {
    throw Object.assign(new Error('killed'),{code:null,killed:true,signal:'SIGKILL',stderr:'slow rc command'});
  }),/超时（10 秒）.*slow rc command/);
});

test('Python defaults are platform-specific and a selected interpreter overrides them', () => {
  assert.equal(options([],{platform:'darwin'}).python,'python3');
  assert.equal(options([],{platform:'win32'}).python,'python');
  assert.equal(options(['--python','custom-python'],{platform:'darwin'}).python,'custom-python');
});

test('Python validation accepts 3.11 and explains old or unavailable interpreters to Mac users', async () => {
  const run = version => async (command,args) => {
    assert.equal(command,'python3');
    assert.deepEqual(args,['-c',"import sys;print('%d.%d'%sys.version_info[:2])"]);
    return {stdout:version+'\n'};
  };
  await checkPythonVersion('python3',{platform:'darwin',execute:run('3.11')});
  await assert.rejects(checkPythonVersion('python3',{platform:'darwin',execute:run('3.10')}),/当前 python3 是 3\.10.*brew install python/);
  await assert.rejects(checkPythonVersion('python3',{platform:'win32',execute:run('3.10')}),{message:'Crew 需要 Python 3.11 或更新版本，当前 python3 是 3.10。'});
  await assert.rejects(checkPythonVersion('python3',{platform:'darwin',execute:async () => { throw Error('missing executable'); }}),/无法运行.*missing executable.*brew install python/);
});

test('local backend rejects a missing Python before spawning the backend program', async t => {
  const home = await fs.mkdtemp(path.join(os.tmpdir(),'crew-python-test-'));
  t.after(() => fs.rm(home,{recursive:true,force:true}));
  const backend = new Backend({home,backendDir:home,python:path.join(home,'no-python'),port:0}, {spawn:()=>assert.fail('backend spawned')});
  await assert.rejects(backend.start(),/Python 3\.11.*no-python.*无法运行.*ENOENT/);
  assert.equal(backend.owned,false);
});

test('Crew paths compare without case on Windows and Mac and retain case on Linux', () => {
  for (const platform of ['win32','darwin']) assert.equal(samePath('Crew/HOME','crew/home',platform),true);
  assert.equal(samePath('Crew/HOME','crew/home','linux'),false);
  assert.equal(samePath('Crew/HOME','Other/HOME','darwin'),false);
});

test('packaged smoke and resource verification resolve the native bundle layouts', () => {
  const root = path.resolve('desktop');
  const mac = path.join(root,'dist','mac-arm64','Crew.app');
  const windows = path.join(root,'dist','win-unpacked');
  assert.deepEqual(bundlePaths(root,'darwin'),{application:mac,executable:path.join(mac,'Contents','MacOS','Crew'),resources:path.join(mac,'Contents','Resources')});
  assert.deepEqual(bundlePaths(root,'win32'),{application:windows,executable:path.join(windows,'Crew.exe'),resources:path.join(windows,'resources')});
  assert.equal(bundlePaths(root,'darwin','unzip/Crew.app').resources,path.resolve('unzip/Crew.app/Contents/Resources'));
});
