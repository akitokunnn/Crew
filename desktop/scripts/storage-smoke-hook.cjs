'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {app, ipcMain} = require('electron');
const output = path.resolve(__dirname, '../smoke-output/storage');
const evidence = {checks:[], screenshots:[], errors:[], noAIWorkRequested:true};
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

app.once('browser-window-created', (_event, win) => {
  const contents = win.webContents;
  contents.setBackgroundThrottling(false);
  ipcMain.removeHandler('desktop:ready'); ipcMain.handle('desktop:ready', () => {});
  contents.on('console-message', event => {if (event.level === 'error') evidence.errors.push(event.message);});
  contents.once('did-finish-load', async () => {
    const js = async source => {
      const result = await contents.executeJavaScript(`(async()=>{try{return {value:await (${source})};}catch(error){return {failure:error.stack||String(error)};}})()`, true);
      if (result.failure) throw Error(result.failure + '\nExpression: ' + source);
      return result.value;
    };
    const click = selector => js(`document.querySelector(${JSON.stringify(selector)}).click()`);
    async function wait(source) {
      const deadline = Date.now() + 25000;
      while (Date.now() < deadline) {if (await js(source)) return; await pause(40);}
      throw Error('Storage smoke wait timed out: ' + source);
    }
    async function capture(name) {
      await js('document.fonts.ready');
      await js('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
      await pause(100);
      const file = path.join(output, name + '.png');
      await fs.writeFile(file, (await contents.capturePage(undefined, {stayHidden:true, stayAwake:true})).toPNG());
      evidence.screenshots.push(file);
    }
    try {
      await fs.mkdir(output, {recursive:true});
      await wait('document.documentElement.dataset.crewDesktopChromeReady === "true"');
      const boot = await js('window.crewDesktop.bootstrap()');
      const state = await js('window.crewDesktop.storageRead()');
      evidence.home = state.home; evidence.service = boot.service;
      const fixture = JSON.parse(await fs.readFile(path.join(state.home, 'storage-fixture.json'), 'utf8'));
      assert.equal(state.policy.automatic_enabled, false);
      assert.equal(state.categories.find(item => item.id === 'logs').candidate_count, 1);
      await click('#nav-settings'); await wait('!!document.querySelector("[data-settings-category=storage]")');
      await click('[data-settings-category=storage]');
      await wait('!!document.querySelector("[data-storage-category=backups]")');
      assert.equal(await js('document.querySelector("[data-storage-category=logs]").checked'), true);
      assert.equal(await js('document.querySelector("[data-storage-category=backups]").checked'), false);
      await capture('storage-light');
      evidence.checks.push('native settings loads the packaged shared panel through real IPC and scans the actual isolated HOME');
      await click('[data-storage-category=backups]'); await click('[data-storage-category=attachments]');
      await click('[data-storage-action=preview]'); await wait('!!document.querySelector("#storage-preview-title")');
      await fs.access(fixture.log); await fs.access(fixture.orphan); await fs.access(fixture.old_backup);
      await capture('storage-preview'); await click('[data-storage-action=cancel-preview]');
      assert.equal(await js('document.querySelector("[data-storage-category=backups]").checked'), true);
      evidence.checks.push('preview is read-only and cancelling keeps the selected categories');
      await click('[data-storage-action=preview]'); await wait('!!document.querySelector("#storage-preview-title")');
      await click('[data-storage-action=confirm-clean]'); await wait('document.querySelector("#storage-result-title")?.textContent === "清理完成"');
      let after = await js('window.crewDesktop.storageRead()');
      assert(after.trash_bytes > 0); assert(after.history[0].freed_bytes > 0); assert.equal(after.history[0].restorable_count, 2);
      for (const name of ['log','orphan','old_backup']) await assert.rejects(fs.access(fixture[name]), {code:'ENOENT'});
      await fs.access(fixture.referenced);
      assert.equal(await fs.readFile(path.join(fixture.project, 'keep.txt'), 'utf8'), 'project files stay unchanged');
      evidence.checks.push('confirmed cleanup releases only the expired log, recycles old backup and orphan image, and retains referenced images and project files');
      await click('[data-storage-action=restore]'); await wait('document.querySelector("#storage-result-title")?.textContent === "恢复完成"');
      await fs.access(fixture.orphan); await fs.access(fixture.old_backup);
      assert.equal((await js('window.crewDesktop.storageRead()')).trash_bytes, 0);
      evidence.checks.push('recoverable files restore through real IPC without changing retained history');
      await click('[data-storage-action=preview]'); await wait('!!document.querySelector("#storage-preview-title")');
      await click('[data-storage-action=confirm-clean]'); await wait('document.querySelector("#storage-result-title")?.textContent === "清理完成"');
      await click('[data-storage-action=empty-trash]'); await wait('!!document.querySelector("#storage-empty-title")');
      await click('[data-storage-action=cancel-empty]'); assert((await js('window.crewDesktop.storageRead()')).trash_bytes > 0);
      await click('[data-storage-action=empty-trash]'); await click('[data-storage-action=confirm-empty]');
      await wait('document.querySelector("#storage-result-title")?.textContent === "回收站已清空"');
      after = await js('window.crewDesktop.storageRead()'); assert.equal(after.trash_bytes, 0);
      evidence.checks.push('emptying trash has a separate cancellable confirmation and then frees its actual bytes');
      win.setSize(860, 670);
      await js('(()=>{document.documentElement.dataset.theme="dark"; document.documentElement.style.setProperty("--fs","1.25"); document.querySelector(".cs-body").scrollTop=0;})()');
      await capture('storage-dark-narrow');
      const bounds = await js('(()=>{const b=document.querySelector(".cs-body");return {width:b.clientWidth,scrollWidth:b.scrollWidth,window:innerWidth,document:document.documentElement.scrollWidth};})()');
      assert(bounds.scrollWidth <= bounds.width + 1, JSON.stringify(bounds));
      assert(bounds.document <= bounds.window + 1, JSON.stringify(bounds));
      evidence.bounds = bounds;
      evidence.checks.push('dark appearance, a narrow native window and 125 percent typography fit without horizontal scrolling');
      await click('#nav-workspaces'); await wait('document.querySelector("#settings-page").hidden');
      evidence.checks.push('leaving settings unmounts the panel and returns to workspaces');
      assert.deepEqual(evidence.errors, []); evidence.ok = true;
      await fs.writeFile(path.join(output, 'evidence.json'), JSON.stringify(evidence, null, 2));
      app.quit();
    } catch (error) {
      evidence.errors.push(error.stack || String(error));
      try {await capture('failure');} catch (captureError) {evidence.errors.push(String(captureError));}
      await fs.mkdir(output, {recursive:true});
      await fs.writeFile(path.join(output, 'evidence.json'), JSON.stringify(evidence, null, 2));
      console.error(error); process.exitCode = 1; app.quit();
    }
  });
});
