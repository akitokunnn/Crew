"use strict";
// Human decisions and manual actions own their drafts, expanded state and commands.
// The host supplies snapshots, I/O and shared presentation primitives only.
(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
  function todoResolvePayload(item, draft) {
    if (!item || !draft) throw new Error("这件待办已更新，请刷新后查看。");
    const note = draft.note.trim();
    if (draft.status === "cancelled" && note === "") throw new Error("请写一下不做的原因。");
    if (!["done", "cancelled"].includes(draft.status)) throw new Error("待办状态无效。");
    return {id:item.id, status:draft.status, note, expected_version:draft.version};
  }
  function create(options) {
    let disposed = false;
    const state = options.state, api = (...args) => options.api(...args), toast = (...args) => { if (!disposed) options.notify(...args); };
    const changed = () => { if (!disposed) options.changed(); }, rich = options.rich;
    const refresh = () => disposed ? Promise.resolve() : options.refresh();
    const {icon:ic, avatar:av, priority:priorityBadge, date, ago, empty} = options.ui;
    const doc = options.document || document;
    const sideDecisionsOpen = new Set(), decisionsOpen = new Set(), heldExpanded = new Set();
    const todoExpanded = new Set(), todoActions = new Map(), todoSaving = new Set(), operations = new Set();
    const leader = id => state()?.leaders.find(item => item.session_id === id);
    const member = id => state()?.members.find(item => item.session_id === id);
    const decisionsOf = id => (state()?.decisions || []).filter(item => item.leader_session_id === id || item.session_id === id);
    const pendingOf = id => decisionsOf(id).filter(item => !item.held);
    const openTodos = () => (Array.isArray(state()?.todos) ? state().todos : []).filter(item => item.status === undefined || item.status === "open");
    const todoById = id => openTodos().find(item => String(item.id) === String(id));
    const todosOf = id => openTodos().filter(item => item.leader_session_id === id || item.session_id === id);
    const decisionTrust = item => (leader(item.leader_session_id) || leader(item.session_id))?.trust || {enabled:false,goal:"",updated_at:null};
    const decisionPriority = item => leader(item.leader_session_id)?.priority ?? null;
    async function run(key, operation) {
      if (operations.has(key)) return;
      operations.add(key); try { return await operation(); } finally { operations.delete(key); }
    }
  function compactDecisionText(value, limit = 88) {
    const firstLine = String(value || "").split(/\r?\n/).find(line => line.trim() !== "") || "待你拍板";
    const text = firstLine.trim().replace(/\s+/g, " "), characters = Array.from(text);
    return characters.length > limit ? characters.slice(0, limit).join("") + "…" : text;
  }
  function decisionOptions(item) {
    const brief = item.brief && typeof item.brief === "object" ? item.brief : null;
    return (item.options || []).map((original, index) => {
      const option = brief?.options?.[index], key = String.fromCharCode(65 + index);
      return {label:option?.label || compactDecisionText(String(original).replace(/^[A-Z][.、．]\s*/, ""), 80),
        consequence:option?.consequence || null, index,
        recommended:item.recommendation != null && item.recommendation !== "" && (item.recommendation === key || item.recommendation === original || item.recommendation === option?.label)};
    });
  }
  function decisionPreview(value, sessionId, limit = 280) {
    const text = String(value || "").replace(/\s+/g, " "), characters = Array.from(text);
    return rich.inline(characters.length > limit ? characters.slice(0, limit).join("") + "…" : text, sessionId);
  }
  function decisionLongExplanation(item, options) {
    const brief = item.brief;
    return `<section class="decision-full-brief"><h4>为什么需要你</h4>${rich.markdown(brief.why_user || "", item.session_id)}<h4>推荐理由</h4>${rich.markdown(brief.recommendation_reason || "", item.session_id)}${brief.impact ? `<h4>影响</h4>${rich.markdown(brief.impact, item.session_id)}` : ""}<h4>完整选项和后果</h4>${options.map(option => `<h5>${rich.inline(option.label, item.session_id)}</h5>${option.consequence ? rich.markdown(option.consequence, item.session_id) : '<p class="note">尚未说明结果</p>'}`).join("")}</section>`;
  }
  function decisionCard(item) {
    const stage = item.presentation_stage || "notice";
    if (stage !== "ready") return `<article class="dc decision-notice" data-dc="${item.id}"><div>${av(item.asker, 22, true)}<span>${esc(item.asker)}：这里需要你${stage === "discussion" ? "，先聊清背景和影响" : ""}</span></div><button class="btn" data-act="decision-discuss" data-id="${item.id}">${stage === "discussion" ? "继续聊" : "听它说明"}</button>${stage === "discussion" ? `<button class="btn ghost" data-act="decision-ready" data-id="${item.id}">聊清楚了，给我选项</button>` : ""}</article>`;
    const opened = decisionsOpen.has(item.id), brief = item.brief && typeof item.brief === "object" ? item.brief : null;
    const options = decisionOptions(item), title = brief?.title || compactDecisionText(item.question);
    const recommended = options.find(option => option.recommended);
    const missing = !brief || !brief.why_user || options.some(option => !option.consequence) || (item.recommendation && !brief.recommendation_reason);
    const context = brief ? brief.context : item.question;
    const longBrief = brief && ([brief.why_user, brief.recommendation_reason, brief.impact, ...options.flatMap(option => [option.label, option.consequence])].some(value => Array.from(String(value || "")).length > 280) || options.some(option => Array.from(option.label).length > 140));
    return `<article class="dc decision-card ${item.held ? "held" : ""}" data-dc="${item.id}"><div class="dc-head"><div class="dc-top">${av(item.asker, 22, true)}<h3 class="q">${rich.inline(title, item.session_id)}</h3>${priorityBadge(decisionPriority(item))}<span class="from">${esc(item.asker)}</span></div><button class="btn ghost dc-hold" data-act="${item.held ? "decision-unhold" : "decision-hold"}" data-id="${item.id}">${item.held ? "取消挂起" : "挂起"}</button></div><div class="decision-summary">${brief?.why_user ? `<p class="decision-why"><span>为什么需要你</span>${decisionPreview(brief.why_user, item.session_id)}</p>` : '<p class="decision-missing">缺少说明：还没说清为什么需要你来决定。</p>'}${brief?.recommendation_reason ? `<div class="decision-recommendation"><strong>${recommended ? `建议：${decisionPreview(recommended.label, item.session_id, 140)}` : "推荐理由"}</strong><p>${decisionPreview(brief.recommendation_reason, item.session_id)}</p></div>` : ""}${brief?.impact ? `<p class="decision-impact"><span>影响</span>${decisionPreview(brief.impact, item.session_id)}</p>` : ""}${missing ? `<div class="decision-clarify"><span>这张卡的说明还不完整</span><button type="button" class="text-link" data-act="decision-clarify" data-id="${item.id}">让 leader 说明白</button></div>` : ""}</div><div class="dc-body"><div class="opts">${options.map(option => `<button class="opt ${option.recommended ? "recommended" : ""}" data-act="answer-option" data-id="${item.id}" data-option="${option.index}"><span class="t"><strong>${decisionPreview(option.label, item.session_id, 140)}</strong><small class="${option.consequence ? "" : "missing-consequence"}">${option.consequence ? decisionPreview(option.consequence, item.session_id) : "尚未说明这个选择的结果"}</small></span>${option.recommended ? '<span class="rec">推荐</span>' : ""}</button>`).join("")}</div><form class="free" data-answer-form="${item.id}"><input data-preserve="answer-${item.id}" name="answer" placeholder="或者直接说你的想法，回车发送" required aria-label="答复 ${esc(item.asker)}"><button class="icon-btn" type="submit" title="发送答复">${ic("check", "sm")}</button></form>${context || !brief || longBrief ? `<details class="decision-context" data-decision-context="${item.id}" ${opened ? "open" : ""}><summary>${brief ? longBrief ? "完整说明与技术背景" : "技术背景" : "查看原文和原选项"}</summary><div class="markdown">${longBrief ? decisionLongExplanation(item, options) : ""}${context ? rich.markdown(context, item.session_id) : ""}${brief ? "" : `<ul>${(item.options || []).map(option => `<li>${rich.inline(option, item.session_id)}</li>`).join("")}</ul>`}</div></details>` : ""}${decisionTrust(item).enabled ? '<p class="decision-trust-note">信任模式已开：已授权范围内由 AI 自主推进，需要亲自动手的事见“我的待办”。</p>' : ""}</div></article>`;
  }
  function sideDecision(item) {
    const title = item.brief?.title || compactDecisionText(item.question);
    return `<details class="side-decision" data-side-decision="${item.id}" ${sideDecisionsOpen.has(item.id) ? "open" : ""}><summary><span>${esc(title)}</span><span class="side-decision-owner">${esc(item.asker)}</span></summary>${decisionCard(item)}</details>`;
  }
  function heldGroup(items, context) {
    if (items.length === 0) return "";
    const expanded = heldExpanded.has(context);
    return `<section class="held-group ${context === "all" ? "ov-group" : ""} ${expanded ? "" : "collapsed"}" data-holds="${context}"><button class="tray-head held-head" data-act="holds" data-context="${context}" aria-expanded="${expanded}" aria-controls="held-list-${context}">${ic("down", "sm chev")}挂起 · ${items.length}</button><div class="${context === "tray" ? "tray-list" : "ov-list"} held-list" id="held-list-${context}">${items.map(decisionCard).join("")}</div></section>`;
  }

  function todoInstructions(value, item, ordered = false) {
    if (Array.isArray(value)) return `<${ordered ? "ol" : "ul"}>${value.map(text => `<li>${rich.inline(String(text), item.session_id)}</li>`).join("")}</${ordered ? "ol" : "ul"}>`;
    return value ? rich.markdown(String(value), item.session_id) : '<p class="note">还没写，可以去问它。</p>';
  }
  function todoCard(item) {
    const key = String(item.id), expanded = todoExpanded.has(key), draft = todoActions.get(key), saving = todoSaving.has(key);
    const owner = state().leaders.find(person => person.session_id === item.leader_session_id || person.session_id === item.session_id);
    const blocking = item.blocking ? `<span class="todo-blocking">卡着 ${esc(typeof item.blocking === "string" ? item.blocking : item.proposer_name || "本组")}</span>` : "";
    const group = item.group || item.leader_name || "未分组", proposer = item.proposer_name || "原会话";
    return `<article class="todo-card ${expanded ? "open" : ""}" data-todo-id="${esc(key)}"><button class="todo-top" data-act="todo-toggle" data-id="${esc(key)}" aria-expanded="${expanded}" aria-controls="todo-details-${esc(key)}"><span class="todo-check" aria-hidden="true">${ic("check", "sm")}</span><span class="todo-title">${esc(item.title)}</span>${ic("down", "sm chev")}</button><div class="todo-meta"><span>${esc(group)} · ${esc(proposer)}</span><time title="${esc(date(item.created_at))}">${ago(item.created_at)}</time>${blocking}</div><div class="todo-details" id="todo-details-${esc(key)}" ${expanded ? "" : "hidden"}><section><h4>怎么做</h4><div class="markdown">${todoInstructions(item.steps, item, true)}</div></section><section><h4>完成后怎么确认</h4><div class="markdown">${todoInstructions(item.verification, item)}</div></section></div><div class="todo-card-actions"><button class="btn todo-done" data-act="todo-action" data-id="${esc(key)}" data-status="done" ${saving ? "disabled" : ""}>${ic("check", "sm")}做完了</button><button class="btn ghost" data-act="todo-action" data-id="${esc(key)}" data-status="cancelled" ${saving ? "disabled" : ""}>不做了</button><span class="gap"></span><button class="btn ghost" data-act="todo-ask" data-id="${esc(key)}" ${owner === undefined ? 'disabled title="原 leader 已结束"' : ""}>去问它 ${ic("right", "xs")}</button></div>${draft === undefined ? "" : `<form class="todo-resolution" data-todo-form="${esc(key)}"><label for="todo-note-${esc(key)}">${draft.status === "cancelled" ? "为什么不做了" : "完成备注（可选）"}</label><textarea id="todo-note-${esc(key)}" name="note" rows="2" data-preserve="todo-note-${esc(key)}" placeholder="${draft.status === "cancelled" ? "告诉它原因，方便继续安排" : "比如：已授权，可以继续验证"}" ${draft.status === "cancelled" ? "required" : ""} ${saving ? "disabled" : ""}>${esc(draft.note)}</textarea>${draft.error ? `<p class="error-line" role="alert">${esc(draft.error)}</p>` : ""}<div class="acts"><button type="button" class="btn ghost" data-act="todo-action-close" data-id="${esc(key)}" ${saving ? "disabled" : ""}>取消</button><button type="submit" class="btn ${draft.status === "cancelled" ? "" : "primary"}" ${saving ? "disabled" : ""}>${saving ? "正在保存…" : draft.status === "cancelled" ? "确认不做" : "确认做完"}</button></div></form>`}</article>`;
  }
  function todoTray(items, id) {
    if (items.length === 0) return "";
    return `<section class="todo-tray"><h3 class="attention-section-title">待你动手 <span>${items.length}</span></h3><div class="tray-list todo-tray-list" id="todo-tray-${esc(id)}">${items.map(todoCard).join("")}</div></section>`;
  }

  async function resolveTodo(id) {
    const key = String(id), item = todoById(key), draft = todoActions.get(key);
    if (todoSaving.has(key)) return;
    let payload;
    try { payload = todoResolvePayload(item, draft); }
    catch (error) { if (draft) { draft.error = error.message; changed(); } return; }
    todoSaving.add(key); changed();
    try {
      const result = await api("/api/todo/resolve", payload);
      todoActions.delete(key); todoExpanded.delete(key);
      await refresh();
      toast(payload.status === "done" ? "已标记做完" : "已说明不做的原因", async () => {
        await api("/api/todo/undo", {id:item.id, before:result.before, version:result.version});
        await refresh(); toast("已撤销");
      });
    } catch (error) {
      draft.error = error.message;
      await refresh();
      const current = todoById(key);
      if (current && current.version !== draft.version) { draft.version = current.version; draft.error = "待办内容已更新，请确认后再提交。"; todoExpanded.add(key); }
      else if (!current) toast(error.message);
    } finally { todoSaving.delete(key); changed(); }
  }

    function forChat({ownerId, goalId = null, member = false}) {
      const inGoal = item => goalId == null || String(item.goal_id) === String(goalId);
      const pending = member ? [] : pendingOf(ownerId).filter(inGoal), held = member ? [] : decisionsOf(ownerId).filter(item => item.held && inGoal(item));
      const todos = member ? [] : todosOf(ownerId).filter(inGoal);
      const markup = member ? "" : `<div class="tray-in">${todoTray(todos, ownerId)}${pending.length ? `<section class="tray-pending"><h3 class="attention-section-title">等你拍板 <span>${pending.length}</span></h3><div class="tray-list">${pending.map(sideDecision).join("")}</div></section>` : ""}${heldGroup(held, "tray")}</div>`;
      return {pending, held, todos, count:pending.length+todos.length, totalWithHeld:pending.length+held.length+todos.length, markup};
    }
    function pageMarkup() {
      const items = openTodos();
      return `<div class="head big"><div><div class="title">我的待办${items.length > 0 ? ` <span class="todo-page-count">${items.length}</span>` : ""}</div><div class="sub">需要你亲自动手，做完后通知提出它的会话继续</div></div></div><div class="page"><div class="page-in todos-page">${items.length === 0 ? empty("现在没有待你动手的事") : `<div class="todo-list">${items.map(todoCard).join("")}</div>`}</div></div>`;
    }
    function toggleTodo(id) { const key=String(id); if (todoExpanded.has(key)) todoExpanded.delete(key); else todoExpanded.add(key); changed(); }
    function startTodoAction(id, status) {
      const key=String(id), item=todoById(key); if (!item || todoSaving.has(key)) return;
      const previous=todoActions.get(key);
      todoActions.set(key,{status,note:previous?.note || "",version:item.version,error:null});
      todoExpanded.add(key); changed(); doc.getElementById("todo-note-"+key)?.focus();
    }
    function setTodoNote(id, value) { const draft=todoActions.get(String(id)); if(draft)draft.note=value; }
    function todoState(id) { const key=String(id),draft=todoActions.get(key); return {expanded:todoExpanded.has(key),saving:todoSaving.has(key),draft:draft?{...draft}:null}; }
    function closeTodoAction(id) { const key=String(id); if(todoSaving.has(key))return; todoActions.delete(key); changed(); }
    function setDecisionOpen(id, open) { if(open)decisionsOpen.add(Number(id));else decisionsOpen.delete(Number(id)); }
    async function answer(id, text) {
      const item = state().decisions.find(item => item.id === id);
      const result = await api("/api/answer", {id, text});
      decisionsOpen.delete(id); toast(`已答复 ${item.asker}，${result.routed_to === null ? "它下一轮会收到" : `${result.routed_to} 会叫醒它`}`); await refresh();
    }
    async function discuss(id, ready = false) {
      const item = state().decisions.find(item => String(item.id) === String(id));
      const response = await api(ready ? "/api/decision/ready" : "/api/decision/discuss", {id:Number(id)});
      if (disposed) return;
      await refresh();
      if (disposed) return;
      const owner = item && (leader(item.leader_session_id) || leader(item.session_id) || leader(member(item.session_id)?.leader_session_id));
      const goalId = Object.hasOwn(response,"conversation_goal_id") ? response.conversation_goal_id : item?.goal_id || null;
      if (!ready && owner) options.navigate(owner.session_id, goalId);
    }
    const unregister = options.actions?.register("attention", {
      "todo-toggle":({element})=>toggleTodo(element.dataset.id),
      "todo-action":({element})=>startTodoAction(element.dataset.id,element.dataset.status),
      "todo-action-close":({element})=>closeTodoAction(element.dataset.id),
      "todo-ask":({element})=>{const item=todoById(element.dataset.id),owner=item&&(leader(item.leader_session_id)||leader(item.session_id));if(owner)options.navigate(owner.session_id);},
      holds:({element})=>{const key=element.dataset.context;if(heldExpanded.has(key))heldExpanded.delete(key);else heldExpanded.add(key);changed();},
      "decision-toggle":({element})=>{const id=Number(element.dataset.id);setDecisionOpen(id,!decisionsOpen.has(id));changed();},
      "decision-clarify":({element})=>run(`clarify:${element.dataset.id}`,async()=>{element.disabled=true;try{await api("/api/decision/clarify",{id:Number(element.dataset.id)});await refresh();toast("已请 leader 补充说明");}finally{element.disabled=false;}}),
      "decision-discuss":({element})=>run(`discuss:${element.dataset.id}`,()=>discuss(element.dataset.id)),
      "decision-ready":({element})=>run(`discuss:${element.dataset.id}`,()=>discuss(element.dataset.id,true)),
      "decision-hold":({element})=>hold(element.dataset.id,true),
      "decision-unhold":({element})=>hold(element.dataset.id,false),
      "answer-option":({element})=>run(`answer:${element.dataset.id}`,()=>answer(Number(element.dataset.id),state().decisions.find(item=>item.id===Number(element.dataset.id)).options[Number(element.dataset.option)]))
    });
    function hold(id, held) { return run(`hold:${id}`,async()=>{await api(`/api/decision/${held?"hold":"unhold"}`,{id:Number(id)});await refresh();toast(held?"已挂起，暂时不拍":"已取消挂起，放回待拍板");}); }
    function input(event) { const form=event.target.closest?.("[data-todo-form]"); if(form&&event.target.name==="note")setTodoNote(form.dataset.todoForm,event.target.value); }
    function submit(event) {
      const form=event.target;
      if(form.dataset.todoForm!==undefined){event.preventDefault();setTodoNote(form.dataset.todoForm,form.elements.note.value);void run(`todo:${form.dataset.todoForm}`,()=>resolveTodo(form.dataset.todoForm)).catch(error=>toast(error.message));}
      else if(form.dataset.answerForm!==undefined){event.preventDefault();const text=form.elements.answer.value.trim();if(text)void run(`answer:${form.dataset.answerForm}`,()=>answer(Number(form.dataset.answerForm),text)).catch(error=>toast(error.message));}
    }
    function toggle(event) {
      const details=event.target;
      if(details.dataset.sideDecision!==undefined){const id=Number(details.dataset.sideDecision);if(details.open)sideDecisionsOpen.add(id);else sideDecisionsOpen.delete(id);}
      else if(details.dataset.decisionContext!==undefined)setDecisionOpen(details.dataset.decisionContext,details.open);
    }
    doc.addEventListener("input",input);doc.addEventListener("submit",submit);doc.addEventListener("toggle",toggle,true);
    return {openTodos,todosOf,pendingOf,decisionsOf,forChat,pageMarkup,todoCard,todoTray,decisionCard,heldGroup,decisionTrust,decisionOptions,compactDecisionText,
      toggleTodo,startTodoAction,setTodoNote,closeTodoAction,todoState,setDecisionOpen,resolveTodo,todoResolvePayload,
      destroy(){disposed=true;unregister?.();doc.removeEventListener("input",input);doc.removeEventListener("submit",submit);doc.removeEventListener("toggle",toggle,true);}};
  }
  const exported={create,todoResolvePayload};
  if(typeof module!=="undefined"&&module.exports)module.exports=exported;else window.CrewAttention=exported;
})();
