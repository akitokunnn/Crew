'use strict';
// Real renderer + fixture-only loopback API. Run: node desktop/test/sidebar-smoke.cjs --before
const electron = require('electron');
if (typeof electron === 'string') {
  const {spawn} = require('node:child_process');
  const env = {...process.env}; delete env.ELECTRON_RUN_AS_NODE;
  const child = spawn(electron, [__filename, ...process.argv.slice(2)], {env, windowsHide:true, stdio:'inherit'});
  child.on('error', error => { console.error(error); process.exitCode=1; });
  child.on('exit', code => { process.exitCode=code ?? 1; });
} else {
  const {app,BrowserWindow} = electron;
  const fs = require('node:fs/promises'), syncFs = require('node:fs'), path = require('node:path'), os = require('node:os'), http = require('node:http');
  const root = path.resolve(__dirname, '../..');
  const phase = process.argv.includes('--short-before') ? 'short-before' : process.argv.includes('--before') ? 'before' : 'after';
  const captureOnly = process.argv.includes('--capture-only');
  const probe518 = process.argv.includes('--probe518');
  const web = path.join(root,phase==='short-before'?'desktop/.cache/sidebar-before-short-window':phase==='before'?'desktop/.cache/sidebar-before-b650da6':'plugin/crew/web');
  const output = path.join(root, 'desktop/smoke-output/sidebar', probe518?'probe518':phase);
  const calls=[], errors=[], checks=[], results=[], streams=new Set(); let server, win, origin, temporary;
  temporary=syncFs.mkdtempSync(path.join(os.tmpdir(),'crew-sidebar-smoke-'));
  app.setPath('userData',temporary);app.setPath('sessionData',temporary);
  app.commandLine.appendSwitch('user-data-dir',temporary);
  app.commandLine.appendSwitch('disk-cache-dir',path.join(temporary,'cache'));
  app.on('window-all-closed',()=>{});
  const palette = {'--p1':'#008FA8','--p2':'#B52B96','--p3':'#5555DC'};
  const theme = {id:'celadon',name:'青瓷',light:{...palette,'--accent':'#3E7C74','--accent-text':'#2E6760','--on-accent':'#F7FBFA'},dark:{...palette,'--accent':'#7DB3AA','--accent-text':'#7DB3AA','--on-accent':'#15201E'}};
  function person(index) {
    return {session_id:'leader-'+index,name:index===1?'多模态':index===2?'语音和声纹':'资料组'+String(index).padStart(2,'0'),
      runtime:'codex',model:null,effort:null,status:'idle',where:'bg',busy:false,running:true,web_chat:true,
      last_seq:0,read_seq:0,last_reply_id:null,unread:index<=2?314:0,pending:index<=2?3:0,
      priority:index<=2?index:null,goal:'调度自己的成员，按原始要求完成验证与交付',task:'隔离布局验收',
      context:index<=2?{used:1300,max:10000}:null,activity:null,codex:[],codex_older:0,member_ids:[],
      applied:null,trust:{enabled:false},runtime_switch:{allowed:true}};
  }
  const state = {project:{name:'侧栏布局验收',root:'C:/fixture/plain-workspace',theme:'celadon'},projects:[{name:'侧栏布局验收',theme:'celadon'}],themes:[theme],
    runtimes:[{id:'codex',name:'Codex',available:true,models:[],efforts:[]}],
    ai:{default:{runtime:'codex',model:null,effort:null},digest:null,git:null,operations:null},
    leaders:Array.from({length:20},(_,index)=>person(index+1)),members:[],goals:[],decisions:[],todos:[],attention:[],
    candidates:{running:[],others:[]},native_approvals:[],limits:{claude:{seven_day:null,five_hour:null},codex:null},
    channel:{unread:0,latest_id:0,digesting:false,digest_error:null,digest_id:null},
    notification:{manual_away:false,away:false,error:null,channel:{state:'connected',detail:'隔离 QQ 连接'}},vscode_error:null};
  const json=(response,value,status=200)=>{response.writeHead(status,{'Content-Type':'application/json'});response.end(JSON.stringify(value));};
  async function route(request,response) {
    const url=new URL(request.url,origin), name=url.pathname;let body;
    if(request.method==='POST') {let raw='';for await(const part of request)raw+=part;body=JSON.parse(raw||'{}');}
    if(!name.startsWith('/api/')) {
      const file=path.resolve(web,'.'+(name==='/'?'/index.html':name));
      if(!file.startsWith(web+path.sep)) {response.writeHead(403);return response.end();}
      try { const content=await fs.readFile(file);response.writeHead(200,{'Content-Type':({'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml','.woff2':'font/woff2'})[path.extname(file)]||'application/octet-stream'});return response.end(content); }
      catch(error) { if(error.code!=='ENOENT')throw error; response.writeHead(404);return response.end(); }
    }
    calls.push({method:request.method,name,body,query:Object.fromEntries(url.searchParams)});
    if(name==='/api/state')return json(response,{...state,project:url.searchParams.has('project')?state.project:null});
    if(name==='/api/runtimes')return json(response,{runtimes:state.runtimes});
    if(name==='/api/chat/history')return json(response,{events:[],busy:false,read_seq:0,execution_id:null});
    if(name==='/api/chat/events') {response.writeHead(200,{'Content-Type':'text/event-stream'});response.write(': isolated fixture\n\n');streams.add(response);response.on('close',()=>streams.delete(response));return;}
    if(name==='/api/chat/read')return json(response,{read_seq:0});
    if(name==='/api/comments')return json(response,{comments:[]});
    if(name==='/api/documents')return json(response,{documents:[]});
    if(name==='/api/settings')return json(response,{ai:state.ai,execution:{codex:'workspace-write',claude:'default'},runtimes:state.runtimes});
    if(name==='/api/leaders/priority') {
      if(!Array.isArray(body.ranks)||body.ranks.length>3||body.ranks.some(id=>!state.leaders.some(item=>item.session_id===id)))throw Error('Invalid fixture ranks');
      for(const item of state.leaders) {const index=body.ranks.indexOf(item.session_id);item.priority=index<0?null:index+1;}
      return json(response,{ok:true});
    }
    errors.push('Unexpected fixture API: '+request.method+' '+name);
    return json(response,{error:'Fixture does not execute '+name},409);
  }
  async function evaluate(expression) {
    const result=await win.webContents.executeJavaScript(`(async()=>{try{return {value:await (${expression})};}catch(error){return {error:error.stack};}})()`);
    if(result.error)throw Error(result.error);return result.value;
  }
  async function wait(expression) {
    for(let attempt=0;attempt<160;attempt++) {if(await evaluate(expression))return;await new Promise(resolve=>setTimeout(resolve,30));}
    throw Error('Timed out waiting for '+expression);
  }
  function check(caseId,name,ok,details) {checks.push({case:caseId,name,ok:!!ok,details});}
  const scrollFinder = `function(){const last=document.querySelector('#sidebar [data-act="leader"][data-id="leader-20"]');const candidates=[document.querySelector('#sidebar'),...document.querySelectorAll('#sidebar *')].filter(node=>node.contains(last)&&/auto|scroll/.test(getComputedStyle(node).overflowY)&&node.scrollHeight>node.clientHeight+8);return candidates.at(-1)||document.querySelector('#sidebar');}`;
  const listWindow = `function(){const scroll=(${scrollFinder})(),r=scroll.getBoundingClientRect();const port={top:r.top+scroll.clientTop,bottom:r.top+scroll.clientTop+scroll.clientHeight,left:r.left+scroll.clientLeft,right:r.left+scroll.clientLeft+scroll.clientWidth,height:scroll.clientHeight};const inside=node=>{const b=node.getBoundingClientRect();return b.width>0&&b.height>0&&b.top>=port.top-1&&b.bottom<=port.bottom+1&&b.left>=port.left-1&&b.right<=port.right+1;};const cards=[...scroll.querySelectorAll('[data-act="leader"]')];const complete=cards.filter(inside);return {port,cardHeight:cards[0]?.getBoundingClientRect().height??0,complete:complete.map(node=>node.dataset.id),readable:complete.filter(node=>[node.querySelector('.leader-name')||node.querySelector('.nm'),...node.querySelectorAll('.b-unread,.b-pending,.context-ring')].filter(Boolean).every(inside)).map(node=>node.dataset.id)};}`;
  const layout = `function(){const rect=node=>{if(!node)return null;const r=node.getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,width:r.width,height:r.height};};const visible=node=>!!node&&node.getClientRects().length>0;const sidebar=document.querySelector('#sidebar'),scroll=(${scrollFinder})();return {width:innerWidth,height:innerHeight,documentWidth:document.documentElement.scrollWidth,sidebar:{...rect(sidebar),scrollWidth:sidebar.scrollWidth,clientWidth:sidebar.clientWidth,scrollTop:sidebar.scrollTop},footer:rect(document.querySelector('.sb-foot')),settings:rect(document.querySelector('#sidebar [data-act="settings"]')),qq:rect(document.querySelector('.qq-status')),scroll:{tag:scroll.tagName,classes:scroll.className,id:scroll.id,top:scroll.scrollTop,height:scroll.clientHeight,full:scroll.scrollHeight,containsFooter:scroll.contains(document.querySelector('.sb-foot'))},leaders:['leader-1','leader-2'].map(id=>{const node=document.querySelector('#sidebar [data-act="leader"][data-id="'+id+'"]');const name=id==='leader-1'?'多模态':'语音和声纹';const walker=document.createTreeWalker(node,NodeFilter.SHOW_TEXT);let text;while((text=walker.nextNode())&&text.textContent.trim()!==name){}const range=document.createRange();if(text)range.selectNodeContents(text);const lines=text?[...range.getClientRects()].filter(r=>r.width>0&&r.height>0):[];return {id,name,lines:[...new Set(lines.map(r=>Math.round(r.top)))].length,text:lines.map(r=>({top:r.top,width:r.width,height:r.height})),bounds:rect(node),unread:visible(node.querySelector('.b-unread')),pending:visible(node.querySelector('.b-pending')),context:visible(node.querySelector('.context-ring'))};})};}`;
  const selectionStyle = `function(){const node=document.querySelector('#sidebar [data-id="leader-2"]');const style=(part)=>{const s=getComputedStyle(node,part);return [s.backgroundColor,s.backgroundImage,s.borderColor,s.borderWidth,s.boxShadow,s.content].map(value=>value.replace(/from [-\\d.]+deg,\\s*/g,''));};return {active:node.classList.contains('active'),ariaCurrent:node.getAttribute('aria-current'),style:[style(null),style('::before'),style('::after'),getComputedStyle(node.querySelector('.nm')).color]};}`;
  async function capture(name) {
    await fs.writeFile(path.join(output,name),(await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true})).toPNG());
    const sidebar=name.match(/^(light|dark)-1024x700-top\.png$/);
    if(sidebar)await fs.writeFile(path.join(output,'sidebar-'+sidebar[1]+'.png'),(await win.webContents.capturePage({x:0,y:0,width:268,height:700},{stayHidden:true,stayAwake:true})).toPNG());
  }
  async function key(name,code,virtual,text) {
    await win.webContents.debugger.sendCommand('Input.dispatchKeyEvent',{type:'keyDown',key:name,code,windowsVirtualKeyCode:virtual,nativeVirtualKeyCode:virtual,...(text?{text,unmodifiedText:text}:{})});
    await win.webContents.debugger.sendCommand('Input.dispatchKeyEvent',{type:'keyUp',key:name,code,windowsVirtualKeyCode:virtual,nativeVirtualKeyCode:virtual});
  }
  async function exercise(mode,width,height,fontScale=1) {
    const caseId=mode+'-'+width+'x'+height+(fontScale===1?'':'-font140');
    win=new BrowserWindow({width,height,useContentSize:true,show:false,webPreferences:{contextIsolation:true,nodeIntegration:false,sandbox:true,offscreen:true,backgroundThrottling:false}});
    win.webContents.debugger.attach('1.3');
    win.webContents.on('console-message',(_event,...args)=>{const message=typeof args[0]==='object'?args[0].message:args[1];if(String(message).includes('Uncaught'))errors.push(message);});
    await win.loadURL(origin);await evaluate(`(()=>{localStorage.setItem('crew.theme',${JSON.stringify(mode)});localStorage.setItem('crew.fontScale',${fontScale});})()`);
    await win.loadURL(origin+'/?project='+encodeURIComponent(state.project.name)+'#leader=leader-2');
    await wait(`document.querySelectorAll('#sidebar [data-act="leader"]').length===20&&document.querySelector('#chat-head')`);
    await evaluate('document.fonts.ready.then(()=>true)');await win.webContents.insertCSS('*{animation:none!important;transition:none!important}');
    await evaluate(`(${scrollFinder})().scrollTop=0`);
    const top=await evaluate('('+layout+')()');top.listWindow=await evaluate('('+listWindow+')()');await capture(caseId+'-top.png');
    if(captureOnly) {win.destroy();win=null;return;}
    check(caseId,'middle viewport can contain a current leader card',top.listWindow.cardHeight>0&&top.listWindow.port.height+1>=top.listWindow.cardHeight,top.listWindow);
    check(caseId,'initial list displays at least one complete card',top.listWindow.complete.length>0,top.listWindow);
    check(caseId,'initial complete card title and state are not clipped by list viewport',top.listWindow.readable.length>0,top.listWindow);
    if(probe518) {results.push({case:caseId,top});win.destroy();win=null;return;}
    for(const leader of top.leaders) {
      check(caseId,leader.id+' title stays on one line',leader.lines===1,leader);
      check(caseId,leader.id+' unread, pending and context remain visible',leader.unread&&leader.pending&&leader.context,leader);
    }
    const counts=await evaluate(`['leader-1','leader-2'].map(id=>{const n=document.querySelector('#sidebar [data-id="'+id+'"]');return {id,unread:n.querySelector('.b-unread')?.textContent.trim(),pending:n.querySelector('.b-pending')?.textContent.trim(),context:n.querySelector('.context-pct')?.textContent.trim()};})`);
    for(const item of counts)check(caseId,item.id+' preserves full 314 / 3 / 13% values',item.unread==='314'&&item.pending?.match(/\d+/)?.[0]==='3'&&item.context==='13%',item);
    check(caseId,'no document or sidebar horizontal overflow',top.documentWidth<=top.width+1&&top.sidebar.scrollWidth<=top.sidebar.clientWidth+1,top);
    check(caseId,'QQ and settings visible at list top',top.qq.top>=0&&top.qq.bottom<=top.height&&top.settings.top>=0&&top.settings.bottom<=top.height,top);
    check(caseId,'only middle list scrolls; footer is outside scroll area',top.scroll.id!=='sidebar'&&!top.scroll.containsFooter,top.scroll);
    const wheelPoint=await evaluate(`(()=>{const r=(${scrollFinder})().getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+Math.min(r.height/2,100)};})()`);
    await win.webContents.debugger.sendCommand('Input.dispatchMouseEvent',{type:'mouseMoved',...wheelPoint});
    await win.webContents.debugger.sendCommand('Input.dispatchMouseEvent',{type:'mouseWheel',...wheelPoint,deltaX:0,deltaY:170});
    let wheeled;
    for(let attempt=0;attempt<20;attempt++) {wheeled=await evaluate('('+layout+')()');if(wheeled.scroll.top>0)break;await new Promise(resolve=>setTimeout(resolve,25));}
    check(caseId,'real wheel scrolls middle while footer stays fixed',wheeled.scroll.top>0&&Math.abs(wheeled.footer.top-top.footer.top)<=1,{before:top.scroll,after:wheeled.scroll,footerBefore:top.footer,footerAfter:wheeled.footer});
    const selected=await evaluate('('+selectionStyle+')()');
    await evaluate(`document.querySelector('#sidebar [data-id="leader-3"]').click()`);
    await wait('location.hash.includes("leader-3")');
    const unselected=await evaluate('('+selectionStyle+')()');
    check(caseId,'selected priority leader has a distinct persistent visual',selected.active&&!unselected.active&&JSON.stringify(selected.style)!==JSON.stringify(unselected.style),{selected,unselected});
    await evaluate(`(${scrollFinder})().scrollTop=170`);
    const before=await evaluate('('+layout+')()');
    const previousName=state.leaders[10].name;state.leaders[10].name='资料组11刷新'+caseId;
    await wait(`document.querySelector('#sidebar [data-id="leader-11"]').textContent.includes(${JSON.stringify(state.leaders[10].name)})`);
    const refreshed=await evaluate('('+layout+')()');
    check(caseId,'state refresh preserves the middle scroll position',before.scroll.top>0&&Math.abs(refreshed.scroll.top-before.scroll.top)<=1,{before:before.scroll,after:refreshed.scroll});
    state.leaders[10].name=previousName;
    await evaluate(`(${scrollFinder})().scrollTop=100000`);
    const bottom=await evaluate('('+layout+')()');await capture(caseId+'-bottom.png');
    check(caseId,'footer stays fixed while list scrolls',Math.abs(bottom.footer.top-top.footer.top)<=1&&bottom.settings.bottom<=bottom.height,{top:top.footer,bottom:bottom.footer});
    await evaluate(`(()=>{(${scrollFinder})().scrollTop=0;document.querySelector('#sidebar [data-id="leader-1"]').focus();})()`);
    let reached=false;
    for(let step=0;step<70;step++) {
      if(await evaluate('document.activeElement?.matches(\'#sidebar [data-act="leader"][data-id="leader-20"]\')')) {reached=true;break;}
      await key('Tab','Tab',9);
      await new Promise(resolve=>setTimeout(resolve,12));
    }
    const focused=await evaluate(`(()=>{const n=document.activeElement,r=n.getBoundingClientRect(),list=(${listWindow})();return {id:n.dataset.id,top:r.top,bottom:r.bottom,height:innerHeight,port:list.port};})()`);
    check(caseId,'keyboard can reach and reveal the last leader',reached&&focused.top>=focused.port.top-1&&focused.bottom<=focused.port.bottom+1,focused);
    if(reached) {
      await key('Enter','Enter',13,'\r');
      for(let attempt=0;attempt<20&&!await evaluate('location.hash.includes("leader-20")');attempt++)await new Promise(resolve=>setTimeout(resolve,25));
      check(caseId,'keyboard Enter opens the focused last leader',await evaluate('location.hash.includes("leader-20")'),{hash:await evaluate('location.hash')});
    }
    if(mode==='light'&&width===1440&&fontScale===1) {
      const drag=async target=>{
        await evaluate(`(()=>{const source=document.querySelector('#sidebar [data-act="leader"][data-id="leader-3"]'),zone=document.querySelector('#sidebar [data-leader-zone="${target}"] .sec-label'),data=new DataTransfer();source.dispatchEvent(new DragEvent('dragstart',{bubbles:true,dataTransfer:data}));zone.dispatchEvent(new DragEvent('dragover',{bubbles:true,cancelable:true,dataTransfer:data}));zone.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:data}));source.dispatchEvent(new DragEvent('dragend',{bubbles:true,dataTransfer:data}));})()`);
      };
      const initial=calls.filter(call=>call.name==='/api/leaders/priority').length;
      await drag('priority');await wait(`document.querySelector('#sidebar [data-id="leader-3"]').closest('[data-leader-zone]').dataset.leaderZone==='priority'`);
      await drag('normal');await wait(`document.querySelector('#sidebar [data-id="leader-3"]').closest('[data-leader-zone]').dataset.leaderZone==='normal'`);
      const writes=calls.filter(call=>call.name==='/api/leaders/priority').slice(initial);
      check(caseId,'HTML5 drag/drop zones persist one priority and one cancellation',writes.length===2&&JSON.stringify(writes[0].body.ranks)===JSON.stringify(['leader-1','leader-2','leader-3'])&&JSON.stringify(writes[1].body.ranks)===JSON.stringify(['leader-1','leader-2']),writes);
    }
    results.push({case:caseId,top,refreshed,bottom,focused});win.destroy();win=null;
  }
  (async()=>{
    await app.whenReady();await fs.mkdir(output,{recursive:true});
    server=http.createServer((request,response)=>route(request,response).catch(error=>{errors.push(error.stack);json(response,{error:error.message},500);}));
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin='http://127.0.0.1:'+server.address().port;
    if(captureOnly) {
      for(const mode of ['light','dark'])await exercise(mode,1024,700);
      for(const response of streams)response.end();server.close();console.log('Sidebar captures:',output);app.exit(0);return;
    }
    for(const mode of ['light','dark']) {
      if(probe518) {await exercise(mode,860,518,1.4);continue;}
      for(const [width,height] of [[1024,700],[860,600],[1440,900]])await exercise(mode,width,height);
      await exercise(mode,1024,700,1.4);
      await exercise(mode,860,600,1.4);
      await exercise(mode,860,518,1.4);
    }
    check('fixture','no model-execution requests',!calls.some(call=>/\/send$|\/leader$|\/spawn$|\/wake$/.test(call.name)),calls);
    check('fixture','no renderer or fixture API errors',errors.length===0,errors);
    const failed=checks.filter(item=>!item.ok),evidence={ok:failed.length===0,phase,webRoot:web,noModels:true,isolatedUserData:temporary,checks,failed,results,calls,errors};
    await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify(evidence,null,2));
    await fs.rm(path.join(output,'failure.txt'),{force:true});
    console.log(JSON.stringify({phase,output,passed:checks.length-failed.length,failed:failed.map(item=>({case:item.case,name:item.name})),noModels:true},null,2));
    for(const response of streams)response.end();server.close();app.exit(failed.length?1:0);
  })().catch(async error=>{console.error(error);await fs.mkdir(output,{recursive:true});await fs.writeFile(path.join(output,'failure.txt'),error.stack||String(error));win?.destroy();for(const response of streams)response.end();server?.close();app.exit(1);});
}
