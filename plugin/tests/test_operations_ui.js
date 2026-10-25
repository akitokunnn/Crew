"use strict";
const {test} = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const sandbox = {window:{}}; vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, "../crew/web/operations-ui.js"), "utf8"), sandbox);
const ui = sandbox.window.CrewOperations, plain = value => JSON.parse(JSON.stringify(value));

test("operations preflight follows explicit task/default choices and only infers a sole installed carrier", () => {
  const catalog=[{id:"claude",name:"Claude Code",available:true},{id:"codex",name:"Codex",available:true}];
  const ai={default:{runtime:null,model:null,effort:null},operations:null};
  assert.match(ui.brainIssue({ai,runtimes:catalog}),/多个 AI 载体/);
  assert.equal(ui.brainIssue({ai,runtimes:[catalog[1]]}),"");
  ai.default.runtime="claude";assert.equal(ui.brainIssue({ai,runtimes:catalog}),"");
  ai.operations={runtime:"codex"};assert.match(ui.brainIssue({ai,runtimes:[catalog[0]]}),/codex.*未就绪/);
  assert.equal(ui.brainIssue({ai,runtimes:catalog}),"");
  ai.operations={runtime:null};assert.match(ui.brainIssue({ai,runtimes:catalog}),/多个 AI 载体/);
  assert.match(ui.brainIssue({ai,runtimes:[]}),/安装并登录/);
});
test("operations form preserves exact argument boundaries and quoted Windows paths", () => {
  const policy = {enabled:true, repository:"C:\\Crew source", reproduce:[["C:\\Python Tools\\python.exe", "-m", "unittest", "tests.test_one"]], verify:[["node", "--test", "desktop/test/a.test.cjs"]], timeout_seconds:180, merge_ref:"main"};
  const draft = ui.policyDraft(policy); assert.equal(typeof draft.timeout_seconds, "string");
  assert.deepEqual(plain(ui.policyValue(draft)), policy); assert.deepEqual(policy.reproduce[0], ["C:\\Python Tools\\python.exe", "-m", "unittest", "tests.test_one"]);
});
test("operations form rejects malformed JSON and commands without program names", () => {
  assert.throws(() => ui.parseCommands('["python"', "复现命令"), /复现命令格式不正确/);
  for (const text of ['"python"', '["python"]', '[[]]', '[[" "]]', '[["python", 3]]']) assert.throws(() => ui.parseCommands(text, "验证命令"), /验证命令需要填写命令列表/);
  assert.deepEqual(plain(ui.parseCommands("[]", "验证命令")), []);
});
