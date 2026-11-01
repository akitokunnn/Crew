"use strict";
const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../crew/web/remote-access-ui.js'),'utf8');
const clone=value=>JSON.parse(JSON.stringify(value));
const initial=()=>({enabled:false,bind:'0.0.0.0',port:47812,urls:[],devices:[],pair_pending:false,binding_available:true,
  addresses:[{address:'192.168.1.7',name:'Wi-Fi',kind:'lan',available:true},{address:'10.8.9.7',name:'ZeroTier',kind:'zerotier',available:true}],discovery:{complete:true,error:null}});
async function panel(value=initial(),effect=null){
  let state=clone(value),tick,readError=null;const calls=[],host={innerHTML:'',contains:()=>false};
  const window={},read=async()=>{if(readError)throw Error(readError);return clone(state);};
  vm.runInNewContext(source,{window,document:{activeElement:null},setInterval:fn=>(tick=fn,1),clearInterval(){}});
  const action=async body=>{calls.push(clone(body));if(effect)await effect(body);if(body.action==='stop')state.enabled=false;if(body.action==='start'){state.enabled=true;state.bind=body.bind;state.urls=['http://'+body.bind+':'+body.port];}return clone(state);};
  const close=window.CrewRemoteAccess.mount(host,{read,action});await new Promise(setImmediate);
  return {host,calls,close,setState:next=>{state=next;},setReadError:value=>{readError=value;},refresh:async()=>{tick();await new Promise(setImmediate);},
    select:(name,value)=>host.onchange({target:{name,value}}),click:kind=>host.onclick({target:{closest:()=>({dataset:{remoteAction:kind},disabled:false})}})};
}
test('selecting ZeroTier preserves a draft without starting access, then binds only the chosen address',async()=>{
  const p=await panel();p.select('remote-method','zerotier');await p.refresh();assert.deepEqual(p.calls,[]);await p.click('start');
  assert.deepEqual(p.calls,[{action:'start',bind:'10.8.9.7',port:47812}]);assert.match(p.host.innerHTML,/http:\/\/10\.8\.9\.7/);p.close();
});
test('missing ZeroTier never silently falls back to all interfaces or a LAN address',async()=>{
  const value=initial();value.addresses=value.addresses.filter(row=>row.kind==='lan');const p=await panel(value);p.select('remote-method','zerotier');await p.click('start');
  assert.deepEqual(p.calls,[]);assert.match(p.host.innerHTML,/未检测到可用的 ZeroTier 地址/);assert.match(p.host.innerHTML,/data-remote-action="start" disabled/);p.close();
});
test('switching makes one stop and one start and does not submit a second lifecycle action while pending',async()=>{
  let release;const pending=new Promise(resolve=>release=resolve),value={...initial(),enabled:true,bind:'192.168.1.7'};
  const p=await panel(value,body=>body.action==='stop'?pending:undefined);p.select('remote-method','zerotier');const first=p.click('switch');await p.click('switch');assert.deepEqual(p.calls,[{action:'stop'}]);release();await first;
  assert.deepEqual(p.calls,[{action:'stop'},{action:'start',bind:'10.8.9.7',port:47812}]);p.close();
});
test('a failed switch keeps the selected address and actual stopped state, with no automatic retry',async()=>{
  let fail=true;const p=await panel({...initial(),enabled:true,bind:'192.168.1.7'},body=>{if(body.action==='start'&&fail)throw Error('端口被占用');});p.select('remote-method','zerotier');await p.click('switch');await p.refresh();
  assert.match(p.host.innerHTML,/端口被占用/);assert.match(p.host.innerHTML,/data-remote-action="start"/);assert.equal(p.calls.length,2);fail=false;await p.click('start');
  assert.deepEqual(p.calls.map(row=>row.action),['stop','start','start']);assert(p.calls.filter(row=>row.action==='start').every(row=>row.bind==='10.8.9.7'));p.close();
});
test('stop rejection never reaches start, and a later read cannot replace the real operation error',async()=>{
  const p=await panel({...initial(),enabled:true,bind:'192.168.1.7'},()=>{throw Error('关闭服务失败');});p.select('remote-method','zerotier');p.setReadError('读取失败');await p.click('switch');
  assert.deepEqual(p.calls,[{action:'stop'}]);await p.refresh();assert.match(p.host.innerHTML,/关闭服务失败/);assert.doesNotMatch(p.host.innerHTML,/读取失败/);p.close();
});
test('a disappeared selected adapter is retained and blocks start instead of choosing another network',async()=>{
  const p=await panel();p.select('remote-method','zerotier');const value=initial();value.addresses[1].available=false;value.addresses[1].reason='ZeroTier 当前未连接';p.setState(value);await p.refresh();await p.click('start');
  assert.deepEqual(p.calls,[]);assert.match(p.host.innerHTML,/ZeroTier 当前未连接/);assert.match(p.host.innerHTML,/value="10.8.9.7" selected disabled/);p.close();
});

test('stopping a legacy all-network listener visibly selects LAN before the next explicit start',async()=>{
  const p=await panel({...initial(),enabled:true,bind:'0.0.0.0'});
  assert.match(p.host.innerHTML,/value="all" checked/);await p.click('stop');
  assert.match(p.host.innerHTML,/value="lan" checked/);assert.doesNotMatch(p.host.innerHTML,/value="all"/);
  assert.deepEqual(p.calls,[{action:'stop'}]);await p.click('start');
  assert.deepEqual(p.calls,[{action:'stop'},{action:'start',bind:'192.168.1.7',port:47812}]);p.close();
});

test('a removed selected address remains visibly unavailable until the user explicitly chooses its replacement',async()=>{
  const p=await panel();p.select('remote-method','zerotier');const value=initial();value.addresses[1].address='10.8.9.8';p.setState(value);await p.refresh();
  assert.match(p.host.innerHTML,/value="10\.8\.9\.7" selected disabled>10\.8\.9\.7 · 当前不可用/);
  assert.match(p.host.innerHTML,/原地址当前不可用/);await p.click('start');assert.deepEqual(p.calls,[]);
  p.select('remote-bind','10.8.9.8');assert.deepEqual(p.calls,[]);await p.click('start');
  assert.deepEqual(p.calls,[{action:'start',bind:'10.8.9.8',port:47812}]);p.close();
});
