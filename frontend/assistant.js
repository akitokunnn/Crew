"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let mounted=null, opened=false, loading=false, timer=null, toastTimer=null, assistantSession=null, note=null, notePrefill=false, opening=null, lastState=null;
  const NOTE_OPENING="帮我把这个想法想清楚";
  // "助手开局" on a blank workspace opens #opening=<project>: the intent is written into the composer, nothing is sent.
  const OPENING=name=>`我想在工作区「${name}」开局，请你来起盘：先问我 2–3 个关键问题，对齐目标、范围和优先级；再给出领域划分、各领域的 leader 编制（载体与模型）和首批任务清单。等我确认方案后再创建和启动，先不要直接动手。`;
  const systemTheme=matchMedia("(prefers-color-scheme: dark)");
  function theme(){let selected="system";if(!window.crewDesktopTheme)try{selected=localStorage.getItem("crew.theme")||selected;}catch{}document.documentElement.dataset.theme=selected==="system"?(systemTheme.matches?"dark":"light"):selected;}
  theme();systemTheme.addEventListener("change",theme);window.addEventListener("storage",theme);window.addEventListener("crew:theme-change",theme);
  const toast=message=>{const node=$("assistant-toast");node.textContent=message;node.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>{node.hidden=true;},5000);};
  document.addEventListener("crew:sound-error",event=>toast(event.detail));
  async function api(path,body) {
    const response = await fetch(path, body === undefined ? {method:"GET", headers:{"X-Crew":"1"}} : window.CrewBrowser.postOptions(body));
    const value=await response.json();if(!response.ok)throw Error(value.error||`请求失败 (${response.status})`);return value;
  }
  function error(message) {
    const node=$("assistant-error");node.replaceChildren();node.hidden=!message;
    if(message){node.append(document.createTextNode(message));const retry=document.createElement("button");retry.className="btn ghost";retry.textContent="重试";retry.onclick=()=>{void refresh();};node.append(retry);}
  }
  function section(title,items,label,projectName,empty) {
    const node=document.createElement("section"),heading=document.createElement("h3");heading.textContent=title;node.append(heading);
    if(!items.length){const quiet=document.createElement("p");quiet.className="quiet";quiet.textContent=empty;node.append(quiet);}
    for(const item of items.slice(0,12)){
      const name=projectName(item);if(!name)continue;
      const link=document.createElement("a"),url=new URL("/",location.href);url.searchParams.set("project",name);
      if(item.leader_session_id)url.hash=new URLSearchParams({leader:item.leader_session_id,...(item.id&&title==="进行中的任务"?{goal:item.id}:{})}).toString();
      link.href=url.href;link.textContent=label(item);const detail=document.createElement("small");detail.textContent=name;link.append(detail);node.append(link);
    }
    return node;
  }
  function overview(state) {
    window.CrewGlobalRail?.update({projects: state.projects || [], todos: state.todos || [], goals: state.goals || []});
    const node=$("assistant-overview");node.replaceChildren(
      section("我的 TODO",(state.todos||[]).filter(item=>item.status==null||item.status==="open"),item=>item.title||item.text||"待办",item=>item.project,"暂时没有需要你动手的事项。"),
      section("进行中的任务",(state.goals||[]).filter(item=>!["completed","cancelled"].includes(item.status)),item=>item.title,item=>item.project,"你可以让我汇总项目近况，或记下新想法。"),
      section("工作区",state.projects||[],item=>item.display_name||item.name,item=>item.name,"先打开一个文件夹，开始项目。")
    );
  }
  // High-risk operations an AI asked for wait here until the user confirms or rejects them.
  const ROLE_LABELS={assistant:"助手",leader:" leader ",execution:"任务执行",git_admin:" Git 助手"};
  function argumentLine(args){return Object.entries(args||{}).filter(([key])=>key!=="request_id").map(([key,value])=>`${key}: ${typeof value==="string"?value:JSON.stringify(value)}`).join("，");}
  const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const tokens=n=>n>=1e8?`${+(n/1e8).toFixed(2)} 亿`:n>=1e4?`${+(n/1e4).toFixed(1)} 万`:String(n);
  const RUNTIMES={claude:"Claude",codex:"Codex"};
  const runtime=value=>`<span class="ac-rt" data-rt="${esc(value)}">${RUNTIMES[value]||"未知载体"}</span>`;
  // An opening plan and a war-room budget raise are shown from what they will do, read once per request: the plan
  // through campaign.preview (read-only), the room through war_room.get.
  const extras=new Map();let shown=null;
  function lookup(item){
    if(!extras.has(item.id)){
      const {request_id,board_version,...plan}=item.args,project=item.args.project||item.project;
      const read=item.op==="campaign.launch"?api("/api/op/campaign.preview",{...plan,project}).then(preview=>({preview}))
        :RESTRUCTURE.has(item.op)?api("/api/op/domains.restructure.preview",{project,changes:planChanges(item)}).then(preview=>({preview}))
        :api(`/api/op/war_room.get?${new URLSearchParams({id:item.args.room,project,limit:1})}`).then(room=>({room}));
      extras.set(item.id,read.catch(failure=>({failure:failure.message})));
    }
    return extras.get(item.id);
  }
  function costHTML(cost){
    if(!cost)return '<section class="ac-cost" aria-label="会花什么"><h4>会花什么</h4><p class="ac-cost-note">费用预览没读到，列不出要启动的模型和预算。</p></section>';
    const e=cost.estimate,carriers=cost.carriers.map(c=>`<li>${runtime(c.runtime)}<span>${esc(c.model)} · ${esc(c.effort)}</span><b>× ${c.count}</b></li>`).join("");
    const budget=!cost.start?"这次不开作战室":cost.budget_tokens?`<b>${tokens(cost.budget_tokens)} tokens</b>，到了暂停等你确认`:"作战室不设上限";
    const estimate=!cost.start||!cost.budget_tokens?"":e?`约 <b>$${e.low_usd} – $${e.high_usd}</b><small>按 ${e.priced_models.map(esc).join("、")} 的价格估，预算用完时的范围</small>`:"没有价格表，无法估算金额";
    return `<section class="ac-cost" aria-label="会花什么"><h4>会花什么</h4><p class="ac-cost-note">${esc(cost.note)}</p>${cost.start?"":'<p class="ac-flag">只建架构图，不启动模型</p>'}`
      +`<dl class="ac-facts"><dt>新建 leader</dt><dd><b>${cost.new_leaders} 位</b></dd><dt>载体 / 模型</dt><dd><ul class="ac-carriers">${carriers}</ul></dd><dt>作战室预算</dt><dd>${budget}</dd>${estimate?`<dt>估算金额</dt><dd>${estimate}</dd>`:""}</dl></section>`;
  }
  function campaignHTML(item,{preview,failure}){
    const args=item.args,plan=preview||{parts:args.parts||[],leaders:args.leaders||[],goals:args.goals||[],room:args.room||null,cost:null,errors:[]};
    const problems=failure?[`没能读取开局预览：${failure}`]:plan.errors;
    const alert=problems.length?`<div class="ac-problems" role="alert"><strong>${failure?"预览没读到，下面按 AI 提交的原始方案显示":`方案还有 ${problems.length} 处问题`}</strong><ul>${problems.map(text=>`<li>${esc(text)}</li>`).join("")}</ul><small>仍可点“确认执行”，后端会再校验；有问题会拒绝执行并告诉发起的 AI。</small></div>`:"";
    const open=matchMedia("(min-width:761px)").matches?" open":"",names=new Map(plan.leaders.map(l=>[l.part,l.part_name]).concat(plan.parts.map(p=>[p.ref,p.name])));
    const sec=(title,count,body)=>`<details class="ac-sec"${open}><summary>${title}<span class="ac-count">${count}</span></summary>${body}</details>`;
    const parts=plan.parts.length?sec("新领域",plan.parts.length,`<ul class="ac-list">${plan.parts.map(p=>`<li><div class="ac-row"><b>${esc(p.name)}</b><small>${p.parent==null?"顶层":`挂在 ${esc(names.get(p.parent)||`已有领域 ${p.parent}`)} 下`}</small></div></li>`).join("")}</ul>`):"";
    const leaders=sec("新 leader",plan.leaders.length,`<ul class="ac-list">${plan.leaders.map(l=>`<li><div class="ac-row"><b>${esc(l.name)}</b>${runtime(l.runtime||"claude")}<span class="ac-model">${esc(l.model||"默认模型")}${l.effort?` · ${esc(l.effort)}`:""}</span>${l.lead?'<span class="ac-tag">负责</span>':""}<small>${l.part==null?"不放进领域":`放进 ${esc(l.part_name||names.get(l.part)||l.part)}`}</small></div><details class="ac-brief"><summary>${esc(l.brief)}</summary></details></li>`).join("")}</ul>`);
    const goals=plan.goals.length?sec("首批任务",plan.goals.length,`<ul class="ac-list">${plan.goals.map(g=>{const checks=Array.isArray(g.acceptance)?g.acceptance:[];return `<li><details class="ac-goal"><summary><b>${esc(g.title)}</b><small>${esc(g.leader)} 负责 · 验收标准 ${checks.length} 条</small></summary><p>${esc(g.request)}</p><ol>${checks.map(text=>`<li>${esc(text)}</li>`).join("")}</ol></details></li>`;}).join("")}</ul>`):"";
    const room=plan.room&&plan.cost?.start!==false?sec("作战室",1,`<dl class="ac-facts"><dt>标题</dt><dd><b>${esc(plan.room.title)}</b></dd><dt>目标</dt><dd>${esc(plan.room.goal)}</dd><dt>牵头</dt><dd>${esc((ROLE_LABELS[item.role]||item.role).trim())}牵头，新建的 ${plan.leaders.length} 位 leader 都是成员</dd></dl>`):"";
    return alert+costHTML(plan.cost)+`<div class="ac-build"><h4>建什么</h4>${parts}${leaders}${goals}${room}</div>`;
  }
  function budgetHTML(item,{room,failure}){
    const b=room?.budget;
    return `<dl class="ac-facts"><dt>作战室</dt><dd>#${esc(item.args.room)}${room?` 《${esc(room.title)}》${room.status==="closed"?" · 已收尾":""}`:""}</dd>`
      +(b?`<dt>已用 / 原上限</dt><dd>${tokens(b.spent)} / ${tokens(b.tokens)} tokens${b.paused_at?'<span class="ac-tag stop">已暂停</span>':""}</dd>`:room?"<dt>原上限</dt><dd>没设预算</dd>":"")
      +`<dt>新上限</dt><dd><b>${tokens(item.args.budget_tokens)} tokens</b></dd></dl>${failure?`<p class="ac-quiet">作战室详情没读到：${esc(failure)}</p>`:""}`;
  }
  // A restructure (or a one-change merge / retire) is shown as the board's own before / after, its effects on leaders and
  // tasks, and whether it wakes anyone; the preview is read again now, so a plan gone stale already says so here.
  const RESTRUCTURE=new Set(["domains.restructure.apply","leader.merge","leader.retire"]);
  function planChanges(item){
    const a=item.args;
    return item.op==="domains.restructure.apply"?a.changes:[{type:item.op,leader:a.leader,[item.op==="leader.merge"?"into":"successor"]:item.op==="leader.merge"?a.into:a.successor}];
  }
  function restructureHTML(item,{preview,failure}){
    if(failure)return `<div class="ac-problems" role="alert"><strong>没能读取重整预览</strong><ul><li>${esc(failure)}</li></ul><small>仍可点“确认执行”，后端会再校验。</small></div>`;
    if(!preview.ok)return `<div class="ac-problems" role="alert"><strong>方案现在执行不了（${preview.errors.length} 处问题）</strong><ul>${preview.errors.map(e=>`<li>${esc(e.message||e)}</li>`).join("")}</ul><small>多半是预览之后情况变了；可以拒绝，让助手按新情况重新预览。</small></div>`;
    const R=window.CrewRestructure,handoffs=item.op==="domains.restructure.apply"?item.args.handoffs||[]:item.args.handoff?[item.args.handoff]:[];
    const wakes=handoffs.filter(h=>h.wake).length+preview.goals.filter(g=>g.wakes).length;
    const effects=preview.leaders.length?`<ul class="ac-list">${preview.leaders.map(l=>`<li><div class="ac-row"><b>${esc(l.name)}</b><small>${esc(l.effects.join("；"))}</small></div></li>`).join("")}</ul>`:"";
    const goals=preview.goals.length?`<ul class="ac-list">${preview.goals.map(g=>`<li><div class="ac-row"><span class="ac-tag">${g.action==="cancel"?"作废":"改派"}</span><b>${esc(g.title)}</b><small>${esc(g.leader.name)} 负责${g.to?` → ${esc(g.to.name)}`:""}</small></div></li>`).join("")}</ul>`:"";
    return `<section class="ac-cost" aria-label="会动什么"><h4>会动什么</h4><p class="ac-cost-note">${esc(R.diffSummary(preview.diff)||"没有结构变化")}${preview.archived.length?` · 归档 ${preview.archived.map(a=>esc(a.name)).join("、")}（原会话保留）`:""}</p>`
      +`<p class="ac-flag">${wakes?`执行后会唤醒 ${wakes} 处，可能产生用量`:"执行不会启动任何模型"}；存成一个架构图版本，可以回滚</p></section>`
      +`<div class="ac-build"><h4>改前 / 改后</h4>${R.compare(preview.before,preview.after,{beforeTitle:"改前 · 现在"})}`
      +(effects?`<details class="ac-sec" open><summary>受影响的 leader<span class="ac-count">${preview.leaders.length}</span></summary>${effects}</details>`:"")
      +(goals?`<details class="ac-sec" open><summary>任务<span class="ac-count">${preview.goals.length}</span></summary>${goals}</details>`:"")
      +(handoffs.length?`<p class="ac-quiet">交接稿 ${handoffs.length} 份已随请求附上（${handoffs.map(h=>Array.from(h.text||"").length+" 字").join("、")}）。</p>`:"")+"</div>";
  }
  const SPECIAL={"campaign.launch":["开局方案",campaignHTML],"war_room.budget":["追加作战室预算",budgetHTML],
    "domains.restructure.apply":["架构重整",restructureHTML],"leader.merge":["合并 leader",restructureHTML],"leader.retire":["退役 leader",restructureHTML]};
  function confirmations(requests){
    const key=requests.map(item=>item.id).join();if(key===shown)return;shown=key;
    for(const id of extras.keys())if(!requests.some(item=>item.id===id))extras.delete(id);
    const node=$("assistant-confirmations");node.replaceChildren();node.hidden=!requests.length;
    if(!requests.length)return;
    const heading=document.createElement("h2");heading.textContent=`等你确认（${requests.length}）`;node.append(heading);
    for(const item of requests){
      const special=SPECIAL[item.op],card=document.createElement("article");card.className=`assistant-confirmation${special?" assistant-confirmation-rich":""}`;
      const title=document.createElement("strong");title.textContent=special?special[0]:item.summary;
      const meta=document.createElement("p");meta.textContent=`由${ROLE_LABELS[item.role]||item.role}发起${item.project?` · 工作区 ${item.project}`:""}`;card.append(title,meta);
      if((item.op==="campaign.launch"||item.op==="domains.restructure.apply")&&item.args.note){const note=document.createElement("p");note.className="ac-note";note.textContent=`方案说明：${item.args.note}`;card.append(note);}
      const detail=document.createElement(special?"div":"p");detail.className=special?"ac-detail":"assistant-confirmation-args";
      if(special){detail.innerHTML='<p class="ac-quiet">正在读取…</p>';void lookup(item).then(result=>{detail.innerHTML=special[1](item,result);});}
      else detail.textContent=argumentLine(item.args)||"无参数";
      const actions=document.createElement("div");actions.className="assistant-confirmation-actions";
      for(const [label,approve,kind] of [["确认执行",true,"btn primary"],["拒绝",false,"btn ghost"]]){
        const button=document.createElement("button");button.className=kind;button.textContent=label;
        button.onclick=async()=>{for(const other of actions.querySelectorAll("button"))other.disabled=true;
          try{const result=await api("/api/op/interaction.resolve",{id:item.id,approve});
            toast(result.status==="approved"?"已确认并执行":result.status==="rejected"?"已拒绝":`执行失败：${result.error}`);}
          catch(failure){toast(failure.message);}
          shown=null;await loadConfirmations();};
        actions.append(button);
      }
      card.append(detail,actions);node.append(card);
    }
  }
  async function loadConfirmations(){try{confirmations((await api("/api/op/interaction.list",{})).requests);}catch(failure){toast(`确认请求读取失败：${failure.message}`);}}
  async function refresh() {
    if(loading || window.CrewAccess?.expired)return;loading=true;clearTimeout(timer);
    try {
      if(!opened){await api("/api/op/assistant.open",{});opened=true;}
      const state=await api("/api/op/assistant.state");assistantSession=state.assistant.session_id;CrewSound.observe(state.sounds,state.now);overview(state);error("");
      await loadConfirmations();
      const identity=state.assistant.session_id;lastState=state;
      if(mounted!==identity){CrewChat.unmount();CrewChat.mount({host:$("assistant-chat"),session:state.assistant,state,api,toast,onRouted:()=>{},onNoteCleared:dropNote});mounted=identity;attachNote();}
      else CrewChat.update(state);
      attachOpening();
    }catch(failure){error(failure.message||"暂时连不上助手");}
    finally{loading=false;timer=setTimeout(refresh,4000);}
  }
  window.addEventListener("pagehide",()=>{clearTimeout(timer);CrewChat.unmount();});
  // "去聊聊" on the idea wall opens #note=<id>: attach the idea above the
  // composer and prefill an opening line; nothing is sent until the user sends.
  function attachNote(){if(mounted===null||note===null)return;CrewChat.setNote(note,notePrefill?NOTE_OPENING:null);notePrefill=false;}
  function dropNote(){note=null;notePrefill=false;if(new URLSearchParams(location.hash.slice(1)).has("note"))history.replaceState(null,"",location.pathname+location.search);}
  async function readNote(){
    const id=new URLSearchParams(location.hash.slice(1)).get("note");
    if(!id)return;
    let result;
    try{result=await api("/api/op/notes.list?include_archived=1");}catch(failure){toast(`想法读取失败：${failure.message}`);return;}
    const found=result.notes.find(item=>item.id===id);
    if(!found){toast("没有找到这条想法，可能已被删除。");dropNote();return;}
    note={id,title:found.title||found.text.split("\n").map(line=>line.trim()).find(Boolean)};notePrefill=true;attachNote();
  }
  function readOpening(){const project=new URLSearchParams(location.hash.slice(1)).get("opening");if(project){opening=project;attachOpening();}}
  function attachOpening(){
    if(opening===null||mounted===null||lastState===null)return;
    const name=opening,found=(lastState.projects||[]).find(item=>item.name===name);opening=null;
    history.replaceState(null,"",location.pathname+location.search);
    if(!found){toast(`没有找到工作区「${name}」，可能已被移除。`);return;}
    if(!CrewChat.prefill(OPENING(found.display_name||found.name)))toast("输入框里有未发送的内容，开局说明没有覆盖它；发送或清空后再点一次“让助手开局”。");
  }
  window.addEventListener("hashchange",()=>{void readNote();readOpening();});
  $("assistant-prompts").onclick=()=>{const dialog=document.createElement("dialog");dialog.className="personal-notes-dialog assistant-prompts-dialog";dialog.innerHTML='<header><h2>助手行为配置</h2><button class="btn ghost" data-close aria-label="关闭行为配置">×</button></header><div data-prompts></div>';document.body.append(dialog);dialog.showModal();const cleanup=CrewBehaviorPrompts.mount(dialog.querySelector('[data-prompts]'),{...window.CrewBehaviorPrompts.requests(api),session:assistantSession});const close=()=>{cleanup();dialog.close();dialog.remove();};dialog.querySelector('[data-close]').onclick=close;dialog.oncancel=event=>{event.preventDefault();close();};};
  $("assistant-notes").onclick=()=>CrewPersonalNotes.create({api}).open();
  (async()=>{if(window.CrewAccess && !await CrewAccess.ready)return;$("assistant-prompts").hidden=Boolean(window.CrewAccess&&!CrewAccess.local_controls);CrewChat.initFiles(api,toast);readOpening();void refresh();void readNote();})();
})();
