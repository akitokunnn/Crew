"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");

function renderer() {
  const context = vm.createContext({matchMedia:() => ({matches:true}), atob, btoa});
  context.window = context;
  for (const name of ["vendor/markdown-it.min.js", "chat.js"]) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, "../crew/web", name), "utf8"), context);
  }
  return context.CrewChat;
}

test("a leader without an optional goal renders empty inline text instead of breaking the workspace", () => {
  const chat = renderer();
  assert.equal(chat.renderInline(null, "leader"), "");
  assert.equal(chat.renderInline(undefined, "leader"), "");
  assert.equal(chat.renderInline("**验收：**继续", "leader"), "<strong>验收</strong>：继续");
});
