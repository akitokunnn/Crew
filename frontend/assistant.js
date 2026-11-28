"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let mounted=null, opened=false, loading=false, timer=null, toastTimer=null, assistantSession=null, note=null, notePrefill=false, discussion=null, lastState=null, stopped=false;
  // One page, two assistants: no query is the 系统管理员 (the HOME-wide assistant); ?project=X is workspace X's own assistant.
  const scope=window.CrewBrowser.pageProject(), PROJECT=scope.mode==="workspace"?scope.project:null;
  const projectQuery=PROJECT===null?"":`?project=${encodeURIComponent(PROJECT)}`;
  const NOTE_OPENING="帮我把这个想法想清楚";
  // An empty conversation offers a few starting points; picking one only writes it into the composer.
  const ICONS={recap:["M4 5h16v14H4z","M8 9h8M8 13h5"],note:["M4 20h4L19 9l-4-4L4 16v4Z","m13.5 6.5 4 4"],opening:["M12 3v7M5 10h14M5 10v5m14-5v5","M8 3h8v4H8zM2 15h6v5H2zm14 0h6v5h-6z"],task:["M9 6h11M9 12h11M9 18h11","m3 6 1.5 1.5L7 5M3 12l1.5 1.5L7 11M3 18l1.5 1.5L7 17"]};
  const ADMIN_GUIDE={title:"从哪里聊起？",text:"系统管理员看得到所有工作区。点一个示例填进输入框，改好再发送。",prompts:[
    {label:"汇总各工作区近况",detail:"在推进什么、卡在哪、等你处理什么",icon:ICONS.recap,text:"汇总一下各个工作区的近况：各自在推进什么、卡在哪里、有哪些事在等我处理。"},
    {label:"记一个想法",detail:"说清放进哪个工作区，之后再慢慢想",icon:ICONS.note,text:"帮我记一个想法：先问我放进哪个工作区，再记下来："},
    {label:"让助手开局",detail:"先对齐目标，再给出结构和首批任务",icon:ICONS.opening,text:"帮我给一个工作区开局：先问我是哪个工作区，再问 2–3 个关键问题，对齐目标、范围和优先级；然后给出领域划分、各领域的 leader 编制和首批任务清单。等我确认方案后再创建和启动，先不要直接动手。"},
    {label:"给 leader 建任务",detail:"说清要做成什么样，确认后再创建",icon:ICONS.task,text:"帮我给一个 leader 建任务：先问清是哪个工作区、交给哪位 leader、要做成什么样；整理出任务标题、要求和验收标准，等我确认后再创建。"}]};
  const WORKSPACE_GUIDE={title:"从哪里聊起？",text:"这位助手只管这个工作区。点一个示例填进输入框，改好再发送。",prompts:[
    {label:"理一理这里的近况",detail:"在推进什么、卡在哪、等你处理什么",icon:ICONS.recap,text:"理一理这个工作区的近况：在推进什么、卡在哪里、有哪些事在等我处理。"},
    {label:"记一个想法",detail:"先记进这个工作区，之后再慢慢想清楚",icon:ICONS.note,text:"帮我在这个工作区记一个想法："},
    {label:"让助手开局",detail:"先对齐目标，再给出结构和首批任务",icon:ICONS.opening,text:"帮我给这个工作区开局：先问 2–3 个关键问题，对齐目标、范围和优先级；然后给出领域划分、各领域的 leader 编制和首批任务清单。等我确认方案后再创建和启动，先不要直接动手。"},
    {label:"给 leader 建任务",detail:"说清要做成什么样，确认后再创建",icon:ICONS.task,text:"帮我给这个工作区的一个 leader 建任务：先问清交给哪位 leader、要做成什么样；整理出任务标题、要求和验收标准，等我确认后再创建。"}]};
  const systemTheme=matchMedia("(prefers-color-scheme: dark)");
  function theme(){let selected="system";if(!window.crewDesktopTheme)try{selected=localStorage.getItem("crew.theme")||selected;}catch{}document.documentElement.dataset.theme=selected==="system"?(systemTheme.matches?"dark":"light"):selected;}
  theme();systemTheme.addEventListener("change",theme);window.addEventListener("storage",theme);window.addEventListener("crew:theme-change",theme);
  const toast=message=>{const node=$("assistant-toast");node.textContent=message;node.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>{node.hidden=true;},5000);};
  document.addEventListener("crew:sound-error",event=>toast(event.detail));
  async function api(path,body) {
    const response = await fetch(path, body === undefined ? {method:"GET", headers:{"X-Crew":"1"}} : window.CrewBrowser.postOptions(body));
    const value=await response.json();
    // error_code marks a failure even on 202 (outcome_unknown); the other codes come with 404 / 409.
    if(!response.ok||value?.error_code){const failure=Error(value.error||`请求失败 (${response.status})`);failure.status=response.status;failure.body=value;throw failure;}
    return value;
  }
  function fatal(message) {
    stopped=true;clearTimeout(timer);
    const node=$("assistant-error");node.replaceChildren(document.createTextNode(message));node.hidden=false;
    const home=document.createElement("a");home.className="btn ghost";home.href="/";home.textContent="回到工作区总览";node.append(home);
    $("assistant-chat").replaceChildren();
  }
  const displayName=state=>state?.project?.display_name||state?.project?.name||PROJECT;
  // Page title and header follow the mode; a workspace page speaks for that workspace only.
  function header(state) {
    if(PROJECT===null){document.title="系统管理员 · Crew";return;}
    $("assistant-persona").hidden=false;
    $("assistant-persona").onclick=()=>CrewPersona.open({session_id:assistantSession,name:displayName(state)+" · 工作区助手",request:CrewPersona.webRequest(api)});
    const name=displayName(state);
    $("assistant-title").textContent="助手";$("assistant-scope").textContent=name;$("assistant-scope").hidden=false;
    $("assistant-sub").textContent=`「${name}」的助手：只看这个工作区，帮你理清想法、开局和推进。`;
    $("assistant-context-title").textContent="这里的事";$("assistant-overview-open").textContent="这里的事";$("assistant-context-sub").textContent="本区的待办与近况";
    document.title=`助手 · ${name} · Crew`;
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
      link.href=url.href;link.textContent=label(item);
      // A workspace page lists only its own items; naming the workspace on each would repeat the title.
      if(PROJECT===null){const detail=document.createElement("small");detail.textContent=name;link.append(detail);}
      node.append(link);
    }
    return node;
  }
  function overview(state) {
    window.CrewGlobalRail?.update({projects: state.projects || [], todos: state.todos || [], goals: state.goals || []});
    // A workspace page lists only its own work; anything carrying another project is left out.
    const own=item=>PROJECT===null||item.project==null||item.project===PROJECT,where=item=>item.project||PROJECT;
    const node=$("assistant-overview");node.replaceChildren(
      section("我的 TODO",(state.todos||[]).filter(item=>(item.status==null||item.status==="open")&&own(item)),item=>item.title||item.text||"待办",where,"暂时没有需要你动手的事项。"),
      section("进行中的任务",(state.goals||[]).filter(item=>!["completed","cancelled"].includes(item.status)&&own(item)),item=>item.title,where,PROJECT===null?"各工作区暂时没有进行中的任务。":"这个工作区暂时没有进行中的任务。"),
      ...(PROJECT===null?[section("工作区",state.projects||[],item=>item.display_name||item.name,item=>item.name,"先打开一个文件夹，开始项目。")]:[])
    );
  }
  // High-risk operations an AI asked for wait here until the user confirms or rejects them.
  const ROLE_LABELS={assistant:"系统管理员",workspace_assistant:"助手",leader:" leader ",execution:"任务执行",git_admin:" Git 助手"};
  function argumentLine({args,arg_labels:labels}){
    return Object.entries(args).filter(([key])=>key!=="request_id").map(([key,value])=>
      `${Object.hasOwn(labels,key)?labels[key]:key}：${typeof value==="string"?value:JSON.stringify(value)}`).join("，");
  }
  const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const tokens=n=>n>=1e8?`${+(n/1e8).toFixed(2)} 亿`:n>=1e4?`${+(n/1e4).toFixed(1)} 万`:String(n);
  const RUNTIMES={claude:"Claude",codex:"Codex"};
  const runtime=value=>`<span class="ac-rt" data-rt="${esc(value)}">${RUNTIMES[value]||"未知载体"}</span>`;
  // An opening plan and a war-room budget raise are shown from what they will do, read once per request: the plan
  // through campaign.preview (read-only), the room through war_room.get.
  const extras=new Map(), resolving=new Set();
  const confirmationDialog=$("assistant-confirmation-dialog"), confirmationTrigger=$("assistant-confirmation-trigger");
  let shown=null, pendingRequests=[], confirmationRead=0;
  function openConfirmations(){
    if(confirmationDialog.open)return;
    $("assistant-confirmation-status").textContent="";
    confirmationDialog.showModal();
  }
  confirmationTrigger.onclick=openConfirmations;
  function confirmationFeedback(message){
    if(confirmationDialog.open)$("assistant-confirmation-status").textContent=message;
    else toast(message);
  }
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
    const leaders=sec("新 leader",plan.leaders.length,`<ul class="ac-list">${plan.leaders.map(l=>`<li><div class="ac-row"><b>${esc(l.name)}</b>${runtime(l.runtime||"claude")}<span class="ac-model">${esc(l.model||"默认模型")}${l.effort?` · ${esc(l.effort)}`:""}</span>${l.lead?'<span class="ac-tag">负责</span>':""}<small>${l.part==null?"不放进领域":`放进 ${esc(l.part_name||names.get(l.part)||l.part)}`}</small></div>${Array.isArray(l.owns)?`<p class="ac-owns"><span>只改</span>${l.owns.map(path=>`<code>${esc(path)}</code>`).join("")}</p>`:""}<details class="ac-brief"><summary>${esc(l.brief)}</summary></details></li>`).join("")}</ul>`);
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
    pendingRequests=requests;
    const key=JSON.stringify(requests.map(item=>item.id));if(key===shown)return;shown=key;
    for(const id of extras.keys())if(!requests.some(item=>item.id===id))extras.delete(id);
    const node=$("assistant-confirmation-cards");node.replaceChildren();
    confirmationTrigger.hidden=!requests.length;
    if(!requests.length){if(confirmationDialog.open)confirmationDialog.close();return;}
    const label=`等你确认 (${requests.length})`;
    confirmationTrigger.textContent=label;
    $("assistant-confirmation-heading").textContent=label;
    for(const item of requests){
      const special=SPECIAL[item.op],card=document.createElement("article");card.className=`assistant-confirmation${special?" assistant-confirmation-rich":""}`;
      const title=document.createElement("strong");title.textContent=special?special[0]:item.summary;
      const meta=document.createElement("p");meta.textContent=`由${ROLE_LABELS[item.role]||item.role}发起${item.project?` · 工作区 ${item.project}`:""}`;card.append(title,meta);
      if((item.op==="campaign.launch"||item.op==="domains.restructure.apply")&&item.args.note){const note=document.createElement("p");note.className="ac-note";note.textContent=`方案说明：${item.args.note}`;card.append(note);}
      const detail=document.createElement(special?"div":"p");detail.className=special?"ac-detail":"assistant-confirmation-args";
      if(special){detail.innerHTML='<p class="ac-quiet">正在读取…</p>';void lookup(item).then(result=>{detail.innerHTML=special[1](item,result);});}
      else detail.textContent=argumentLine(item)||"无参数";
      const actions=document.createElement("div");actions.className="assistant-confirmation-actions";
      for(const [label,approve,kind] of [["确认执行",true,"btn primary"],["拒绝",false,"btn ghost"]]){
        const button=document.createElement("button");button.type="button";button.className=kind;button.textContent=label;button.disabled=resolving.has(item.id);
        button.onclick=async()=>{
          if(button.disabled||resolving.has(item.id))return;
          resolving.add(item.id);
          for(const other of actions.querySelectorAll("button"))other.disabled=true;
          let result;
          try{result=await api("/api/op/interaction.resolve",{id:item.id,approve,...(PROJECT===null?{}:{project:PROJECT})});}
          catch(failure){
            resolving.delete(item.id);
            shown=null;confirmations(pendingRequests);
            confirmationFeedback(failure.message);
            return;
          }
          resolving.delete(item.id);
          ++confirmationRead; // An older list response can still include the request that just completed.
          confirmations(pendingRequests.filter(request=>request.id!==item.id));
          confirmationFeedback(result.status==="approved"?"已确认并执行":result.status==="rejected"?"已拒绝":`执行失败：${result.error}`);
        };
        actions.append(button);
      }
      card.append(detail,actions);node.append(card);
    }
    const unseen=requests.map(item=>`crew.confirmation.seen.${item.id}`).filter(id=>localStorage.getItem(id)===null);
    if(unseen.length){
      openConfirmations();
      for(const id of unseen)localStorage.setItem(id,"1");
    }
  }
  // The administrator keeps the HOME-wide inbox; a workspace page only sees and resolves its own workspace's cards.
  async function loadConfirmations(){
    const read=++confirmationRead;
    try{
      const {requests}=await (PROJECT===null?api("/api/op/interaction.list",{}):api(`/api/op/interaction.list${projectQuery}`));
      if(read===confirmationRead)confirmations(requests);
    }catch(failure){if(read===confirmationRead)confirmationFeedback(`确认请求读取失败：${failure.message}`);}
  }
  async function refresh() {
    if(loading || stopped || window.CrewAccess?.expired)return;loading=true;clearTimeout(timer);
    try {
      if(!opened){await api("/api/op/assistant.open",PROJECT===null?{}:{project:PROJECT});opened=true;}
      const state=await api(`/api/op/assistant.state${projectQuery}`);assistantSession=state.assistant.session_id;CrewSound.observe(state.sounds,state.now);header(state);overview(state);error("");
      const identity=state.assistant.session_id;lastState=state;
      if(mounted!==identity){CrewChat.unmount();CrewChat.mount({host:$("assistant-chat"),session:PROJECT===null?{...state.assistant,name:"系统管理员"}:state.assistant,state,api,toast,onRouted:()=>{},onNoteCleared:dropNote,emptyGuide:PROJECT===null?ADMIN_GUIDE:WORKSPACE_GUIDE});mounted=identity;attachNote();}
      else CrewChat.update(state);
      attachDiscussion();
      await loadConfirmations();
    }catch(failure){
      // An unknown or removed workspace is a dead link: say so instead of retrying or opening the administrator.
      if(PROJECT!==null&&(failure.body?.error_code==="project_unavailable"||failure.status===404&&!opened)){fatal(`工作区「${PROJECT}」不存在或已移除，这个助手打不开。`);return;}
      error(failure.message||"暂时连不上助手");
    }
    finally{loading=false;if(!stopped)timer=setTimeout(refresh,4000);}
  }
  window.addEventListener("pagehide",()=>{clearTimeout(timer);CrewChat.unmount();});
  // "去聊聊" on the idea wall opens #note=<id>: attach the idea above the
  // composer and prefill an opening line; nothing is sent until the user sends.
  function attachNote(){if(mounted===null||note===null)return;CrewChat.setNote(note,notePrefill?NOTE_OPENING:null);notePrefill=false;}
  function dropNote(){note=null;notePrefill=false;if(new URLSearchParams(location.hash.slice(1)).has("note"))history.replaceState(null,"",location.pathname+location.search);}
  async function readNote(){
    const id=new URLSearchParams(location.hash.slice(1)).get("note");
    if(!id)return;
    // Ideas belong to workspaces: a new discussion happens with the idea's own workspace assistant.
    if(PROJECT===null){toast("想法要在它所属工作区的助手里讨论，请从那个工作区的想法页点“去聊聊”。");dropNote();return;}
    let found;
    try{const result=await api(`/api/op/note.get?project=${encodeURIComponent(PROJECT)}&note_id=${encodeURIComponent(id)}`);found=result.note||result;}
    catch(failure){toast(failure.status===404?"这条想法不在这个工作区，或已被删除。":`想法读取失败：${failure.message}`);dropNote();return;}
    if(!found?.id){toast("没有找到这条想法，可能已被删除。");dropNote();return;}
    note={id,title:found.title||found.text.split("\n").map(line=>line.trim()).find(Boolean)};notePrefill=true;attachNote();
  }
  // A past discussion (#discussion=<session>) reopens the conversation it happened in: this assistant,
  // or the administrator for discussions held there before ideas moved into workspaces.
  function readDiscussion(){const id=new URLSearchParams(location.hash.slice(1)).get("discussion");if(id){discussion=id;attachDiscussion();}}
  function attachDiscussion(){
    if(discussion===null||mounted===null||lastState===null)return;
    const id=discussion;discussion=null;
    if(id===lastState.assistant.session_id){history.replaceState(null,"",location.pathname+location.search);toast("这是那条想法讨论过的对话。");return;}
    if(PROJECT!==null){location.replace("/assistant.html#discussion="+encodeURIComponent(id));return;}
    history.replaceState(null,"",location.pathname+location.search);toast("原讨论所在的对话已不在，可以从想法页重新去聊聊。");
  }
  window.addEventListener("hashchange",()=>{void readNote();readDiscussion();});
  $("assistant-prompts").onclick=()=>{const dialog=document.createElement("dialog");dialog.className="personal-notes-dialog assistant-prompts-dialog";dialog.innerHTML=`<header><h2>${PROJECT===null?"系统管理员行为配置":"助手行为配置"}</h2><button class="btn ghost" data-close aria-label="关闭行为配置">×</button></header><div data-prompts></div>`;document.body.append(dialog);dialog.showModal();const cleanup=CrewBehaviorPrompts.mount(dialog.querySelector('[data-prompts]'),{...window.CrewBehaviorPrompts.requests(api),session:assistantSession,...(PROJECT===null?{}:{project:PROJECT})});const close=()=>{cleanup();dialog.close();dialog.remove();};dialog.querySelector('[data-close]').onclick=close;dialog.oncancel=event=>{event.preventDefault();close();};};
  // 随手记 writes into this workspace; on the administrator page it asks which workspace first.
  $("assistant-notes").onclick=()=>CrewPersonalNotes.create({api,project:PROJECT,projects:()=>lastState?.projects||null}).open();
  // 运维: the desktop shows its native 系统管理员 · 运维 page; a browser opens the Web operations panel.
  $("assistant-operations").onclick=()=>{
    if(window.crewDesktopShell?.openOperations){void window.crewDesktopShell.openOperations().catch(failure=>toast(`没能打开运维页：${failure.message}`));return;}
    location.href="/#settings=backend";
  };
  (async()=>{
    if(scope.mode==="invalid"){fatal(`${scope.reason}，打不开助手。`);return;}
    if(window.CrewAccess && !await CrewAccess.ready)return;
    $("assistant-prompts").hidden=Boolean(window.CrewAccess&&!CrewAccess.local_controls);
    // Host operations need this computer (or the desktop's own page); a LAN browser without them does not see 运维.
    $("assistant-operations").hidden=PROJECT!==null||!window.crewDesktopShell&&Boolean(window.CrewAccess&&!(CrewAccess.host_admin&&CrewAccess.local_controls));
    CrewChat.initFiles(api,toast);readDiscussion();void refresh();void readNote();
  })();
})();
