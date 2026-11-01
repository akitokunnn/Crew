"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict"),vm=require("node:vm"),fs=require("node:fs"),path=require("node:path");
const {webcrypto}=require("node:crypto");
const source=fs.readFileSync(path.join(__dirname,"../crew/web/browser-utils.js"),"utf8");
test("HTTP browser identifiers work without the secure-context randomUUID API",()=>{
  const sandbox={window:{},crypto:{getRandomValues:array=>webcrypto.getRandomValues(array)}};
  vm.runInNewContext(source,sandbox);
  const ids=Array.from({length:1000},()=>sandbox.window.CrewBrowser.requestId());
  assert.equal(new Set(ids).size,ids.length);assert(ids.every(id=>/^[0-9a-f-]{36}$/.test(id)));
});
test("HTTP copy uses the selected text and restores the existing editor focus",async()=>{
  let selected,removed=false,focused=false;
  const editor={focus:options=>{assert(options.preventScroll);focused=true;}};
  const input={style:{},select(){selected=this.value;},setSelectionRange(){},remove(){removed=true;}};
  const sandbox={window:{},navigator:{},document:{activeElement:editor,getSelection:()=>null,
    createElement:()=>input,body:{append(){}},execCommand:command=>{assert.equal(command,"copy");assert.equal(selected,"the complete commit identifier");return true;}}};
  vm.runInNewContext(source,sandbox);
  await sandbox.window.CrewBrowser.copyText("the complete commit identifier");assert(removed&&focused);
});
test("a rejected HTTP copy reports failure and still removes its temporary field",async()=>{
  let removed=false;
  const sandbox={window:{},navigator:{},document:{activeElement:null,getSelection:()=>null,
    createElement:()=>({style:{},select(){},setSelectionRange(){},remove(){removed=true;}}),body:{append(){}},execCommand:()=>false}};
  vm.runInNewContext(source,sandbox);
  await assert.rejects(sandbox.window.CrewBrowser.copyText("text"),/未允许/);assert(removed);
});
