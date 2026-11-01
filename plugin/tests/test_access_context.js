"use strict";
const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../crew/web/access-context.js'),'utf8');
function boot(fetch){
  const nodes=new Map(),events=[];
  const element=()=>({children:[],setAttribute(){},append(...items){this.children.push(...items);for(const item of items)if(item.id)nodes.set(item.id,item);},replaceChildren(){this.children=[];}});
  const document={documentElement:{dataset:{}},body:element(),createElement:element,getElementById:id=>nodes.get(id)};
  const location={pathname:'/',search:'?project=健身',hash:'#leader=L&goal=G',origin:'http://lan:1234',href:'http://lan:1234/?project=健身#leader=L&goal=G'};
  const window={fetch,dispatchEvent:event=>events.push(event.type)};
  vm.runInNewContext(source,{window,document,location,URL,Event});
  return {window,document,nodes,events};
}
const response=(status,value)=>({status,ok:status>=200&&status<300,json:async()=>value});
test('controls stay unavailable until the actual access context is known',async()=>{
  let release;const pending=new Promise(resolve=>release=resolve);const app=boot(()=>pending);
  assert.equal(app.window.CrewAccess.local_controls,false);assert.equal(app.window.CrewAccess.local_desktop,false);
  release(response(200,{remote:false,local_controls:true,local_desktop:true}));assert.equal(await app.window.CrewAccess.ready,true);assert.equal(app.window.CrewAccess.local_controls,true);
});
test('failed or malformed context never silently enables local actions',async()=>{
  for(const value of [response(503,{error:'后台未就绪'}),response(200,{remote:false})]){
    const app=boot(async()=>value);assert.equal(await app.window.CrewAccess.ready,false);assert.equal(app.window.CrewAccess.local_controls,false);assert(app.nodes.get('access-notice').children[0].textContent);
  }
});
test('an expired write prompts once, preserves the exact response, and never retries the POST',async()=>{
  let requests=0;const denied=response(401,{error:'连接已失效'});
  const app=boot(async()=>++requests===1?response(200,{remote:true,local_controls:false,local_desktop:false,authenticated:true}):denied);
  await app.window.CrewAccess.ready;
  assert.equal(await app.window.fetch('/api/chat/send',{method:'POST',body:'original input'}),denied);
  assert.equal(requests,2);assert.equal(app.window.CrewAccess.expired,true);assert.deepEqual(app.events,['crew-access-expired']);
  const link=app.nodes.get('access-notice').children[1];assert.equal(new URL(link.href,'http://lan:1234').searchParams.get('next'),'/?project=健身#leader=L&goal=G');
  await app.window.fetch('/api/chat/history');assert.deepEqual(app.events,['crew-access-expired']);
});
test('a different origin refusing a request does not replace the Crew connection',async()=>{
  let requests=0;const app=boot(async()=>++requests===1?response(200,{remote:false,local_controls:true,local_desktop:true}):response(401,{}));
  await app.window.CrewAccess.ready;await app.window.fetch('https://elsewhere.invalid/api/example');assert.equal(app.window.CrewAccess.expired,false);assert.equal(app.window.CrewAccess.local_controls,true);
});
