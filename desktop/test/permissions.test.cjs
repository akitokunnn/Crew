'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {installPermissions}=require('../src/permissions.cjs');

test('only Crew top-level documents can write the clipboard',()=>{
  let check,request;
  const session={setPermissionCheckHandler:fn=>check=fn,setPermissionRequestHandler:fn=>request=fn};
  const document=url=>({getURL:()=>url,isDestroyed:()=>false});
  const homePage='file:///C:/Crew/home.html',origin='http://127.0.0.1:7122';
  const home=document(homePage),workspace=document(origin+'/?project=test'),foreign=document(origin+'/');
  installPermissions(session,{homeContents:home,homePage,workspaceContents:wc=>wc===workspace,workspaceOrigin:()=>origin});
  const details=wc=>({isMainFrame:true,requestingUrl:wc.getURL()});
  for(const wc of [home,workspace]){
    assert.equal(check(wc,'clipboard-sanitized-write','',details(wc)),true);
    let result;request(wc,'clipboard-sanitized-write',value=>result=value,details(wc));assert.equal(result,true);
    for(const permission of ['clipboard-read','deprecated-sync-clipboard-read','media','notifications'])assert.equal(check(wc,permission,'',details(wc)),false);
    assert.equal(check(wc,'clipboard-sanitized-write','',{...details(wc),isMainFrame:false}),false);
    assert.equal(check(wc,'clipboard-sanitized-write','',{...details(wc),requestingUrl:'https://example.com/'}),false);
    assert.equal(check(wc,'clipboard-sanitized-write','',{isMainFrame:true}),false);
  }
  assert.equal(check(null,'clipboard-sanitized-write','',{}),false);
  assert.equal(check(foreign,'clipboard-sanitized-write','',details(foreign)),false);
  home.getURL=()=>homePage.replace('home.html','other.html');
  assert.equal(check(home,'clipboard-sanitized-write','',details(home)),false);
  workspace.getURL=()=> 'http://127.0.0.1:9999/';
  assert.equal(check(workspace,'clipboard-sanitized-write','',details(workspace)),false);
});

test('sensitive permissions ask on use; denial and navigation never grant access',async()=>{
  let check,request,answer=true,asked=[];
  const session={setPermissionCheckHandler:fn=>check=fn,setPermissionRequestHandler:fn=>request=fn};
  let url='file:///C:/Crew/home.html';const home={getURL:()=>url,isDestroyed:()=>false};
  installPermissions(session,{homeContents:home,homePage:url,workspaceContents:()=>false,workspaceOrigin:()=>'',
    requestPermission:async label=>{asked.push(label);return answer;}});
  const details=()=>({isMainFrame:true,requestingUrl:url});
  const ask=permission=>new Promise(resolve=>request(home,permission,resolve,details()));
  assert.equal(check(home,'clipboard-read','',details()),false);
  assert.equal(await ask('clipboard-read'),true);
  assert.equal(check(home,'clipboard-read','',details()),true);
  assert.equal(await ask('clipboard-read'),true);assert.equal(asked.length,1);
  answer=false;assert.equal(await ask('media'),false);
  assert.equal(check(home,'media','',details()),false);
  answer=true;assert.equal(await ask('media'),true);
  assert.equal(await ask('notifications'),true);
  assert.equal(await ask('fullscreen'),true);
  const count=asked.length;
  assert.equal(await ask('geolocation'),false);assert.equal(asked.length,count);
  url='file:///C:/Crew/other.html';
  assert.equal(check(home,'clipboard-read','',details()),false);
  assert.equal(await ask('media'),false);assert.equal(asked.length,count);
});
