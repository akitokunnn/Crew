"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const {test}=require("node:test");
const tick=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
function node(){const classes=new Set(),children=[],attributes=new Map();return {matches:()=>false,focus(){},setSelectionRange(){},value:"",textContent:"",innerHTML:"",hidden:false,disabled:false,style:{setProperty(){}},dataset:{},scrollTop:0,scrollHeight:0,clientHeight:200,firstChild:null,children,contains:()=>false,querySelectorAll:()=>[],getBoundingClientRect:()=>({top:0}),setAttribute(name,value){attributes.set(name,String(value));},getAttribute:name=>attributes.get(name)??null,removeAttribute:name=>attributes.delete(name),
  insertBefore(child,before){const old=children.indexOf(child);if(old>=0)children.splice(old,1);const index=before?children.indexOf(before):-1;children.splice(index<0?children.length:index,0,child);child.parentNode=this;this.firstChild=children[0]||null;for(let i=0;i<children.length;i++)children[i].nextSibling=children[i+1]||null;},
  remove(){const parent=this.parentNode;if(!parent)return;const index=parent.children.indexOf(this);if(index>=0)parent.children.splice(index,1);parent.firstChild=parent.children[0]||null;for(let i=0;i<parent.children.length;i++)parent.children[i].nextSibling=parent.children[i+1]||null;this.parentNode=null;},
  classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),toggle(x,on){if(on)classes.add(x);else classes.delete(x);},contains:x=>classes.has(x)}};}
