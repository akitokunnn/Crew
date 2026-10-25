"use strict";
const test = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname,"../crew/web/workspace-settings-ui.js"),"utf8");
const plain = value => JSON.parse(JSON.stringify(value));
const snapshot = extra => ({project:{name:"stable-project",display_name:"显示名称",root:"C:/work/old",theme:"green"},revision:7,directory_change:null,
  sessions:[{session_id:"leader",name:"负责人",cwd:"C:/work/old",worktree:null,running:true,busy:true},{session_id:"member",name:"成员",cwd:"C:/work/old/src",worktree:null,running:false,busy:false}],goals_count:3,...extra});
function harness(overrides={}) {
  const handlers={},nodes=new Map(),calls={read:[],update:[],remove:[],saved:[],removed:[],pick:[],closed:0};let html="";
  const node=()=>({value:"",textContent:"",disabled:false,isConnected:true,attributes:{},classList:{toggle(){}},setAttribute(key,value){this.attributes[key]=value;},focus(){this.focused=true;}});
  const dialog={...node(),open:false,append(){},showModal(){this.open=true;},close(){this.open=false;},addEventListener:(type,handler)=>handlers[type]=handler,
    querySelector(selector){if(!nodes.has(selector))nodes.set(selector,node());return nodes.get(selector);},querySelectorAll:()=>[...nodes.values()]};
  Object.defineProperty(dialog,"innerHTML",{get:()=>html,set(value){html=value;nodes.clear();for(const name of ["root","display_name"]){const match=value.match(new RegExp('name="'+name+'"[^>]*value="([^"]*)"'));if(match)dialog.querySelector('[name="'+name+'"]').value=match[1];}}});
  const sandbox={window:{},document:{body:{append(){}},activeElement:node(),createElement:()=>dialog}};vm.createContext(sandbox);vm.runInContext(source,sandbox);
  let current=snapshot();const options={read:async key=>{calls.read.push(key);return current;},update:async body=>{calls.update.push(plain(body));const change=ui.rootChanged(body.root,current.project.root)?{old_root:current.project.root,new_root:body.root}:null;current={...current,project:{...current.project,root:body.root,display_name:body.display_name},revision:current.revision+1,directory_change:change};return current;},
    remove:async body=>{calls.remove.push(plain(body));return {ok:true,project:body.project,removed:true};},onSaved:next=>calls.saved.push(plain(next)),onRemoved:next=>calls.removed.push(plain(next)),onClose:()=>calls.closed++,...overrides};
  const ui=sandbox.window.CrewWorkspaceSettings, settings=ui.create(options);
  const input=(name,value)=>{dialog.querySelector('[name="'+name+'"]').value=value;handlers.input({target:{name,value}});};
  const click=action=>handlers.click({target:{closest:()=>({dataset:{workspaceAction:action},focus(){}})}});
  return {ui,settings,calls,dialog,nodes,input,click,cancel:()=>handlers.cancel({preventDefault(){}}),html:()=>html};
}

test("workspace payload keeps the stable key and reserves stop for directory changes",()=>{
  const {ui}=harness(),state=snapshot();
  assert.equal(ui.displayName(state.project),"显示名称");assert.equal(ui.displayName({name:"stable"}),"stable");
  assert.deepEqual(plain(ui.updatePayload(state,{display_name:" 新名称 ",root:"C:\\work\\old\\"})),{project:"stable-project",display_name:"新名称",root:"C:\\work\\old\\",revision:7});
  assert.equal(ui.updatePayload(state,{display_name:"名称",root:"C:/new"}).stop,true);
  assert.throws(()=>ui.updatePayload(state,{display_name:"名称",root:" "}),/工作目录/);
});

test("Windows drive and UNC comparisons ignore case while POSIX paths remain case sensitive",()=>{
  const {ui}=harness();
  assert.equal(ui.rootChanged("C:\\Work\\Project\\","c:/work/project"),false);
  assert.equal(ui.rootChanged("\\\\SERVER\\Share\\Work\\","//server/share/work"),false);
  assert.equal(ui.rootChanged("/Work/Project","/work/project"),true);
  assert.equal(ui.rootChanged("/work/project/","/work/project"),false);
});

test("editing only the display name saves without stop and reopening reads the saved state",async()=>{
  const h=harness();await h.settings.open("stable-project");assert(h.html().includes('maxlength="100"'));assert(!h.html().includes('data-workspace-action="browse"'));
  h.input("display_name","新的显示名称");await h.settings.save();
  assert.deepEqual(h.calls.update,[{project:"stable-project",display_name:"新的显示名称",root:"C:/work/old",revision:7}]);assert.equal(h.calls.saved.length,1);
  assert.match(h.html(),/工作区设置已保存/);h.settings.close();await h.settings.open("stable-project");
  assert.equal(h.settings.getDraft().display_name,"新的显示名称");assert.deepEqual(h.calls.read,["stable-project","stable-project"]);
});

