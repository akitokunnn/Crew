'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {app, ipcMain, webContents} = require('electron');
const core = require('../src/core.cjs');
const output = path.resolve(__dirname, '../smoke-output/handoff');
const evidence = {checks:[], screenshots:[], errors:[], scope:'Real desktop and backend, fixture chat journals; no native or model turn requested.'};
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function bounded(promise, label, timeout=8000) {
  let timer;
  try {return await Promise.race([promise, new Promise((_, reject) => {timer=setTimeout(() => reject(Error('Timed out: '+label)), timeout);})]);}
  finally {clearTimeout(timer);}
}

app.once('browser-window-created', (_event, win) => {
  win.show = () => {}; win.focus = () => {};
  const main = win.webContents;
  main.setBackgroundThrottling(false);
  ipcMain.removeHandler('desktop:ready'); ipcMain.handle('desktop:ready', () => {});
  const observe = contents => {
    contents.setBackgroundThrottling(false);
    contents.on('console-message', event => {if (event.level === 'error') evidence.errors.push(event.message);});
  };
  observe(main); app.on('web-contents-created', (_event, contents) => observe(contents));
  main.once('did-finish-load', async () => {
    async function js(contents, source) {
      const result = await contents.executeJavaScript(`(async()=>{try{return {value:await (${source})};}catch(error){return {failure:error.stack||String(error)};}})()`, true);
      if (result.failure) throw Error(result.failure + '\nExpression: ' + source);
      return result.value;
    }
    async function until(callback, label) {
      const deadline = Date.now()+25000;
      while (Date.now()<deadline) {if (await callback()) return; await pause(50);}
      throw Error('Handoff smoke wait timed out: '+label);
    }
    const wait = (contents, source) => until(() => js(contents, source), source);
    const click = (contents, selector) => js(contents, `document.querySelector(${JSON.stringify(selector)}).click()`);
    async function capture(contents, name) {
      await js(contents, 'Promise.race([document.fonts.ready,new Promise(resolve=>setTimeout(resolve,1500))])');
      // Hidden child views can retain the previous compositor surface. A fully
      // transparent, non-activating window permits a real paint without showing
      // content or taking focus from the user; hide it again after capture.
      win.setOpacity(0); win.showInactive();
      let bytes;
      try {
        await bounded(js(contents,'new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))'),'current rendered chat frame');
        await pause(150);
        bytes=(await bounded(contents.capturePage(undefined,{stayHidden:true,stayAwake:true}),'current chat screenshot')).toPNG();
      } finally {win.hide();}
      const file=path.join(output,name+'.png'); await fs.writeFile(file,bytes); evidence.screenshots.push(file);
    }
    let view;
    try {
      await fs.mkdir(output,{recursive:true});
      await wait(main,'document.documentElement.dataset.crewDesktopChromeReady === "true"');
      const boot=await js(main,'window.crewDesktop.bootstrap()');
      evidence.service=boot.service;
      const health=await core.request(boot.service.url,'/api/admin/health');
      evidence.home=health.home; evidence.backend=health.plugin_root;
      assert.equal(health.ok,true); assert(health.pid>0);
      const fixture=JSON.parse(await fs.readFile(path.join(evidence.home,'handoff-fixture.json'),'utf8'));
      await js(main,'window.crewDesktop.openProject("handoff-demo")');
      await until(() => {
        view=webContents.getAllWebContents().find(contents => {
          try {const url=new URL(contents.getURL());return url.origin===boot.service.url && url.searchParams.get('project')==='handoff-demo';}
          catch (_) {return false;}
        });
        return Boolean(view);
      },'real workspace view');
      await wait(view,'!!document.querySelector("[data-act=leader][data-id=offline-claude]")');
      evidence.checks.push('private HOME opens in a real workspace tab and the real backend health identifies its source and PID');
      await click(view,'[data-act=leader][data-id=offline-claude]');
      await wait(view,'document.querySelector(".chat-inner")?.textContent.includes("会话已离线")');
      assert.equal(await js(view,'document.querySelectorAll(".tool.live").length'),0);
      assert(await js(view,'document.querySelector(".chat-inner").textContent.includes("结果未确认")'));
      evidence.checks.push('legacy Claude offline history stops the tool spinner and explicitly keeps the missing result unknown');
      await click(view,'[data-chat-fold="tool:legacy-tool"]');
      assert.equal(await js(view,'document.querySelector(\'[data-chat-fold="tool:legacy-tool"]\').getAttribute("aria-expanded")'),'true');
      assert(await js(view,'document.querySelector(".chat-inner").textContent.includes("python verify.py")'));
      await capture(view,'01-legacy-offline-expanded');
      await click(view,'[data-chat-fold="tool:legacy-tool"]');
      assert.equal(await js(view,'document.querySelector(\'[data-chat-fold="tool:legacy-tool"]\').getAttribute("aria-expanded")'),'false');
      evidence.checks.push('an ended tool expands its real input and collapses through the actual chat controls');
      const draft='这份多行草稿需要保留\n第二行：继续核对原始结果';
      await js(view,`(()=>{const input=document.querySelector(".composer textarea");input.value=${JSON.stringify(draft)};input.dispatchEvent(new Event("input",{bubbles:true}));})()`);
      await click(view,'[data-act=leader][data-id=handoff-codex]');
      await wait(view,'document.querySelector(".chat-inner")?.textContent.includes("已转交执行")');
      assert.equal(await js(view,'document.querySelectorAll(".tool.live").length'),0);
      assert.equal(await js(view,`document.querySelector('[data-chat-goal="${fixture.goal_id}"]')?.textContent`),'查看任务 →');
      assert.equal(await js(view,'document.querySelector(".chat-inner").textContent.includes("执行失败")'),false);
      await click(view,'[data-chat-fold="tool:handoff-tool"]');
      await capture(view,'02-confirmed-handoff');
      evidence.checks.push('Codex identity handoff renders as transferred, retains its task link and does not become an invented tool failure');
      if (await js(view,'!document.querySelector("#right-panel").classList.contains("show")')) await click(view,'[data-act=right-open]');
      await wait(view,'!!document.querySelector("[data-act=member-chat][data-id=failed-member]")');
      await click(view,'[data-act=member-chat][data-id=failed-member]');
      await wait(view,'document.querySelector(".chat-inner")?.textContent.includes("子命令退出 23")');
      assert(await js(view,'document.querySelector(".chat-inner").textContent.includes("本轮失败")'));
      assert.equal(await js(view,'document.querySelectorAll(".tool.live").length'),0);
      await click(view,'[data-chat-fold="tool:failed-tool"]');
      await capture(view,'03-real-failure');
      evidence.checks.push('a Claude member true failure retains both its terminal label and exact failure text');
      if (await js(view,'!document.querySelector("#right-panel").classList.contains("show")')) await click(view,'[data-act=right-open]');
      await click(view,'[data-act=member-chat][data-id=pending-member]');
      await wait(view,`!!document.querySelector('[data-chat-fold="tool:next-tool"]')`);
      assert(await js(view,'document.querySelector(\'[data-chat-fold="tool:prior-tool"]\').textContent.includes("本轮已结束")'));
      assert(await js(view,'document.querySelector(\'[data-chat-fold="tool:next-tool"]\').classList.contains("live")'));
      await click(view,'[data-chat-fold="tool:next-tool"]');
      assert.equal(await js(view,'document.querySelector(\'[data-chat-fold="tool:next-tool"]\').getAttribute("aria-expanded")'),'true');
      assert(await js(view,'document.querySelector(".chat-inner").textContent.includes("正在执行…")'));
      evidence.checks.push('a new Codex member tool after a completed turn keeps its own live state and can expand without inheriting the previous terminal');
      await capture(view,'04-next-tool-pending');
      await click(view,'[data-act=leader][data-id=offline-claude]');
      await wait(view,`!!document.querySelector('[data-chat-fold="tool:legacy-tool"]')`);
      assert.equal(await js(view,'document.querySelector(".composer textarea").value'),draft);
      assert.equal(await js(view,'document.querySelector(\'[data-chat-fold="tool:legacy-tool"]\').getAttribute("aria-expanded")'),'false');
      evidence.checks.push('switching between leaders and members preserves the exact multiline draft and each tool fold state');
      win.setSize(980,740);
      for (let attempt=0;attempt<3 && !await js(view,'document.documentElement.dataset.theme === "dark"');attempt++) await click(view,'[data-act=theme]');
      assert(await js(view,'document.documentElement.dataset.theme === "dark"'));
      await js(view,'document.documentElement.style.setProperty("--fs","1.2")');
      evidence.darkPalette=await js(view,'(()=>{const css=getComputedStyle(document.documentElement);return {mode:document.documentElement.dataset.theme,bg:css.getPropertyValue("--bg").trim(),text:css.getPropertyValue("--text").trim(),bodyBg:getComputedStyle(document.body).backgroundColor,bodyText:getComputedStyle(document.body).color};})()');
      assert.equal(evidence.darkPalette.mode,'dark');
      const darkRGB=evidence.darkPalette.bodyBg.match(/\d+/g).slice(0,3).map(Number);
      assert(Math.max(...darkRGB)<80,JSON.stringify(evidence.darkPalette));
      await capture(view,'05-dark-narrow');
      const bounds=await js(view,'({width:innerWidth,document:document.documentElement.scrollWidth})');
      assert(bounds.document<=bounds.width+1,JSON.stringify(bounds)); evidence.bounds=bounds;
      evidence.checks.push('the shared chat appearance remains readable in a narrow native window with dark theme and larger typography');
      const state=await core.request(boot.service.url,'/api/state?project=handoff-demo');
      assert(state.leaders.every(item => !item.running)); assert(state.members.every(item => !item.running));
      const spawnFiles=await fs.readdir(path.join(evidence.home,'spawn')).catch(error=>{if(error.code==='ENOENT')return [];throw error;});
      const leases=await fs.readdir(path.join(evidence.home,'runtime-locks')).catch(error=>{if(error.code==='ENOENT')return [];throw error;});
      assert.deepEqual(spawnFiles,[]); assert.deepEqual(leases,[]);
      await assert.rejects(fs.access(path.join(evidence.home,'fixture-model-start-attempts.txt')),{code:'ENOENT'});
      evidence.modelTurnsStarted=0; evidence.checks.push('no fixture identity is running, no spawn or native lease was created, and acceptance sent no chat or goal-start request');
      assert.deepEqual(evidence.errors,[]); evidence.ok=true;
      await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));
      app.quit();
    } catch (error) {
      evidence.errors.push(error.stack||String(error));
      if(view) try {await capture(view,'failure');} catch (captureError) {evidence.errors.push(String(captureError));}
      await fs.mkdir(output,{recursive:true}); await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));
      console.error(error); process.exitCode=1; app.quit();
    }
  });
});
