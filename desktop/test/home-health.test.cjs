'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {test}=require('node:test');
const tick=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
const healthy=extra=>({state:'healthy',checked_at:'2026-10-24T07:00:00Z',supervisor_pid:10,service_pid:20,url:'http://127.0.0.1:9999',recoveries:2,last_recovery_at:null,incidents:[],restart:{allowed:false,reason:'后台正在运行，不会强制重启。'},instance_id:'fixture',...extra});
function harness(bridge){
  const nodes=new Map(),timers=new Map(),listeners=new Map();let next=1,observer;
  function element(){const events=new Map(),classes=new Set();return{hidden:false,textContent:'',dataset:{},children:[],disabled:false,attributes:{},classList:{add:v=>classes.add(v),remove:v=>classes.delete(v),toggle(v,on){if(on)classes.add(v);else classes.delete(v);},contains:v=>classes.has(v)},append(...items){this.children.push(...items);},replaceChildren(...items){this.children=items;},setAttribute(k,v){this.attributes[k]=v;},removeAttribute(k){delete this.attributes[k];},addEventListener(k,fn){if(!events.has(k))events.set(k,[]);events.get(k).push(fn);},click(){if(!this.disabled)for(const fn of events.get('click')||[])fn();},focus(){}};}
  const html=fs.readFileSync(path.join(__dirname,'../src/home.html'),'utf8');for(const [,id]of html.matchAll(/id="([^"]+)"/g))nodes.set(id,element());
  nodes.set('crew-desktop-home',element());nodes.get('admin-page').hidden=true;nodes.get('tools-page').hidden=true;
  const sandbox={document:{body:element(),visibilityState:'visible',getElementById:id=>nodes.get(id)||null,createElement:element,addEventListener:(type,fn)=>listeners.set(type,fn)},setTimeout:(fn,ms)=>{const id=next++;timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id),MutationObserver:class{constructor(fn){observer=fn;}observe(){}},window:{crewDesktop:bridge,CrewHomeTools:{hide(){nodes.get('workspaces-page').hidden=false;}},addEventListener:(type,fn)=>listeners.set(type,fn)}};
  vm.createContext(sandbox);vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/home-health.js'),'utf8'),sandbox);
  return{ui:sandbox.window.CrewHomeHealth,nodes,timers,document:sandbox.document,observer:()=>observer(),listeners,sandbox};
}

test('administrator reads one global snapshot and never restarts a live backend',async()=>{
  const calls=[],h=harness({adminStatus:async()=>{calls.push('status');return healthy();},adminCheck:async()=>healthy(),adminRestart:async()=>{calls.push('restart');return{ok:true};}});
  h.ui.show();await tick();assert.deepEqual(calls,['status']);assert.equal(h.nodes.get('admin-state').textContent,'运行正常');assert.equal(h.nodes.get('admin-recoveries').textContent,'2');
  assert.equal(h.nodes.get('admin-restart').disabled,true);h.nodes.get('admin-restart').click();await tick();assert.deepEqual(calls,['status']);assert.match(h.nodes.get('admin-restart-reason').textContent,/不会强制重启/);
  assert.equal(h.nodes.get('admin-incidents').children[0].textContent,'尚无异常记录。');assert.equal([...h.timers.values()][0].ms,5000);
});

test('administrator exposes the maintenance conversation and configuration entry',()=>{
  const opened=[],h=harness({});
  h.sandbox.window.CrewHomeSettings={show:category=>opened.push(category)};
  h.nodes.get('admin-conversation').click();
  assert.deepEqual(opened,['backend']);
});

test('safe recovery needs an explicit click and refreshes actual state after the receipt',async()=>{
  let status=healthy({state:'stopped',restart:{allowed:true,reason:'后台已退出，可安全恢复。'}}),restarts=0;
  const h=harness({adminStatus:async()=>status,adminCheck:async()=>status,adminRestart:async()=>{restarts++;status=healthy({state:'recovering'});return{ok:true};}});
  h.ui.show();await tick();assert.equal(restarts,0);assert.equal(h.nodes.get('admin-restart').disabled,false);h.nodes.get('admin-restart').click();await tick();
  assert.equal(restarts,1);assert.equal(h.nodes.get('admin-state').textContent,'正在自动恢复');assert.match(h.nodes.get('admin-note').textContent,/读取实际状态/);assert.equal(h.nodes.get('admin-restart').disabled,true);
});

test('checks are serialized and failures remain retryable without displaying a false healthy state',async()=>{
  let finish,calls=0;const h=harness({adminStatus:()=>{calls++;return new Promise(resolve=>{finish=resolve;});},adminCheck:async()=>{throw Error('检测源暂不可用');},adminRestart:async()=>({ok:true})});
  h.ui.show();h.ui.show();assert.equal(calls,1);assert.equal(h.nodes.get('admin-check').disabled,true);finish(healthy());await tick();h.nodes.get('admin-check').click();await tick();
  assert.equal(h.nodes.get('admin-state').textContent,'检测失败');assert.match(h.nodes.get('admin-error').textContent,/检测源暂不可用/);assert.equal(h.nodes.get('admin-check').disabled,false);assert.equal(h.nodes.get('admin-restart').disabled,true);
});

