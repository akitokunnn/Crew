"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const {test}=require("node:test");
const sandbox={window:{},CrewChat:{renderMarkdown:text=>`<p>${text}</p>`}};vm.createContext(sandbox);
const source=fs.readFileSync(path.join(__dirname,"../crew/web/channel.js"),"utf8").replace('window.CrewChannel = {mount, update, unmount};','window.CrewChannel = {mount, update, unmount, renderDigest};');vm.runInContext(source,sandbox);
function render(extra){const pin={hidden:true,innerHTML:""};sandbox.window.CrewChannel.renderDigest({pin,digest:{created_at:"2026-10-25T00:00:00Z",total_message_count:5,body:"已有要点",...extra}});return pin.innerHTML;}
test("digest metadata shows the actual Codex model and unknown fees without implying a zero charge",()=>{
  const html=render({runtime:"codex",model:"gpt-actual",cost_usd:null});assert(html.includes("Codex · gpt-actual · 费用未知"));assert(!html.includes("Haiku"));assert(!html.includes("$0.0000"));
});
test("known digest fees retain their value and runtime/model labels are escaped",()=>{
  const html=render({runtime:"claude",model:'actual-<model>',cost_usd:.0123});assert(html.includes("Claude Code · actual-&lt;model&gt; · $0.0123"));assert(render({runtime:"codex",model:"native",cost_usd:0}).includes("$0.0000"));
});
test("missing metadata is shown as unknown rather than fabricated defaults",()=>{
  assert(render({model:null}).includes("执行工具未知 · 模型未知 · 费用未知"));
});
