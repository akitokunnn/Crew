"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const {test}=require("node:test");
const listeners=new Map();
const document={visibilityState:"visible",activeElement:null,addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:(name,fn)=>{if(listeners.get(name)===fn)listeners.delete(name);}};
const escapeHTML=text=>String(text).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
const sandbox={document,setTimeout,clearTimeout,matchMedia:()=>({matches:false,addEventListener(){},removeEventListener(){}}),
  CrewChat:{renderMarkdown:text=>`<p>${escapeHTML(text)}</p>`},CrewBrowser:{enableChatLists(){},chatSendKey:()=>false}};
sandbox.window=sandbox;vm.createContext(sandbox);
const folder=path.join(__dirname,"../../frontend");
vm.runInContext(fs.readFileSync(path.join(folder,"channel.js"),"utf8"),sandbox);
vm.runInContext(fs.readFileSync(path.join(folder,"war-room.js"),"utf8").replace("window.CrewWarRoom = {mount, update, changed, unmount};","window.CrewWarRoom = {mount, update, changed, unmount, since, contractVersion, timeline, timelineHTML, crewHTML, headHTML, asideHTML, panelbarHTML, pinHTML};"),sandbox);
const W=sandbox.CrewWarRoom;
const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};

const ago=minutes=>new Date(Date.now()-minutes*60000-5000).toISOString();
const ev=(id,minutes,kind,detail,actor="pay",actor_name="支付")=>({id,at:ago(minutes),actor,actor_name,kind,detail});
const mention=(session_id,name,delivery,extra={})=>({session_id,name,delivery,queued_at:ago(3),delivered_at:null,via:null,waiting:null,reply_id:null,replied:false,...extra});
const message=(id,minutes,speaker,name,body,mentions=[])=>({id,project:"demo",speaker_session_id:speaker,speaker_name:name,body,created_at:ago(minutes),reply_to:null,reply:null,detail:false,auto:false,room:null,mentions,notified:[]});
function room(extra={}){
  const t=(id,minutes,item_id,title,status,was,note=null)=>ev(id,minutes,"item",{id:item_id,title,status,was,note,owner:"支付"});
  return {id:3,project:"demo",title:"签名 v2",goal:"三端切到 v2",lead_session_id:"pay",lead_name:"支付",status:"open",summary:null,created_at:ago(90),closed_at:null,
    origin_message_id:212,opened_message_id:300,closed_message_id:null,
    members:[{session_id:"pay",crew_name:"支付",joined_at:ago(90)},{session_id:"voice",crew_name:"语音",joined_at:ago(90)},{session_id:"web",crew_name:"前端",joined_at:ago(90)}],
    items:{todo:1,doing:1,blocked:1,done:0},open_decisions:1,unread:1,latest_id:305,
    origin:{id:212,speaker_name:"支付",excerpt:"谁来接？",replies:5},
    contract:{text:"POST /login\nX-Sign: HMAC",updated_at:ago(12),updated_by:"支付"},
    board:[{id:2,title:"SDK 适配",owner_session_id:"voice",owner_name:"语音",status:"blocked",note:"等算法定稿",updated_at:ago(5)},
      {id:1,title:"网关验签",owner_session_id:"pay",owner_name:"支付",status:"doing",note:"双签名",updated_at:ago(70)},
      {id:4,title:"回归",owner_session_id:"web",owner_name:"前端",status:"todo",note:null,updated_at:ago(78)}],
    decisions:[{id:7,question:"作战室《签名 v2》：nonce 谁生成？",options:["端上生成","网关下发"],recommendation:"端上生成",status:"open",answer:null,created_at:ago(10),answered_at:null}],
    events:[ev(1,90,"opened",{members:["支付","语音","前端"]}),t(2,80,1,"网关验签","todo",null),t(3,79,2,"SDK 适配","todo",null),t(4,78,4,"回归","todo",null),
      t(5,70,1,"网关验签","doing","todo"),t(6,45,2,"SDK 适配","doing","todo"),ev(7,40,"contract",{length:20}),ev(8,30,"contract",{length:24}),
      t(9,19,2,"SDK 适配","blocked","doing","等算法定稿"),ev(10,12,"contract",{length:26}),ev(11,10,"decision",{id:7,question:"nonce 谁生成？"}),t(12,5,2,"SDK 适配","blocked","blocked","等算法定稿")],
    messages:[message(301,75,"pay","支付","目标定了 @所有成员",[mention("voice","语音","queued",{queued_at:ago(75),waiting:"external"})]),message(305,3,"voice","语音","我这边 <b>卡住</b> 了")],
    has_more:false,read_id:301,budget:null,...extra};
}
const state=()=>({project:{name:"demo"},channel:{latest_id:305},goals:[{id:11,title:"网关验签 v2 上线"}],
  leaders:[{session_id:"pay",name:"支付",where:"bg",busy:true,runtime:"claude",active_goal_id:11,context:null},{session_id:"voice",name:"语音",where:"vscode",busy:false,runtime:"codex",context:null}]});