test("directory migration needs an explicit confirmation, preserves counts, and sends once while pending",async()=>{
  let finish;const writes=[];const h=harness({update:body=>{writes.push(plain(body));return new Promise(resolve=>{finish=resolve;});}});
  await h.settings.open("stable-project");h.input("root","C:/work/new");await h.settings.save();
  assert.equal(writes.length,0);assert(h.html().includes("先停止 1 个运行会话"));assert(h.html().includes("全部 2 个已有 leader 和成员"));assert(h.html().includes("不搬动磁盘文件"));assert(h.html().includes("不会自动重新开工"));
  const pending=h.click("confirm");await h.click("confirm");await h.click("close");assert.equal(writes.length,1);assert(h.dialog.open);
  assert.deepEqual(writes[0],{project:"stable-project",display_name:"显示名称",root:"C:/work/new",revision:7,stop:true});assert.match(h.nodes.get('[data-workspace-note]').textContent,/正在停止并迁移/);
  finish(snapshot({project:{...snapshot().project,root:"C:/work/new"},revision:8,directory_change:{old_root:"C:/work/old",new_root:"C:/work/new"}}));await pending;
  assert.match(h.html(),/尚未重新开工/);assert.equal(h.settings.getDraft().root,"C:/work/new");
});

test("a failed save and returning to edit retain drafts, while closing discards them",async()=>{
  const h=harness({update:async()=>{throw Error("成员路径无法映射：C:/external");}});await h.settings.open("stable-project");h.input("display_name","未保存名称");h.input("root","C:/invalid/new");await h.settings.save();await h.click("confirm");
  assert.equal(h.nodes.get('[data-workspace-note]').attributes.role,"alert");assert.equal(h.nodes.get('[data-workspace-note]').textContent,"成员路径无法映射：C:/external");
  await h.click("back");assert.equal(h.nodes.get('[name="root"]').value,"C:/invalid/new");assert.equal(h.nodes.get('[name="display_name"]').value,"未保存名称");
  await h.click("close");await h.settings.open("stable-project");assert.equal(h.settings.getDraft().root,"C:/work/old");assert.equal(h.settings.getDraft().display_name,"显示名称");
});

test("Escape cancellation discards unsaved edits and reopening reads the stored values",async()=>{
  const h=harness();await h.settings.open("stable-project");h.input("display_name","取消的名称");h.input("root","D:/cancelled");h.cancel();
  assert.equal(h.dialog.open,false);await h.settings.open("stable-project");assert.equal(h.settings.getDraft().display_name,"显示名称");assert.equal(h.settings.getDraft().root,"C:/work/old");assert.equal(h.calls.update.length,0);
});

test("the save receipt reports migration only when the backend confirms directory_change",async()=>{
  const h=harness({update:async()=>snapshot({revision:8,directory_change:null})});await h.settings.open("stable-project");h.input("root","C:/same-directory-alias");await h.settings.save();await h.click("confirm");
  assert(h.html().includes("工作区设置已保存"));assert(!h.html().includes("已停止并迁移"));
});

test("native browsing only edits the path draft; cancellation and errors do not register or save",async()=>{
  let selected=null;const picked=[];const h=harness({pickDirectory:async current=>{picked.push(current);if(selected instanceof Error)throw selected;return selected;}});
  await h.settings.open("stable-project");await h.click("browse");assert.equal(h.settings.getDraft().root,"C:/work/old");
  selected=Error("目录选择失败");await h.click("browse");assert.equal(h.nodes.get('[data-workspace-note]').textContent,"目录选择失败");assert.equal(h.settings.getDraft().root,"C:/work/old");
  selected="D:/new-folder";await h.click("browse");assert.equal(h.settings.getDraft().root,selected);assert.equal(h.calls.update.length,0);assert.equal(h.calls.remove.length,0);assert.deepEqual(picked,["C:/work/old","C:/work/old","C:/work/old"]);
});

test("remove requires confirmation and success before closing, with retry after a real failure",async()=>{
  let fail=true;const writes=[];const h=harness({remove:async body=>{writes.push(plain(body));if(fail)throw Error("会话尚未停止");return {ok:true,project:body.project,removed:true};}});
  await h.settings.open("stable-project");await h.click("remove");assert.equal(writes.length,0);assert(h.html().includes("磁盘文件和全部记录都会保留"));assert(h.html().includes("重新打开同一目录"));
  await h.click("confirm");assert(h.dialog.open);assert.equal(h.calls.removed.length,0);assert.equal(h.nodes.get('[data-workspace-note]').textContent,"会话尚未停止");
  fail=false;await h.click("confirm");assert(!h.dialog.open);assert.deepEqual(writes[1],{project:"stable-project",revision:7,stop:true});assert.equal(h.calls.removed.length,1);
});

test("late reads from a previous workspace never replace the active workspace form",async()=>{
  const pending={};const h=harness({read:key=>new Promise(resolve=>{pending[key]=resolve;})});
  const a=h.settings.open("A"),b=h.settings.open("B");pending.B(snapshot({project:{name:"B",root:"D:/B",display_name:"B名称"}}));await b;
  pending.A(snapshot({project:{name:"A",root:"D:/A",display_name:"A名称"}}));await a;
  assert.equal(h.settings.getDraft().root,"D:/B");assert(h.html().includes("B名称"));assert(!h.html().includes("A名称"));
});

test("a failed initial read exposes its real error and only retries on the explicit action",async()=>{
  let reads=0;const h=harness({read:async()=>{reads++;if(reads===1)throw Error("后台暂未连接");return snapshot();}});
  await h.settings.open("stable-project");assert.equal(reads,1);assert(h.html().includes("后台暂未连接"));assert(h.html().includes('data-workspace-action="reload"'));assert.equal(h.settings.getDraft(),null);
  await h.click("reload");assert.equal(reads,2);assert.equal(h.settings.getDraft().root,"C:/work/old");assert.equal(h.calls.update.length,0);
});
