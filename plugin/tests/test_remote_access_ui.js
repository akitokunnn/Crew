"use strict";
const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../../frontend/remote-access-ui.js'),'utf8');
const clone=value=>JSON.parse(JSON.stringify(value));
const initial=()=>({enabled:false,bind:'0.0.0.0',port:47812,urls:[],devices:[],pair_pending:false,binding_available:true,
  addresses:[{address:'192.168.1.7',name:'Wi-Fi',kind:'lan',available:true},{address:'10.8.9.7',name:'ZeroTier',kind:'zerotier',available:true}],discovery:{complete:true,error:null}});
async function panel(value=initial(),effect=null){
  let state=clone(value),tick,readError=null;const calls=[],host={innerHTML:'',contains:()=>false};
  const window={},read=async()=>{if(readError)throw Error(readError);return clone(state);};
  vm.runInNewContext(source,{window,document:{activeElement:null},setInterval:fn=>(tick=fn,1),clearInterval(){},setTimeout:()=>2,clearTimeout(){}});
  const action=async body=>{calls.push(clone(body));if(effect)await effect(body);if(body.action==='stop')state.enabled=false;if(body.action==='start'){state.enabled=true;state.bind=body.bind;state.urls=['http://'+body.bind+':'+body.port];}
    if(body.action==='pair'){state.pair_pending=true;return {...clone(state),code:'12345678',expires_at:new Date(Date.now()+300000).toISOString()};}return clone(state);};
  const close=window.CrewRemoteAccess.mount(host,{read,action});await new Promise(setImmediate);
  return {host,calls,close,window,setState:next=>{state=next;},setReadError:value=>{readError=value;},refresh:async()=>{tick();await new Promise(setImmediate);},
    select:value=>host.onchange({target:{name:'remote-bind',value}}),
    click:(kind,deviceId)=>host.onclick({target:{closest:()=>({dataset:{remoteAction:kind,deviceId},disabled:false})}})};
}
test('selecting ZeroTier preserves a draft without starting access, then binds only the chosen address',async()=>{
  const p=await panel();p.select('10.8.9.7');await p.refresh();assert.deepEqual(p.calls,[]);await p.click('start');
  assert.deepEqual(p.calls,[{action:'start',bind:'10.8.9.7',port:47812}]);assert.match(p.host.innerHTML,/http:\/\/10\.8\.9\.7/);p.close();
});
test('one dropdown lists non-loopback interfaces and does not start when none is available',async()=>{
  const value=initial();
  value.addresses[0]={...value.addresses[0],available:false,description:'Intel Ethernet'};
  value.addresses[1]={...value.addresses[1],available:false,description:'ZeroTier'};
  value.addresses.push({address:'127.0.0.1',kind:'loopback',name:'Loopback',available:true});
  const p=await panel(value);await p.click('start');
  assert.deepEqual(p.calls,[]);
  assert.equal(p.host.innerHTML.match(/<select /g).length,1);
  assert.doesNotMatch(p.host.innerHTML,/remote-method|127\.0\.0\.1/);
  assert.match(p.host.innerHTML,/<option value="192\.168\.1\.7"\s+disabled>192\.168\.1\.7 · Wi-Fi · Intel Ethernet · 不可用/);
  assert.match(p.host.innerHTML,/<option value="10\.8\.9\.7"\s+disabled>10\.8\.9\.7 · ZeroTier · 不可用/);
  assert.match(p.host.innerHTML,/未检测到可用的网络地址，请检查网络连接后刷新/);
  assert.match(p.host.innerHTML,/data-remote-action="start" disabled/);p.close();
});
test('switching makes one stop and one start and does not submit a second lifecycle action while pending',async()=>{
  let release;const pending=new Promise(resolve=>release=resolve),value={...initial(),enabled:true,bind:'192.168.1.7'};
  const p=await panel(value,body=>body.action==='stop'?pending:undefined);p.select('10.8.9.7');const first=p.click('switch');await p.click('switch');assert.deepEqual(p.calls,[{action:'stop'}]);release();await first;
  assert.deepEqual(p.calls,[{action:'stop'},{action:'start',bind:'10.8.9.7',port:47812}]);p.close();
});
test('a failed switch keeps the selected address and actual stopped state, with no automatic retry',async()=>{
  let fail=true;const p=await panel({...initial(),enabled:true,bind:'192.168.1.7'},body=>{if(body.action==='start'&&fail)throw Error('端口被占用');});p.select('10.8.9.7');await p.click('switch');await p.refresh();
  assert.match(p.host.innerHTML,/端口被占用/);assert.match(p.host.innerHTML,/data-remote-action="start"/);assert.equal(p.calls.length,2);fail=false;await p.click('start');
  assert.deepEqual(p.calls.map(row=>row.action),['stop','start','start']);assert(p.calls.filter(row=>row.action==='start').every(row=>row.bind==='10.8.9.7'));p.close();
});
test('stop rejection never reaches start, and a later read cannot replace the real operation error',async()=>{
  const p=await panel({...initial(),enabled:true,bind:'192.168.1.7'},()=>{throw Error('关闭服务失败');});p.select('10.8.9.7');p.setReadError('读取失败');await p.click('switch');
  assert.deepEqual(p.calls,[{action:'stop'}]);await p.refresh();assert.match(p.host.innerHTML,/关闭服务失败/);assert.doesNotMatch(p.host.innerHTML,/读取失败/);p.close();
});
test('a disappeared selected adapter is retained and blocks start instead of choosing another network',async()=>{
  const p=await panel();p.select('10.8.9.7');const value=initial();value.addresses[1].available=false;value.addresses[1].reason='ZeroTier 当前未连接';p.setState(value);await p.refresh();await p.click('start');
  assert.deepEqual(p.calls,[]);assert.match(p.host.innerHTML,/ZeroTier 当前未连接/);assert.match(p.host.innerHTML,/value="10.8.9.7" selected disabled/);p.close();
});

