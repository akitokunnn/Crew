"use strict";
const {test} = require("node:test"), assert = require("node:assert/strict");
const {createState, geometry, create} = require("../crew/web/conversation-workspace.js");

test("independent pending and architecture survive same-scope refresh and close on another conversation", () => {
  const state = createState(); state.update({active:true, scope:"leader:goal-a", task:true});
  state.toggle("tree"); state.toggle("attention");
  state.update({scope:"leader:goal-a", materialsOpen:false});
  assert.deepEqual(state.snapshot().visible, {attention:true,tree:true,tasks:false});
  state.update({scope:"member:goal-a"});
  assert.deepEqual(state.snapshot().visible, {attention:false,tree:false,tasks:false});
});

test("returning from a list does not resurrect a previous conversation's floating panels", () => {
  const state = createState(); state.update({active:true, scope:"leader:"}); state.setOpen("tree",true);
  state.update({listing:true}); assert.equal(state.snapshot().auxiliaryOpen,false);
  state.update({listing:false}); assert.equal(state.snapshot().auxiliaryOpen,false);
  state.setOpen("attention",true); state.update({active:false}); state.update({active:true});
  assert.equal(state.snapshot().auxiliaryOpen,false);
});

test("Escape unwinds tasks, pending and architecture independently and leaves higher materials to their owner", () => {
  const state = createState(); state.update({active:true,scope:"leader:goal",task:true});
  for (const section of ["tree","attention","tasks"]) state.setOpen(section,true);
  state.update({reader:true}); assert.equal(state.escape(),null); assert.equal(state.snapshot().blocked,true);
  state.update({reader:false,materialsOpen:true}); assert.equal(state.escape(),null);
  state.update({materialsOpen:false});
  for (const section of ["tasks","attention","tree"]) { assert.equal(state.escape(),section); state.setOpen(section,false); }
  assert.equal(state.escape(),null);
});

test("snapshots cannot mutate owned panel state and unknown sections fail explicitly", () => {
  const state = createState(); state.update({active:true,scope:"leader:"}); state.setOpen("tree",true);
  const snapshot = state.snapshot(); snapshot.opened.tree=false; snapshot.visible.tree=false;
  assert.equal(state.snapshot().visible.tree,true);
  assert.throws(()=>state.toggle("another-panel"),/Unknown workspace section/);
});

test("workspace geometry reserves reading room on a wide column and overlays a narrow column", () => {
  const wide = geometry({viewportWidth:1680,viewportHeight:960,anchorBottom:98,mainWidth:1412,treeOpen:true});
  assert.equal(wide.sideSpace,360); assert.equal(wide.overlap,false); assert.equal(wide.attentionMaxHeight,320);
  assert.equal(wide.top,108); assert.equal(wide.top+wide.height,946);
  const narrow = geometry({viewportWidth:820,viewportHeight:600,anchorBottom:130,mainWidth:552,treeOpen:false});
  assert.equal(narrow.sideSpace,0); assert.equal(narrow.overlap,true); assert.equal(narrow.top+narrow.height,586);
  assert.equal(narrow.attentionMaxHeight,narrow.height-66);
});

function fixture() {
  const properties = () => ({setProperty(name,value){this[name]=value;}});
  const controls = new Map(["attention","right-open","task-panel"].map(action=>[action,{attributes:{},focusCalls:0,
    setAttribute(name,value){this.attributes[name]=value;},focus(){this.focusCalls++;}}]));
  const ids = ["chat-attention","attention-slot","right-panel","side-attention-section","side-task-section","main","chat-head","goal-head"];
  const nodes = new Map(ids.map(id=>[id,{hidden:false,inert:false,dataset:{},style:properties(),scrollTop:0,
    getBoundingClientRect:()=>({bottom:98,height:id==="side-attention-section"?200:0}),setAttribute(name,value){this[name]=value;}}]));
  nodes.get("main").clientWidth=1412;
  nodes.get("chat-head").querySelector=selector=>controls.get(selector.match(/data-act="([^"]+)"/)[1]);
  const taskClose = {focused:false,focus(){this.focused=true;}}; nodes.get("side-task-section").querySelector=()=>taskClose;
  const listeners = new Map(), observers=[];
  const win={innerWidth:1680,innerHeight:960,addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name),
    ResizeObserver:class{constructor(fn){this.fn=fn;this.nodes=[];this.disconnected=false;observers.push(this);}observe(node){this.nodes.push(node);}disconnect(){this.disconnected=true;}}};
  const doc={getElementById:id=>nodes.get(id)}; let lists=0, materialsClosed=0;
  const workspace=create({document:doc,window:win,onTaskList:()=>lists++,closeMaterials:()=>materialsClosed++});
  return {workspace,nodes,controls,win,listeners,observers,taskClose,lists:()=>lists,materialsClosed:()=>materialsClosed};
}

