"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const {webcrypto} = require("node:crypto");
const settle = () => new Promise(resolve => setImmediate(resolve));

// The board and canvas install their real pointer and command handlers on these event targets.
async function screen(phone = true) {
  const calls = [], opened = [], messages = [], plans = [];
  const elements = new Map(), classes = () => {
    const values = new Set();
    return {add:value => values.add(value), remove:value => values.delete(value), contains:value => values.has(value),
      toggle:(value,on) => on ? values.add(value) : values.delete(value)};
  };
  function element(name) {
    if (!elements.has(name)) elements.set(name, {
      dataset:{}, style:{setProperty() {}}, classList:classes(), events:{}, hidden:true,
      offsetWidth:160, offsetHeight:64, focus() {}, select() {}, contains:() => false,
      closest:() => null, querySelector:element, getBoundingClientRect:() => ({left:45,top:100,right:205,bottom:164,width:160,height:64}),
      setAttribute() {}, setPointerCapture() {}, hasPointerCapture:() => false,
      addEventListener(type, fn) { this.events[type] = fn; },
      showModal() { this.open = true; }, close() { this.open = false; }
    });
    return elements.get(name);
  }
  let board = {version:11,direction:"TB",parts:["root001","other01"].map((id,i) => (
    {id,name:id,parent_id:null,x:0,y:i*210,collapsed:false})),independent:[],
    leaders:[{session_id:"leader1",domain_id:"root001",lead:false}]};
  let failSave = false;
  const host=element("host"), canvas=element(".arch-canvas"), win=element("window");
  canvas.getBoundingClientRect=() => ({left:0,top:0,width:phone?375:1100,height:700,right:phone?375:1100,bottom:700});
  const part=id => Object.assign(element(`.arch-part[data-part="${id}"]`), {dataset:{part:id}});
  const row=Object.assign(element('[data-leader="leader1"]'), {dataset:{leader:"leader1"}});
  function target(id, leader=false, pin=null) {
    const node=id && part(id);
    const closest = selector => {
      if (selector===".arch-part") return node;
      if (selector===".arch-member" && leader) return row;
      if (selector===".arch-pin" && pin) return {dataset:{pin}};
      return null;
    };
    return {closest};
  }
  host.querySelectorAll = selector => selector===".arch-part" ? board.parts.map(p=>part(p.id)) : selector===".arch-member" ? [row] : [];
  host.contains = () => true;
  Object.assign(win,{CrewBrowser:{keyboardShortcut:k=>k}, matchMedia:() => ({matches:phone,addEventListener(){}})});
  const ctx=vm.createContext({window:win, CSS:{escape:s=>s}, crypto:webcrypto, localStorage:{getItem:()=>null,setItem(){}},
    document:{activeElement:null,elementFromPoint:()=>null}, ResizeObserver:class {observe(){}}, requestAnimationFrame:fn=>fn(),
    CrewRestructure:{compare:()=>"",diffSummary:()=>"删除分项",create:()=>({open:plan=>plans.push(plan)})}});
  const source=file=>fs.readFileSync(path.join(__dirname,"../../frontend",file),"utf8");
  vm.runInContext(source("canvas-core.js"),ctx);
  const arch=vm.runInContext(source("architecture.js")+"\nCrewArchitecture",ctx);
  const meta={number:2,source:"edit",note:"旧版",created_at:"2026-11-27"};
  async function api(url, body) {
    const op=url.split("/").pop().split("?")[0];
    calls.push({op,body:body && structuredClone(body)});
    if (op==="domains.get") return structuredClone(board);
    if (op==="domains.versions.list") return {versions:[meta],latest_matches:true};
    if (op==="domains.version.get") return {version:meta,state:{parts:[],leaders:[]},current:board,board_version:board.version,
      rollback:{nothing:false,gone:[],archived:[],unplaced:[]},diff:{parts:{added:[],removed:board.parts,renamed:[],moved:[]},leaders:{placed:[],moved:[],unplaced:[],lead_on:[],lead_off:[]}}};
    if (op==="domains.rollback") return {version:3};
    assert.equal(op,"domains.apply");
    if (failSave) { failSave=false; throw Object.assign(Error("版本冲突"),{status:409}); }
    const effects=[];
    board=structuredClone(board);
    for (const edit of body.ops) {
      const p=board.parts.find(n=>n.id===edit.id), place=board.leaders.find(n=>n.session_id===edit.session_id);
      const effect={};
      switch(edit.op) {
        case "create": board.parts.push({...edit,collapsed:false}); effect.id=edit.id; break;
        case "rename": p.name=edit.name; break;
        case "parent": p.parent_id=edit.parent_id; break;
        case "move": for (const pos of edit.items) Object.assign(board.parts.find(n=>n.id===pos.id),pos); break;
        case "delete": board.parts=board.parts.filter(n=>n.id!==edit.id); Object.assign(effect,{children:[],leaders:[]}); break;
        case "lead": place.lead=edit.lead; break;
        case "assign": board.leaders=board.leaders.filter(n=>n!==place); break;
        case "direction": board.direction=edit.direction; break;
        default: throw Error("unexpected op "+edit.op);
      }
      effects.push(effect);
    }
    board.version++;
    return {version:board.version,board:structuredClone(board),effects};
  }
  arch.mount(host,{project:"demo", state:{domains:{version:11},leaders:["leader1","leader2"].map(id=>({session_id:id,name:id,status:"idle"}))},
    api,openLeader:id=>opened.push(id),toast:msg=>messages.push(msg),avatar:()=>"",tint:()=>"#aaa"});
  await settle();
  const press=async (archAct,cmd,extra={}) => {
    const btn=Object.assign(element("button"),{dataset:{archAct,cmd,...extra}});
    host.events.click({target:{closest:()=>btn}}); await settle();
  };
  const gesture=async (node,dx=0) => {
    const ev={button:0,pointerId:1,pointerType:phone?"touch":"mouse",clientX:120,clientY:130,target:node,preventDefault(){}};
    canvas.events.pointerdown(ev);
    if (dx) win.events.pointermove({...ev,clientX:120+dx});
    win.events.pointerup({...ev,clientX:120+dx}); await settle();
  };
  const writes=()=>calls.filter(c=>c.op==="domains.apply").map(c=>c.body);
  const submit=async name=>{
    element("#arch-new-name").value=name;
    host.events.submit({target:{closest:()=>element(".arch-name-dialog")},preventDefault(){}}); await settle();
  };
  return {element,part,row,gesture,target,press,action:cmd=>press("action",cmd),submit,calls,writes,opened,messages,plans,
    version:()=>board.version,conflict:()=>{failSave=true;}};
}

