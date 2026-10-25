"use strict";
const {test} = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const sandbox = {window:{}}; vm.createContext(sandbox); vm.runInContext(fs.readFileSync(path.join(__dirname,"../crew/web/launch-settings-ui.js"),"utf8"),sandbox);
const ui = sandbox.window.CrewLaunchSettings, plain = value => JSON.parse(JSON.stringify(value));
const settings = () => ({mode:"local",home:"C:\\Crew Data",source:"custom",backend_directory:"C:\\Crew source\\plugin",service_url:"",port:0,python:"C:\\Python Tools\\python.exe"});
test("launch form preserves all startup settings and Windows path boundaries", () => {
  const source = settings(), draft = ui.draftSettings(source); assert.equal(draft.port,"0"); draft.port="7133";
  assert.equal(source.port,0); assert.deepEqual(plain(ui.settingsValue(draft)),{...source,port:7133});
});
test("launch form validates port syntax while preserving the automatic-port choice", () => {
  for (const port of [""," ","1.5","-1","65536","wrong"]) assert.throws(() => ui.settingsValue({...ui.draftSettings(settings()),port}),/0 到 65535/);
  for (const port of ["0","1","65535"]) assert.equal(ui.settingsValue({...ui.draftSettings(settings()),port}).port,Number(port));
});