function host(){const nodes=new Map();return {...node(),querySelector(selector){if(!nodes.has(selector))nodes.set(selector,node());return nodes.get(selector);}};}
function harness(){
  const journals=new Map(),journalKey=params=>JSON.stringify([params.get("project")||"",params.get("session_id")]);
  const timers=new Map(),saved=new Map(),streams=[],connections=[],frames=new Map(),handlers=new Map();let next=1;
  const motion={matches:true};
  const sandbox={matchMedia:()=>motion,atob,btoa,URLSearchParams,AbortController,TextDecoder,
    EventSource:class{static CLOSED=2;constructor(url){connections.push({url});}close(){}},
    addEventListener(type,fn){if(!handlers.has(type))handlers.set(type,new Set());handlers.get(type).add(fn);},removeEventListener(type,fn){handlers.get(type)?.delete(fn);},
    localStorage:{getItem:key=>saved.get(key)??null,setItem:(key,value)=>saved.set(key,value)},
    document:{visibilityState:"visible",activeElement:null,querySelectorAll:()=>[],addEventListener(type,fn){if(!handlers.has(type))handlers.set(type,new Set());handlers.get(type).add(fn);},removeEventListener(type,fn){handlers.get(type)?.delete(fn);},createElement:node},
    setTimeout:(fn,ms)=>{const id=next++;timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id),requestAnimationFrame:fn=>{const id=next++;frames.set(id,fn);return id;},cancelAnimationFrame:id=>frames.delete(id),
    fetch:async(url,{signal})=>{streams.push(String(url));let pending;const record=journals.get(journalKey(new URLSearchParams(String(url).split("?")[1])))||{executionId:"",latestSeq:0},headers=new Map([["X-Crew-Execution-Id",record.executionId],["X-Crew-Latest-Seq",String(record.latestSeq)]]);const queue=[],stream={url:String(url),signal,reads:0,emit(event){record.latestSeq=Math.max(record.latestSeq,event.seq);const chunk={value:new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`),done:false};if(pending){const {resolve}=pending;pending=null;resolve(chunk);}else queue.push(chunk);}};connections.push(stream);
      signal.addEventListener("abort",()=>{pending?.reject(Object.assign(Error("aborted"),{name:"AbortError"}));pending=null;},{once:true});
      return {ok:true,headers,body:{getReader:()=>({read:()=>{stream.reads++;return queue.length?Promise.resolve(queue.shift()):new Promise((resolve,reject)=>{pending={resolve,reject};});}})}};}};
  sandbox.window=sandbox;vm.createContext(sandbox);
  for(const file of ["vendor/markdown-it.min.js","browser-utils.js","chat.js","goal-work-ui.js"])vm.runInContext(fs.readFileSync(path.join(__dirname,"../crew/web",file),"utf8"),sandbox);
  function mount(id,api,extra={},options={}){const target=host(),session={session_id:id,name:id,busy:false,last_seq:0,activity:null,where:"bg",...extra};const client=async(url,data)=>{const value=await api(url,data);if(url.startsWith("/api/chat/history?"))journals.set(journalKey(new URLSearchParams(url.split("?")[1])),{executionId:value.execution_id??"",latestSeq:value.events.at(-1)?.seq||0});return value;};sandbox.CrewChat.mount({host:target,session,state:{project:{name:"fixture"},leaders:[session],members:[]},api:client,toast(){},...options});return target;}
  function update(id,extra={}){const session={session_id:id,name:id,busy:false,last_seq:1,activity:null,where:"bg",...extra};sandbox.CrewChat.update({project:{name:"fixture"},leaders:[session],members:[]});}
  function visibility(visible,desktop=false){const type=desktop?"crew-tab-visibility":"visibilitychange";if(desktop)sandbox.crewTabVisible=visible;else sandbox.document.visibilityState=visible?"visible":"hidden";for(const fn of handlers.get(type)||[])fn({type,detail:{visible}});}
  return {chat:sandbox.CrewChat,mount,update,visibility,timers,saved,streams,connections,handlers,sandbox,motion,setJournal(id,value,project="fixture"){journals.set(JSON.stringify([project,id]),value);},flushFrames(){for(const [id,fn] of frames){frames.delete(id);fn();}}};
}
const history=()=>({events:[],busy:false,read_seq:0,first_seq:0,has_more:false});

test("cached transcripts paint synchronously and catch up only through SSE after their cursor",async()=>{
  for(const runtime of ["claude","codex"]){
    const h=harness(),calls=[],read=async url=>{calls.push(url);return {...history(),events:[{seq:7,type:"message",id:"old",text:"缓存正文"}]};};
    h.mount("leader",read,{runtime});await tick();h.chat.unmount();
    const restored=h.mount("leader",read,{runtime});
    assert.match(rendered(restored).join(""),/缓存正文/,"cached content is visible before a network promise settles");
    await tick();assert.equal(calls.length,1);assert.match(calls[0],/tail=1500/);
    assert.match(h.connections[1].url,/after=7/);h.connections[1].emit({seq:8,type:"message",id:"new",text:"增量正文"});
    await tick();h.flushFrames();assert.match(rendered(restored).join(""),/增量正文/);h.chat.unmount();
    assert([...h.saved.keys()].every(key=>key.startsWith("crew.draft:")),"transcripts never enter localStorage");
  }
});

test("SSE execution changes and shortened journals discard cached records before reading any stream data",async()=>{
  for(const replacement of [{executionId:"replacement",latestSeq:20},{executionId:"original",latestSeq:2}]){
    const h=harness();let reads=0;
    const read=async()=>({...history(),execution_id:++reads===1?"original":replacement.executionId,events:reads===1?[{seq:10,type:"message",id:"stale",text:"过期正文"}]:[{seq:1,type:"message",id:"fresh",text:"新日志正文"}]});
    h.mount("leader",read);await tick();h.chat.unmount();
    h.setJournal("leader",replacement);const restored=h.mount("leader",read);
    assert.match(rendered(restored).join(""),/过期正文/);await tick();
    assert.equal(h.connections[1].signal.aborted,true);assert.equal(h.connections[1].reads,0);
    assert.equal(reads,2);assert.match(rendered(restored).join(""),/新日志正文/);assert.doesNotMatch(rendered(restored).join(""),/过期正文/);h.chat.unmount();
  }
});

test("the transcript LRU evicts the least recently visited of more than eight scopes",async()=>{
  const h=harness(),reads=new Map(),read=id=>async()=>{reads.set(id,(reads.get(id)||0)+1);return history();};
  for(let i=0;i<9;i++){h.mount(String(i),read(String(i)));await tick();}
  h.chat.unmount();h.mount("1",read("1"));await tick();assert.equal(reads.get("1"),1);
  h.mount("0",read("0"));await tick();assert.equal(reads.get("0"),2);h.chat.unmount();
});

test("optimistic input is visible before send starts and merges a same-ID receipt without duplication",async()=>{
  for(const runtime of ["claude","codex"])for(const bound of [false,true]){
    const h=harness();let target,finish,payload;
    h.saved.set("crew.draft:fixture:leader",JSON.stringify({text:"",images:[{media_type:"image/png",data:"AAAA"}]}));
    target=h.mount("leader",async(url,data)=>{if(url!=="/api/chat/send")return history();payload=data;assert.match(rendered(target).join(""),/m-user pending/);assert.match(rendered(target).join(""),/立即可见/);return new Promise(resolve=>{finish=resolve;});},{runtime});await tick();
    if(bound){h.chat.setTask({id:"goal",title:"任务甲",leader_session_id:"leader"});target.querySelector(".chat-task-binding").onclick();}
    target.querySelector("textarea").value="立即可见";target.querySelector("textarea").oninput();target.querySelector("form").onsubmit({preventDefault(){}});
    assert.match(rendered(target).join(""),/待确认/);assert.match(rendered(target).join(""),/data:image\/png;base64,AAAA/);
    if(bound){assert.equal(payload.goal_id,"goal");assert.match(rendered(target).join(""),/→ 任务甲/);}
    else h.connections[0].emit({seq:1,type:"message",id:payload.message_id,text:payload.text,source:"human",images:["receipt.png"]});
    finish({accepted:true});await tick();h.flushFrames();
    assert.equal(rendered(target).filter(html=>html.includes("立即可见")).length,1);assert.doesNotMatch(rendered(target).join(""),/m-user pending/);
    assert.equal(target.querySelector("textarea").value,"");
    if(bound){h.mount("other",async()=>history());await tick();target=h.mount("leader",async()=>history(),{runtime});await tick();assert.equal(rendered(target).filter(html=>html.includes("立即可见")).length,1);assert.match(rendered(target).join(""),/→ 任务甲/);}
    h.chat.unmount();
  }
});

test("rejected and uncertain sends retain their draft, true reason and one marked message without automatic retry",async()=>{
  for(const accepted of [false,"unknown"])for(const bound of [false,true]){
    const h=harness();let sends=0;
    const target=h.mount("leader",async url=>url==="/api/chat/send"?(sends++,{accepted,error:"明确的投递原因"}):history());await tick();
    if(bound){h.chat.setTask({id:"goal",title:"任务甲",leader_session_id:"leader"});target.querySelector(".chat-task-binding").onclick();}
    target.querySelector("textarea").value="保留的输入";target.querySelector("textarea").oninput();target.querySelector("form").onsubmit({preventDefault(){}});await tick();h.flushFrames();
    const html=rendered(target).join("");assert.match(html,accepted==="unknown"?/m-user pending/:/m-user failed/);assert.match(html,/明确的投递原因/);
    assert.equal(rendered(target).filter(html=>html.includes("保留的输入")).length,1);if(bound)assert.match(html,/→ 任务甲/);
    assert.equal(target.querySelector("textarea").value,"保留的输入");assert.equal(sends,1);h.chat.unmount();
  }
});

test("a tail window renders orphan tool results, restores their owner on prepend and keeps the reading anchor",async()=>{
  const h=harness(),calls=[],result={seq:3,type:"tool_result",tool_use_id:"tool",content:"窗口边界结果",is_error:false};
  const target=h.mount("leader",async url=>{calls.push(url);return {...history(),has_more:!url.includes("before="),first_seq:url.includes("before=")?1:3,events:url.includes("before=")?[{seq:1,type:"assistant",id:"owner",content:[{type:"tool_use",id:"tool",name:"Shell",input:{}}]},{seq:2,type:"message",id:"earlier",text:"更早正文"}]:[result,{seq:4,type:"message",id:"anchor",text:"正在阅读"}]};});await tick();
  assert.match(rendered(target).join(""),/窗口边界结果/);const scroll=target.querySelector(".chat-scroll"),inner=target.querySelector(".chat-inner");scroll.scrollHeight=1000;scroll.scrollTop=100;scroll.getBoundingClientRect=()=>({top:0,bottom:200});
  const anchor=inner.children.find(node=>node.innerHTML.includes("正在阅读"));anchor.getBoundingClientRect=()=>({top:(inner.children.some(node=>node.innerHTML.includes("更早正文"))?200:80)-(scroll.scrollTop-100),bottom:240});
  await inner.onclick({target:{closest:selector=>selector==="[data-chat-earlier]"?{}:null}});
  assert.match(calls[1],/tail=1500/);assert.match(calls[1],/before=3/);assert.equal(anchor.getBoundingClientRect().top,80);
  assert.equal(rendered(target).filter(html=>html.includes("窗口边界结果")).length,1);assert.doesNotMatch(rendered(target).join(""),/data-chat-earlier/);h.chat.unmount();
});

test("state polls update activity without regenerating transcript HTML or replacing its nodes",async()=>{
  const h=harness(),target=h.mount("leader",async()=>({...history(),events:[{seq:1,type:"message",id:"message",text:"稳定正文"}]}));await tick();
  const node=target.querySelector(".chat-inner").children.find(node=>node.innerHTML.includes("稳定正文"));let reads=0,value=node.dataset.html;
  Object.defineProperty(node.dataset,"html",{get(){reads++;return value;},set(next){value=next;}});
  for(let i=0;i<3;i++){h.update("leader",{busy:true,last_seq:1,activity:{phase:"thinking",output_tokens:i+1}});h.flushFrames();}
  assert.equal(reads,0);assert(target.querySelector(".chat-inner").children.includes(node));assert.match(rendered(target).join(""),/思考中/);h.chat.unmount();
});

test("an unread anchor evicted from the new tail window positions the reader at the window top",async()=>{
  const h=harness();
  const first=h.mount("leader",async()=>({...history(),events:[{seq:1,type:"text",id:"old-unread",text:"旧未读正文"}]}));await tick();
  first.querySelector(".chat-scroll").scrollTop=160;first.querySelector(".chat-scroll").onscroll();
  for(let i=0;i<8;i++){h.mount("other-"+i,async()=>history());await tick();}
  let release;
  const restored=h.mount("leader",()=>new Promise(resolve=>{release=resolve;}));
  restored.querySelector(".chat-scroll").scrollHeight=1000;
  release({...history(),has_more:true,first_seq:20,events:[{seq:20,type:"text",id:"new-unread",text:"新窗口正文"}]});await tick();
  assert.equal(restored.querySelector(".chat-scroll").scrollTop,0);assert.doesNotMatch(rendered(restored).join(""),/旧未读正文/);h.chat.unmount();
});

test("task binding follows the selected task, resets on switch and only scopes a highlighted send",async()=>{
  for(const runtime of ["claude","codex"]){
    const h=harness(),calls=[],toasts=[];
    const target=h.mount("leader",async(url,data)=>{calls.push({url,data});return url==="/api/chat/send"?{accepted:true}:history();},{runtime},{toast:text=>toasts.push(text)});
    await tick();const chip=target.querySelector(".chat-task-binding"),input=target.querySelector("textarea");
    h.chat.setTask({id:"a",title:"任务甲",leader_session_id:"leader"});assert(chip.innerHTML.includes("<span>任务甲</span>"));assert(chip.innerHTML.includes("<svg"));assert.equal(chip.getAttribute("aria-pressed"),"false");chip.onclick();assert.equal(chip.getAttribute("aria-pressed"),"true");
    h.chat.setTask({id:"b",title:"任务乙",leader_session_id:"leader"});assert(chip.innerHTML.includes("<span>任务乙</span>"));assert.equal(chip.getAttribute("aria-pressed"),"false");
    input.value="普通交流";input.oninput();target.querySelector("form").onsubmit({preventDefault(){}});await tick();assert(!Object.hasOwn(calls.find(call=>call.url==="/api/chat/send").data,"goal_id"));
    chip.onclick();assert.equal(input.placeholder,"补充给任务「任务乙」的要求");input.value="补充要求";input.oninput();target.querySelector("form").onsubmit({preventDefault(){}});await tick();h.flushFrames();
    const send=calls.filter(call=>call.url==="/api/chat/send").at(-1).data;assert.equal(send.session_id,"leader");assert.equal(send.goal_id,"b");assert(send.message_id);assert(toasts.includes("已补充到任务，AI 会按新要求继续"));assert(rendered(target).join("").includes("→ 任务乙"));
    assert(calls.filter(call=>call.url.includes("/api/chat/history")).every(call=>!call.url.includes("goal_id=")));assert(h.streams.every(url=>!url.includes("goal_id=")));
    h.chat.setTask(null);assert.equal(chip.hidden,true);h.chat.unmount();
  }
});

test("an idea from the wall rides on exactly one send as note_id, prefilling only an empty composer",async()=>{
  for(const runtime of ["claude","codex"]){
    const h=harness(),calls=[],cleared=[];
    const target=h.mount("assistant",async(url,data)=>{calls.push({url,data});return url==="/api/chat/send"?{accepted:true}:history();},{runtime},{onNoteCleared:()=>cleared.push("cleared")});
    await tick();const chip=target.querySelector(".chat-note-chip"),input=target.querySelector("textarea"),sends=()=>calls.filter(call=>call.url==="/api/chat/send").map(call=>call.data);
    h.chat.setNote({id:"note-1",title:"想法墙 <b>"},"帮我把这个想法想清楚");
    assert.equal(chip.hidden,false);assert(chip.innerHTML.includes("想法：想法墙 &lt;b&gt;"));assert.match(chip.getAttribute("aria-label"),/点按移除/);
    assert.equal(input.value,"帮我把这个想法想清楚");assert.equal(sends().length,0,"attaching an idea never sends");
    target.querySelector("form").onsubmit({preventDefault(){}});await tick();h.flushFrames();
    const first=sends()[0];assert.equal(first.note_id,"note-1");assert.equal(first.text,"帮我把这个想法想清楚");assert.equal(first.session_id,"assistant");
    assert(!JSON.stringify(first).includes("想法墙"),"the idea text is never sent by the page");
    assert.equal(chip.hidden,true);assert.deepEqual(cleared,["cleared"]);
    input.value="接着聊";input.oninput();target.querySelector("form").onsubmit({preventDefault(){}});await tick();h.flushFrames();
    assert(!Object.hasOwn(sends()[1],"note_id"),"later messages carry no note");
    input.value="我已有的草稿";input.oninput();h.chat.setNote({id:"note-2",title:"另一条"},"帮我把这个想法想清楚");
    assert.equal(input.value,"我已有的草稿","an existing draft is never replaced");
    chip.onclick();assert.equal(chip.hidden,true);assert.equal(cleared.length,2);
    target.querySelector("form").onsubmit({preventDefault(){}});await tick();h.flushFrames();
    assert(!Object.hasOwn(sends()[2],"note_id"),"a removed idea is not sent");
    h.chat.setNote({id:"note-3",title:"任务里不带"});h.chat.setTask({id:"g",title:"任务",leader_session_id:"assistant"});target.querySelector(".chat-task-binding").onclick();
    input.value="补充";input.oninput();target.querySelector("form").onsubmit({preventDefault(){}});await tick();h.flushFrames();
    assert.equal(sends()[3].goal_id,"g");assert(!Object.hasOwn(sends()[3],"note_id"),"a task supplement does not carry the idea");
    h.chat.unmount();
  }
});

test("a rejected task supplement keeps the exact backend reason, draft and target on retry",async()=>{
  const h=harness(),toasts=[],sent=[];let fail=true;
  const target=h.mount("leader",async(url,data)=>{if(url!=="/api/chat/send")return history();sent.push(data);if(fail)throw Error("该目标已暂停，请先恢复");return {accepted:true};},{},{toast:text=>toasts.push(text)});
  await tick();h.chat.setTask({id:"a",title:"任务甲",leader_session_id:"leader"});target.querySelector(".chat-task-binding").onclick();
  const input=target.querySelector("textarea");input.value="保留输入";input.oninput();target.querySelector("form").onsubmit({preventDefault(){}});await tick();
  assert.equal(toasts.at(-1),"该目标已暂停，请先恢复");assert.equal(input.value,"保留输入");fail=false;target.querySelector("form").onsubmit({preventDefault(){}});await tick();assert.equal(sent[0].message_id,sent[1].message_id);assert.equal(input.value,"");h.chat.unmount();
});

test("hidden browser and desktop conversations release their stream and resume after the last event",async()=>{
  for(const runtime of ["claude","codex"])for(const readonly of [false,true])for(const desktop of [false,true]){
    const h=harness(),target=h.mount("leader",async()=>({...history(),events:[{seq:1,type:"message",id:"first",text:"已有上下文"}]}),{runtime},{readonly});
    await tick();h.flushFrames();
    if(!readonly){target.querySelector("textarea").value="未发送草稿";target.querySelector("textarea").oninput();}
    h.connections[0].emit({seq:2,type:"text",id:"reply",text:"已接收回复"});await tick();h.flushFrames();
    h.visibility(false,desktop);await tick();
    assert.equal(h.connections[0].signal.aborted,true,"hidden chats must release the HTTP connection");
    assert(rendered(target).join("").includes("已有上下文"));assert(rendered(target).join("").includes("已接收回复"));
    h.visibility(true,desktop);await tick();
    assert.equal(h.connections.length,2);assert(h.connections[1].url.includes("after=2"));
    h.visibility(true,desktop);await tick();assert.equal(h.connections.length,2,"duplicate visibility notices cannot open duplicate streams");
    h.connections[1].emit({seq:3,type:"text",id:"reply",text:"及离开后的内容"});await tick();h.flushFrames();
    assert(rendered(target).join("").includes("已接收回复及离开后的内容"));
    if(!readonly)assert.equal(target.querySelector("textarea").value,"未发送草稿");
    h.chat.unmount();assert.equal(h.handlers.get("crew-tab-visibility")?.size||0,0);
  }
});

test("a hidden initial history read never opens a stream until both visibility and history are ready",async()=>{
  for(const desktop of [false,true]){
    const h=harness();h.visibility(false,desktop);let release;
    h.mount("assistant",()=>new Promise(resolve=>{release=resolve;}));
    h.visibility(true,desktop);await tick();assert.equal(h.connections.length,0);
    h.visibility(false,desktop);release(history());await tick();assert.equal(h.connections.length,0);
    h.visibility(true,desktop);await tick();assert.equal(h.connections.length,1);h.chat.unmount();
  }
});

test("rendering an idle neighbor keeps a newly inserted activity clock moving",()=>{
  const h=harness(),now=Date.now(),startedAt=new Date(now).toISOString();
  const html=h.sandbox.CrewActivity.render("visible",{phase:"requesting",turn_started_at:startedAt});
  assert.match(html,/data-activity-elapsed>0秒/);
  h.sandbox.CrewActivity.markup({session_id:"idle-neighbor",activity:null});
  const clock={textContent:"0秒"};
  h.sandbox.document.querySelectorAll=()=>[{dataset:{activity:"visible"},querySelector:selector=>selector==='[data-activity-elapsed]'?clock:null}];
  h.sandbox.CrewActivity.timestamp(new Date(now+2000).toISOString());
  for(const [id,timer]of [...h.timers]){h.timers.delete(id);timer.fn();}
  assert.equal(clock.textContent,"2秒");h.sandbox.CrewActivity.release("visible");
});

test("Claude and Codex chats render live tokens without a page-level activity sync",async()=>{
  for(const runtime of ["claude","codex"]){
    const h=harness(),startedAt=new Date(Date.now()-65000).toISOString();
    const activity={phase:"thinking",label:null,turn_started_at:startedAt,output_tokens:128};
    const target=h.mount("assistant",async()=>({...history(),busy:true,events:[{seq:1,type:"status",status:"working",at:startedAt}]}),{runtime,busy:true,last_seq:1,activity});
    await tick();h.flushFrames();
    const html=()=>target.querySelector(".chat-inner").children.map(item=>item.innerHTML).join("");
    assert.match(html(),/思考中/);assert.match(html(),/↓ 128 tokens/);assert.match(html(),/1分\d+秒/);
    h.update("assistant",{runtime,busy:true,last_seq:1,activity:{...activity,output_tokens:256}});h.flushFrames();
    assert.match(html(),/↓ 256 tokens/);
    h.connections[0].emit({seq:2,type:"message",id:"steer",text:"A follow-up while busy",at:new Date().toISOString()});await tick();h.flushFrames();
    assert.match(html(),/1分\d+秒/,"steering the current reply does not restart its clock");
    h.connections[0].emit({seq:3,type:"turn_result",status:"completed",at:new Date().toISOString()});await tick();h.flushFrames();
    assert(!html().includes('data-activity='));h.chat.unmount();
  }
});


test("a pending send uses the server clock and a later token report starts with a real number",async()=>{
  const h=harness(),clientNow=Date.now(),serverNow=new Date(clientNow+600000).toISOString();let finish;
  const target=h.mount("assistant",async(url)=>url==="/api/chat/send"?new Promise(resolve=>{finish=resolve;}):history(),{},
    {state:{now:serverNow,leaders:[],members:[]}});await tick();h.flushFrames();
  target.querySelector("textarea").value="Wait for this reply";target.querySelector("textarea").oninput();
  target.querySelector("form").onsubmit({preventDefault(){}});h.flushFrames();
  const html=()=>target.querySelector(".chat-inner").children.map(item=>item.innerHTML).join("");
  assert.match(html(),/正在发送…/);assert.match(html(),/data-activity-elapsed>0秒/);assert(!html().includes("tokens"));
  finish({accepted:true});await tick();
  h.motion.matches=false;
  h.update("assistant",{busy:true,last_seq:1,activity:{phase:"writing",turn_started_at:serverNow,output_tokens:100}});h.flushFrames();
  assert.match(html(),/↓ 100 tokens/);assert(!html().includes("NaN"));h.chat.unmount();
});

const rendered=target=>target.querySelector(".chat-inner").children.map(item=>item.innerHTML);
const at=seconds=>new Date(Date.UTC(2026,9,27,0,0,seconds)).toISOString();
const workTurn=(start=1)=>[
  {seq:start,type:"message",id:`input-${start}`,source:"human",text:"检查并给我结论",at:at(0)},
  {seq:start+1,type:"status",status:"working",at:at(1)},
  {seq:start+2,type:"assistant",id:`work-${start}`,at:at(2),content:[{type:"text",text:"先检查现状。"},{type:"tool_use",id:`tool-${start}`,name:"Bash",input:{command:"git status"}}]},
  {seq:start+3,type:"tool_result",tool_use_id:`tool-${start}`,content:"clean",is_error:false,at:at(8)},
  {seq:start+4,type:"text",id:`reply-${start}`,text:"**主回复**：检查已完成。",at:at(9)},
  {seq:start+5,type:"turn_result",status:"completed",success:true,at:at(65)},
  {seq:start+6,type:"status",status:"idle",at:at(65)}];

test("live steps stay visible and the terminal receipt folds them into a duration above the final reply",async()=>{
  for(const runtime of ["claude","codex"]){
    const h=harness(),target=h.mount("leader",async()=>history(),{runtime},{});await tick();
    const stream=h.connections[0],events=workTurn();
    for(const event of events.slice(0,5))stream.emit(event);await tick();h.flushFrames();
    assert(rendered(target).some(html=>html.includes("先检查现状")));assert(!rendered(target).some(html=>html.includes("turn-process")));
    for(const event of events.slice(5))stream.emit(event);await tick();h.flushFrames();
    const rows=rendered(target),process=rows.find(html=>html.includes('class="turn-process ')),reply=rows.find(html=>html.startsWith('<div class="m-lead">'));
    assert(process.includes("用时 1 分 5 秒"));assert(process.includes('class="turn-toggle"'));assert(process.includes('aria-expanded="false"'));
    assert(process.includes("先检查现状"));assert(process.includes("git status"));assert(process.includes("clean"));
    assert(!process.includes("主回复"));assert(reply.includes("<strong>主回复</strong>"));
    assert(rows.indexOf(process)<rows.indexOf(reply));assert(!rows.some(html=>html.includes('class="activity"')));
    h.chat.unmount();
  }
});

test("expanded process survives revisiting and still permits expanding the original tool",async()=>{
  const h=harness(),read=async()=>({...history(),events:workTurn(),read_seq:7}),target=h.mount("leader",read);await tick();h.flushFrames();
  const group=target.querySelector(".chat-inner").children.find(item=>item.innerHTML.includes("turn-process"));
  const button={...node(),dataset:{chatFold:"turn:1"}};group.contains=value=>value===button;
  await target.querySelector(".chat-inner").onclick({target:{closest:selector=>selector==="[data-chat-fold]"?button:null}});
  assert.equal(button.getAttribute("aria-expanded"),"true");assert(group.dataset.html.includes('class="turn-toggle open"'));
  const tool={...node(),dataset:{chatFold:"tool:tool-1"}};group.contains=value=>value===tool;
  await target.querySelector(".chat-inner").onclick({target:{closest:selector=>selector==="[data-chat-fold]"?tool:null}});
  assert.equal(tool.getAttribute("aria-expanded"),"true");
  h.chat.unmount();const reopened=h.mount("leader",read);await tick();h.flushFrames();
  const html=rendered(reopened).join("");assert(html.includes('class="turn-toggle open"'));assert(html.includes('class="tool indent open"'));
  h.chat.unmount();
});

test("failed and interrupted turns keep their actual errors visible and never become successful replies",async()=>{
  for(const status of ["failed","interrupted"]){
    const events=workTurn().slice(0,4);events.push({seq:5,type:"error",text:"真实失败原因",at:at(10)},
      {seq:6,type:"turn_result",status,success:false,at:at(11)});
    const h=harness(),target=h.mount("leader",async()=>({...history(),events}));await tick();h.flushFrames();
    const rows=rendered(target);assert(rows.some(html=>html.includes(`turn-incomplete`) && html.includes(status==="failed"?"执行失败":"已中断")));
    assert(rows.some(html=>html.startsWith('<div class="chat-error"')&&html.includes("真实失败原因")));
    assert(!rows.some(html=>html.startsWith('<div class="m-lead">')));h.chat.unmount();
  }
});

test("text before a final tool is process, not a fabricated final answer, and unknown timing stays unknown",async()=>{
  const events=workTurn().filter(event=>event.type!=="text").map(({at,...event})=>event);
  const h=harness(),target=h.mount("leader",async()=>({...history(),events}));await tick();h.flushFrames();
  const rows=rendered(target),process=rows.find(html=>html.includes("turn-process"));
  assert(process.includes("过程记录"));assert(process.includes("本轮未提供最终回复"));assert(!process.includes("用时 0"));
  assert(!rows.some(html=>html.startsWith('<div class="m-lead">')));h.chat.unmount();
});

test("steering inputs remain in chronological order and a following live turn does not join the prior duration",async()=>{
  const events=workTurn();events.splice(4,0,{seq:4.5,type:"message",id:"steer",source:"human",text:"补充要求",at:at(8)});
  events.push({seq:8,type:"message",id:"second",source:"human",text:"再看看另一处",at:at(70)},
    {seq:9,type:"status",status:"working",at:at(70)},
    {seq:10,type:"assistant",id:"second-work",content:[{type:"text",text:"第二轮正在检查"}],at:at(71)});
  const h=harness(),target=h.mount("leader",async()=>({...history(),events,busy:true}));await tick();h.flushFrames();
  const rows=rendered(target);assert.equal(rows.filter(html=>html.includes('class="turn-process ')).length,1);
  assert(rows.findIndex(html=>html.includes('class="m-user"')&&html.includes("补充要求"))<rows.findIndex(html=>html.startsWith('<div class="m-lead">')&&html.includes("主回复")));
  assert(rows.findIndex(html=>html.includes('class="m-user"')&&html.includes("再看看另一处"))>rows.findIndex(html=>html.startsWith('<div class="m-lead">')&&html.includes("主回复")));
  assert(rows.some(html=>html.startsWith('<div class="m-lead">')&&html.includes("第二轮正在检查")));h.chat.unmount();
});

test("an idle legacy reply cannot absorb the next prompt and tools with real errors remain visible in the summary",async()=>{
  const events=[{seq:1,type:"assistant",id:"legacy",content:[{type:"text",text:"历史回复，没有结束回执"}],at:at(0)},
    ...workTurn(2)];
  events.find(event=>event.type==="tool_result").is_error=true;
  const h=harness(),target=h.mount("leader",async()=>({...history(),events}));await tick();h.flushFrames();
  const rows=rendered(target),groups=rows.filter(html=>html.includes('class="turn-process '));
  assert.equal(groups.length,2);assert(groups[1].includes("用时 1 分 5 秒"));assert(groups[1].includes("1 项工具报错"));
  assert(!groups[1].includes("turn-incomplete"),"a recovered tool error is not a failed native turn");
  assert.equal(rows.filter(html=>html.startsWith('<div class="m-lead">')).length,2);h.chat.unmount();
});

test("folding a turn above a visible steering message preserves that message's viewport position",async()=>{
  const events=workTurn(),h=harness(),target=h.mount("leader",async()=>({...history(),busy:true,
    events:[...events.slice(0,4),{seq:4.5,type:"message",id:"steer",source:"human",text:"正在阅读的补充要求",at:at(8)}]}));
  await tick();h.flushFrames();
  const inner=target.querySelector(".chat-inner"),scroll=target.querySelector(".chat-scroll");
  scroll.scrollHeight=2000;scroll.clientHeight=200;scroll.scrollTop=300;scroll.getBoundingClientRect=()=>({top:0,bottom:200});
  const message=inner.children.find(item=>item.innerHTML.includes("正在阅读的补充要求"));
  message.getBoundingClientRect=()=>{const folded=inner.children.some(item=>item.innerHTML.includes('class="turn-process '));const top=(folded?20:80)-(scroll.scrollTop-300);return{top,bottom:top+40};};
  for(const event of events.slice(4))h.connections[0].emit(event);await tick();h.flushFrames();
  assert.equal(message.getBoundingClientRect().top,80);assert.equal(scroll.scrollTop,240);h.chat.unmount();
});

test("compact watchers use finite history reads and finish once on a new native receipt",async()=>{
  for(const terminal of [{type:"compact",seq:2,pre_tokens:50000,post_tokens:10000},
    {type:"compact_cancelled",reason:"上一轮未成功结束"},{type:"status",status:"offline"},{type:"error",text:"native failed"}]){
    const h=harness(),finished=[],toasts=[],calls=[];let events=[{type:"compact",seq:1,pre_tokens:5,post_tokens:1}];
    const close=await h.chat.watchCompact("member",{state:{project:{name:"fixture"}},api:async url=>{calls.push(url);return {events};},toast:text=>toasts.push(text),onFinish:event=>finished.push(event)});
    const poll=async()=>{const entry=[...h.timers.entries()].find(([,timer])=>timer.ms===3000);assert(entry,"a finite read should be scheduled");h.timers.delete(entry[0]);await entry[1].fn();await tick();};
    assert.equal(finished.length,0,"an earlier compression is not this request's receipt");
    events=[...events,{seq:2,type:"turn_result",success:true}];await poll();assert.equal(finished.length,0);
    events=[...events,{...terminal,seq:3}];await poll();assert.equal(finished.length,1);close();
    assert.equal(h.timers.size,0);assert.equal(h.connections.length,0);
    assert(calls.every(url=>url.startsWith("/api/chat/history?")&&url.includes("session_id=member")&&!url.includes("goal_id=")));
    assert.equal(toasts.length,terminal.type==="compact"?1:0);
  }
});

test("hidden compact watchers pause reads, catch up on return and ignore disposed late responses",async()=>{
  for(const desktop of [false,true]){
    const h=harness(),finished=[];let reads=0,resolve;
    h.visibility(false,desktop);
    const close=await h.chat.watchCompact("member",{state:{project:{name:"fixture"}},api:async()=>{reads++;return reads===1?history():new Promise(done=>{resolve=done;});},toast(){},onFinish:event=>finished.push(event)});
    assert.equal(reads,1);assert.equal(h.timers.size,0);
    h.visibility(true,desktop);await tick();assert.equal(reads,2);
    h.visibility(false,desktop);h.visibility(true,desktop);await tick();assert.equal(reads,2,"visibility changes cannot overlap a pending read");
    close();resolve({events:[{seq:1,type:"compact",pre_tokens:10,post_tokens:1}]});await tick();
    assert.equal(finished.length,0);assert.equal(h.timers.size,0);assert.equal(h.handlers.get("visibilitychange")?.size||0,0);assert.equal(h.handlers.get("crew-tab-visibility")?.size||0,0);
  }
});



test("compact history transport failures report the real cause and retry reads without finishing or resending",async()=>{
  const h=harness(),toasts=[],finished=[],calls=[];let fail=false;
  const close=await h.chat.watchCompact("member",{state:{project:{name:"fixture"}},api:async url=>{calls.push(url);if(fail)throw Error("temporary connection failure");return history();},toast:text=>toasts.push(text),onFinish:event=>finished.push(event)});
  const poll=async()=>{const entry=[...h.timers.entries()].find(([,timer])=>timer.ms===3000);assert(entry);h.timers.delete(entry[0]);await entry[1].fn();await tick();};
  fail=true;await poll();await poll();assert.equal(toasts.length,1);assert.match(toasts[0],/temporary connection failure/);assert.match(toasts[0],/自动重试/);assert.equal(finished.length,0);
  fail=false;await poll();fail=true;await poll();assert.equal(toasts.length,2);assert(calls.every(url=>url.startsWith("/api/chat/history?")));close();assert.equal(h.timers.size,0);
});

test("general conversation unread dividers follow reply text, excluding tools and unchanged snapshots",async()=>{
  {
    const tool={type:"tool_use",id:"call",name:"Shell",input:{command:"git status"}};
    const events=[{seq:1,type:"text",id:"seen",text:"已读正文"},
      {seq:2,type:"assistant",id:"seen",content:[{type:"text",text:"已读正文"},tool]},
      {seq:3,type:"assistant",id:"tool-only",content:[{...tool,id:"another"}]},
      {seq:4,type:"tool_result",tool_use_id:"another",content:"clean"},
      {seq:5,type:"text",id:"new",text:"未读正文"},
      {seq:6,type:"assistant",id:"new",content:[{type:"text",text:"未读正文"}]}];
    const h=harness(),target=h.mount("leader",async()=>({...history(),events,read_seq:1}),{},{});
    await tick();h.flushFrames();
    const children=target.querySelector(".chat-inner").children;
    const divider=children.findIndex(item=>item.innerHTML==="以下是你离开后的消息");
    assert(divider>0);
    assert(children[divider+1].innerHTML.includes("未读正文"));
    assert(!children[divider+1].innerHTML.includes("已读正文"));
    assert.equal(target.querySelector(".chat-connection-error").hidden,true);
    h.chat.unmount();

    const done=harness(),doneTarget=done.mount("leader",async()=>({...history(),events:events.slice(0,4),read_seq:1}),{},{});
    await tick();done.flushFrames();
    assert(!doneTarget.querySelector(".chat-inner").children.some(item=>item.innerHTML==="以下是你离开后的消息"));
    assert.equal(doneTarget.querySelector(".chat-connection-error").hidden,true);
    done.chat.unmount();
  }
});

test("a goal-start tool followed by identity handoff and native interruption renders as transferred, not executing forever",async()=>{
  // Minimized from the real journal: tool -> handoff -> interrupted turn -> offline; no tool_result exists.
  const events=[{seq:1,type:"status",status:"working"},
    {seq:2,type:"assistant",id:"start",content:[{type:"tool_use",id:"call-start",name:"crew_goal",input:{action:"start",goal_id:"goal"}}]},
    {seq:3,type:"identity_handoff",target_execution:"execution",text:"当前 Crew 身份已转交执行；保留此段历史，旧原生进程停止。"},
    {seq:4,type:"turn_result",status:"interrupted",success:false},
    {seq:5,type:"status",status:"offline"}];
  const h=harness(),calls=[],routed=[];
  const target=h.mount("leader",async url=>{calls.push(url);return {...history(),events,read_seq:5};},{},{onRouted:id=>routed.push(id)});await tick();h.flushFrames();
  const html=target.querySelector(".chat-inner").children.map(item=>item.innerHTML).join("");
  assert(!html.includes("正在执行…"),"the former general turn has already ended");
  assert(!html.includes('aria-label="执行中"'));assert(html.includes("已转交"));
  assert(!html.includes("执行失败"));assert(calls.every(url=>url.startsWith("/api/chat/history?")));
  assert(html.includes('data-chat-goal="goal"'));
  await target.querySelector(".chat-inner").onclick({target:{closest:selector=>selector==="[data-chat-goal]"?{dataset:{chatGoal:"goal"}}:null}});
  assert.deepEqual(routed,["goal"]);assert(calls.every(url=>url.startsWith("/api/chat/history?")));
  h.chat.unmount();
});

test("live handoff closes a pending tool, while a later native result retains its actual failure and output",async()=>{
  const h=harness(),target=h.mount("leader",async()=>history());await tick();
  const stream=h.connections[0],html=()=>target.querySelector(".chat-inner").children.map(item=>item.innerHTML).join("");
  stream.emit({seq:1,type:"status",status:"working"});
  stream.emit({seq:2,type:"assistant",id:"call",content:[{type:"tool_use",id:"tool",name:"crew_goal",input:{action:"start",goal_id:"goal"}}]});
  await tick();h.flushFrames();assert(html().includes("正在执行…"));
  stream.emit({seq:3,type:"identity_handoff",target_execution:"execution"});
  stream.emit({seq:4,type:"turn_result",status:"interrupted"});
  stream.emit({seq:5,type:"status",status:"offline"});await tick();h.flushFrames();
  assert(html().includes("已转交"));assert(!html().includes("正在执行…"));
  stream.emit({seq:6,type:"tool_result",tool_use_id:"tool",is_error:true,content:"目标启动被拒绝的真实原因"});await tick();h.flushFrames();
  assert(html().includes("执行失败"));assert(html().includes("目标启动被拒绝的真实原因"));assert(!html().includes("已转交"));h.chat.unmount();
});

test("terminal turn receipts stop waiting before a separate status event arrives",async()=>{
  for(const event of [{type:"turn_result",status:"completed"},{type:"turn_result",status:"failed"},
    {type:"turn_result",status:"interrupted"},{type:"identity_handoff",target_execution:"task"}]){
    const h=harness(),target=h.mount("leader",async()=>history());await tick();
    const stream=h.connections[0],progress=()=>target.querySelector(".chat-inner").children.filter(item=>item.className==="chat-activity");
    stream.emit({seq:1,type:"status",status:"working"});await tick();h.flushFrames();assert.equal(progress().length,1);
    stream.emit({seq:2,...event});await tick();h.flushFrames();
    assert.equal(progress().length,0,"a terminal receipt is sufficient to stop waiting");
    assert.equal(target.querySelector(".chat-stop").hidden,true);
    h.update("leader",{busy:true,last_seq:2});h.flushFrames();assert.equal(progress().length,0,"the same snapshot cannot revive an ended turn");
    stream.emit({seq:3,type:"status",status:"working"});await tick();h.flushFrames();assert.equal(progress().length,1,"new work still shows progress");
    h.chat.unmount();
  }
});

test("replayed legacy goal handoff cannot regain a waiting spinner from the same presence snapshot",async()=>{
  const events=[{seq:1,type:"status",status:"working"},
    {seq:2,type:"assistant",id:"start",content:[{type:"tool_use",id:"call",name:"Bash",input:{command:"crew goal start task"}}]},
    {seq:3,type:"turn_result",status:"interrupted",reason:"identity_handoff"},
    {seq:4,type:"status",status:"offline"},{seq:5,type:"identity_handoff",target_execution:"task"}];
  const h=harness(),read=async()=>({...history(),events,read_seq:5}),extra={busy:true,last_seq:5};
  for(let opened=0;opened<2;opened++){
    const target=h.mount("leader",read,extra);await tick();h.flushFrames();
    h.update("leader",extra);h.flushFrames();
    const rows=target.querySelector(".chat-inner").children;
    assert(rows.some(item=>item.innerHTML.includes("已转交执行")));
    assert.equal(rows.filter(item=>item.className==="chat-activity").length,0);
    assert.equal(target.querySelector(".chat-stop").hidden,true);
    h.chat.unmount();
  }
});

test("legacy offline history stops the spinner but keeps the missing tool result explicitly unconfirmed",async()=>{
  const h=harness(),target=h.mount("leader",async()=>({...history(),read_seq:2,events:[
    {seq:1,type:"assistant",id:"call",content:[{type:"tool_use",id:"tool",name:"crew_goal",input:{action:"start"}}]},
    {seq:2,type:"status",status:"offline"}]}));await tick();h.flushFrames();
  const html=target.querySelector(".chat-inner").children.map(item=>item.innerHTML).join("");
  assert(!html.includes("正在执行…"));assert(html.includes("会话已离线"));assert(html.includes("结果未确认"));
  assert(!html.includes('class="ok"'));assert(!html.includes("执行失败"));h.chat.unmount();
});

test("a confirmed handoff after native output drains upgrades the interrupted tool and preserves a later real receipt",async()=>{
  const h=harness(),target=h.mount("member",async()=>history());await tick();
  const stream=h.connections[0],html=()=>target.querySelector(".chat-inner").children.map(item=>item.innerHTML).join("");
  stream.emit({seq:1,type:"assistant",id:"old",content:[{type:"tool_use",id:"old-tool",name:"Bash",input:{command:"crew goal start goal"}}]});
  stream.emit({seq:2,type:"turn_result",status:"interrupted",reason:"identity_handoff"});
  stream.emit({seq:3,type:"status",status:"offline"});
  stream.emit({seq:4,type:"identity_handoff",target_execution:"execution"});await tick();h.flushFrames();
  assert(html().includes("已转交执行"));assert(!html().includes("本轮已中断"));assert(!html().includes("正在执行…"));
  stream.emit({seq:5,type:"tool_result",tool_use_id:"old-tool",content:"具体执行结果",is_error:false});await tick();h.flushFrames();
  assert(html().includes("具体执行结果"));assert(!html().includes("已转交执行"));h.chat.unmount();
});

test("losing the browser stream does not mark a still running native tool offline",async()=>{
  const h=harness(),target=h.mount("leader",async()=>({...history(),busy:true,events:[
    {seq:1,type:"assistant",id:"call",content:[{type:"tool_use",id:"tool",name:"Bash",input:{command:"long command"}}]}]}));
  await tick();h.flushFrames();
  assert(target.querySelector(".chat-inner").children.some(item=>item.innerHTML.includes("正在执行…")));h.chat.unmount();
});

test("a later handoff never rewrites the failure of an earlier turn's tool",async()=>{
  const h=harness(),target=h.mount("leader",async()=>({...history(),events:[
    {seq:1,type:"assistant",id:"old",content:[{type:"tool_use",id:"old-tool",name:"Bash",input:{command:"old failure"}}]},
    {seq:2,type:"turn_result",status:"failed"},
    {seq:3,type:"assistant",id:"new",content:[{type:"tool_use",id:"new-tool",name:"crew_goal",input:{action:"start"}}]},
    {seq:4,type:"turn_result",status:"interrupted",reason:"identity_handoff"},
    {seq:5,type:"status",status:"offline"},
    {seq:6,type:"identity_handoff",target_execution:"new-execution"}]}));await tick();h.flushFrames();
  const rows=target.querySelector(".chat-inner").children;
  const old=rows.find(item=>item.innerHTML.includes("old failure"));
  assert(old.innerHTML.includes("本轮失败"));assert(!old.innerHTML.includes("已转交"));
  assert(rows.some(item=>item.innerHTML.includes("已转交执行")));h.chat.unmount();
});

test("terminal turns without a tool result remain explicitly unknown, and do not close a later turn's new tool",async()=>{
  for(const [status,label]of [["completed","本轮已结束"],["interrupted","本轮已中断"],["failed","本轮失败"]]){
    const h=harness(),target=h.mount("leader",async()=>({...history(),read_seq:2,events:[
      {seq:1,type:"assistant",id:"old",content:[{type:"tool_use",id:"old-tool",name:"crew_goal",input:{action:"status"}}]},
      {seq:2,type:"turn_result",status}]}));await tick();h.flushFrames();
    let html=target.querySelector(".chat-inner").children.map(item=>item.innerHTML).join("");
    assert(html.includes(label));assert(html.includes("未收到"));assert(!html.includes('class="ok"'));assert(!html.includes("正在执行…"));
    h.connections[0].emit({seq:3,type:"assistant",id:"new",content:[{type:"tool_use",id:"new-tool",name:"crew_goal",input:{action:"status"}}]});await tick();h.flushFrames();
    html=target.querySelector(".chat-inner").children.map(item=>item.innerHTML).join("");assert(html.includes(label));assert(html.includes("正在执行…"));h.chat.unmount();
  }
});

test("starting a queued goal retains the leader transcript, stream, draft and reading position",async()=>{
  const h=harness(),nodes=new Map(),calls=[];
  let execution=null;
  const person={session_id:"leader",name:"测试负责人",runtime:"codex",model:null,effort:null,busy:false,last_seq:0,activity:null,where:"bg",priority:null,status:"idle",codex:[]};
  const goal={assignments:[],work_summary:{total:0,done:0},decision_mode:{mode:"human"},now_doing:[],pending_decision:null,working_count:0,last_activity_at:"2026-11-05T00:00:00Z",supplements_after_report:[],id:"goal",leader_session_id:"leader",title:"真实目标",request:"推进并交付",acceptance:[],status:"queued",current_round:1,requirement_version:1,version:1};
  const state={project:{name:"fixture"},leaders:[person],members:[],decisions:[],todos:[],goals:[goal],runtimes:[{id:"codex",name:"Codex",available:true,models:[],efforts:[]}]};
  const main=host();nodes.set("main",main);
  for(const id of ["chat-attention","attention-slot","attention-scope","attention-empty","native-approval-tray","decision-tray","side-attention-section","side-attention-count","right-panel"])nodes.set(id,host());
  Object.defineProperty(main,"innerHTML",{set(value){this.markup=value;for(const [,id]of value.matchAll(/id="([^"]+)"/g))nodes.set(id,host());},get(){return this.markup||"";}});
  h.sandbox.document.getElementById=id=>nodes.get(id)||null;
  h.sandbox.location={search:"?project=fixture",origin:"http://fixture.local"};
  h.sandbox.CrewContext=h.sandbox.window.CrewContext;
  const api=async(url,data)=>{
    calls.push({url,data});
    if(url==="/api/goals/action"){
      assert.equal(data.action,"start");Object.assign(goal,{status:"running",version:goal.version+1});
      execution={id:"execution-1",session_id:"leader",runtime:"codex",model:null,effort:null,round:1,requirement_version:1,generation:1};return {goal:{...goal}};
    }
    if(url.startsWith("/api/goal?"))return {goal:{...goal},executions:execution?[{...execution}]:[],context:{items:[],manifest:{}},reports:[]};
    if(url.startsWith("/api/chat/history?"))return {...history(),execution_id:execution?.id??null};
    throw Error("Unexpected request: "+url);
  };
  const appSource=fs.readFileSync(path.join(__dirname,"../crew/web/app.js"),"utf8").replace(/\r\n/g,"\n");
  vm.runInContext(appSource.replace("  setup();\n})();",'window.appFixture={renderChat,configure(value,client,request){state=value;goals=client;api=request;view="chat";leaderId="leader";goalId="goal";}};\n})();'),h.sandbox);
  vm.runInContext(fs.readFileSync(path.join(__dirname,"../crew/web/goal-ui.js"),"utf8"),h.sandbox);
  const goalUI=h.sandbox.CrewGoals.create({project:"fixture",state:()=>state,api,avatar:()=>"",ago:()=>"",toast(){},onChange:()=>h.sandbox.appFixture.renderChat(),refreshState:async()=>h.sandbox.appFixture.renderChat()});
  h.sandbox.appFixture.configure(state,goalUI,api);h.sandbox.appFixture.renderChat();await tick();
  assert.equal(h.connections.length,1);const emptyStream=h.connections[0],queuedHost=nodes.get("chat-host");
  queuedHost.querySelector("textarea").value="目标开始后仍要保留的草稿";queuedHost.querySelector("textarea").oninput();
  queuedHost.querySelector(".chat-scroll").scrollTop=123;queuedHost.querySelector(".chat-scroll").onscroll();
  const button={disabled:false,dataset:{goalAct:"action",action:"start"}};
  for(const handler of h.handlers.get("click")||[])await handler({target:{closest:selector=>selector==="[data-goal-act]"?button:null}});
  await tick();
  assert.equal(emptyStream.signal.aborted,false);assert.equal(h.connections.length,1);
  assert.equal(calls.filter(call=>call.url.startsWith("/api/chat/history?")).length,1);
  const runningHost=nodes.get("chat-host");assert.equal(runningHost,queuedHost,"execution metadata must not replace the conversation");
  assert.equal(runningHost.querySelector("textarea").value,"目标开始后仍要保留的草稿");assert.equal(runningHost.querySelector(".chat-scroll").scrollTop,123);
  h.connections[0].emit({seq:1,type:"text",id:"native-reply",text:"已经从真实 execution 接到回复"});await tick();h.flushFrames();
  assert(runningHost.querySelector(".chat-inner").children.some(child=>child.innerHTML.includes("已经从真实 execution 接到回复")));
  h.sandbox.appFixture.renderChat();assert.equal(h.connections.length,1,"task execution must retain the general stream");
  Object.assign(goal,{current_round:2,version:goal.version+1});execution={...execution,id:"execution-2",round:2,generation:1};
  await goalUI.refresh();await tick();assert.equal(h.connections[0].signal.aborted,false);assert.equal(h.connections.length,1);
  assert.equal(nodes.get("chat-host"),runningHost,"a new round switches history without replacing the composer");
  assert.equal(nodes.get("chat-host").querySelector("textarea").value,"目标开始后仍要保留的草稿");
  Object.assign(goal,{current_round:3,status:"queued",version:goal.version+1});execution=null;
  await goalUI.refresh();await tick();assert.equal(h.connections[0].signal.aborted,false);assert.equal(h.connections.length,1);
  assert.equal(nodes.get("chat-host"),runningHost,"a round without an execution also keeps the conversation mounted");
  assert.equal(nodes.get("chat-host").querySelector("textarea").value,"目标开始后仍要保留的草稿");
  assert.equal(calls.filter(call=>call.url==="/api/goals/action").length,1);assert(!calls.some(call=>/chat\/send|wake/.test(call.url)));
  assert(h.connections.every(stream=>!stream.url.includes("goal_id=")));h.chat.unmount();
});

test("initial history failure retries, recovers the connection and preserves a typed draft without sending",async()=>{
  const h=harness(),calls=[];let attempts=0;
  const target=h.mount("a",async(url,data)=>{calls.push({url,data});if(++attempts===1)throw Error("temporary connection failure");return history();});await tick();
  assert.equal(attempts,1);assert.equal(h.timers.size,1);assert.match(target.querySelector(".chat-connection-error").textContent,/自动重试/);
  target.querySelector("textarea").value="重连时继续写的草稿";target.querySelector("textarea").oninput();
  assert.equal(target.querySelector(".send").disabled,true);
  const [id,timer]=[...h.timers][0];assert.equal(timer.ms,3000);h.timers.delete(id);timer.fn();await tick();
  assert.equal(attempts,2);assert.equal(h.streams.length,1);assert.equal(target.querySelector(".chat-connection-error").hidden,true);
  assert.equal(target.querySelector("textarea").value,"重连时继续写的草稿");assert.equal(target.querySelector(".send").disabled,false);
  assert(calls.every(call=>call.url.startsWith("/api/chat/history?")&&call.data===undefined));h.chat.unmount();
});

test("unmount cancels the pending retry and even an already queued callback cannot load a different leader",async()=>{
  const h=harness();let oldCalls=0,newCalls=0;
  const a=h.mount("a",async()=>{oldCalls++;throw Error("offline");});await tick();
  a.querySelector("textarea").value="A的未发送草稿";a.querySelector("textarea").oninput();
  const pending=[...h.timers.values()][0];assert(pending);h.chat.unmount();assert.equal(h.timers.size,0);
  const b=h.mount("b",async()=>{newCalls++;return history();});await tick();pending.fn();await tick();
  assert.equal(oldCalls,1);assert.equal(newCalls,1);assert.equal(b.querySelector("textarea").value,"");
  assert(h.streams.every(url=>url.includes("session_id=b")));
  const remounted=h.mount("a",async()=>history());await tick();assert.equal(remounted.querySelector("textarea").value,"A的未发送草稿");h.chat.unmount();
});

test("a late history response from an unmounted leader cannot render into or connect the current leader",async()=>{
  const h=harness();let finish;
  const a=h.mount("a",()=>new Promise(resolve=>{finish=resolve;}));
  const b=h.mount("b",async()=>history());await tick();
  finish({events:[{seq:1,type:"message",id:"old-message",text:"old leader only"}],busy:true,read_seq:0});await tick();
  assert.equal(a.querySelector(".chat-inner").innerHTML,"");assert.equal(b.querySelector(".chat-connection-error").hidden,true);
  assert(h.streams.every(url=>url.includes("session_id=b")));assert.equal(h.timers.size,0);h.chat.unmount();
});

test("the general stop waits for its process-exit receipt and then confirms the identity is stopped",async()=>{
  const h=harness(),calls=[];let acknowledge;
  const target=h.mount("a",async(url,data)=>{calls.push({url,data});if(url==="/api/session/stop")return new Promise(resolve=>{acknowledge=resolve;});return {...history(),busy:true};},{busy:true});await tick();
  assert(target.innerHTML.includes('class="chat-stop"'));const stop=target.querySelector(".chat-stop");assert.equal(stop.hidden,false);assert.equal(stop.disabled,false);
  target.querySelector("textarea").value="中断后继续编辑的草稿";target.querySelector("textarea").oninput();
  stop.onclick();await tick();assert.equal(stop.disabled,true);assert.equal(stop.textContent,"");assert.equal(stop.getAttribute("aria-busy"),"true");assert.equal(target.querySelector(".chat-interrupt-status").hidden,true);
  const sent=calls.find(call=>call.url==="/api/session/stop");assert.deepEqual(JSON.parse(JSON.stringify(sent.data)),{session_id:"a"});
  acknowledge({ok:true});await tick();assert.equal(stop.disabled,true);assert.equal(stop.hidden,true);assert.equal(target.querySelector(".send").disabled,false);
  h.update("a",{busy:false});assert.equal(stop.hidden,true);assert.equal(target.querySelector(".send").disabled,false);
  assert.equal(stop.getAttribute("aria-busy"),"false");assert.equal(target.querySelector(".chat-interrupt-status").hidden,true);assert.equal(target.querySelector("textarea").value,"中断后继续编辑的草稿");
  assert(!calls.some(call=>/send|wake|archive/.test(call.url)));h.chat.unmount();
});

test("the general stop confirmation locks repeated clicks and cancellation sends no stop request",async()=>{
  const h=harness(),calls=[];let resolve,confirmations=0;const target=h.mount("leader",async(url)=>{calls.push(url);return history();},{busy:true},{beforeStop:()=>{confirmations++;return new Promise(done=>{resolve=done;});}});await tick();h.update("leader",{busy:true});const button=target.querySelector(".chat-stop");button.onclick();button.onclick();assert.equal(confirmations,1);assert.equal(button.disabled,true);assert.equal(button.getAttribute("aria-busy"),"true");resolve(false);await tick();assert(!calls.includes("/api/session/stop"));assert.equal(button.disabled,false);assert.equal(button.getAttribute("aria-busy"),"false");h.chat.unmount();
});


test("an accepted general send opens the task only after acceptance and preserves a failed draft",async()=>{
  const h=harness(),calls=[],routed=[];let failed=true;
  const general=h.mount("leader",async(url,data)=>{calls.push({url,data});if(url!=="/api/chat/send")return history();if(failed)throw Error("连接失败");return {accepted:true,goal_id:"goal",delivery:"steered"};},{},{onRouted:id=>routed.push(id)});await tick();
  general.querySelector("textarea").value="开始了吗";general.querySelector("textarea").oninput();general.querySelector("form").onsubmit({preventDefault(){}});await tick();assert.deepEqual(routed,[]);assert.equal(general.querySelector("textarea").value,"开始了吗");
  failed=false;general.querySelector("form").onsubmit({preventDefault(){}});await tick();assert.deepEqual(routed,["goal"]);assert.equal(JSON.parse(h.saved.get("crew.draft:fixture:leader")).text,"");
  const sends=calls.filter(call=>call.url==="/api/chat/send");assert.equal(sends.length,2);assert.equal(sends[0].data.message_id,sends[1].data.message_id);assert(!("goal_id" in sends[0].data));assert.equal(h.connections.length,1);h.chat.unmount();
});
test("interrupt failure is explicit and can be retried without discarding a draft",async()=>{
  const h=harness();let stops=0;
  const target=h.mount("a",async url=>{if(url==="/api/session/stop"){if(++stops===1)throw Error("temporary stop failure");return {ok:true};}return {...history(),busy:true};},{busy:true});await tick();
  const stop=target.querySelector(".chat-stop");stop.onclick();await tick();assert.equal(stop.disabled,false);assert.match(target.querySelector(".chat-interrupt-status").textContent,/temporary stop failure/);
  stop.onclick();await tick();assert.equal(stops,2);assert.equal(stop.disabled,true);h.chat.unmount();
});

test("external busy sessions explain why Crew cannot interrupt and idle sessions hide the control",async()=>{
  const h=harness(),calls=[];
  const target=h.mount("external",async url=>{calls.push(url);return {...history(),busy:true};},{busy:true,where:"vscode"});await tick();
  const stop=target.querySelector(".chat-stop");assert.equal(stop.hidden,false);assert.equal(stop.disabled,true);assert.match(target.querySelector(".chat-interrupt-status").textContent,/原工具/);
  stop.onclick();await tick();assert(!calls.includes("/api/session/stop"));h.update("external",{busy:false,where:"vscode"});assert.equal(stop.hidden,true);h.chat.unmount();
});

test("busy input sends a stable idempotent message and keeps failed drafts until actual acceptance",async()=>{
  const h=harness(),calls=[];let sends=0;
  const target=h.mount("worker",async(url,data)=>{calls.push({url,data});if(url==="/api/chat/send"){if(++sends===1)throw Error("connection lost after send");return {accepted:true,delivery:"steered",message_id:data.message_id};}return {...history(),busy:true};},{busy:true});await tick();
  const input=target.querySelector("textarea");input.value="改为先验证短句";input.oninput();
  assert.equal(target.querySelector(".send").disabled,false);assert.equal(target.querySelector(".send").hidden,false);assert.equal(target.querySelector(".hint").textContent,"");
  target.querySelector("form").onsubmit({preventDefault(){}});await tick();assert.equal(input.value,"改为先验证短句");
  target.querySelector("form").onsubmit({preventDefault(){}});await tick();
  const sent=calls.filter(call=>call.url==="/api/chat/send");assert.equal(sent.length,2);assert.equal(sent[0].data.message_id,sent[1].data.message_id);assert(sent[0].data.message_id);
  assert(!("goal_id" in sent[0].data));assert.equal(input.value,"");assert.equal(target.querySelector(".chat-delivery-status").hidden,true);assert.equal(target.querySelector(".send").hidden,true);h.chat.unmount();
});



test("readonly status transitions preserve the same conversation draft and reading position without sending",async()=>{
  const h=harness(),calls=[],api=async url=>{calls.push(url);return history();};
  const editable=h.mount("member",api,{web_chat:false,where:"offline"},{readonly:false});await tick();
  editable.querySelector("textarea").value="恢复后继续编辑的草稿";editable.querySelector("textarea").oninput();
  const scroll=editable.querySelector(".chat-scroll");scroll.scrollTop=123;scroll.onscroll();
  const paused=h.mount("member",api,{web_chat:false,where:"offline"},{readonly:true,readonlyReason:"总对话可讨论；继续工作先恢复/再开一轮。"});await tick();
  assert(paused.innerHTML.includes('class="readonly-note"'));assert(!paused.innerHTML.includes('class="composer-wrap"'));assert.equal(paused.querySelector(".chat-scroll").scrollTop,123);
  const resumed=h.mount("member",api,{web_chat:false,where:"offline"},{readonly:false});await tick();
  assert.equal(resumed.querySelector("textarea").value,"恢复后继续编辑的草稿");assert.equal(resumed.querySelector(".chat-scroll").scrollTop,123);
  assert(calls.every(url=>url.startsWith("/api/chat/history?")));h.chat.unmount();
});

test("a detached conversation scroll event cannot reset the reading position while the same chat remounts",async()=>{
  const h=harness();const first=h.mount("leader",async()=>history(),{},{});await tick();const detached=first.querySelector(".chat-scroll");detached.scrollTop=160;detached.onscroll();
  h.mount("member",async()=>history(),{},{});await tick();
  let reads=0;const restored=h.mount("leader",async()=>{reads++;return history();});detached.scrollTop=0;detached.onscroll();await tick();assert.equal(reads,0,"revisiting reuses the applied transcript");assert.equal(restored.querySelector(".chat-scroll").scrollTop,160);h.chat.unmount();
});

test("opening readonly first restores a persisted draft when work is explicitly resumed",async()=>{
  const h=harness();h.saved.set("crew.draft:fixture:member",JSON.stringify({text:"上次保留的草稿",images:[]}));
  h.mount("member",async()=>history(),{web_chat:false},{readonly:true});await tick();
  const resumed=h.mount("member",async()=>history(),{web_chat:false},{readonly:false});await tick();
  assert.equal(resumed.querySelector("textarea").value,"上次保留的草稿");h.chat.unmount();
});

test("an accepted in-flight send can finish after readonly remount without restoring an already sent draft",async()=>{
  const h=harness();let release,sends=0;const api=async(url)=>{if(url==="/api/chat/send"){sends++;return new Promise(resolve=>{release=resolve;});}return history();};
  const target=h.mount("member",api,{},{readonly:false});await tick();target.querySelector("textarea").value="这条已经提交";target.querySelector("textarea").oninput();target.querySelector("form").onsubmit({preventDefault(){}});await tick();
  h.mount("member",api,{},{readonly:true});await tick();release({accepted:true,delivery:"started"});await tick();
  assert.equal(JSON.parse(h.saved.get("crew.draft:fixture:member")).text,"");
  const resumed=h.mount("member",api,{},{readonly:false});await tick();assert.equal(resumed.querySelector("textarea").value,"");assert.equal(resumed.querySelector(".chat-delivery-status").hidden,true);assert.equal(sends,1);h.chat.unmount();
});

test("a busy turn has one progress row and swaps send for the icon stop until a follow-up draft is ready",async()=>{
  const h=harness(),calls=[];let accept;
  const target=h.mount("worker",async(url,data)=>{calls.push({url,data});if(url==="/api/chat/send")return new Promise(resolve=>{accept=resolve;});return {...history(),busy:true};},{busy:true});await tick();h.flushFrames();
  const input=target.querySelector("textarea"),send=target.querySelector(".send"),stop=target.querySelector(".chat-stop");
  const progress=()=>target.querySelector(".chat-inner").children.filter(child=>child.className==="chat-activity");
  assert.equal(progress().length,1,"busy without provider activity still shows a single progress row");
  assert.equal(target.querySelector(".hint").textContent,"");assert.equal(target.querySelector(".chat-delivery-status").hidden,true);assert.equal(target.querySelector(".chat-interrupt-status").hidden,true);
  assert.equal(send.hidden,true);assert.equal(stop.hidden,false);assert.equal(stop.textContent,"");assert.equal(stop.getAttribute("aria-label"),"停止运行");
  input.value="先检查复现步骤";input.oninput();assert.equal(send.hidden,false);assert.equal(send.disabled,false);assert.equal(stop.hidden,false);
  target.querySelector("form").onsubmit({preventDefault(){}});assert.equal(send.disabled,true);await tick();
  accept({accepted:true,delivery:"steered"});await tick();h.flushFrames();
  assert.equal(calls.filter(call=>call.url==="/api/chat/send").length,1);assert.equal(input.value,"");assert.equal(send.hidden,true);
  assert.equal(progress().length,1);assert.equal(target.querySelector(".hint").textContent,"");assert.equal(target.querySelector(".chat-delivery-status").hidden,true);assert.equal(target.querySelector(".chat-interrupt-status").hidden,true);
  h.update("worker",{busy:false});h.flushFrames();assert.equal(progress().length,0);assert.equal(stop.hidden,true);assert.equal(send.hidden,false);h.chat.unmount();
});

test("a member page can show one task execution record and talk to that task",async()=>{
  const h=harness(),calls=[],toasts=[];let routed=0;
  const api=async(url,data)=>{calls.push([url,data]);if(url==="/api/chat/send")return {accepted:true,goal_id:"goal-a",execution_id:"exec-a"};if(url==="/api/chat/read")return {read_seq:data.seq};
    return {...history(),execution_id:"exec-a",events:[{seq:1,type:"message",id:"task",source:"crew",text:"计算 1 + 1"},{seq:2,type:"assistant",id:"reply",content:[{type:"text",text:"1 + 1 = 2"}]},{seq:3,type:"turn_result",status:"completed",success:true}]};};
  const target=h.mount("member",api,{},{goalId:"goal-a",goalTitle:"算术A",toast:text=>toasts.push(text),onRouted(){routed++;}});await tick();h.flushFrames();
  assert.match(calls[0][0],/goal_id=goal-a/);assert.match(h.connections[0].url,/goal_id=goal-a/);
  assert.match(rendered(target).join(""),/1 \+ 1 = 2/);
  assert.match(target.innerHTML,/placeholder="在任务「算术A」里跟 member 说…"/);
  h.chat.setTask(null);assert.match(target.querySelector("textarea").placeholder,/在任务「算术A」里跟 member 说/);
  target.querySelector("textarea").value="再算 2 + 2";target.querySelector("textarea").oninput();target.querySelector("form").onsubmit({preventDefault(){}});await tick();
  const sent=calls.find(([url])=>url==="/api/chat/send")[1];
  assert.equal(sent.session_id,"member");assert.equal(sent.goal_id,"goal-a");assert.equal(routed,0,"staying on the member page, not jumping to the leader task");
  assert(h.saved.has("crew.draft:fixture:member:goal:goal-a"),"the task record keeps its own draft");
  h.connections[0].emit({seq:4,type:"native_replaced",lost_native:"old",native_id:"new",text:"这是新开的原生会话：原会话 old 已丢失"});await tick();h.flushFrames();
  assert.match(rendered(target).join(""),/context-divider native-replaced[^>]*>这是新开的原生会话/);
  assert.deepEqual(toasts,["这是新开的原生会话：原会话 old 已丢失"]);
  h.chat.unmount();
  const general=h.mount("member",async url=>{calls.push([url]);return history();});await tick();
  assert.doesNotMatch(calls.at(-1)[0],/goal_id/);assert.match(general.innerHTML,/placeholder="跟 member 说…（可以直接粘贴截图）"/);h.chat.unmount();
});
