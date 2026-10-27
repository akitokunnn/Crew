"use strict";
const {test} = require("node:test"), assert = require("node:assert/strict");
const {create} = require("../crew/web/ui-menu.js"), {fixture} = require("./shell_ui_fixture.cjs");
const {create:actions} = require("../crew/web/ui-actions.js"), {create:visibility} = require("../crew/web/ui-visibility.js");
function setup(selection) {
  const dom = fixture(), root = dom.element("div", {id:"menu", role:"menu"}), anchor = dom.element("button", {"data-act":"session-menu"});
  const commands = actions(), visible = visibility(), menu = create({document:dom.doc, window:dom.win, element:root, actions:commands, visibility:visible, selection});
  return {...dom, root, anchor, commands, visible, menu};
}
const markup = '<button data-act="one">One</button><button disabled data-act="disabled">Disabled</button><div class="menu-branch"><button data-act="submenu" aria-expanded="false">More</button><div role="menu" class="sub-menu" hidden><button data-act="child">Child</button></div></div><button data-act="last">Last</button>';
const key = (fixture, value, extra = {}) => { const event = {key:value, defaultPrevented:false, preventDefault(){this.defaultPrevented=true;}, ...extra}; assert.equal(fixture.menu.handleKey(event), true); return event; };
test("menu owns anchored/context-point geometry and root keyboard excludes disabled and nested items", () => {
  const f = setup(); f.anchor.rect = {left:480,right:500,top:380,bottom:400}; f.menu.show(f.anchor, markup);
  assert.equal(f.visible.blocked(), true); assert.equal(f.root.style.left,"312px"); assert.equal(f.root.style.top,"292px"); assert.equal(f.doc.activeElement.dataset.act,"one");
  key(f,"ArrowDown"); assert.equal(f.doc.activeElement.dataset.act,"submenu"); key(f,"End"); assert.equal(f.doc.activeElement.dataset.act,"last");
  key(f,"ArrowDown"); assert.equal(f.doc.activeElement.dataset.act,"one"); key(f,"ArrowUp"); assert.equal(f.doc.activeElement.dataset.act,"last");
  f.menu.show(f.anchor, markup, {x:-100,y:900}); assert.equal(f.root.style.left,"8px"); assert.equal(f.root.style.top,"292px");
});
test("submenu flips at viewport edge, focuses child and Escape restores each parent before anchor", () => {
  const f = setup(); f.menu.show(f.anchor, markup); const button = f.root.querySelector('[data-act="submenu"]'); button.rect = {left:380,right:490,top:360,bottom:380}; button.focus();
  key(f,"ArrowRight"); const child = button.nextElementSibling;
  assert.equal(child.hidden,false); assert.equal(button.getAttribute("aria-expanded"),"true"); assert.equal(child.style.left,"200px"); assert.equal(child.style.top,"292px"); assert.equal(f.doc.activeElement.dataset.act,"child");
  key(f,"Escape"); assert.equal(child.hidden,true); assert.equal(f.doc.activeElement,button); assert.equal(f.menu.isOpen(),true);
  key(f,"Escape"); assert.equal(f.menu.isOpen(),false); assert.equal(f.doc.activeElement,f.anchor); assert.equal(f.visible.blocked(),false);
});
test("pointer, selection policy and outside dismissal compose with registered feature actions", () => {
  const f = setup({keepOpen:["one"], restoreFocus:["last"]}); f.menu.show(f.anchor,markup);
  const submenu = f.root.querySelector('[data-act="submenu"]'); f.doc.emit("pointerover",{target:submenu}); assert.equal(submenu.getAttribute("aria-expanded"),"true");
  f.menu.handleClick({target:f.root.querySelector('[data-act="one"]')}); assert.equal(f.menu.isOpen(),true);
  f.menu.handleClick({target:f.root.querySelector('[data-act="last"]')}); assert.equal(f.menu.isOpen(),false); assert.equal(f.doc.activeElement,f.anchor);
  f.menu.show(f.anchor,markup); f.doc.emit("click",{target:f.anchor}); assert.equal(f.menu.isOpen(),true);
  f.doc.emit("click",{target:f.doc.body}); assert.equal(f.menu.isOpen(),false);
});
test("empty menus and lost focus are safe, Tab restores, and destroy unregisters resources", () => {
  const f = setup(); f.menu.show(f.anchor,""); f.doc.body.focus(); key(f,"ArrowDown"); key(f,"ArrowLeft");
  f.menu.show(f.anchor,markup); key(f,"Tab"); assert.equal(f.doc.activeElement,f.anchor);
  f.menu.show(f.anchor,markup); f.menu.destroy(); f.menu.destroy(); assert.equal(f.doc.listenerCount(),0); assert.deepEqual(f.commands.owners(),[]); assert.deepEqual(f.visible.owners(),[]);
  f.menu.show(f.anchor,markup); assert.equal(f.menu.isOpen(),false); assert.equal(f.menu.anchor(),null);
});
