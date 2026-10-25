'use strict';
// Real renderer + in-memory loopback API. No Crew HOME, CLI or model is used.
const {app,BrowserWindow}=require('electron'),fs=require('node:fs/promises'),path=require('node:path'),os=require('node:os'),http=require('node:http'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..'),web=path.join(root,'plugin/crew/web'),output=path.join(root,'desktop/smoke-output/git-settings');
app.commandLine.appendSwitch('force-device-scale-factor','1');
const calls=[],errors=[],checks=[],shots=[],waiting=[];
let win,server,origin,home,graph='pending',failSave=false,staleSave=false;
const leader={session_id:'git-chief',name:'Git 管理',runtime:'codex',model:null,effort:null,status:'offline',where:'offline',busy:false,running:false,web_chat:false,last_seq:0,read_seq:0,last_reply_id:null,unread:0,pending:0,priority:null,goal:'协调项目提交与合并',task:'协调项目提交与合并',context:null,activity:null,codex:[],codex_older:0,member_ids:[],applied:null,trust:{enabled:false},runtime_switch:{allowed:true}};
const catalog=[{id:'codex',name:'Codex',available:true,models:[],efforts:[]},{id:'claude',name:'Claude Code',available:true,models:[],efforts:[]}];
const project={name:'MIB fixture',display_name:'MIB 协作',root:'C:/fixture/MIB',theme:'green'};
const state={project,projects:[project],themes:[{id:'green',name:'青绿',light:{'--accent':'#3E7C74','--on-accent':'#F7FBFA','--accent-text':'#2E6760'},dark:{'--accent':'#83B9A8','--on-accent':'#182A24','--accent-text':'#A8D6C7'}}],runtimes:catalog,ai:{default:{runtime:'codex',model:null,effort:null},digest:null,git:null,operations:null},leaders:[leader],members:[],goals:[],decisions:[],todos:[],attention:[],candidates:{running:[],others:[]},limits:{claude:{seven_day:null,five_hour:null},codex:null},channel:{unread:0,latest_id:0,digesting:false,digest_error:null,digest_id:null},notification:{manual_away:false,away:false,error:null,channel:{state:'connected',detail:'本地夹具'}},vscode_error:null};
let settings={project,revision:'initial',config:null,available:true,choices:{branches:['develop','master','feature/long-name'],base:'develop',stable:'master'},leaders:[{session_id:leader.session_id,name:leader.name,cwd:'C:/fixture/MIB/Temp/wt/git',runtime:'codex',model:null,effort:null}],remotes:[{name:'origin',url:'https://github.com/example/project.git',direction:'fetch'},{name:'origin',url:'https://github.com/example/project.git',direction:'push'}]};
function json(response,value,status=200){response.writeHead(status,{'Content-Type':'application/json'});response.end(JSON.stringify(value));}
async function route(request,response){
  const url=new URL(request.url,origin),name=url.pathname;let body;
  if(request.method==='POST'){let raw='';for await(const part of request)raw+=part;body=JSON.parse(raw||'{}');}
  if(!name.startsWith('/api/')){
    const file=path.resolve(web,'.'+(name==='/'?'/index.html':name));
    if(!file.startsWith(web+path.sep)){response.writeHead(403);return response.end();}
    try{const content=await fs.readFile(file);response.writeHead(200,{'Content-Type':({'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml','.woff2':'font/woff2'})[path.extname(file)]||'application/octet-stream'});return response.end(content);}catch(_){response.writeHead(404);return response.end();}
  }
  calls.push({name,method:request.method,body});
  if(name==='/api/state')return json(response,state);
  if(name==='/api/runtimes')return json(response,{runtimes:catalog});
  if(name==='/api/git'){
    if(graph==='pending'){waiting.push(response);return;}
    if(graph==='error')return json(response,{error:'fixture Git 读取超时，请重试'},503);
    return json(response,{enabled:false,repository:'MIB fixture',choices:settings.choices});
  }
  if(name==='/api/git/settings'){
    if(request.method!=='POST')return json(response,settings);
    if(failSave){failSave=false;return json(response,{error:'fixture 保存失败，请保留草稿重试'},500);}
    if(staleSave){staleSave=false;settings.revision='changed-by-ai';return json(response,{error:'Git 设置已被 AI 修改，请重新读取后再保存。'},409);}
    assert.equal(body.project,project.name);assert.equal(body.revision,settings.revision);
    settings={...settings,revision:settings.revision+'-saved',config:body.enabled===false?null:{...body,stable:body.stable||null}};
    return json(response,{ok:true,config:settings.config,revision:settings.revision});
  }
  return json(response,{error:'Unexpected fixture route: '+name},404);
}
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const run=expression=>win.webContents.executeJavaScript(expression);
async function wait(expression){for(let i=0;i<200;i++){if(await run(expression))return;await sleep(25);}throw Error('Timed out: '+expression);}
async function click(selector){await run(`(()=>{const node=document.querySelector(${JSON.stringify(selector)});if(!node)throw Error('Missing '+${JSON.stringify(selector)});node.click();})()`);}
async function fill(selector,value){await run(`(()=>{const node=document.querySelector(${JSON.stringify(selector)});node.value=${JSON.stringify(value)};node.dispatchEvent(new Event('input',{bubbles:true}));})()`);}
async function shot(name){await sleep(120);await fs.writeFile(path.join(output,name),(await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true})).toPNG());shots.push(name);}
async function check(name,fn){await fn();checks.push(name);}
async function main(){
  home=await fs.mkdtemp(path.join(os.tmpdir(),'crew-git-settings-ui-'));app.setPath('userData',home);await app.whenReady();await fs.mkdir(output,{recursive:true});
  server=http.createServer((request,response)=>{route(request,response).catch(error=>{errors.push(error.stack);if(!response.headersSent)json(response,{error:error.message},500);else response.end();});});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin='http://127.0.0.1:'+server.address().port;
  win=new BrowserWindow({show:false,width:1440,height:900,webPreferences:{offscreen:true,contextIsolation:true,nodeIntegration:false}});
  win.webContents.on('console-message',event=>{if(event.level==='error')errors.push(event.message);});
  await win.loadURL(origin+'/?project='+encodeURIComponent(project.name)+'#git');
  await wait(`document.querySelector('[data-git-act="settings"]') !== null`);
  await check('提交图未返回时，Git 设置已可打开',async()=>{await click('[data-git-act="settings"]');await wait(`document.querySelector('#gits-admin') !== null`);assert.equal(await run(`document.querySelector('.git-settings-dialog').open`),true);assert(waiting.length>0);});
  await check('绑定已有负责人，原生大脑和仓库信息可查',async()=>{await fill('#gits-admin',leader.session_id);await fill('#gits-policy','测试和 CI 通过后提交 PR；合并到稳定分支前让我验收。');assert.match(await run(`document.querySelector('[data-git-admin-info]').textContent`),/Codex/);await shot('01-绑定已有Git管理员.png');});
  await check('后台轮询不重置填写中的设置',async()=>{await sleep(3200);assert.match(await run(`document.querySelector('#gits-policy').value`),/CI/);});
  await check('关闭和重新打开保留未保存草稿',async()=>{await click('[data-git-setting="close"]');await click('[data-git-act="settings"]');await wait(`document.querySelector('.git-settings-dialog').open`);assert.equal(await run(`document.querySelector('#gits-admin').value`),leader.session_id);});
  await check('保存失败保留输入，再次保存不重复召唤',async()=>{failSave=true;await click('[data-git-setting="save"]');await wait(`document.querySelector('[data-git-settings-note]').textContent.includes('保存失败')`);assert.match(await run(`document.querySelector('#gits-policy').value`),/CI/);graph='error';await click('[data-git-setting="save"]');await wait(`document.querySelector('[data-git-settings-note]').textContent.includes('已保存')`);assert.equal(settings.config.admin_session_id,leader.session_id);assert.equal(settings.config.base,'develop');});
  await check('AI 同时更新配置时提示冲突，明确放弃草稿后读取最新',async()=>{await fill('#gits-policy','不要覆盖 AI 的新规则');staleSave=true;await click('[data-git-setting="save"]');await wait(`document.querySelector('[data-git-settings-note]').textContent.includes('重新读取')`);assert.equal(await run(`document.querySelector('#gits-policy').value`),'不要覆盖 AI 的新规则');await click('[data-git-setting="reload"]');await click('[data-git-setting="discard"]');await wait(`document.querySelector('#gits-policy').value.includes('CI')`);});
  await check('深色与窄窗口不横向溢出',async()=>{await run(`CrewTheme.setMode('dark');CrewTheme.apply(${JSON.stringify(state.themes)},'green','dark');void 0`);win.setSize(900,780);await sleep(180);assert.equal(await run(`document.querySelector('.git-settings-dialog').scrollWidth <= document.querySelector('.git-settings-dialog').clientWidth+1`),true);await shot('02-深色Git设置.png');win.setSize(540,800);await sleep(180);assert.equal(await run(`document.querySelector('.git-settings-dialog').scrollWidth <= document.querySelector('.git-settings-dialog').clientWidth+1`),true);await shot('03-窄窗口Git设置.png');win.setSize(1440,900);});
  await check('停用先确认，只解除绑定',async()=>{await click('[data-git-setting="disable"]');assert.notEqual(settings.config,null);await click('[data-git-setting="confirm-disable"]');await wait(`document.querySelector('[data-git-settings-note]').textContent.includes('已解除')`);assert.equal(settings.config,null);assert.equal(state.leaders.length,1);});
  await check('图读取失败有真实错误和重试入口，设置仍可打开',async()=>{for(const response of waiting.splice(0))json(response,{error:'fixture Git 读取超时，请重试'},503);await click('[data-git-setting="close"]');await wait(`document.querySelector('[data-git-act="check"]') !== null`);assert.match(await run(`document.querySelector('#git-view').textContent`),/读取超时/);graph='ready';await click('[data-git-act="check"]');await wait(`document.querySelector('#git-enable-form') !== null`);await click('[data-git-act="settings"]');await wait(`document.querySelector('#gits-admin') !== null`);});
  await check('新建专用管理员返回现有创建表单，不自动启动 AI',async()=>{await wait(`document.querySelector('[data-git-setting="create"]')?.disabled === false`);await click('[data-git-setting="create"]');await wait(`document.querySelector('#git-enable-form') !== null`);assert.equal(await run(`document.querySelector('.git-settings-dialog').open`),false);await click('[data-git-act="settings"]');await wait(`document.querySelector('#gits-admin') !== null`);});
  await check('仓库错误仍显示绑定信息与原始原因，禁止误保存',async()=>{await click('[data-git-setting="close"]');settings={...settings,available:false,reason:'fixture repository access denied'};await click('[data-git-act="settings"]');await wait(`document.querySelector('.gits-warning') !== null`);assert.match(await run(`document.querySelector('.gits-warning').textContent`),/access denied/);assert.equal(await run(`document.querySelector('[data-git-setting="save"]').disabled`),true);});
  assert.deepEqual(calls.filter(call=>call.method==='POST'&&call.name!=='/api/git/settings'),[],'No model start, stop, archive, fetch or other writes');
  assert.deepEqual(errors,[]);
}
main().then(async()=>{await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify({ok:true,checks,shots,calls},null,2));console.log(JSON.stringify({ok:true,checks:checks.length,output}));await finish(0);}).catch(async error=>{console.error(error);await fs.mkdir(output,{recursive:true});await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify({ok:false,error:String(error.stack),checks,shots,errors,calls},null,2));await finish(1);});
async function finish(code){win?.destroy();for(const response of waiting.splice(0))response.end();server?.closeAllConnections();if(server)await new Promise(resolve=>server.close(resolve));if(home)await fs.rm(home,{recursive:true,force:true,maxRetries:10,retryDelay:100}).catch(()=>{});app.exit(code);}