const avatar=name=>`<span class="av">${escapeHTML(name.slice(0,1))}</span>`;
const ctxOf=(r=room())=>({room:r,state:state(),avatar,ring:leader=>`<ring:${leader.name}>`,folds:new Set(),readFrom:301,doneOpen:false,contractOpen:false,selected:new Set(),all:false,replyTo:null,sending:false});

test("timeline puts board events between posts by time, folds three or more minor changes and never folds blocks, contracts or cards",()=>{
  const ctx=ctxOf(),rows=W.timeline(ctx.room);
  assert.deepEqual([...rows].map(row=>row.fold?`fold:${row.fold.map(e=>e.id).join(",")}`:row.message?`msg:${row.message.id}`:`ev:${row.event.id}`),
    ["ev:1","fold:2,3,4","msg:301","ev:5","ev:6","ev:7","ev:8","ev:9","ev:10","ev:11","ev:12","msg:305"]);
  const html=W.timelineHTML(ctx),at=text=>{const index=html.indexOf(text);assert(index>=0,text);return index;};
  assert(html.includes("3 条状态变化")&&html.includes('data-wr-fold="2" aria-expanded="false"')&&!html.includes("wr-fold-list"));
  assert(at('data-wr-fold="2"')<at('data-wr-msg="301"'));
  assert(at('data-wr-msg="301"')<at('data-s="contract"'));
  assert(at('data-s="contract"')<at('data-s="blocked" role="note"'));
  assert(at('data-s="blocked" role="note"')<at('data-wr-decision="7"'));
  assert(at('data-wr-decision="7"')<at('data-wr-msg="305"'));
  assert(html.includes('<span class="wr-node-why">等算法定稿</span>'));
  assert(at('新发言 · 1 条未读')<at('data-wr-msg="305"'));
  assert(html.includes("&lt;b&gt;卡住&lt;/b&gt;")&&!html.includes("<b>卡住</b>"));
  ctx.folds.add(2);
  const open=W.timelineHTML(ctx);
  assert(open.includes('aria-expanded="true"')&&open.includes("wr-fold-list")&&open.includes("<span>新增</span><q>回归</q>"));
});
test("blocked minutes count from the last status change, not later note edits, and the contract version counts contract events",()=>{
  const ctx=ctxOf();
  assert.equal(W.since(ctx.room,2),ctx.room.events[8].at);
  assert.equal(W.since(ctx.room,1),ctx.room.events[4].at);
  assert.equal(W.contractVersion(ctx.room),3);
  const aside=W.asideHTML(ctx);
  assert(aside.includes("阻塞：等算法定稿 · 已 19 分钟"));
  assert(aside.includes("第 3 版"));
  assert(aside.includes("nonce 谁生成？")&&!aside.includes("作战室《签名 v2》："));
  assert(aside.includes('<p class="wr-goal">三端切到 v2</p>'));
});
test("member cards show live state, the active task, context rings, and an external window cannot be paused",()=>{
  const ctx=ctxOf(),html=W.crewHTML(ctx);
  const card=id=>html.slice(html.indexOf(`data-wr-member="${id}"`),html.indexOf("</li>",html.indexOf(`data-wr-member="${id}"`)));
  assert(card("pay").includes('data-p="working"')&&card("pay").includes("网关验签 v2 上线")&&card("pay").includes("<ring:支付>")&&card("pay").includes("牵头"));
  assert(!card("pay").includes("aria-disabled"));
  assert(card("voice").includes("外部窗口")&&card("voice").includes("SDK 适配（阻塞）")&&card("voice").includes('data-rt="Codex"'));
  assert(card("voice").includes('data-wr-pause="voice" aria-disabled="true" title="在外部窗口运行，Crew 停不了它"'));
  assert(card("web").includes("离线")&&!card("web").includes("<ring:"));
  assert(html.includes('data-wr="invite"'));
});

