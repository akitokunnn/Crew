"use strict";
(() => {
  const escape = value => String(value).replace(/[&<>"']/g, character => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[character]);
  const time = at => new Date(at).toLocaleString("zh-CN", {month:"numeric", day:"numeric", hour:"2-digit", minute:"2-digit", hour12:false});
  let active = null;
  const current = context => active === context;
  const atBottom = context => context.scroll.scrollHeight - context.scroll.scrollTop - context.scroll.clientHeight < 48;
  function composerState(context) {
    context.send.disabled = context.sending || context.input.value.trim() === "" || (context.allLeaders && context.state.leaders.length === 0);
    for (const button of context.targets.querySelectorAll("button")) button.disabled = context.sending;
  }
  function resizeInput(context) {
    context.input.style.height = "auto";
    context.input.style.height = `${Math.min(context.input.scrollHeight, 220)}px`;
  }
  function renderTargets(context) {
    const ids = new Set(context.state.leaders.map(item => item.session_id));
    for (const id of context.selected) if (!ids.has(id)) context.selected.delete(id);
    const all = ids.size || context.allLeaders ? `<button type="button" class="channel-target ${context.allLeaders ? "selected" : ""}" data-channel-to="*" aria-pressed="${Boolean(context.allLeaders)}" title="发送时点名本工作区所有在用的 leader" ${context.sending ? "disabled" : ""}>@所有 leader</button>` : "";
    const html = `<span class="channel-target-label">点名</span>${all}${context.state.leaders.map(item => `<button type="button" class="channel-target ${context.allLeaders || context.selected.has(item.session_id) ? "selected" : ""}" data-channel-to="${escape(item.session_id)}" aria-pressed="${context.allLeaders || context.selected.has(item.session_id)}" ${context.sending ? "disabled" : ""}>@${escape(item.name)}</button>`).join("")}${ids.size === 0 ? '<span class="note">还没有 leader</span>' : ""}`;
    if (context.targets.innerHTML !== html) context.targets.innerHTML = html;
  }
  function messageHTML(message) {
    const user = message.speaker_session_id === "user";
    return `<article class="m-lead channel-message ${user ? "channel-user" : ""}" data-channel-id="${message.id}"><span class="av round" style="--s:30px;--tint:var(--accent)">${escape(message.speaker_name.slice(0, 1))}</span><div class="body"><div class="channel-meta"><strong>${escape(message.speaker_name)}</strong><time datetime="${escape(message.created_at)}">${escape(time(message.created_at))}</time></div><div class="${user ? "bubble" : "channel-content"}"><div class="markdown">${CrewChat.renderMarkdown(message.body, message.speaker_session_id)}</div>${message.mentions.length === 0 ? "" : `<div class="channel-mentions">${message.mentions.map(item => `<span class="channel-mention">@${escape(item.name)}</span>`).join("")}</div>`}</div></div></article>`;
  }
  function renderMessages(context, position = "keep") {
    const height = context.scroll.scrollHeight, top = context.scroll.scrollTop;
    const originals = context.digest === null ? "" : `<div class="channel-originals"><button class="channel-originals-toggle" type="button" aria-expanded="${context.originalsOpen}">已整理的 ${context.digest.total_message_count} 条原始发言 ${context.originalsOpen ? "▾" : "▸"}</button>${context.originalsOpen ? `<div class="channel-original-list">${context.originalMessages.map(messageHTML).join("")}${context.hasMoreOriginals ? `<button type="button" class="btn ghost channel-originals-older" ${context.loadingOriginals ? "disabled" : ""}>${context.loadingOriginals ? "正在加载…" : "加载更早的"}</button>` : ""}</div>` : ""}</div>`;
    context.inner.innerHTML = originals + (context.messages.length === 0 ? `<div class="channel-empty">${context.digest === null ? "还没有发言。在这里和所有 leader 交流。" : "要点之后暂无新发言"}</div>` : context.messages.map(messageHTML).join(""));
    context.older.hidden = !context.hasMore;
    context.older.disabled = context.loadingOlder;
    context.older.textContent = context.loadingOlder ? "正在加载…" : "加载更早的";
    if (position === "bottom") context.scroll.scrollTop = context.scroll.scrollHeight;
    else if (position === "prepend") context.scroll.scrollTop = top + context.scroll.scrollHeight - height;
    else context.scroll.scrollTop = top;
    markRead(context);
  }
  function renderDigest(context) {
    context.pin.hidden = context.digest === null;
    if (context.digest === null) { context.pin.innerHTML = ""; return; }
    const runtime = {claude:"Claude Code",codex:"Codex"}[context.digest.runtime] || "执行工具未知";
    const cost = context.digest.cost_usd == null ? "费用未知" : `$${Number(context.digest.cost_usd).toFixed(4)}`;
    context.pin.innerHTML = `<details class="channel-digest" open><summary>要点 · 截至 ${escape(time(context.digest.created_at))} · 累计整理 ${context.digest.total_message_count} 条</summary><div class="m-lead"><div class="body"><div class="markdown">${CrewChat.renderMarkdown(context.digest.body, "user")}</div><p class="note">${escape(runtime)} · ${escape(context.digest.model || "模型未知")} · ${escape(cost)}</p></div></div></details>`;
  }
  function digestState(context) {
    context.digestButton.disabled = context.requestingDigest || context.state.channel.digesting;
    context.digestButton.textContent = context.digestButton.disabled ? "整理中…" : "整理";
    const reason = context.state.channel.digest_error;
    if (!context.digestButton.disabled) {
      if (reason !== null && reason !== context.digestError) context.toast(reason);
      context.digestError = reason;
    }
  }
  async function organize(context) {
    if (context.digestButton.disabled) return;
    context.requestingDigest = true; digestState(context);
    try {
      await context.api("/api/channel/digest", {project:context.state.project.name});
      context.state.channel.digesting = true; context.digestError = null;
    } catch (error) {
      if (current(context)) context.toast(error.message);
    } finally {
      context.requestingDigest = false;
      if (current(context)) digestState(context);
    }
  }
  async function originals(context) {
    if (context.loadingOriginals || !context.hasMoreOriginals) return;
    const digestId = context.digest.id;
    context.loadingOriginals = true; renderMessages(context);
    try {
      const before = context.originalMessages.length === 0 ? context.digest.upto_message_id + 1 : context.originalMessages[0].id;
      const result = await context.api(`/api/channel?limit=20&before=${before}`);
      if (!current(context) || context.digest.id !== digestId) return;
      context.originalMessages = [...result.messages, ...context.originalMessages];
      context.hasMoreOriginals = result.has_more;
    } catch (error) { if (current(context)) context.toast(error.message); }
    finally { context.loadingOriginals = false; if (current(context)) renderMessages(context); }
  }
  function merge(context, messages, advance = true) {
    const byId = new Map(context.messages.map(item => [item.id, item]));
    for (const message of messages) byId.set(message.id, message);
    context.messages = Array.from(byId.values()).sort((a, b) => a.id - b.id);
    if (advance && messages.length > 0) context.latestId = Math.max(context.latestId, ...messages.map(item => item.id));
  }
  async function markRead(context) {
    if (!current(context) || context.loading || context.reading || context.latestId <= context.readId || document.visibilityState !== "visible" || !atBottom(context)) return;
    context.reading = true;
    let succeeded = false;
    try {
      const result = await context.api("/api/channel/read", {project:context.state.project.name, id:context.latestId});
      context.readId = result.read_id;
      succeeded = true;
      if (current(context)) context.onRead(result);
    } catch (error) { if (current(context)) context.toast(error.message); }
    finally { context.reading = false; if (succeeded && current(context)) markRead(context); }
  }
  async function load(context) {
    context.loading = true;
    try {
      const result = await context.api("/api/channel?limit=20");
      if (!current(context)) return;
      context.digest = result.digest; context.messages = []; context.originalMessages = [];
      context.originalsOpen = false; context.hasMoreOriginals = context.digest !== null;
      merge(context, result.messages.filter(item => context.digest === null || item.id > context.digest.upto_message_id));
      if (context.digest !== null) context.latestId = Math.max(context.latestId, context.digest.upto_message_id);
      context.pageBefore = result.messages.length === 0 ? null : result.messages[0].id;
      context.hasMore = result.has_more && (context.digest === null || context.pageBefore > context.digest.upto_message_id);
      context.loading = false; renderDigest(context);
      renderMessages(context, "bottom");
      if (context.state.channel.latest_id > context.latestId) refresh(context);
    } catch (error) {
      if (current(context)) { context.loading = false; context.inner.textContent = "交流区加载失败"; context.toast(error.message); }
    }
  }
  async function older(context) {
    if (context.loadingOlder || !context.hasMore) return;
    context.loadingOlder = true; context.older.disabled = true; context.older.textContent = "正在加载…";
    try {
      const result = await context.api(`/api/channel?limit=20&before=${context.pageBefore}`);
      if (!current(context)) return;
      merge(context, result.messages.filter(item => context.digest === null || item.id > context.digest.upto_message_id));
      context.pageBefore = result.messages[0].id;
      context.hasMore = result.has_more && (context.digest === null || context.pageBefore > context.digest.upto_message_id);
      renderMessages(context, "prepend");
    } catch (error) { if (current(context)) context.toast(error.message); }
    finally { context.loadingOlder = false; if (current(context)) { context.older.disabled = false; context.older.textContent = "加载更早的"; } }
  }
  async function refresh(context) {
    if (context.loading || context.refreshing) return;
    context.refreshing = true;
    const known = context.latestId, messages = [];
    let before = null, succeeded = false;
    try {
      while (true) {
        const result = await context.api(`/api/channel?limit=20${before === null ? "" : `&before=${before}`}`);
        if (!current(context)) return;
        if ((result.digest === null ? null : result.digest.id) !== (context.digest === null ? null : context.digest.id)) {
          await load(context); return;
        }
        messages.push(...result.messages.filter(item => item.id > known));
        if (!result.has_more || result.messages[0].id <= known) break;
        before = result.messages[0].id;
      }
      const follow = atBottom(context);
      merge(context, messages); renderMessages(context, follow ? "bottom" : "keep"); succeeded = true;
    } catch (error) { if (current(context)) context.toast(error.message); }
    finally {
      context.refreshing = false;
      if (succeeded && current(context) && context.state.channel.latest_id > context.latestId) refresh(context);
    }
  }
  async function send(context) {
    if (context.send.disabled) return;
    const body = context.input.value.trim(), version = context.version;
    const allLeaders = context.allLeaders;
    const to = allLeaders ? ["*"] : context.state.leaders.filter(item => context.selected.has(item.session_id)).map(item => item.name);
    context.sending = true; composerState(context);
    try {
      const result = await context.api("/api/channel", {project:context.state.project.name, body, to});
      if (!current(context)) return;
      if (context.version === version) { context.input.value = ""; resizeInput(context); }
      context.selected.clear(); context.allLeaders = false; renderTargets(context);
      // Fetch through intervening posts before advancing the cursor to our new message.
      merge(context, [result.message], false); renderMessages(context, "bottom"); refresh(context);
      context.toast(allLeaders ? `已点名本工作区的 ${result.message.mentions.length} 位 leader` : to.length === 0 ? "已发到交流区" : `已发到交流区，点名的 ${to.join("、")} 会被叫醒`);
    } catch (error) { if (current(context)) context.toast(error.message); }
    finally { context.sending = false; if (current(context)) composerState(context); }
  }
  function mount(options) {
    unmount();
    const context = {...options, digest:null, digestError:null, requestingDigest:false, originalMessages:[], originalsOpen:false, hasMoreOriginals:false, loadingOriginals:false, messages:[], selected:new Set(), allLeaders:false, latestId:0, readId:0, loading:true, loadingOlder:false, refreshing:false, reading:false, sending:false, composing:false, version:0};
    active = context;
    options.host.innerHTML = '<div class="channel-pin" hidden></div><div class="scroll channel-scroll" tabindex="0" aria-label="交流区发言"><div class="channel-history"><button type="button" class="btn ghost channel-older" hidden>加载更早的</button></div><div class="chat-inner channel-inner"><div class="channel-empty">正在加载…</div></div></div><div class="composer-wrap channel-composer-wrap"><div class="channel-targets" role="group" aria-label="点名 leader"></div><form class="composer channel-composer"><textarea rows="1" aria-label="发到交流区" placeholder="和所有 leader 说…"></textarea><div class="c-row"><span class="hint">Enter 发送 · Shift+Enter 换行</span><button type="submit" class="send channel-send" aria-label="发送到交流区" disabled><svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="m5 11 7-7 7 7M12 4v16"/></svg></button></div></form></div>';
    context.pin = options.host.querySelector(".channel-pin");
    context.digestButton.onclick = () => organize(context);
    context.scroll = options.host.querySelector(".channel-scroll"); context.inner = options.host.querySelector(".channel-inner");
    context.older = options.host.querySelector(".channel-older"); context.targets = options.host.querySelector(".channel-targets");
    context.input = options.host.querySelector("textarea"); context.send = options.host.querySelector(".channel-send");
    context.older.onclick = () => older(context);
    context.inner.onclick = event => {
      if (event.target.closest(".channel-originals-toggle")) {
        context.originalsOpen = !context.originalsOpen; renderMessages(context);
        if (context.originalsOpen && context.originalMessages.length === 0) originals(context);
      } else if (event.target.closest(".channel-originals-older")) originals(context);
    };
    context.scroll.onscroll = () => markRead(context);
    context.visibility = () => markRead(context);
    document.addEventListener("visibilitychange", context.visibility);
    context.targets.onclick = event => {
      const button = event.target.closest("[data-channel-to]");
      if (!button || button.disabled) return;
      const id = button.dataset.channelTo;
      if (id === "*") { context.allLeaders = !context.allLeaders; context.selected.clear(); }
      else if (context.allLeaders) {
        context.allLeaders = false; context.selected.clear();
        for (const item of context.state.leaders) if (item.session_id !== id) context.selected.add(item.session_id);
      } else if (context.selected.has(id)) context.selected.delete(id); else context.selected.add(id);
      renderTargets(context); composerState(context);
    };
    context.input.oninput = () => { context.version += 1; resizeInput(context); composerState(context); };
    context.input.oncompositionstart = () => { context.composing = true; };
    context.input.oncompositionend = () => { context.composing = false; };
    context.input.onkeydown = event => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing && !context.composing && event.keyCode !== 229) { event.preventDefault(); send(context); }
    };
    options.host.querySelector("form").onsubmit = event => { event.preventDefault(); if (!context.composing) send(context); };
    renderTargets(context); digestState(context); load(context);
    return {update, unmount};
  }
  function update(state) {
    if (!active) return;
    active.state = state; renderTargets(active); composerState(active); digestState(active);
    if (state.channel.latest_id > active.latestId || state.channel.digest_id !== (active.digest === null ? null : active.digest.id)) refresh(active);
  }
  function unmount() {
    if (!active) return;
    document.removeEventListener("visibilitychange", active.visibility);
    active = null;
  }
  window.CrewChannel = {mount, update, unmount};
})();
