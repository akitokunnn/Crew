"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const {test}=require("node:test");
function harness(report){
  const main={innerHTML:""},sandbox={window:{},document:{addEventListener(){},getElementById:()=>main},location:{search:"?project=fixture",href:"http://fixture/",origin:"http://fixture"},URL,URLSearchParams};
  vm.createContext(sandbox);
  const source=fs.readFileSync(path.join(__dirname,"../crew/web/app.js"),"utf8").replace(/\r\n/g,"\n");
  vm.runInContext(source.replace("  setup();\n})();",`window.ui={render(report){state={project:{name:"fixture"}};usage=report;quotas=()=>[];replace=(node,html)=>{node.innerHTML=html;};renderUsage();},collapse(id){usageCollapsed.add(id);renderUsage();},tab(value){usageTab=value;renderUsage();}};\n})();`),sandbox);
  sandbox.window.ui.render(report);return {ui:sandbox.window.ui,main};
}
const report=()=>({tree:[{leader_session_id:"leader",name:"协调",tokens:14400,cost:0.051,share:100,codex_runs:3,codex_tokens:8500,
  self:{tokens:3600,cost:0.01275,codex_runs:1,codex_tokens:500},members:[
    {session_id:"worker",name:"Claude组员",tokens:10800,cost:0.03825,share:75,codex_runs:0,codex_tokens:0},
    {session_id:"codex-worker",name:"Codex组员 <保留>",tokens:0,cost:0,share:0,codex_runs:2,codex_tokens:8000}]}],
  daily:[{date:"2026-10-27",display_name:"Claude组员",total_tokens:10800,cost_usd:0.03825}],models:[]});
const week=(since,resets,used,cost,extra={})=>({since,resets_at:resets,current:false,used_percent:used,captured_at:resets,machine_cost_usd:null,crew_share:null,
  crew:{cost_usd:cost,points:cost===null?null:cost/20,usd_per_point:20,usd_per_point_range:[16,27]},...extra});
test("weekly quota tab lists recorded and estimated weeks without guessing missing values",()=>{
  const h=harness({...report(),claude_weeks:[
    week("2026-11-19T02:00:00.000Z","2026-11-26T02:00:00.000Z",34,412.4,{current:true,captured_at:"2026-11-19T05:26:00.000Z",machine_cost_usd:600,crew_share:0.6873}),
    week("2026-11-12T02:00:00.000Z","2026-11-19T02:00:00.000Z",96,1240.6,{captured_at:"2026-11-16T02:00:00.000Z"}),
    week("2026-11-05T02:00:00.000Z","2026-11-12T02:00:00.000Z",null,8,{captured_at:null}),
    week("2026-10-29T02:00:00.000Z","2026-11-05T02:00:00.000Z",null,null,{captured_at:null})]});
  h.ui.tab("weeks");const html=h.main.innerHTML;
  for(const value of ["每周额度","已留档 4 周","本周","34%","21%","$412","占本机 69%","96%","截至","$1,241","62%","未留档","<1%","$8","未定价","每 1% ≈ $20"])assert(html.includes(value),value);
  assert.equal((html.match(/<tr>/g)||[]).length,5);assert.equal((html.match(/data-label="账户已用"/g)||[]).length,4);
  assert(!html.includes("NaN"));assert(!html.includes("undefined"));
  h.ui.render({...report(),claude_weeks:[]});h.ui.tab("weeks");assert(h.main.innerHTML.includes("还没采集到 Claude 每周额度"));
});
test("usage shows member Claude and native Codex totals, including leader self and collapsed team totals",()=>{
  const h=harness(report()),html=h.main.innerHTML;
  assert(html.includes("Claude组员"));assert(html.includes("Codex组员 &lt;保留&gt;"));assert(!html.includes("Codex组员 <保留>"));
  for(const value of ["Codex 8.5K · 3 次","Codex 500 · 1 次","Codex 8K · 2 次","75%","Claude Token","Claude 折合","Claude 占比","Claude 14.4K token · Codex 8.5K token"])assert(html.includes(value),value);
  h.ui.collapse("leader");assert(!h.main.innerHTML.includes("Claude组员"));assert(!h.main.innerHTML.includes("leader 本身"));
  assert(h.main.innerHTML.includes("Codex 8.5K · 3 次"));
  h.ui.tab("daily");assert(h.main.innerHTML.includes("Claude组员"));assert(h.main.innerHTML.includes("2026-10-27"));
});