function mounted(r=room()){
  const nodes={},calls=[],toasts=[],changed=[],chats=[];
  const element=()=>{let html="";const classes=new Set(),attributes=new Map();return {hidden:false,disabled:false,value:"",style:{},dataset:{},scrollHeight:100,clientHeight:400,scrollTop:0,
    classList:{toggle:(name,on)=>on?classes.add(name):classes.delete(name),contains:name=>classes.has(name),add:name=>classes.add(name),remove:name=>classes.delete(name)},
    setAttribute:(name,value)=>attributes.set(name,value),getAttribute:name=>attributes.get(name)??null,removeAttribute:name=>attributes.delete(name),focus(){},querySelector:()=>null,
    get innerHTML(){return html;},set innerHTML(value){html=value;}};};
  const host={innerHTML:"",querySelector:selector=>nodes[selector]||(nodes[selector]=element())};
  const api=async(route,data)=>{calls.push({route,data});
    if(route.startsWith("/api/op/war_room.get?"))return JSON.parse(JSON.stringify(r));
    if(route==="/api/op/war_room.mark_read")return {read_id:data.id,unread:0};
    return {};};
  const s=state();
  W.mount({host,id:3,state:s,api,avatar,ring:leader=>`<ring:${leader.name}>`,toast:text=>toasts.push(text),onChanged:()=>changed.push(1),openChat:(id,focus)=>chats.push([id,focus]),openChannel(){}});
  const click=(selector,dataset)=>host.onclick({target:{closest:wanted=>wanted===selector?{dataset,disabled:false}:null}});
  return {nodes,calls,toasts,changed,chats,host,click,state:s};
}
test("answering a decision card posts the option text, or the free answer, to /api/answer and reloads the room",async()=>{
  const f=mounted();await flush();
  assert(f.nodes[".wr-inner"].innerHTML.includes('data-wr-answer="7" data-wr-option="0"'));
  f.click("[data-wr-answer]",{wrAnswer:"7",wrOption:"0"});await flush();
  const answers=()=>f.calls.filter(call=>call.route==="/api/op/decision.answer").map(call=>JSON.parse(JSON.stringify(call.data)));
  assert.deepEqual(answers(),[{id:7,text:"端上生成"}]);
  assert.equal(f.changed.length,1);
  assert.equal(f.calls.filter(call=>call.route.startsWith("/api/op/war_room.get?id=3")).length,2);
  assert(f.toasts.some(text=>text.includes("答复回到牵头 支付")));
  f.host.onsubmit({preventDefault(){},target:{dataset:{wrFree:"7"},querySelector:()=>({value:"再等等"})}});await flush();
  assert.deepEqual(answers()[1],{id:7,text:"再等等"});
  W.unmount();assert.equal(listeners.size,0);
});
test("pause stops the leader's turn through /api/session/stop; an external window only explains why it cannot",async()=>{
  const f=mounted();await flush();
  f.click("[data-wr-pause]",{wrPause:"voice"});await flush();
  assert.equal(f.calls.filter(call=>call.route==="/api/op/session.stop").length,0);
  assert(f.toasts.includes("语音：在外部窗口运行，Crew 停不了它"));
  f.click("[data-wr-pause]",{wrPause:"pay"});await flush();
  assert.deepEqual(JSON.parse(JSON.stringify(f.calls.find(call=>call.route==="/api/op/session.stop").data)),{session_id:"pay"});
  f.click("[data-wr-chat]",{wrChat:"voice"});
  assert.deepEqual(JSON.parse(JSON.stringify(f.chats)),[["voice",{at:null,prefix:null}]]);
  W.unmount();
});
test("a closed room is read-only: no composer, pause, reply, invite or close, and queued badges stay queued",async()=>{
  const r=room({status:"closed",summary:"三端已切到 v2",closed_at:ago(1),closed_message_id:320,title:'<script>x</script>'});
  r.events.push(ev(13,1,"closed",{summary:"三端已切到 v2"}));
  const f=mounted(r);await flush();
  assert.equal(f.nodes[".cx-composer"].hidden,true);
  assert.equal(f.nodes[".cx-targets"].hidden,true);
  assert.equal(f.nodes[".wr-closedbar"].hidden,false);
  const head=f.nodes[".wr-head"].innerHTML,crew=f.nodes[".wr-crew"].innerHTML,feed=f.nodes[".wr-inner"].innerHTML,aside=f.nodes[".wr-aside"].innerHTML;
  assert(head.includes("只读")&&head.includes('data-wr-channel="320"')&&!head.includes('data-wr="close"')&&!head.includes('data-wr="invite"'));
  assert(head.includes("&lt;script&gt;x&lt;/script&gt;")&&!head.includes("<script>"));
  assert(!crew.includes("data-wr-pause")&&!crew.includes('data-wr="invite"')&&crew.includes("已回原岗")&&!crew.includes("<ring:"));
  assert(!feed.includes("data-wr-reply")&&!feed.includes("新发言"));
  assert(feed.includes('data-state="queued"')&&!feed.includes("已送达"));
  assert(feed.includes('data-wr-option="0" disabled')&&!feed.includes("data-wr-free"));
  assert(!aside.includes('data-wr="add-item"')&&!aside.includes('data-wr="edit-contract"')&&!aside.includes('tabindex="0"'));
  assert(f.nodes[".wr-pin"].innerHTML.includes("收尾结论")&&f.nodes[".wr-pin"].innerHTML.includes("三端已切到 v2"));
  f.click("[data-wr-row]",{wrRow:"2"});
  assert.equal(f.nodes[".wr-layerbox"].innerHTML,"");
  W.unmount();
});
test("live changes: this room's war_room change re-reads it, another room's does not, a badge change replaces the post",async()=>{
  const f=mounted();await flush();
  const reads=()=>f.calls.filter(call=>call.route.startsWith("/api/op/war_room.get?id=3")).length;
  assert.equal(reads(),1);
  W.changed([{type:"war_room.updated",entity:"9",data:{}}]);await flush();
  assert.equal(reads(),1);
  W.changed([{type:"war_room.updated",entity:"3",data:{}}]);await flush();
  assert.equal(reads(),2);
  const shown=room().messages[0];
  W.changed([{type:"channel_message.updated",entity:String(shown.id),data:{...shown,body:"徽章已更新的那条"}}]);await flush();
  assert.equal(reads(),2);
  assert(f.nodes[".wr-inner"].innerHTML.includes("徽章已更新的那条"));
  W.changed(null);await flush();
  assert.equal(reads(),3);
  W.unmount();
});

