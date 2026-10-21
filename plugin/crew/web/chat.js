"use strict";
(() => {
  const md = window.markdownit({html:false, breaks:true});
  const linkOpen = md.renderer.rules.link_open || ((tokens, i, options, env, renderer) => renderer.renderToken(tokens, i, options));
  md.renderer.rules.link_open = (tokens, i, options, env, renderer) => {
    tokens[i].attrSet("target", "_blank"); tokens[i].attrSet("rel", "noopener noreferrer");
    return linkOpen(tokens, i, options, env, renderer);
  };
  let selected = null, stream = null, messages = [], byId = new Map(), results = new Map(), cursor = 0;
  let busy = false, running = false, loading = false, generation = 0, frame = null, followOnRender = false;
  const drafts = new Map();
  const webSessions = () => allSessions().filter(s => s.web_chat);
  const currentSession = () => webSessions().find(s => s.session_id === selected);
  function error(text) { $("chat-error").hidden = !text; $("chat-error").textContent = text || ""; }
  function status() {
    const badge = $("chat-status");
    const inVscode = currentSession()?.open?.location === "vscode";
    badge.className = "pill " + (busy ? "working" : running ? "idle" : "offline");
    badge.textContent = loading ? "连接中" : inVscode ? "在 VS Code 中" : busy ? "正在处理" : running ? "在线" : selected ? "已暂停" : "未连接";
    $("chat-input").disabled = !selected || loading || inVscode;
    $("chat-send").disabled = !selected || busy || loading || inVscode;
    $("chat-hint").textContent = inVscode ? "这段对话正在 VS Code 中继续" : busy ? "当前会话正在处理，也会接收同组消息" : !running && selected ? "发送消息后继续这段对话" : "Enter 发送 · Shift + Enter 换行";
  }
  function addMessage(message) {
    if (byId.has(message.id)) return byId.get(message.id);
    messages.push(message); byId.set(message.id, message); return message;
  }
  function apply(event) {
    if (event.seq <= cursor) return;
    cursor = event.seq;
    if (event.type === "message") { error(null); addMessage({...event}); }
    else if (event.type === "text") {
      const message = addMessage({id:event.id, role:"assistant", text:""});
      message.text = (message.text || "") + event.text;
    } else if (event.type === "assistant") {
      const message = addMessage({id:event.id, role:"assistant"});
      message.content = event.content;
      message.text = "";
    } else if (event.type === "tool_result") results.set(event.tool_use_id, event);
    else if (event.type === "status") { busy = event.status === "working"; running = event.status !== "offline"; }
    else if (event.type === "error") error(event.text);
    scheduleRender(); status();
  }
  function tool(block) {
    const result = results.get(block.id);
    const content = result?.content;
    const output = typeof content === "string" ? content : JSON.stringify(content, null, 2);
    return `<details class="tool-call" data-tool="${esc(block.id)}"><summary><span class="tool-icon">⌘</span><span>${esc(block.name)}</span><small class="${result?.is_error ? "tool-failed" : ""}">${result ? result.is_error ? "执行失败" : "已完成" : "调用工具"}</small></summary><div class="tool-body"><small>参数</small><pre>${esc(JSON.stringify(block.input, null, 2))}</pre>${result ? `<small>结果</small><pre>${esc(output)}</pre>` : ""}</div></details>`;
  }
  function body(message) {
    if (message.role === "user") return `<div class="message-text">${esc(message.text)}</div>`;
    const blocks = message.content || [{type:"text", text:message.text || ""}];
    return blocks.map(block => block.type === "text" ? `<div class="markdown">${md.render(block.text)}</div>` : block.type === "tool_use" ? tool(block) : "").join("") + (message.content && message.text ? `<div class="markdown">${md.render(message.text)}</div>` : "");
  }
  function render() {
    frame = null;
    const log = $("chat-log"), follow = followOnRender || log.scrollHeight - log.scrollTop - log.clientHeight < 100;
    followOnRender = false;
    const openTools = new Set([...log.querySelectorAll("[data-tool][open]")].map(d => d.dataset.tool));
    if (!selected) {
      log.innerHTML = `<div class="chat-welcome"><div class="welcome-mark">C<span>·</span></div><small>CREW / 你的协作空间</small><h1>想法交给你。<br>推进交给会话。</h1><p>在网页开新会话，或召集已有的会话。<br>让组员各司其职，让 leader 帮你掌握进展。</p><div class="actions"><button class="primary" data-chat-create>开一个 leader ↗</button><button data-chat-spawn>开新会话</button></div><div class="welcome-features"><span>◌ 直接收编已有会话</span><span>↗ 按需转到 VS Code</span><span>◇ 需要你时再拍板</span></div></div>`;
      return;
    }
    log.innerHTML = messages.length ? messages.map(message => `<article class="chat-message ${message.role === "user" ? message.source === "peer" ? "peer-message" : "human-message" : "assistant-message"}" data-message="${esc(message.id)}"><div class="message-byline">${message.role === "assistant" ? `<span class="mini-avatar">C</span>${esc(currentSession()?.crew_name || "会话")}` : message.source === "peer" ? `<span class="peer-symbol">↳</span>${esc(message.sender || "组员")} <span class="peer-label">同组消息</span>` : "你"}</div>${body(message)}</article>`).join("") + (busy ? '<div class="typing-indicator" aria-label="当前会话正在处理"><i></i><i></i><i></i></div>' : "") : `<div class="conversation-empty"><span class="welcome-mark">C<span>·</span></span><h2>${loading ? "正在读取这段对话…" : "从目标开始聊聊"}</h2><p>${loading ? "" : "说说想完成什么，或者问问当前进展。"}</p><div class="suggestions"><button data-suggest="帮我看看当前进展，告诉我现在需要关注什么。">看看当前进展 ↗</button><button data-suggest="我们一起梳理下一步的目标和分工。">梳理下一步安排 ↗</button></div></div>`;
    for (const detail of log.querySelectorAll("[data-tool]")) detail.open = openTools.has(detail.dataset.tool);
    if (follow) log.scrollTop = log.scrollHeight;
  }
  function scheduleRender() { if (frame === null) frame = requestAnimationFrame(render); }
  function update() {
    if (!state) return;
    const available = webSessions();
    const groupNames = [...new Set(available.map(s => s.group_name || ""))];
    $("chat-sessions").innerHTML = groupNames.map(name => `<div class="chat-group-label">${esc(name || "未分组")}</div>` + available.filter(s => (s.group_name || "") === name).sort((a,b) => Number(b.role === "leader") - Number(a.role === "leader")).map(s => `<button class="leader-choice ${selected === s.session_id ? "selected" : ""}" data-chat="${esc(s.session_id)}"><span class="leader-choice-icon">${s.role === "leader" ? "★" : esc((s.crew_name || "?").slice(0, 1))}</span><span><strong>${esc(s.crew_name)}</strong><small>${esc(s.task || (s.role === "leader" ? "leader" : "组员"))}</small></span><i class="status-dot ${esc(s.status)}"></i></button>`).join("")).join("") || '<p class="sidebar-empty">还没有网页会话<br>点右上角 ＋ 开始。</p>';
    const current = currentSession();
    $("chat-title").textContent = current?.crew_name || (selected ? "会话" : "选择一个会话");
    $("chat-subtitle").textContent = current?.task || (selected ? "一起推进这个分组。" : "你的工作，从一次对话开始。");
    $("chat-open").hidden = !current?.open;
    $("chat-open").dataset.open = selected || "";
    $("chat-open").disabled = !current?.open?.enabled;
    $("chat-open").title = current?.open?.reason || "在 VS Code 接着聊";
    const group = state.groups.find(g => g.name === current?.group_name);
    const members = (group?.members || []).filter(s => s.session_id !== selected);
    $("chat-team-count").textContent = members.length;
    $("chat-members").innerHTML = members.map(s => `<article class="team-member"><div><span class="member-avatar">${esc((s.crew_name || "?").slice(0,1))}</span><strong>${esc(s.crew_name || s.session_id.slice(0,8))}</strong><i class="status-dot ${esc(s.status)}" title="${esc(states[s.status])}"></i></div><small>${states[s.status]} · ${ago(s.last_event_at)}</small><p>${esc(s.status_note || s.task || "尚未填写任务")}</p></article>`).join("") || `<p class="sidebar-empty">${selected ? "还没有组员。<br>去看板收进几个已有会话吧。" : "选定分组后，<br>在这里查看组员动态。"}</p>`;
    const memberIds = new Set((group?.members || []).map(s => s.session_id));
    if (selected) memberIds.add(selected);
    const decisions = state.decisions.filter(d => d.status === "open" && memberIds.has(d.session_id));
    $("chat-decisions").innerHTML = decisions.length ? `<h3>等你拍板 <span class="count">${decisions.length}</span></h3>` + decisions.map(d => `<button class="chat-decision" data-decision="${d.id}"><small>${esc(displayName(d.session_id))}</small><span>${esc(d.question)}</span><b>去答复 ↗</b></button>`).join("") : "";
    if (activeTab === "chat" && !selected && available.length) { const wanted = available.find(s => s.session_id === requestedChat); requestedChat = null; select((wanted || available[0]).session_id); }
    status();
  }
  function endpoint(path, id, after) {
    const params = new URLSearchParams({session_id:id});
    if (project) params.set("project", project);
    if (after !== undefined) params.set("after", after);
    return path + "?" + params;
  }
  async function select(id) {
    if (!id) return;
    if (selected === id && stream && !loading) { setTab("chat"); return; }
    if (selected) drafts.set(selected, $("chat-input").value);
    stream?.close(); stream = null;
    selected = id; messages = []; byId = new Map(); results = new Map(); cursor = 0;
    history.replaceState(null, "", "#chat=" + encodeURIComponent(id));
    busy = false; running = false; loading = true; const token = ++generation;
    $("chat-input").value = drafts.get(id) || ""; error(null); status(); scheduleRender();
    setTab("chat"); update();
    try {
      const data = await request(endpoint("/api/chat/history", id));
      if (token !== generation) return;
      for (const event of data.events) apply(event);
      followOnRender = true;
      busy = data.busy; running = data.running;
      stream = new EventSource(endpoint("/api/chat/events", id, cursor));
      let connectionIssue = false;
      stream.onmessage = event => { if (token === generation) apply(JSON.parse(event.data)); };
      stream.onerror = () => { if (token === generation) { connectionIssue = true; error("对话连接中断，正在重新连接…"); } };
      stream.onopen = () => { if (token === generation && connectionIssue) { connectionIssue = false; error(null); } };
    } catch (failure) { if (token === generation) error(failure.message); }
    finally { if (token === generation) { loading = false; update(); scheduleRender(); status(); } }
  }
  function reset() {
    if (selected) drafts.set(selected, $("chat-input").value);
    stream?.close(); stream = null; selected = null; generation++;
    messages = []; byId = new Map(); results = new Map(); cursor = 0; busy = running = loading = false;
    $("chat-input").value = ""; error(null); scheduleRender(); status();
    if (activeTab === "chat") history.replaceState(null, "", "#chat");
  }
  $("chat-form").onsubmit = async event => {
    event.preventDefault(); const text = $("chat-input").value.trim(), id = selected;
    if (!text || !id || $("chat-send").disabled) return;
    busy = true; status(); error(null);
    try {
      await post("/api/chat/send", {session_id:id, text});
      if (id === selected) { $("chat-input").value = ""; drafts.delete(id); }
    } catch (failure) { if (id === selected) { busy = false; error(failure.message); status(); } }
  };
  $("chat-input").onkeydown = event => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); if (!$("chat-send").disabled) $("chat-form").requestSubmit(); } };
  $("chat-new").onclick = () => $("new-session").click();
  document.addEventListener("click", event => {
    const choice = event.target.closest("[data-chat]"); if (choice) select(choice.dataset.chat);
    if (event.target.closest("[data-chat-create]")) $("new-leader").click();
    if (event.target.closest("[data-chat-spawn]")) $("new-session").click();
    const suggestion = event.target.closest("[data-suggest]"); if (suggestion) { $("chat-input").value = suggestion.dataset.suggest; $("chat-input").focus(); }
    const decision = event.target.closest("[data-decision]"); if (decision) { setTab("board"); $("decision-" + decision.dataset.decision)?.scrollIntoView({behavior:"smooth", block:"center"}); }
  });
  window.crewChat = {select, update, reset, selectedId:() => selected, visibility(visible) { if (visible) { update(); scheduleRender(); } }};
  update(); scheduleRender(); status();
})();