test("DOM controller pushes architecture below pending without replacing panel content or its scroll position", () => {
  const {workspace,nodes,controls}=fixture(); workspace.update({active:true,scope:"leader:"});
  workspace.toggle("tree"); assert.equal(nodes.get("right-panel").hidden,false); assert.equal(nodes.get("attention-slot").style.height,"0px");
  nodes.get("side-attention-section").scrollTop=73; workspace.toggle("attention");
  assert.equal(nodes.get("attention-slot").style.height,"212px"); assert.equal(nodes.get("main").style["--chat-side-space"],"360px");
  workspace.update({materialsOpen:false}); assert.equal(nodes.get("side-attention-section").scrollTop,73);
  assert.equal(controls.get("attention").attributes["aria-expanded"],"true");
  assert.equal(workspace.escape(),true); assert.equal(nodes.get("attention-slot").style.height,"0px");
  assert.equal(nodes.get("right-panel").hidden,false); assert.equal(controls.get("attention").focusCalls,1);
});

test("materials and reports cover auxiliary input and prevent conversation-read claims", () => {
  const {workspace,nodes}=fixture(); workspace.update({active:true,scope:"leader:goal",task:true});
  workspace.open("tree"); workspace.open("attention"); assert.equal(workspace.blocksReading(),false);
  workspace.setReader(true);
  assert.equal(nodes.get("chat-attention").inert,true); assert.equal(nodes.get("right-panel").inert,true);
  assert.equal(nodes.get("attention-slot").inert,true); assert.equal(workspace.blocksReading(),true);
  workspace.setReader(false); assert.equal(nodes.get("chat-attention").inert,false); assert.equal(workspace.blocksReading(),false);
  workspace.update({materialsOpen:true}); assert.equal(workspace.expanded("tasks"),true); assert.equal(workspace.blocksReading(),true);
});

test("task button requests a list in general chat and opens task tools only inside a task", () => {
  const f=fixture(); f.workspace.update({active:true,scope:"leader:"}); f.workspace.toggle("tasks");
  assert.equal(f.lists(),1); assert.equal(f.workspace.snapshot().visible.tasks,false);
  f.workspace.update({scope:"leader:goal",task:true,materialsOpen:true}); f.workspace.toggle("tasks");
  assert.equal(f.materialsClosed(),1); assert.equal(f.taskClose.focused,true); assert.equal(f.nodes.get("side-task-section").hidden,false);
  assert.equal(f.workspace.blocksReading(),true); f.workspace.close("tasks",true);
  assert.equal(f.controls.get("task-panel").focusCalls,1); assert.equal(f.workspace.blocksReading(),false);
});

test("resize and destruction are owned by the workspace controller", () => {
  const f=fixture(); f.workspace.update({active:true,scope:"leader:"}); f.workspace.open("tree");
  f.nodes.get("main").clientWidth=552; f.win.innerWidth=820; f.listeners.get("resize")();
  assert.equal(f.nodes.get("main").dataset.sideOverlap,"true"); assert.equal(f.workspace.blocksReading(),true);
  f.workspace.closeAuxiliary(); assert.equal(f.nodes.get("main").style["--chat-side-space"],"0px"); assert.equal(f.workspace.blocksReading(),false);
  f.workspace.destroy(); assert.equal(f.observers[0].disconnected,true); assert.equal(f.listeners.size,0);
});
