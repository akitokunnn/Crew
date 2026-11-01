'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');

function preload({location='file:///crew/home.html',mainFrame=true}={}){
  const invocations=[];let bridge;
  const settings={home:'file:///crew/home.html',error:'file:///crew/error.html'};
  const sandbox={
    require:name=>{assert.equal(name,'electron');return{
      contextBridge:{exposeInMainWorld:(name,value)=>{assert.equal(name,'crewDesktop');bridge=value;}},
      ipcRenderer:{on(){},invoke:async(channel,...args)=>{invocations.push({channel,args});return{ok:true};}}
    };},
    process:{isMainFrame:mainFrame,argv:['--crew-desktop-config='+encodeURIComponent(JSON.stringify(settings))]},
    location:{href:location},window:{addEventListener(){}},Promise,encodeURIComponent,decodeURIComponent,setTimeout,clearTimeout
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../src/preload.cjs'),'utf8'),sandbox);
  return{bridge,invocations};
}

test('remote access has separate read and action bridges without exposing an arbitrary backend route',async()=>{
  const h=preload();
  assert.deepEqual(await h.bridge.remoteAccessRead(),{ok:true});
  const body={action:'revoke',device_id:'paired-device'};
  assert.deepEqual(await h.bridge.remoteAccessAction(body),{ok:true});
  assert.deepEqual(h.invocations,[{channel:'desktop:remote-access-read',args:[]},{channel:'desktop:remote-access-action',args:[body]}]);
});

test('workspace, external, error and child frames cannot acquire the remote access management bridge',()=>{
  for(const settings of [{location:'http://127.0.0.1:47811/?project=demo'},{location:'https://example.test/'},{location:'file:///crew/error.html'},{mainFrame:false}])
    assert.equal(preload(settings).bridge,undefined);
});
