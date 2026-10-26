'use strict';
// Real Chromium IME composition + real app polling, served by a fixture-only API.
// Run with node desktop/test/todo-ime-smoke.cjs. No Crew service or model is used.
const electron = require('electron');
if (typeof electron === 'string') {
  const {spawn} = require('node:child_process');
  const env = {...process.env};
  for (const key of ['ELECTRON_RUN_AS_NODE','CREW_SESSION_ID','CLAUDE_CODE_SESSION_ID']) delete env[key];
  const child = spawn(electron, [__filename], {env, windowsHide:true, stdio:'inherit'});
  child.on('error', error => {console.error(error); process.exitCode=1;});
  child.on('exit', code => {process.exitCode=code ?? 1;});
} else {
  const {app,BrowserWindow}=electron, fs=require('node:fs'), path=require('node:path'),
    os=require('node:os'), http=require('node:http'), assert=require('node:assert/strict');
  const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'crew-todo-ime-'));
  app.setPath('userData',temporary); app.setPath('sessionData',temporary);
  const web=path.resolve(__dirname,'../../plugin/crew/web');
  const calls=[],errors=[]; let server,win,origin,polls=0;
  const state={project:{name:'fixture',root:'C:/fixture',theme:'green'},projects:[],themes:[{id:'green',name:'竹青',light:{},dark:{}}],
    leaders:[],members:[],goals:[],decisions:[],attention:[],native_approvals:[],
    todos:[{id:1,title:'验证授权',steps:'打开设置',verification:'显示已授权',status:'open',version:1,
      session_id:'fixture-member',leader_session_id:null,proposer_name:'组员',group_name:'测试组',created_at:new Date().toISOString()}],
    runtimes:[],ai:{default:null},candidates:{running:[],others:[]},
    limits:{claude:{seven_day:null,five_hour:null},codex:null},channel:{unread:0},
    notification:{manual_away:false,away:false,error:null,channel:{state:'unconfigured',detail:null}}};
  const json=(res,value)=>{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(value));};
  async function route(req,res) {
    const name=new URL(req.url,origin).pathname;
    if(name==='/api/state'){polls++;return json(res,state);}
    if(name==='/api/runtimes')return json(res,{runtimes:[]});
    if(req.method==='POST'){
      let raw='';for await(const part of req)raw+=part;
      const body=JSON.parse(raw);calls.push({name,body});
      if(name==='/api/todo/resolve'){state.todos=[];return json(res,{ok:true,before:{},version:2});}
      throw Error('Unexpected POST '+name);
    }
    const file=path.resolve(web,'.'+(name==='/'?'/index.html':name));
    if(!file.startsWith(web+path.sep))throw Error('Invalid fixture path');
    try {const content=await fs.promises.readFile(file);res.writeHead(200,{'Content-Type':({'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml'})[path.extname(file)]||'application/octet-stream'});res.end(content);}
    catch(error){if(error.code!=='ENOENT')throw error;res.writeHead(404);res.end();}
  }
  const js=code=>win.webContents.executeJavaScript(code);
  async function until(predicate,label){for(let n=0;n<250;n++){if(await predicate())return;await new Promise(resolve=>setTimeout(resolve,25));}throw Error('Timeout: '+label);}
  const wait=code=>until(()=>js(code),code);
  async function compose(text){await win.webContents.debugger.sendCommand('Input.imeSetComposition',{text,selectionStart:text.length,selectionEnd:text.length});}
  (async()=>{
    try {
      await app.whenReady();
      server=http.createServer((req,res)=>route(req,res).catch(error=>{errors.push(error.stack);res.writeHead(500);res.end();}));
      await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin='http://127.0.0.1:'+server.address().port;
      win=new BrowserWindow({show:false,webPreferences:{offscreen:true,backgroundThrottling:false,contextIsolation:true,nodeIntegration:false,sandbox:true}});
      win.webContents.on('console-message',(_event,...args)=>{const message=typeof args[0]==='object'?args[0].message:args[1];if(String(message).includes('Uncaught'))errors.push(message);});
      win.webContents.debugger.attach('1.3');
      await win.loadURL(origin+'/?project=fixture#todos');
      await wait('!!document.querySelector("[data-act=todo-action]")');
      await js('document.querySelector("[data-act=todo-action]").click()');
      await wait('!!document.querySelector("#todo-note-1")');
      await js(`window.originalNote=document.querySelector('#todo-note-1'); originalNote.focus();
        window.imeEvents=[];for(const type of ['compositionstart','compositionupdate','compositionend','input'])
          originalNote.addEventListener(type,e=>imeEvents.push({type,data:e.data,value:originalNote.value}));`);
      await compose('yi');
      const mark=polls;
      state.todos[0].title='授权步骤已更新';
      await until(()=>polls>mark,'real three-second state poll');
      await js('new Promise(resolve=>setTimeout(resolve,100))');
      const during=await js('({same:originalNote===document.querySelector("#todo-note-1"),focused:document.activeElement===originalNote,value:document.querySelector("#todo-note-1").value,events:imeEvents})');
      console.log(JSON.stringify({during},null,2));
      assert(during.same && during.focused,'Polling must retain the composing textarea itself');
      assert.equal(during.events.filter(e=>e.type==='compositionend').length,0,'poll must not commit pinyin');
      assert.equal(await js('document.querySelector(".todo-title").textContent'),'验证授权','composition defers its containing surface');
      await compose('yi shou quan');
      await win.webContents.debugger.sendCommand('Input.insertText',{text:'已授权'});
      await wait('document.querySelector("#todo-note-1").value==="已授权"');
      await js('new Promise(resolve=>setTimeout(resolve,100))');
      assert.equal(await js('document.querySelector(".todo-title").textContent'),'授权步骤已更新','pending changes render after final input');
      assert.equal(calls.length,0,'candidate confirmation must not submit');
      await js('document.querySelector("[data-todo-form] button[type=submit]").click()');
      await until(()=>calls.length===1,'explicit completion submission');
      assert.deepEqual(calls,[{name:'/api/todo/resolve',body:{id:1,status:'done',note:'已授权',expected_version:1}}]);
      await wait('!document.querySelector("#todo-note-1")');
      assert.deepEqual(errors,[]);
      console.log(JSON.stringify({ok:true,checks:['native composition survives polling','committed Chinese has no pinyin residue','only explicit confirmation submits'],temporary}));
    } catch(error){console.error(error);console.error(await js('document.body.innerText').catch(()=>''),errors);process.exitCode=1;}
    finally {win?.destroy();server?.closeAllConnections();server?.close();app.exit(process.exitCode||0);}
  })();
}