test('polling stops when the panel, native home view, or document is hidden and resumes on return',async()=>{
  let reads=0;const h=harness({adminStatus:async()=>{reads++;return healthy();},adminCheck:async()=>healthy(),adminRestart:async()=>({ok:true})});
  h.ui.show();await tick();assert.equal(h.timers.size,1);h.nodes.get('crew-desktop-home').hidden=true;h.observer();assert.equal(h.timers.size,0);
  h.nodes.get('crew-desktop-home').hidden=false;h.observer();await tick();assert.equal(reads,2);assert.equal(h.timers.size,1);
  h.document.visibilityState='hidden';h.listeners.get('visibilitychange')();assert.equal(h.timers.size,0);h.ui.hide();h.document.visibilityState='visible';h.listeners.get('visibilitychange')();await tick();assert.equal(reads,2);assert.equal(h.timers.size,0);
});

test('incident messages are rendered as text, and unsupported older bridges hide the entry',async()=>{
  const h=harness({adminStatus:async()=>healthy({incidents:[{at:'2026-10-24T07:01:00Z',level:'error',message:'<script>bad</script>'}]}),adminCheck:async()=>healthy(),adminRestart:async()=>({ok:true})});h.ui.show();await tick();
  assert.equal(h.nodes.get('admin-incidents').children[0].children[1].textContent,'<script>bad</script>');
  const old=harness({});assert.equal(old.nodes.get('nav-admin').hidden,true);old.ui.show();assert.equal(old.nodes.get('admin-page').hidden,true);
});

test('check acknowledgement is followed by a snapshot and never claims a newer completed check',async()=>{
  const calls=[],h=harness({adminStatus:async()=>{calls.push('status');return healthy({state:'paused',restart:{allowed:false,reason:'用户已暂停后台'}});},adminCheck:async()=>{calls.push('check');return{ok:true,status:'requested',request_id:'request'};},adminRestart:async()=>({ok:true})});
  h.ui.show();await tick();h.nodes.get('admin-check').click();await tick();assert.deepEqual(calls,['status','check','status']);assert.match(h.nodes.get('admin-note').textContent,/已请求检测/);assert.equal(h.nodes.get('admin-state').textContent,'维护中 / 已暂停');assert.equal(h.nodes.get('admin-restart').disabled,true);
});

test('native administrator action opens immediately while workspace bootstrap never resolves',async()=>{
  let nativeAction,homeReady=0,reads=0;
  const bridge={adminStatus:async()=>{reads++;return healthy();},adminCheck:async()=>({ok:true}),adminRestart:async()=>({ok:true}),bootstrap:()=>new Promise(()=>{}),homeReady:async()=>{homeReady++;},onAction:fn=>{nativeAction=fn;},ready:async()=>{}};
  const h=harness(bridge);vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/home.js'),'utf8'),h.sandbox);nativeAction({action:'admin'});await tick();
  assert.equal(homeReady,1);assert.equal(h.nodes.get('admin-page').hidden,false);assert.equal(reads,1);
});

test('a later matching receipt shows the actual refusal and never imports another request error',async()=>{
  let status=healthy({state:'circuit_open',restart:{allowed:true,reason:'后台已退出'},last_request_id:'older',last_request_error:'another request only'});
  const h=harness({adminStatus:async()=>status,adminCheck:async()=>({ok:true,request_id:'current'}),adminRestart:async()=>({ok:true,request_id:'current'})});
  h.ui.show();await tick();assert.equal(h.nodes.get('admin-state').textContent,'自动恢复已暂停');assert.equal(h.nodes.get('admin-restart').textContent,'尝试恢复');h.nodes.get('admin-restart').click();await tick();assert.equal(h.nodes.get('admin-error').hidden,true);
  status=healthy({state:'stopping',last_request_id:'current',last_request_error:'后台仍然运行，恢复已拒绝。'});const [id,timer]=[...h.timers][0];h.timers.delete(id);timer.fn();await tick();
  assert.match(h.nodes.get('admin-error').textContent,/后台仍然运行/);assert.equal(h.nodes.get('admin-state').textContent,'正在停止');assert.equal(h.nodes.get('admin-note').hidden,true);
});

test('operations shows independently verified commit and an unprivileged HTML report while Web is down',async()=>{
  const operations={incidents:[{id:'incident',status:'awaiting_merge',goals:['goal-a'],attempts:1,result:{commit:'abcdef0123456789'}}],report:{incident_id:'incident',html:'<!doctype html><p>before broken → after fixed</p>'}};
  const h=harness({adminStatus:async()=>healthy({state:'failed',operations}),adminCheck:async()=>({ok:true}),adminRestart:async()=>({ok:true})});
  h.ui.show();await tick();const row=h.nodes.get('admin-operations').children[0];assert.match(row.children[0].textContent,/等待项目合并/);assert.match(row.children[1].textContent,/abcdef012345/);
  const frame=row.children[2].children[1];assert.equal(frame.attributes.sandbox,'');assert.match(frame.srcdoc,/broken/);
});
