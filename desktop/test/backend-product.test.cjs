'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs/promises'),path=require('node:path'),os=require('node:os'),http=require('node:http');
const {requireGeneration,requireHomeGeneration}=require('../src/backend-product.cjs');
const {Backend}=require('../src/core.cjs');
const {listen}=require('./fixtures/loopback.cjs');

async function directory(t) {
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'crew-generation-'));
  t.after(()=>fs.rm(root,{recursive:true,force:true}));
  return root;
}
async function source(root,name,generation) {
  const directory=path.join(root,name);await fs.mkdir(directory);
  if(generation!==undefined)await fs.writeFile(path.join(directory,'crew-product.json'),JSON.stringify({generation,name:'Crew V'+generation}));
  return directory;
}
async function service(t,pluginRoot) {
  const calls=[],server=http.createServer((request,response)=>{
    calls.push(request.url);response.setHeader('Content-Type','application/json');
    if(request.url==='/api/admin/health')response.end(JSON.stringify({ok:true,service:'crew',plugin_root:pluginRoot,home:path.dirname(pluginRoot)}));
    else if(request.url==='/api/projects')response.end('{"projects":[]}');
    else{response.writeHead(404);response.end('{"error":"unknown"}');}
  });
  await listen(server);
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  return {url:'http://127.0.0.1:'+server.address().port,calls};
}

test('only an explicitly enabled product generation requires the first-party marker',async t=>{
  const root=await directory(t),v1=await source(root,'v1'),v2=await source(root,'v2',2);
  assert.equal(await requireGeneration(v1,undefined),null);
  await assert.rejects(requireGeneration(v1,2),error=>error.code==='CREW_PRODUCT_GENERATION'&&error.message.includes('V1'));
  assert.equal((await requireGeneration(v2,2)).generation,2);
  await fs.writeFile(path.join(v2,'crew-product.json'),'{"generation":"2"}');
  await assert.rejects(requireGeneration(v2,2),/generation 不是 2/);
});

test('V2 rejects a V1 backend directory before inspecting HOME or spawning Python',async t=>{
  const root=await directory(t),v1=await source(root,'v1'),home=path.join(root,'unused-home');
  const backend=new Backend({home,backendDir:v1,productGeneration:2},{spawn(){throw Error('must not spawn');}});
  await assert.rejects(backend.start(),error=>error.code==='CREW_PRODUCT_GENERATION');
  assert.equal(backend.child,null);assert.equal(backend.owned,false);
  await assert.rejects(fs.access(home),{code:'ENOENT'});
});

test('a V2 configured source cannot borrow a live V1 backend through either connection path',async t=>{
  const root=await directory(t),v1=await source(root,'v1'),v2=await source(root,'v2',2),live=await service(t,v1);
  const registry=JSON.stringify({url:live.url});await fs.writeFile(path.join(root,'web.json'),registry);
  for(const selected of [false,true]){
    const backend=new Backend({home:root,backendDir:v2,productGeneration:2,...(selected?{serviceUrl:live.url}:{})},
      {spawn(){throw Error('must not spawn');}});
    await assert.rejects(backend.start(),error=>error.code==='CREW_PRODUCT_GENERATION');
    assert.equal(backend.url,null);assert.equal(backend.child,null);
  }
  assert.equal(await fs.readFile(path.join(root,'web.json'),'utf8'),registry);
  assert(!live.calls.includes('/api/projects'));
});

test('a matching V2 live service can be borrowed and recovery can revalidate it',async t=>{
  const root=await directory(t),v2=await source(root,'v2',2),live=await service(t,v2);
  const backend=new Backend({home:root,backendDir:v2,productGeneration:2,serviceUrl:live.url},
    {spawn(){throw Error('must not spawn');}});
  assert.deepEqual(await backend.start(),{url:live.url,owned:false});
  await backend.validateService(live.url);
  await fs.writeFile(path.join(v2,'crew-product.json'),'{"generation":1}');
  await assert.rejects(backend.validateService(live.url),error=>error.code==='CREW_PRODUCT_GENERATION');
});

test('V2 source cannot overwrite a HOME registered to V1 and empty independent HOME is allowed',async t=>{
  const root=await directory(t),v1=await source(root,'v1'),v2=await source(root,'v2',2),home=path.join(root,'custom-v1-home');
  await fs.mkdir(home);const record=JSON.stringify({plugin_root:v1,python:'fixture'});
  await fs.writeFile(path.join(home,'runtime.json'),record);
  const backend=new Backend({home,backendDir:v2,productGeneration:2},{spawn(){throw Error('must not spawn');}});
  await assert.rejects(backend.start(),error=>error.code==='CREW_PRODUCT_GENERATION'&&error.message.includes('不会覆盖'));
  assert.equal(await fs.readFile(path.join(home,'runtime.json'),'utf8'),record);
  await requireHomeGeneration(path.join(root,'new-v2-home'),2);
  await assert.rejects(fs.access(path.join(root,'new-v2-home')),{code:'ENOENT'});
  await fs.writeFile(path.join(home,'runtime.json'),JSON.stringify({plugin_root:v2}));
  await requireHomeGeneration(home,2);
});

test('an unregistered custom HOME with an existing database is refused without opening or modifying it',async t=>{
  const root=await directory(t),v2=await source(root,'v2',2),home=path.join(root,'unregistered-old-home');
  await fs.mkdir(home);const database=Buffer.from('existing database must remain untouched');
  await fs.writeFile(path.join(home,'crew.db'),database);
  const backend=new Backend({home,backendDir:v2,productGeneration:2},{spawn(){throw Error('must not spawn');}});
  await assert.rejects(backend.start(),error=>error.code==='CREW_PRODUCT_GENERATION'&&error.message.includes('来源未知'));
  assert.deepEqual(await fs.readFile(path.join(home,'crew.db')),database);
  assert.deepEqual(await fs.readdir(home),['crew.db']);
});