test('the current all-network listener is selected and disappears after stopping',async()=>{
  const p=await panel({...initial(),enabled:true,bind:'0.0.0.0'});
  assert.match(p.host.innerHTML,/value="0\.0\.0\.0" selected\s*>全部网络 · 当前设置/);await p.click('stop');
  assert.match(p.host.innerHTML,/value="192\.168\.1\.7" selected/);assert.doesNotMatch(p.host.innerHTML,/value="0\.0\.0\.0"/);
  assert.deepEqual(p.calls,[{action:'stop'}]);await p.click('start');
  assert.deepEqual(p.calls,[{action:'stop'},{action:'start',bind:'192.168.1.7',port:47812}]);p.close();
});

test('an active loopback listener can pair without appearing in the dropdown',async()=>{
  const p=await panel({...initial(),enabled:true,bind:'127.0.0.1'});
  assert.doesNotMatch(p.host.innerHTML,/<option value="127\.0\.0\.1"|data-remote-action="pair" disabled/);
  p.close();
});

test('a removed selected address remains visibly unavailable until the user explicitly chooses its replacement',async()=>{
  const p=await panel();p.select('10.8.9.7');const value=initial();value.addresses[1].address='10.8.9.8';p.setState(value);await p.refresh();
  assert.match(p.host.innerHTML,/value="10\.8\.9\.7" selected disabled>10\.8\.9\.7 · 当前不可用/);
  assert.match(p.host.innerHTML,/原地址当前不可用/);await p.click('start');assert.deepEqual(p.calls,[]);
  p.select('10.8.9.8');assert.deepEqual(p.calls,[]);await p.click('start');
  assert.deepEqual(p.calls,[{action:'start',bind:'10.8.9.8',port:47812}]);p.close();
});

test('devices show type and status and use the revoke actions',async()=>{
  const last_seen_at='2026-11-17T08:00:00Z';
  const p=await panel({...initial(),enabled:true,bind:'192.168.1.7',devices:[
    {id:'mac',name:'工作电脑',kind:'desktop',online:true,last_seen_at},
    {id:'phone',name:'手机',kind:'browser',online:false,last_seen_at},
  ]});
  assert.match(p.host.innerHTML,/工作电脑<\/strong><small>Crew 桌面客户端<\/small>\s*<small class="remote-online">在线/);
  assert.match(p.host.innerHTML,/手机<\/strong><small>浏览器<\/small>\s*<small>最近使用 2026/);
  assert.match(p.host.innerHTML,/data-device-id="mac"\s*>强制下线/);
  assert.match(p.host.innerHTML,/30 天不用会自动失效；强制下线后需要重新配对。/);
  assert.match(p.host.innerHTML,/data-remote-action="revoke_all"\s*>全部下线/);
  await p.click('revoke','mac');
  await p.click('revoke_all');
  assert.deepEqual(p.calls,[{action:'revoke',device_id:'mac'},{action:'revoke_all'}]);
  p.close();
});
test('a pairing code comes with a copyable connection link per access address',async()=>{
  const p=await panel({...initial(),enabled:true,bind:'10.8.9.7',urls:['http://10.8.9.7:47812']});const copies=[];
  p.window.CrewBrowser={copyText:async text=>{copies.push(text);}};
  await p.click('pair');
  const link='http://10.8.9.7:47812/remote/login#code=12345678';
  assert.match(p.host.innerHTML,/<strong>12345678<\/strong>/);
  assert.ok(p.host.innerHTML.includes(`<code>${link}</code>`));
  assert.match(p.host.innerHTML,/复制连接链接/);
  await p.host.onclick({target:{closest:()=>({dataset:{remoteAction:'copy-link',link},disabled:false})}});
  assert.deepEqual(copies,[link]);assert.match(p.host.innerHTML,/已复制/);assert.doesNotMatch(p.host.innerHTML,/复制失败/);
  assert.deepEqual(p.calls,[{action:'pair'}]);p.close();
});
