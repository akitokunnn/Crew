'use strict';
// Real isolated renderer interactions. Every model/operations method is a fixture.
const fs = require('node:fs/promises'), path = require('node:path'), os = require('node:os'), assert = require('node:assert/strict');
if (!process.versions.electron) {
  const {spawn} = require('node:child_process'), env = {...process.env}; delete env.ELECTRON_RUN_AS_NODE;
  const child = spawn(require('electron'), [__filename], {env, windowsHide:true, stdio:'inherit'});
  child.on('exit', code => {process.exitCode = code;}); child.on('error', error => {console.error(error); process.exitCode = 1;});
} else {
  const {app, BrowserWindow} = require('electron'), root = path.resolve(__dirname, '../..'), output = path.join(root, 'desktop/smoke-output/operations');
  const isolated = require('node:fs').mkdtempSync(path.join(os.tmpdir(), 'crew-ops-ui-'));
  const sessionData = path.join(isolated, 'session'); require('node:fs').mkdirSync(sessionData);
  app.setPath('userData', isolated); app.setPath('sessionData', sessionData);
  let win; const errors = [], checks = [];
  async function run(code) { return win.webContents.executeJavaScript(code); }
  async function until(code) { for (let i = 0; i < 240; i++) { if (await run(code)) return; await new Promise(resolve => setTimeout(resolve,25)); } throw Error('等待超时：' + code); }
  const click = selector => run(`document.querySelector(${JSON.stringify(selector)}).click()`);
  const input = (selector, text) => run(`(()=>{const input=document.querySelector(${JSON.stringify(selector)});input.value=${JSON.stringify(text)};input.dispatchEvent(new Event('input',{bubbles:true}));})()`);
  const check = (name, value) => {assert(value, name); checks.push(name); require('node:fs').appendFileSync(path.join(output,'progress.log'),name+'\n');};
  async function shot(name) {require('node:fs').appendFileSync(path.join(output,'progress.log'),'capture '+name+'\n');await run('document.fonts.ready.then(()=>true)');await new Promise(resolve=>setTimeout(resolve,120));await fs.writeFile(path.join(output, name), (await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true})).toPNG()); }
  (async () => {
    await app.whenReady(); await fs.mkdir(output, {recursive:true});await fs.writeFile(path.join(output,'progress.log'),'');
    win = new BrowserWindow({width:1180,height:970,show:false,webPreferences:{offscreen:true,contextIsolation:true,nodeIntegration:false,backgroundThrottling:false}});
    win.webContents.on('console-message', (_event, ...args) => {const message = typeof args[0] === 'object' ? args[0].message : args[1]; if (String(message).includes('Uncaught')) errors.push(message);});
    const page = path.join(isolated,'operations.html');
    await fs.writeFile(page,'<html lang="zh-CN"><meta charset="utf-8"><body style="margin:0"><main class="crew-settings" style="height:100vh;--cs-bg:#f7f8fa;--cs-card:#fff"><div class="cs-body"><header class="cs-header"><div><h1>后台与运维</h1><p class="cs-subtitle">运维对话、状态与配置</p></div></header><div id="host" class="cs-content"></div></div></main></body></html>');
    await win.loadFile(page);
    const {pathToFileURL} = require('node:url');
    await win.webContents.insertCSS(`@font-face{font-family:"HarmonyOS Sans SC";src:url("${pathToFileURL(path.join(root,'plugin/crew/web/fonts/HarmonyOS_Sans_SC_Regular.woff2')).href}") format("woff2");font-weight:400} @font-face{font-family:"HarmonyOS Sans SC";src:url("${pathToFileURL(path.join(root,'plugin/crew/web/fonts/HarmonyOS_Sans_SC_Medium.woff2')).href}") format("woff2");font-weight:500}`);
    for (const file of ['settings-ui.css','operations-ui.css']) await win.webContents.insertCSS(await fs.readFile(path.join(root,'plugin/crew/web',file),'utf8'));
    await run(await fs.readFile(path.join(root,'plugin/crew/web/operations-ui.js'),'utf8'));
    await run(`window.fixture={reads:0,sends:[],interrupts:0,saves:[],configReads:0,healthMounts:0,healthUnmounts:0,brain:0,sendFailure:false,configFailure:false,chat:{messages:[],busy:true,error:null,working_since:new Date().toISOString()},policy:{enabled:false,repository:'C:\\Crew source',reproduce:[],verify:[],timeout_seconds:180,merge_ref:'main'}};
      window.api={snapshot:async()=>{fixture.reads++;return structuredClone(fixture.chat)},send:async body=>{fixture.sends.push(body);if(fixture.sendFailure){fixture.sendFailure=false;throw Error('测试发送失败：载体未安装')}fixture.chat.messages.push({id:body.message_id,role:'user',text:body.text,at:new Date().toISOString()});fixture.chat.busy=true;return structuredClone(fixture.chat)},interrupt:async()=>{fixture.interrupts++;return structuredClone(fixture.chat)},configGet:async()=>{fixture.configReads++;return{policy_path:'C:\\Crew Data\\policy.json',configured:false,policy:structuredClone(fixture.policy)}},configSave:async value=>{fixture.saves.push(value);if(fixture.configFailure){fixture.configFailure=false;throw Error('测试保存失败：目录不是 Crew 源码')}fixture.policy=structuredClone(value);return{policy_path:'C:\\Crew Data\\policy.json',configured:true,policy:structuredClone(value)}}};
      window.operations=CrewOperations.create({api,onBrain:()=>fixture.brain++,mountHealth:host=>{fixture.healthMounts++;host.textContent='隔离后台状态：运行正常';return()=>fixture.healthUnmounts++;}});operations.mount(document.querySelector('#host'),{home:'C:\\Crew Data',backend:'C:\\Crew\\plugin'});true;`);
    await until('document.querySelector(".co-chat-empty h2").textContent.includes("和运维")');
    check('打开后台对话不启动 AI', await run('fixture.sends.length===0&&fixture.reads===1'));
    await input('.co-composer textarea', '还在输入的草稿');
    await run('document.querySelector(".co-composer textarea").focus();document.querySelector(".co-composer textarea").setSelectionRange(2,4);fixture.chat.messages.push({id:"one",role:"assistant",text:"后台连接正常。<img src=x onerror=alert(1)>",at:new Date().toISOString()});fixture.chat.busy=false;');
    await until('document.querySelector(".co-message-text")?.textContent.includes("后台连接正常")');
    check('轮询更新保留输入焦点、内容和光标', await run('document.activeElement.matches(".co-composer textarea")&&document.activeElement.value==="还在输入的草稿"&&document.activeElement.selectionStart===2&&document.activeElement.selectionEnd===4'));
    check('AI 文本转义，不执行 HTML', await run('!document.querySelector(".co-messages img")'));
    await run('fixture.chat.messages[0].text="后台连接正常，当前没有需要处理的故障。你可以继续检查具体工作区，或在配置中调整故障处理方式。";');
    await click('[data-operations-tab="status"]');await click('[data-operations-tab="chat"]');await until('document.querySelector(".co-message-text")?.textContent.includes("没有需要处理")');
    await shot('operations-chat-light.png');
    await run('fixture.chat.messages.push(...Array.from({length:20},(_,i)=>({id:"history-"+i,role:"assistant",text:"历史诊断记录 "+i+"\\n"+"已有的连接与后台检查记录。".repeat(10)})));fixture.chat.busy=true;');
    await click('[data-operations-tab="status"]');await click('[data-operations-tab="chat"]');await until('document.querySelectorAll(".co-message").length===21');
    await run('const list=document.querySelector(".co-messages");list.scrollTop=50;list.dispatchEvent(new Event("scroll"));fixture.chat.messages.push({id:"new-history",role:"assistant",text:"新增一条诊断消息"});');
    await until('document.querySelectorAll(".co-message").length===22');
    check('查看历史时新消息不强拉滚动到底部', await run('document.querySelector(".co-messages").scrollTop===50'));
    await run('fixture.chat.messages=fixture.chat.messages.slice(0,1);fixture.chat.busy=false;');
    await click('[data-operations-tab="config"]'); await until('!!document.querySelector("[data-operations-field=repository]")');
    await input('[data-operations-field="repository"]', 'D:\\New Crew');
    await click('[data-operations-tab="chat"]');
    check('切换标签保留聊天草稿', await run('document.querySelector(".co-composer textarea").value==="还在输入的草稿"'));
    await click('[data-operations-tab="config"]');
    check('切换标签保留运维配置草稿', await run('document.querySelector("[data-operations-field=repository]").value==="D:\\\\New Crew"'));
    await run('operations.hide();document.querySelector("#host").replaceChildren();operations.mount(document.querySelector("#host"),{home:"C:/Crew Data",backend:"C:/Crew/plugin"});true;');
    check('离开设置后回来保留配置草稿', await run('document.querySelector("[data-operations-field=repository]").value==="D:\\\\New Crew"'));
    await input('[data-operations-field="reproduce"]', 'bad json'); await click('[data-operations-action="save-config"]');
    check('命令格式错误显示字段名，未提交配置', await run('fixture.saves.length===0&&document.querySelector(".co-config-status").textContent.includes("复现命令格式不正确")'));
    await input('[data-operations-field="reproduce"]', '[["python","-m","unittest","tests.test_connection"]]');
    await input('[data-operations-field="verify"]', '[["node","--test","desktop/test/connection.test.cjs"]]');
    await click('[data-operations-field="enabled"]');
    await run('fixture.configFailure=true'); await click('[data-operations-action="save-config"]'); await until('document.querySelector(".co-config-status").textContent.includes("测试保存失败")');
    check('配置保存失败显示真实原因并保留输入', await run('document.querySelector("[data-operations-field=repository]").value==="D:\\\\New Crew"&&document.querySelector("[data-operations-field=verify]").value.includes("connection.test.cjs")'));
    await click('[data-operations-action="save-config"]'); await until('document.querySelector(".co-config-status").textContent.includes("已保存")');
    check('完整保存修复开关与命令，不启动对话', await run('fixture.saves.length===2&&fixture.policy.enabled&&fixture.policy.verify[0][2]==="desktop/test/connection.test.cjs"&&fixture.sends.length===0&&document.querySelector("[data-operations-action=save-config]").disabled'));
    await shot('operations-config-light.png');
    await click('[data-operations-tab="status"]'); check('状态展示真实位置和既有健康页', await run('fixture.healthMounts===3&&document.querySelector(".co-status-facts").textContent.includes("当前启动位置")&&document.querySelector(".co-native-health").textContent.includes("运行正常")'));
    await click('[data-operations-tab="chat"]'); check('退出状态页停止健康页轮询', await run('fixture.healthUnmounts===3'));
    await run('fixture.sendFailure=true'); await click('[data-operations-action="send"]'); await until('document.querySelector(".co-chat-status").textContent.includes("测试发送失败")');
    check('发送失败保留草稿和真实原因', await run('document.querySelector(".co-composer textarea").value==="还在输入的草稿"'));
    const readsBefore = await run('fixture.reads');await until(`fixture.reads>${readsBefore}`);
    check('定时状态读取不会抹掉发送失败原因', await run('document.querySelector(".co-chat-status").textContent.includes("测试发送失败")'));
    await click('[data-operations-action="send"]'); await until('document.querySelector(".co-composer textarea").value===""');
    check('失败重试沿用幂等消息 ID，成功后清空草稿', await run('fixture.sends.length===2&&fixture.sends[0].message_id===fixture.sends[1].message_id&&!document.querySelector("[data-operations-action=interrupt]").hidden'));
    await click('[data-operations-action="interrupt"]'); await until('fixture.interrupts===1');
    check('停止按钮请求停止且保留状态反馈', await run('document.querySelector(".co-chat-status").textContent.includes("已请求停止")'));
    await click('[data-operations-action="brain"]'); check('运维大脑入口可点击', await run('fixture.brain===1'));
    await run('document.querySelector("[data-operations-tab=chat]").dispatchEvent(new KeyboardEvent("keydown",{key:"ArrowRight",bubbles:true}));');
    check('标签支持方向键切换并移动焦点', await run('document.querySelector("[data-operations-tab=status]").getAttribute("aria-selected")==="true"&&document.activeElement.dataset.operationsTab==="status"'));
    await run('document.activeElement.dispatchEvent(new KeyboardEvent("keydown",{key:"ArrowLeft",bubbles:true}));');
    win.setSize(800,780); await run('document.querySelector(".crew-settings").style.cssText+=";--cs-bg:#1c1f24;--cs-card:#24282f;--cs-line:#373d46;--cs-text:#e3e7ed;--cs-muted:#a0a9b7;--cs-accent:#83bfb2";');
    await shot('operations-chat-dark-narrow.png'); check('窄窗口无水平溢出', await run('document.documentElement.scrollWidth<=innerWidth+1'));
    await run('operations.hide()'); assert.deepEqual(errors,[]);
    await fs.writeFile(path.join(output,'evidence.json'), JSON.stringify({ok:true,checks,errors,isolatedUserData:isolated,noNativeModels:true},null,2));
    console.log('Operations renderer smoke passed:', checks.length, 'checks;', output); win.destroy(); app.exit(0);
  })().catch(async error => {console.error(error); await fs.mkdir(output,{recursive:true}); await fs.writeFile(path.join(output,'failure.txt'),error.stack||String(error));if(win&&!win.isDestroyed())await fs.writeFile(path.join(output,'failure-state.json'),JSON.stringify(await run('({fixture,state:operations.getState(),html:document.querySelector("#host").innerHTML})'),null,2));win?.destroy(); app.exit(1);});
}