const budgetRoom=(budget,extra={})=>room({budget,...extra});
test("budget bar: none without a cap, plain under 80%, amber with words from 80%, red with words when paused",()=>{
  const none=ctxOf();
  assert(!W.asideHTML(none).includes("wr-budget")&&!W.panelbarHTML(none).includes("wr-budget-tag")&&W.pinHTML(none)==="");
  const ok=W.asideHTML(ctxOf(budgetRoom({tokens:200000,spent:32000,paused_at:null})));
  assert(ok.includes('class="wr-budget" data-s="ok"')&&ok.includes("已用 <b>3.2 万</b> / 20 万 tokens")&&ok.includes("已用 16%")&&ok.includes('style="width:16%"'));
  assert(ok.includes('data-wr="budget"')&&!W.panelbarHTML(ctxOf(budgetRoom({tokens:200000,spent:32000,paused_at:null}))).includes("wr-budget-tag"));
  const nearCtx=ctxOf(budgetRoom({tokens:200000,spent:172000,paused_at:null})),near=W.asideHTML(nearCtx);
  assert(near.includes('data-s="near"')&&near.includes("接近上限 · 86%"));
  assert(W.panelbarHTML(nearCtx).includes('<span class="wr-budget-tag" data-s="near">')&&W.panelbarHTML(nearCtx).includes("预算 86%"));
  assert.equal(W.pinHTML(nearCtx),"");
  const pausedCtx=ctxOf(budgetRoom({tokens:200000,spent:203500,paused_at:ago(4)})),paused=W.asideHTML(pausedCtx);
  assert(paused.includes('class="wr-budget" data-s="paused"')&&paused.includes("已暂停")&&paused.includes('style="width:100%"')&&paused.includes("20.4 万"));
  assert(W.panelbarHTML(pausedCtx).includes("预算已暂停"));
});
test("a paused room pins a banner above the timeline with the raise button; a closed room keeps the banner without it",()=>{
  const pin=W.pinHTML(ctxOf(budgetRoom({tokens:200000,spent:203500,paused_at:ago(4)})));
  assert(pin.includes("预算到了，作战室已暂停，成员已停下")&&pin.includes("已用 20.4 万 / 上限 20 万 tokens")&&pin.includes('data-wr="budget"')&&pin.includes("追加预算并继续"));
  const r=budgetRoom({tokens:200000,spent:203500,paused_at:ago(4)},{status:"closed",summary:"先停在这里",closed_at:ago(1),closed_message_id:320});
  r.events.push(ev(13,1,"closed",{summary:"先停在这里"}));
  const closedPin=W.pinHTML(ctxOf(r));
  assert(closedPin.includes("预算到了，作战室已暂停")&&!closedPin.includes('data-wr="budget"')&&closedPin.includes("收尾结论"));
  assert(closedPin.indexOf("wr-paused")<closedPin.indexOf("wr-summary"));
  assert(!W.asideHTML(ctxOf(r)).includes('data-wr="budget"'));
});
test("budget events are handoff nodes in the timeline: the pause with spent and cap, the raise by the user; never folded",()=>{
  const r=room();
  r.events.push(ev(13,2,"budget",{paused:true,spent:203500,budget:200000}),ev(14,1,"budget",{paused:false,spent:203500,budget:400000},"user","用户"));
  const html=W.timelineHTML(ctxOf(r));
  assert(html.includes('data-s="paused" role="note" aria-label="预算暂停"')&&html.includes("预算到了，已暂停（已用 20.4 万 / 上限 20 万 tokens）"));
  assert(html.includes('data-s="resumed" role="note" aria-label="追加预算"')&&html.includes("用户追加预算到 40 万 tokens，继续"));
  const node=html.slice(html.indexOf('aria-label="预算暂停"'),html.indexOf("</div>",html.indexOf('aria-label="预算暂停"')));
  assert(!node.includes("<strong>支付</strong>"),"the pause is the room's, not the lead's");
  assert(W.timeline(r).every(row=>!row.fold||row.fold.every(e=>e.kind!=="budget")));
});
test("raising the budget checks the new cap against the spend and the minimum, then posts war_room.budget and reloads",async()=>{
  const f=mounted(budgetRoom({tokens:200000,spent:203500,paused_at:ago(4)}));await flush();
  assert(f.nodes[".wr-pin"].hidden===false&&f.nodes[".wr-pin"].innerHTML.includes("追加预算并继续"));
  f.nodes[".wr-layerbox"].querySelector=()=>({focus(){}});
  f.click("[data-wr]",{wr:"budget"});
  const dialog=f.nodes[".wr-layerbox"].innerHTML;
  assert(dialog.includes('data-wr-form="budget"')&&dialog.includes('value="400000"')&&dialog.includes('min="203501"')&&dialog.includes("= 40 万 tokens"));
  const error={textContent:""},form=value=>({dataset:{wrForm:"budget"},querySelector:()=>error,elements:{budget:{value}}});
  for(const bad of ["150000","9000","abc"]){
    f.host.onsubmit({preventDefault(){},target:form(bad)});await flush();
    assert.match(error.textContent,/大于已用 20.4 万，且不少于 1 万 tokens/);
  }
  assert.equal(f.calls.filter(call=>call.route==="/api/op/war_room.budget").length,0);
  f.host.onsubmit({preventDefault(){},target:form("400000")});await flush();
  assert.deepEqual(JSON.parse(JSON.stringify(f.calls.find(call=>call.route==="/api/op/war_room.budget").data)),{project:"demo",room:3,budget_tokens:400000});
  assert(f.toasts.includes("预算上限已改为 40 万 tokens，作战室继续，成员接着推进"));
  assert.equal(f.calls.filter(call=>call.route.startsWith("/api/op/war_room.get?id=3")).length,2);
  W.unmount();
});