test("touch pans over unselected parts and people, then moves only the selected part",async()=>{
  const s=await screen(), bar=s.element(".arch-actions"), world=s.element(".arch-world");
  for (const hit of [s.target("root001"),s.target("root001",true)]) {
    const before=world.style.transform;
    await s.gesture(hit,30);
    assert.notEqual(world.style.transform,before); assert.equal(bar.hidden,true);
    assert.deepEqual(s.writes(),[]); assert.deepEqual(s.opened,[]);
  }
  await s.gesture(s.target("root001"));
  assert(s.part("root001").classList.contains("selected")); assert.equal(bar.hidden,false);
  assert.match(bar.innerHTML,/加子项.*改名.*移到….*删除.*更多/);
  await s.gesture(s.target("other01"),20); assert.deepEqual(s.writes(),[]);
  await s.gesture(s.target("root001"),25);
  assert.deepEqual(s.writes(),[{project:"demo",base_version:11,ops:[{op:"move",items:[{id:"root001",x:25,y:0}]}]}]);
});

test("part actions carry the latest board version; name cancel, blank input and target cancel never write",async()=>{
  const s=await screen(), dialog=s.element(".arch-name-dialog"), root=s.target("root001"), other=s.target("other01");
  await s.gesture(root); await s.action("rename"); assert(dialog.open);
  await s.submit("  "); assert(dialog.open); await s.press("name-cancel"); assert(!dialog.open);
  await s.action("more"); assert.match(s.element(".arch-menu").innerHTML,/连接/);
  await s.press("menu","connect"); assert.match(s.element(".arch-target").innerHTML,/点目标设为下级.*取消/);
  await s.press("cancel-target"); assert(s.element(".arch-target").hidden); assert.deepEqual(s.writes(),[]);
  await s.action("rename"); await s.submit("  客户端  ");
  await s.gesture(s.target("root001",false,"out")); await s.gesture(other);
  await s.gesture(other); await s.action("move"); await s.gesture(s.target(null));
  await s.action("move"); await s.gesture(root); await s.action("child");
  const created=s.writes().at(-1).ops[0]; assert.equal(created.parent_id,"other01"); assert(dialog.open);
  await s.press("name-cancel"); await s.action("delete");
  assert.deepEqual(s.writes().map(b=>b.base_version),[11,12,13,14,15,16]);
  assert.deepEqual(s.writes().map(b=>b.ops[0]),[
    {op:"rename",id:"root001",name:"客户端"},{op:"parent",id:"other01",parent_id:"root001"},
    {op:"parent",id:"other01",parent_id:null},{op:"parent",id:"other01",parent_id:"root001"},
    created,{op:"delete",id:created.id}]);
});

