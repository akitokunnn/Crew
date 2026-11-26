"use strict";
const {test} = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const sandbox = {window:{}, URL}; vm.createContext(sandbox); vm.runInContext(fs.readFileSync(path.join(__dirname,"../../frontend/launch-settings-ui.js"),"utf8"),sandbox);
const ui = sandbox.window.CrewLaunchSettings, plain = value => JSON.parse(JSON.stringify(value));
const settings = () => ({mode:"local",home:"C:\\Crew Data",source:"custom",backend_directory:"C:\\Crew source\\plugin",service_url:"",port:0,python:"C:\\Python Tools\\python.exe",remote_backend:""});
test("launch form preserves all startup settings and Windows path boundaries", () => {
  const source = settings(), draft = ui.draftSettings(source); assert.equal(draft.port,"0"); draft.port="7133";
  assert.equal(source.port,0); assert.deepEqual(plain(ui.settingsValue(draft)),{...source,port:7133});
});
test("launch form validates port syntax while preserving the automatic-port choice", () => {
  for (const port of [""," ","1.5","-1","65536","wrong"]) assert.throws(() => ui.settingsValue({...ui.draftSettings(settings()),port}),/0 到 65535/);
  for (const port of ["0","1","65535"]) assert.equal(ui.settingsValue({...ui.draftSettings(settings()),port}).port,Number(port));
});
test("a LAN host choice keeps its saved device and this computer's own launch settings unchanged", () => {
  const source = {...settings(), mode:"remote", remote_backend:"saved-host"};
  assert.deepEqual(plain(ui.settingsValue(ui.draftSettings(source))), source);
});
test("a pasted connection link fills the host address and the one-time code", () => {
  assert.deepEqual(plain(ui.pairingLink(" http://172.22.100.4:47812/remote/login#code=01234567 ")),{url:"http://172.22.100.4:47812",code:"01234567"});
  assert.deepEqual(plain(ui.pairingLink("http://192.168.1.20:47812/#code=87654321")),{url:"http://192.168.1.20:47812",code:"87654321"});
  for (const text of ["192.168.1.20:47812","http://192.168.1.20:47812","http://192.168.1.20:47812/remote/login#code=1234",
    "http://192.168.1.20:47812/other#code=12345678","http://192.168.1.20:47812/remote/login?x=1#code=12345678","https://192.168.1.20:47812/#code=12345678"])
    assert.equal(ui.pairingLink(text),null,text);
});
