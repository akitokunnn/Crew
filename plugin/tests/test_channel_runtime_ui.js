"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const {test}=require("node:test");
const sandbox={window:{},CrewChat:{renderMarkdown:text=>`<p>${text}</p>`},setTimeout,clearTimeout};vm.createContext(sandbox);
const source=fs.readFileSync(path.join(__dirname,"../../frontend/channel.js"),"utf8").replace('window.CrewChannel = {mount, update, changed, updateRooms, focus, unmount};','window.CrewChannel = {mount, update, changed, updateRooms, focus, unmount, renderDigest, deliveryHTML, pendingItems, explain, badgeOf, go};');vm.runInContext(source,sandbox);
const C=sandbox.window.CrewChannel;
function render(extra){const pin={hidden:true,innerHTML:""};C.renderDigest({pin,digest:{created_at:"2026-10-25T00:00:00Z",total_message_count:5,body:"已有要点",...extra}});return pin.innerHTML;}
test("digest metadata shows the actual Codex model and unknown fees without implying a zero charge",()=>{
  const html=render({runtime:"codex",model:"gpt-actual",cost_usd:null});assert(html.includes("Codex · gpt-actual · 费用未知"));assert(!html.includes("Haiku"));assert(!html.includes("$0.0000"));
});
test("known digest fees retain their value and runtime/model labels are escaped",()=>{
  const html=render({runtime:"claude",model:'actual-<model>',cost_usd:.0123});assert(html.includes("Claude Code · actual-&lt;model&gt; · $0.0123"));assert(render({runtime:"codex",model:"native",cost_usd:0}).includes("$0.0000"));
});
test("missing metadata is shown as unknown rather than fabricated defaults",()=>{
  assert(render({model:null}).includes("执行工具未知 · 模型未知 · 费用未知"));
});

const ago=minutes=>new Date(Date.now()-minutes*60000-5000).toISOString();
const target=(session_id,name,delivery,extra={})=>({session_id,name,delivery,queued_at:ago(3),delivered_at:null,via:null,waiting:null,reply_id:null,replied:delivery==="replied",...extra});
const post=(id,mentions,notified=[],speaker="user")=>({id,project:"demo",speaker_session_id:speaker,speaker_name:speaker==="user"?"用户":"架构",body:"发言 "+id,created_at:ago(20),reply_to:null,detail:false,auto:false,reply:null,mentions,notified});
test("delivery badges carry state, shape and a full-sentence label for each contract state",()=>{
  const message=post(7,[target("s1","语音","queued",{waiting:"busy"}),target("s2","网关","delivered",{delivered_at:ago(5),via:"input"}),target("s3","架构","replied",{delivered_at:ago(9),reply_id:9}),target("s4","自己","none")]);
  const html=C.deliveryHTML(message);
  assert(html.includes('<span class="cx-dl-label">点名</span>'));
  assert(html.includes('data-state="queued"><button')&&!html.includes('data-state="queued" data-stale'));
  assert(html.includes('aria-label="点名 语音：已排队，对方还没收到。点开看说明"'));
  assert(html.includes('aria-haspopup="dialog"'));
  assert(html.includes('<span class="cx-dl-text">排队中</span>'));
  assert(html.includes('data-state="delivered"')&&html.includes('<span class="cx-dl-text">已送达</span>'));
  assert(html.includes('data-state="replied"')&&html.includes('<span class="cx-dl-text">已回复</span>'));
  assert(html.includes('data-badge="7:mentions:s1"')&&html.includes('data-go="7:mentions:s1"'));
  assert(html.includes('aria-label="打开 网关 的私聊并定位到这条点名"'));
  const none=html.slice(html.indexOf('data-state="none"'));
  assert(none.includes('<span class="cx-dl-name">@自己</span>')&&!none.includes("<button"));
});
test("a queue older than ten minutes turns amber and says how long, measured from queued_at",()=>{
  const html=C.deliveryHTML(post(8,[target("s1","语音","queued",{queued_at:ago(14)})]));
  assert(html.includes('data-state="queued" data-stale'));
  assert(html.includes('<span class="cx-dl-text">排队 14 分钟</span>'));
  assert(html.includes('aria-label="点名 语音：已排队 14 分钟，对方还没收到。点开看说明"'));
  const fresh=post(9,[target("s1","语音","queued",{queued_at:ago(2)})]);fresh.created_at=ago(60);
  assert(!C.deliveryHTML(fresh).includes("data-stale"));
});
test("reply notifications are labelled separately, without @, and names are escaped",()=>{
  const html=C.deliveryHTML(post(10,[],[target("s9",'<架构>"',"delivered")]));
  assert(html.includes('>回复通知</span>'));
  assert(!html.includes(">点名</span>"));
  assert(html.includes('<span class="cx-dl-name">&lt;架构&gt;&quot;</span>'));
  assert(html.includes('aria-label="回复通知 &lt;架构&gt;&quot;：已送达，还没回复。点开看说明"'));
  assert(!html.includes("<架构>"));
  assert.equal(C.deliveryHTML(post(11,[],[])),"");
});
test("waiting list keeps queued and delivered items only, longest stale queue first",()=>{
  const context={originalMessages:[],messages:[post(1,[target("a","前端","queued",{queued_at:ago(3)}),target("b","网关","delivered",{delivered_at:ago(30)})]),post(2,[target("c","语音","queued",{queued_at:ago(14)}),target("d","架构","replied")]),post(3,[target("a","前端","queued",{queued_at:ago(1)})])]};
  const list=C.pendingItems(context);
  assert.deepEqual([...list.map(x=>x.key)],["2:mentions:c","1:mentions:a","3:mentions:a","1:mentions:b"]);
});
test("explanations say why a mention is still queued and whether it was seen",()=>{
  const context={originalMessages:[],messages:[post(5,[target("ext","前端","queued",{waiting:"external",queued_at:ago(12)}),target("stop","语音","queued",{waiting:"stopped"}),target("ok","网关","replied",{reply_id:6})])],state:{leaders:[{session_id:"ext",name:"前端",where:"vscode",busy:false}]}};
  const external=C.explain(context,C.badgeOf(context,"5:mentions:ext"));
  assert(external.includes("在外部窗口运行，下次在那边提示时才会带上")&&external.includes("<b>它还没看到</b>")&&external.includes("<dd>外部窗口运行</dd>")&&external.includes("12 分钟（超过 10 分钟）"));
  assert(C.explain(context,C.badgeOf(context,"5:mentions:stop")).includes("你已停止 语音"));
  const replied=C.explain(context,C.badgeOf(context,"5:mentions:ok"));
  assert(replied.includes('data-channel-jump="6">跳到回复 #6')&&replied.includes("打开 网关 的私聊"));
});
test("badge chat buttons land on the delivery moment, or the bottom while still queued",()=>{
  const delivered=ago(4),opened=[];
  const context={originalMessages:[],messages:[post(5,[target("q","语音","queued"),target("d","网关","delivered",{delivered_at:delivered})])],popFor:null,asideOpen:false,openChat:(id,focus)=>opened.push([id,focus])};
  C.go(context,"5:mentions:q");C.go(context,"5:mentions:d");
  assert.deepEqual(JSON.parse(JSON.stringify(opened)),[["q",{at:null,prefix:null}],["d",{at:delivered,prefix:null}]]);
});
