'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {app, ipcMain, webContents} = require('electron');
const core = require('../src/core.cjs');
// Acceptance windows stay transparent and never take focus. Disable native
// occlusion throttling so the real child renderer can still produce frames.
app.commandLine.appendSwitch('disable-backgrounding-occluded-windows');
app.commandLine.appendSwitch('disable-features','CalculateNativeWinOcclusion');
const output = path.resolve(__dirname, '../smoke-output/handoff');
const evidence = {checks:[], screenshots:[], errors:[], scope:'Real desktop and backend, fixture chat journals and response-only overview statuses; no native or model turn requested.'};
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
      // Overview states are response-only fixtures. Persisting a working
      // journal would let the startup recovery path request a native turn.
      const workingFile=path.join(evidence.home,'fixture-working.json');
      async function overview(statuses, amount) {
        const temporary=workingFile+'.tmp';
        await fs.writeFile(temporary,JSON.stringify(statuses),'utf8'); await fs.rename(temporary,workingFile);
        await click(view,'[data-act=view][data-view=all]');
        await wait(view,`document.querySelector('#main [data-act="all-tab"][data-tab="working"] b')?.textContent === ${JSON.stringify(String(amount))}`);
        const actual=await core.request(boot.service.url,'/api/state?project=handoff-demo');
        assert(actual.leaders.every(item=>!item.running&&!item.busy)); assert(actual.members.every(item=>!item.running&&!item.busy));
        for(const role of ['leaders','members']) for(const [id,status] of Object.entries(statuses[role])) {
          assert.equal(actual[role].find(item=>item.session_id===id)?.status,status);
        }
        return actual;
      }
      const listRows=()=>js(view,'[...document.querySelectorAll("#main .list .li[data-id]")].map(item=>({id:item.dataset.id,act:item.dataset.act,text:item.textContent}))');
      const openWorking=async()=>{await click(view,'#main [data-act=all-tab][data-tab=working]');};
      win.setSize(1180,850);
      await js(view,'document.documentElement.style.setProperty("--fs","1")');
      await overview({leaders:{'offline-claude':'working','handoff-codex':'idle'},members:{'failed-member':'waiting','pending-member':'offline'}},1);
      await click(view,'#main [data-act=all-tab][data-tab=tree]');
      assert(await js(view,'document.querySelector("#main .leader-node[data-id=offline-claude]").textContent.includes("在干活")'));
      assert(await js(view,'!!document.querySelector("#sidebar [data-id=offline-claude] .group-work-avatar")'));
      assert.equal(await js(view,'document.querySelector("#main .leader-node[data-id=offline-claude] .group-work-label").textContent'),'本人在干活');
      await capture(view,'06-overview-leader-only');
      await openWorking();
      let rows=await listRows();
      assert.equal(rows.length,1); assert.equal(rows[0].id,'offline-claude'); assert.equal(rows[0].act,'leader');
      evidence.overview={leaderOnly:rows};
      assert(rows[0].text.includes('leader')); assert(rows[0].text.includes('核对当前协议与原始证据'));
      await click(view,'#main .list [data-act=leader][data-id=offline-claude]');
      await wait(view,'document.querySelector("#chat-head .chat-name")?.textContent === "旧离线记录"');
      assert.equal(await js(view,'document.querySelector("#chat-head .chat-member-stats").textContent'),'0 个下属 · 本组 1 人在干活');
      assert.equal(await js(view,'document.querySelector(".composer textarea").value'),draft);
      evidence.checks.push('a working Claude leader appears once in both overview count and working list, opens its own conversation with its goal and exact draft, and counts itself in the group header');

      await overview({leaders:{'offline-claude':'working','handoff-codex':'working'},members:{'failed-member':'working','pending-member':'working'}},4);
      await openWorking(); rows=await listRows();
      evidence.overview.mixed=rows;
      assert.equal(rows.length,4); assert.equal(new Set(rows.map(item=>item.id)).size,4);
      assert.deepEqual(rows.filter(item=>item.act==='leader').map(item=>item.id).sort(),['handoff-codex','offline-claude']);
      assert.deepEqual(rows.filter(item=>item.act==='member-chat').map(item=>item.id).sort(),['failed-member','pending-member']);
      assert(rows.find(item=>item.id==='handoff-codex').text.includes('接续交接后的独立任务'));
      assert(rows.find(item=>item.id==='failed-member').text.includes('复核退出 23 的真实失败'));
      await capture(view,'07-overview-mixed-working');
      await click(view,'#main .list [data-act=member-chat][data-id=failed-member]');
      await wait(view,'document.querySelector("#chat-head .chat-name")?.textContent === "真实失败"');
      assert(await js(view,'document.querySelector("#chat-head .chat-scope").textContent.includes("成员本人对话")'));
      assert.equal(await js(view,'document.querySelector("#chat-head .chat-member-stats").textContent'),'2 个下属 · 本组 3 人在干活');
      await wait(view,'document.querySelector(".chat-inner")?.textContent.includes("子命令退出 23")');
      const memberDraft='成员本人草稿不应被全部页切换丢失\n第二行：只核对真实证据';
      await js(view,`(()=>{const input=document.querySelector(".composer textarea");input.value=${JSON.stringify(memberDraft)};input.dispatchEvent(new Event("input",{bubbles:true}));})()`);
      await click(view,'[data-act=view][data-view=all]'); await openWorking();
      await click(view,'#main .list [data-act=leader][data-id=handoff-codex]');
      await wait(view,'document.querySelector("#chat-head .chat-name")?.textContent === "执行交接"');
      assert.equal(await js(view,'document.querySelector("#chat-head .chat-member-stats").textContent'),'2 个下属 · 本组 3 人在干活');
      await wait(view,'document.querySelector(".chat-inner")?.textContent.includes("已转交执行")');
      evidence.checks.push('mixed Claude and Codex leaders and members produce four unique people, retain role-specific goal or task text, and both leader and member routes show the same three-person group count');

      await overview({leaders:{'offline-claude':'idle','handoff-codex':'waiting'},members:{'failed-member':'working','pending-member':'offline'}},1);
      await click(view,'#main [data-act=all-tab][data-tab=tree]');
      const memberWork=await js(view,`(()=>{
        const sidebar=document.querySelector('#sidebar [data-act=leader][data-id=handoff-codex]');
        const node=document.querySelector('#main .leader-node[data-id=handoff-codex]');
        return {sidebar:sidebar.querySelector('.group-work-label')?.textContent,label:sidebar.getAttribute('aria-label'),
          node:node.querySelector('.group-work-label')?.textContent,own:node.querySelector('.st')?.textContent,
          rings:document.querySelectorAll('.group-work-avatar').length,
          idle:!!document.querySelector('#sidebar [data-id=offline-claude] .group-work-avatar')};
      })()`);
      assert.equal(memberWork.sidebar,'组员 1 在干活');assert(memberWork.label.includes('1 位组员在干活'));
      assert.equal(memberWork.node,'1 位组员在干活');assert.equal(memberWork.own,'等你');
      assert.equal(memberWork.rings,2);assert.equal(memberWork.idle,false);
      await capture(view,'09-group-members-working-dark');
      for(let attempt=0;attempt<3 && !await js(view,'document.documentElement.dataset.theme === "light"');attempt++) await click(view,'[data-act=theme]');
      assert(await js(view,'document.documentElement.dataset.theme === "light"'));
      win.setMinimumSize(700,600);
      win.setSize(760,850);
      await wait(view,'innerWidth <= 760');
      const narrowWork=await js(view,`(()=>{
        const button=document.querySelector('#sidebar [data-id=handoff-codex]'),avatar=button.querySelector('.group-work-avatar');
        return {width:avatar.getBoundingClientRect().width,border:getComputedStyle(avatar,'::before').borderTopWidth,
          animation:getComputedStyle(avatar,'::after').animationName,overflow:button.scrollWidth>button.clientWidth};
      })()`);
      assert(Math.abs(narrowWork.width-26)<.1);assert(parseFloat(narrowWork.border)>0);assert.equal(narrowWork.animation,'group-work-glow');assert.equal(narrowWork.overflow,false);
      await capture(view,'10-group-members-working-narrow-light');
      // Reduced motion belongs to the workspace renderer, not the desktop shell.
      await view.debugger.attach('1.3');
      try{
        await view.debugger.sendCommand('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
        assert.equal(await js(view,'getComputedStyle(document.querySelector(".group-work-avatar"),"::after").animationName'),'none');
        assert.equal(await js(view,'getComputedStyle(document.querySelector(".group-work-avatar"),"::before").borderTopWidth'),narrowWork.border);
      }finally{
        await view.debugger.sendCommand('Emulation.setEmulatedMedia',{features:[]});view.debugger.detach();
      }
      win.setSize(1180,850);
      evidence.groupWork={memberWork,narrowWork,reducedMotion:true};
      evidence.checks.push('a waiting leader shows member-only work in the sidebar and architecture with a distinct avatar halo, readable in both themes and narrow layout; reduced motion keeps the static ring');
      await openWorking(); rows=await listRows();
      evidence.overview.memberOnly=rows;
      assert.deepEqual(rows.map(item=>item.id),['failed-member']); assert.equal(rows[0].act,'member-chat');
      await click(view,'#main .list [data-act=member-chat][data-id=failed-member]');
      await wait(view,'document.querySelector("#chat-head .chat-name")?.textContent === "真实失败"');
      assert.equal(await js(view,'document.querySelector("#chat-head .chat-member-stats").textContent'),'2 个下属 · 本组 1 人在干活');
      assert(await js(view,'document.querySelector("#chat-head .chat-member-stats").classList.contains("is-working")'));
      if(!await js(view,'document.querySelector("#right-panel").classList.contains("show")')) await click(view,'[data-act=right-open]');
      assert.equal(await js(view,'document.querySelector("#right-panel .leader-node .group-work-label").textContent'),'1 位组员在干活');
      assert.equal(await js(view,'document.querySelector(".composer textarea").value'),memberDraft);
      evidence.checks.push('idle, waiting and offline people are excluded even beside a working member; opening that member preserves its own multiline draft and counts only the one working person in its group');

      await overview({leaders:{'offline-claude':'idle','handoff-codex':'waiting'},members:{'failed-member':'offline','pending-member':'idle'}},0);
      assert.equal(await js(view,'document.querySelectorAll("#sidebar .group-work-avatar").length'),0);
      await click(view,'#main [data-act=all-tab][data-tab=tree]');
      assert.equal(await js(view,'document.querySelectorAll("#main .group-work-avatar,.leader-work-summary .group-work-label").length'),0);
      await openWorking(); assert.deepEqual(await listRows(),[]);
      assert(await js(view,'document.querySelector("#main .empty")?.textContent.includes("没有正在干活的会话")'));
      await capture(view,'08-overview-no-working');
      await fs.unlink(workingFile);
      await click(view,'[data-act=leader][data-id=offline-claude]');
      await wait(view,'document.querySelector("#chat-head .chat-name")?.textContent === "旧离线记录"');
      assert.equal(await js(view,'document.querySelector(".composer textarea").value'),draft);
      const persisted=await fs.readdir(path.join(evidence.home,'chat'));
      for(const name of persisted.filter(name=>name.endsWith('.jsonl'))) {
        const entries=(await fs.readFile(path.join(evidence.home,'chat',name),'utf8')).trim().split('\n').map(line=>JSON.parse(line));
        assert.notEqual(entries.filter(item=>item.type==='status').at(-1)?.status,'working');
      }
      await assert.rejects(fs.access(path.join(evidence.home,'fixture-model-start-attempts.txt')),{code:'ENOENT'});
      assert.deepEqual(await fs.readdir(path.join(evidence.home,'spawn')).catch(error=>{if(error.code==='ENOENT')return [];throw error;}),[]);
      assert.deepEqual(await fs.readdir(path.join(evidence.home,'runtime-locks')).catch(error=>{if(error.code==='ENOENT')return [];throw error;}),[]);
      evidence.checks.push('all nonworking roles yield the exact session empty state, fixture status overrides never persist as working journals, drafts survive overview navigation, and no native/model start or lease was attempted');
      // Observe this isolated backend PID as a fake executor. No AI process is launched.
      const {spawnSync}=require('node:child_process');
      const inspected=spawnSync('python',['-c','import sys;from crew.codex_runtime import process_identity;print(process_identity(int(sys.argv[1])))',String(health.pid)],{encoding:'utf8',windowsHide:true});
      assert.equal(inspected.status,0,inspected.stderr);
      const pidCreated=inspected.stdout.trim(),stamp=new Date().toISOString();
      const receipt={workdir:fixture.project,task_file:path.join(fixture.project,'读取验收_任务书.md'),sandbox:'read-only',model:null,effort:null,
        crew_session_id:'handoff-codex',crew_execution_id:null,started_at:stamp,ended_at:null,exit_code:null,pid:health.pid,pid_created:pidCreated};
      const codexReceipt={...receipt,run_id:'fake-codex-dispatch',claude_session_id:null,ephemeral:true,usage:null};
      const claudeReceipt={...receipt,run_id:'fake-claude-dispatch',claude_session_id:'fake-claude-child',codex_thread_id:null};
      await fs.mkdir(path.join(evidence.home,'isolated'),{recursive:true});
      const append=async(file,value)=>fs.appendFile(path.join(evidence.home,'isolated',file==='codex_runs.jsonl'?'runs.jsonl':file),JSON.stringify(value)+'\n','utf8');
      await append('codex_runs.jsonl',codexReceipt);await append('claude_runs.jsonl',claudeReceipt);
      await click(view,'[data-act=view][data-view=all]');
      await click(view,'#main [data-act=all-tab][data-tab=tree]');
      await wait(view,'document.querySelectorAll("#main [data-codex-source=skill_dispatch].codex-status-running").length === 2');
      assert.equal(await js(view,'document.querySelector("#sidebar [data-id=handoff-codex] .group-work-label").textContent'),'2 个派工在运行');
      const dispatchLabels=await js(view,'[...document.querySelectorAll("#main [data-codex-source=skill_dispatch]")].map(node=>node.title)');
      assert(dispatchLabels.some(label=>label.includes('Claude 派活')));assert(dispatchLabels.some(label=>label.includes('Codex 派活')));
      assert(dispatchLabels.every(label=>label.includes('PID '+health.pid)));
      assert.equal(await js(view,'document.querySelector("#main [data-tab=working] b").textContent'),'2');
      await openWorking();
      assert.equal(await js(view,'document.querySelectorAll("#main [data-dispatch-id]").length'),2);
      assert(await js(view,'document.querySelector("#main [data-dispatch-id]").textContent.includes("临时派工")'));
      await click(view,'#main [data-dispatch-id=fake-codex-dispatch]');
      await wait(view,'document.querySelector("#chat-head .chat-name")?.textContent === "执行交接"');
      assert(await js(view,'document.querySelector("#chat-head .chat-member-stats").textContent.includes("2 个派工在运行")'));
      await click(view,'[data-act=view][data-view=all]');await click(view,'#main [data-act=all-tab][data-tab=tree]');
      await capture(view,'11-skill-dispatch-both-carriers');
      await append('codex_runs.jsonl',{...codexReceipt,ended_at:new Date().toISOString(),exit_code:0});
      await append('claude_runs.jsonl',{...claudeReceipt,ended_at:new Date().toISOString(),exit_code:7});
      await wait(view,'!!document.querySelector("#main [data-codex-source=skill_dispatch].codex-status-succeeded") && !!document.querySelector("#main [data-codex-source=skill_dispatch].codex-status-failed")');
      assert.equal(await js(view,'document.querySelectorAll("#sidebar .group-work-avatar").length'),0);
      await assert.rejects(fs.access(path.join(evidence.home,'fixture-model-start-attempts.txt')),{code:'ENOENT'});
      await capture(view,'12-skill-dispatch-completion');
      evidence.checks.push('both skill carriers appear beneath the correct idle leader with PID and group halo, then independently show success/failure without waking AI or waiting for the full usage scan');
      const contextFile=path.join(evidence.home,'fixture-context.json'),compactLog=path.join(evidence.home,'fixture-compacts.jsonl');
      const contextMode=async mode=>{await fs.writeFile(contextFile+'.tmp',JSON.stringify({mode}),'utf8');await fs.rename(contextFile+'.tmp',contextFile);};
      const compacts=async()=>{try{return (await fs.readFile(compactLog,'utf8')).trim().split('\n').filter(Boolean).map(line=>JSON.parse(line));}catch(error){if(error.code==='ENOENT')return[];throw error;}};
      await contextMode('complete');
      await wait(view,'document.querySelector("#sidebar .context-ring[data-id=offline-claude]") !== null');
      async function compactRing(selector,id) {
        const before=(await compacts()).length,route=await js(view,'location.hash');
        await wait(view,`!!document.querySelector(${JSON.stringify(selector)}) && !document.querySelector(${JSON.stringify(selector)}).disabled`);
        await wait(view,`(()=>{const ring=document.querySelector(${JSON.stringify(selector)});ring.scrollIntoView({block:'nearest'});const r=ring.getBoundingClientRect();return ring.tagName==='BUTTON' && ring.tabIndex===0 && !ring.parentElement.closest('button') && document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.closest('.context-ring')===ring;})()`);
        await click(view,selector);
        await until(async()=> (await compacts()).length===before+1,'one compact request');
        assert.deepEqual((await compacts()).at(-1),{session_id:id,after_turn:true});
        await wait(view,`!document.querySelector(${JSON.stringify(selector)}).disabled`);
        assert.equal(await js(view,'location.hash'),route,'compacting must not navigate the containing session card');
      }
      await compactRing('#sidebar .context-ring[data-id=offline-claude]','offline-claude');
      await click(view,'#sidebar [data-act=leader][data-id=handoff-codex]');
      await compactRing('#chat-head .context-ring[data-id=handoff-codex]','handoff-codex');
      await click(view,'[data-act=view][data-view=all]');await click(view,'#main [data-act=all-tab][data-tab=tree]');
      await compactRing('#main .node .context-ring[data-id=failed-member]','failed-member');
      await click(view,'#main [data-act=card][data-id=failed-member]');
      await compactRing('#card .context-ring[data-id=failed-member]','failed-member');
      await click(view,'[data-act=card-close]');
      await overview({leaders:{'offline-claude':'working','handoff-codex':'idle'},members:{'failed-member':'working','pending-member':'offline'}},2);
      await openWorking();
      await compactRing('#main .list .context-ring[data-id=offline-claude]','offline-claude');
      await compactRing('#main .list .context-ring[data-id=failed-member]','failed-member');
      await contextMode('queued');await click(view,'#sidebar .context-ring[data-id=handoff-codex]');
      await wait(view,'document.querySelector("#sidebar .context-ring[data-id=handoff-codex]").title.includes("已排队")');
      assert(await js(view,'document.querySelector("#sidebar .context-ring[data-id=handoff-codex]").disabled'));
      await capture(view,'13-context-ring-queued');
      await contextMode('complete');
      await core.request(boot.service.url,'/api/session/compact?project=handoff-demo',{session_id:'handoff-codex',after_turn:true});
      await wait(view,'!document.querySelector("#sidebar .context-ring[data-id=handoff-codex]").disabled');
      await contextMode('failed');await click(view,'#sidebar .context-ring[data-id=handoff-codex]');
      await wait(view,'[...document.querySelectorAll(".toast.show")].some(item=>item.textContent.includes("FixtureNativeRejected"))');
      assert(await js(view,'!document.querySelector("#sidebar .context-ring[data-id=handoff-codex]").disabled'));
      for(let attempt=0;attempt<3 && !await js(view,'document.documentElement.dataset.theme === "dark"');attempt++) await click(view,'[data-act=theme]');
      assert(await js(view,'document.documentElement.dataset.theme === "dark"'));win.setSize(860,760);
      await capture(view,'14-context-ring-narrow-dark');
      await assert.rejects(fs.access(path.join(evidence.home,'fixture-model-start-attempts.txt')),{code:'ENOENT'});
      evidence.checks.push('all six context-ring surfaces target the correct CC/Codex session, use native keyboard buttons without nested buttons or card-navigation interception, and retain clear queued, completed and failed states with zero native/model starts');
      // The user-facing unread badge must count the prose reply, not the 18
      // assistant envelopes carrying tool calls before it.
      const journalFile=path.join(evidence.home,'chat','handoff-codex.jsonl');
      const recorded=(await fs.readFile(journalFile,'utf8')).trim().split('\n').map(line=>JSON.parse(line));
      let seq=recorded.at(-1).seq;
      const additions=[],at=new Date().toISOString();
      for(let index=0;index<18;index++) {
        additions.push({seq:++seq,type:'assistant',at,id:'unread-tool-'+index,content:[{type:'tool_use',id:'unread-call-'+index,name:'Shell',input:{command:'git status'}}]});
        additions.push({seq:++seq,type:'tool_result',at,tool_use_id:'unread-call-'+index,content:'clean',is_error:false});
      }
      additions.push({seq:++seq,type:'assistant',at,id:'unread-answer',content:[{type:'text',text:'Git 检查完成：18 次工具操作，只新增这一条正文回复。'}]});
      await fs.appendFile(journalFile,additions.map(value=>JSON.stringify(value)).join('\n')+'\n','utf8');
      await wait(view,'document.querySelector("#sidebar [data-id=handoff-codex] .b-unread")?.textContent === "1"');
      await capture(view,'13-unread-reply-only');
      await click(view,'[data-act=leader][data-id=handoff-codex]');
      await wait(view,'document.querySelector(".chat-inner")?.textContent.includes("只新增这一条正文回复")');
      // Returning to an existing chat intentionally restores its old scroll
      // position; actually read the new reply before expecting acknowledgment.
      await click(view,'.chat-scroll + .jump');
      await wait(view,'!document.querySelector("#sidebar [data-id=handoff-codex] .b-unread")');
      assert(await js(view,'document.querySelectorAll(".chat-inner .tool").length >= 18'));
      await assert.rejects(fs.access(path.join(evidence.home,'fixture-model-start-attempts.txt')),{code:'ENOENT'});
      evidence.checks.push('18 tool calls plus their outputs produce exactly one unread prose reply; opening and reading it clears the badge while preserving every tool record and never launching AI');
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
