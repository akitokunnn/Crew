"use strict";
const {test} = require("node:test"), assert = require("node:assert/strict");
const {create} = require("../crew/web/command-palette.js"), {fixture} = require("./shell_ui_fixture.cjs");
const {create:actions} = require("../crew/web/ui-actions.js"), {create:visibility} = require("../crew/web/ui-visibility.js");
function setup(injected = false) {
  const dom = fixture(), anchor = dom.element("button"), commands = actions(), visible = visibility(), executed = []; let before = 0;
  let items = [{group:"Leader",key:"Alice goal",html:"Alice",go:()=>executed.push("Alice")},{group:"下属",key:"Bob",html:"Bob",go:()=>executed.push("Bob")}]; anchor.focus();
  const palette = create({document:dom.doc,portal:injected?dom.element("div"):undefined,commands:()=>items,icon:()=>"search",actions:commands,visibility:visible,
    beforeOpen:()=>before++,focus:{capture:()=>dom.doc.activeElement,restore:node=>node?.focus()}});
  const overlay = dom.doc.body.children.at(-1);
  return {...dom, anchor, commands, visible, palette, overlay, executed, before:()=>before, commandsList:value=>{items=value;}};
}
const query = f => f.overlay.querySelector("#palette-query");
const key = (f,value,extra={}) => f.doc.emit("keydown",{target:f.doc.activeElement,key:value,...extra});
test("palette owns filtering, grouped results, bounded selection and enter navigation with return focus", () => {
  const f = setup();f.commands.dispatch("palette");assert.equal(f.before(),1);assert.equal(f.palette.isOpen(),true);assert.equal(f.visible.blocked(),true);assert.equal(f.doc.activeElement,query(f));
  key(f,"ArrowDown");key(f,"ArrowDown");key(f,"ArrowDown");assert.equal(f.overlay.querySelector(".on").dataset.index,"1");
  key(f,"Enter");assert.deepEqual(f.executed,["Bob"]);assert.equal(f.palette.isOpen(),false);assert.equal(f.doc.activeElement,f.anchor);assert.equal(f.visible.blocked(),false);
  f.palette.open();query(f).value=" aLiCe ";f.overlay.emit("input",{target:query(f)});assert.match(f.overlay.querySelector("#palette-results").innerHTML,/Alice/);assert.doesNotMatch(f.overlay.querySelector("#palette-results").innerHTML,/Bob/);
  key(f,"ArrowUp");key(f,"Enter");assert.deepEqual(f.executed,["Bob","Alice"]);
});
test("empty results and IME preserve the palette; dynamic commands update without losing input focus", () => {
  const f = setup();f.palette.open();const input=query(f);input.value="missing";f.overlay.emit("input",{target:input});key(f,"ArrowDown");key(f,"Enter");assert.equal(f.palette.isOpen(),true);assert.deepEqual(f.executed,[]);
  assert.match(f.overlay.querySelector("#palette-results").innerHTML,/没有找到匹配项/);key(f,"Escape",{isComposing:true});assert.equal(f.palette.isOpen(),true);
  f.commandsList([{group:"操作",key:"missing",html:"New",go:()=>f.executed.push("New")}]);f.palette.update();assert.equal(query(f),input);assert.equal(f.doc.activeElement,input);
  key(f,"Enter",{isComposing:true});assert.deepEqual(f.executed,[]);key(f,"Enter");assert.deepEqual(f.executed,["New"]);
});
test("tab loops within the portal, click actions close before execution, and overlay/escape close restores focus", () => {
  const f = setup();f.palette.open();key(f,"Tab",{shiftKey:true});assert.equal(f.doc.activeElement.dataset.index,"1");key(f,"Tab");assert.equal(f.doc.activeElement,query(f));
  f.commands.dispatch("palette-go",{element:{dataset:{index:"999"}}});assert.equal(f.palette.isOpen(),true);
  f.commands.dispatch("palette-go",{element:{dataset:{index:"0"}}});assert.deepEqual(f.executed,["Alice"]);assert.equal(f.doc.activeElement,f.anchor);
  f.palette.open();f.overlay.emit("click");assert.equal(f.palette.isOpen(),false);assert.equal(f.doc.activeElement,f.anchor);
  f.palette.open();key(f,"Escape");assert.equal(f.palette.isOpen(),false);
});
test("destroy unregisters listeners/actions/obstruction and removes only a controller-owned portal", () => {
  for (const injected of [false,true]) {
    const f = setup(injected);f.palette.open();f.palette.destroy();f.palette.destroy();
    assert.equal(f.doc.listenerCount(),0);assert.equal(f.overlay.listenerCount(),0);assert.deepEqual(f.commands.owners(),[]);assert.deepEqual(f.visible.owners(),[]);
    assert.equal(f.overlay.isConnected,injected);assert.equal(f.overlay.classList.contains("show"),false);f.palette.open();assert.equal(f.palette.isOpen(),false);
  }
});
test("opening from another portal captures the visible focus restored by its close operation", () => {
  const f = fixture(), visible = f.element("button"), portal = f.element("section"), hiddenControl = f.element("button",{},portal);
  hiddenControl.focus();
  const palette = create({document:f.doc,commands:()=>[],icon:()=>"",beforeOpen(){portal.hidden=true;visible.focus();},focus:{capture:()=>f.doc.activeElement,restore:node=>node.focus()}});
  palette.open();palette.close();assert.equal(f.doc.activeElement,visible);assert.equal(hiddenControl.isConnected,true);palette.destroy();
});