test("leader selects before chat, uses existing ownership and restructure commands",async()=>{
  const s=await screen();
  await s.gesture(s.target("root001",true));
  assert.deepEqual(s.opened,[]); assert(s.row.classList.contains("selected"));
  assert.match(s.element(".arch-actions").innerHTML,/进对话.*设负责人.*移出.*更多/);
  await s.action("open"); assert.deepEqual(s.opened,["leader1"]);
  for (const cmd of ["merge","retire"]) {
    await s.action("more"); assert.match(s.element(".arch-menu").innerHTML,/合并….*退役…/);
    await s.press("menu",cmd); assert.equal(s.plans.at(-1).changes[0].type,"leader."+cmd);
  }
  await s.action("lead"); await s.action("leave");
  assert.deepEqual(s.writes().map(b=>[b.base_version,b.ops[0].op]),[[11,"lead"],[12,"assign"]]);
  assert.equal(s.writes()[1].ops[0].domain_id,null);
});

test("history cancel and confirm use the selected version preview",async()=>{
  const s=await screen(), drawer=s.element(".arch-history");
  await s.press("history"); await s.press("history-pick",null,{number:"2"});
  assert.match(drawer.innerHTML,/回滚到这个版本/); await s.press("history-rollback");
  assert.match(drawer.innerHTML,/确认回滚/); await s.press("history-cancel");
  assert(!s.calls.some(c=>c.op==="domains.rollback"));
  await s.press("history-rollback"); await s.press("history-confirm");
  assert.deepEqual(s.calls.filter(c=>c.op==="domains.rollback").map(c=>c.body),[{project:"demo",number:2,board_version:11}]);
});

test("header menu replays edits, switches direction and reloads after a conflicting edit",async()=>{
  const s=await screen();
  await s.gesture(s.target("root001")); await s.action("rename"); await s.submit("新版");
  for (const cmd of ["undo","redo","LR","tidy"]) {
    await s.press("board-more"); assert.match(s.element(".arch-menu").innerHTML,/撤销.*重做.*自动排列.*从上到下.*从左到右/);
    await s.press("menu",cmd);
  }
  assert.deepEqual(s.writes().map(b=>b.base_version),[11,12,13,14,15]);
  assert.equal(s.writes()[1].ops[0].name,"root001"); assert.equal(s.writes()[2].ops[0].name,"新版");
  s.conflict(); await s.action("rename"); await s.submit("冲突");
  assert.match(s.messages.at(-1),/重新载入/); assert.equal(s.calls.at(-1).op,"domains.get");
});

test("desktop keeps direct chat, first-drag movement and header tools",async()=>{
  const s=await screen(false), markup=s.element(".arch-head").innerHTML;
  assert.match(markup,/arch-dir/); assert.match(markup,/data-arch-act="undo"/); assert.doesNotMatch(markup,/board-more/);
  await s.gesture(s.target("root001",true)); assert.deepEqual(s.opened,["leader1"]);
  await s.gesture(s.target("other01"),25);
  assert.equal(s.writes()[0].ops[0].op,"move"); assert(s.element(".arch-actions").hidden);
});
