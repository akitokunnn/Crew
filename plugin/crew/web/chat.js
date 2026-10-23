"use strict";
(() => {
  const CONTEXT_WARN = 0.5, CONTEXT_DANGER = 0.8;
  const tokens = value => value >= 10000 ? `${(value / 10000).toFixed(1)} 万` : String(Math.round(value));
  function ring(context) {
    if (context === null || context.used / context.max < CONTEXT_WARN) return "";
    const ratio = context.used / context.max, percent = Math.round(ratio * 100);
    const maximum = context.max >= 10000 ? `${Number((context.max / 10000).toFixed(1))} 万` : tokens(context.max);
    const label = `上下文 已用 ${tokens(context.used)} / ${maximum}（${percent}%）`;
    return `<span class="context-ring ${ratio >= CONTEXT_DANGER ? "danger" : "warn"}" role="img" title="${label}" aria-label="${label}"><svg viewBox="0 0 20 20" aria-hidden="true"><circle class="context-track" cx="10" cy="10" r="7.5"/><circle class="context-arc" cx="10" cy="10" r="7.5" pathLength="100" stroke-dasharray="${Math.min(100, ratio * 100)} 100"/></svg><span class="context-pct" aria-hidden="true">${percent}%</span></span>`;
  }
  window.CrewContext = {tokens, ring};
})();
(() => {
  const turns = new Map();
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  const escape = value => String(value).replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
  let clockOffset = 0, frame = null;
  function tokenCount(turn, now) {
    if (reducedMotion.matches) return turn.to;
    const fraction = Math.min(1, (now - turn.changedAt) / 2800);
    return turn.from + (turn.to - turn.from) * fraction;
  }
  function elapsed(at, now) {
    const seconds = Math.max(0, Math.floor((now + clockOffset - Date.parse(at)) / 1000));
    return seconds < 60 ? `${seconds}秒` : `${Math.floor(seconds / 60)}分${seconds % 60}秒`;
  }
  function tokens(value) {
    return value < 1000 ? String(Math.floor(value)) : `${(value / 1000).toFixed(1)}k`;
  }
  function cancel() {
    if (frame === null) return;
    if (frame.animation) cancelAnimationFrame(frame.id); else clearTimeout(frame.id);
    frame = null;
  }
  // Busy turns last hours: draw every frame only while a token count is rolling, otherwise a cheap tick keeps the clock moving.
  function schedule() {
    cancel();
    if (turns.size === 0) return;
    const now = Date.now();
    frame = [...turns.values()].some(turn => !reducedMotion.matches && turn.from !== turn.to && now - turn.changedAt < 2800)
      ? {animation:true, id:requestAnimationFrame(paint)} : {animation:false, id:setTimeout(paint, 250)};
  }
  function paint() {
    frame = null;
    const now = Date.now();
    for (const node of document.querySelectorAll("[data-activity]")) {
      const turn = turns.get(node.dataset.activity);
      if (turn === undefined) { node.remove(); continue; }
      const clock = node.querySelector("[data-activity-elapsed]"), count = node.querySelector("[data-activity-tokens]");
      const clockText = elapsed(turn.startedAt, now), tokenText = `↓ ${tokens(tokenCount(turn, now))} tokens`;
      if (clock.textContent !== clockText) clock.textContent = clockText;
      if (count !== null && count.textContent !== tokenText) count.textContent = tokenText;
    }
    schedule();
  }
  function sync(state) {
    const now = Date.now(), present = new Set();
    clockOffset = Date.parse(state.now) - now;
    for (const session of state.leaders.concat(state.members)) {
      const activity = session.activity;
      if (activity === null) continue;
      present.add(session.session_id);
      const previous = turns.get(session.session_id);
      if (previous !== undefined && previous.startedAt === activity.turn_started_at && previous.to === activity.output_tokens) continue;
      const from = previous === undefined || previous.startedAt !== activity.turn_started_at ? activity.output_tokens : tokenCount(previous, now);
      turns.set(session.session_id, {startedAt:activity.turn_started_at, from, to:activity.output_tokens, changedAt:now});
    }
    for (const id of turns.keys()) if (!present.has(id)) turns.delete(id);
    schedule();
  }
  function markup(session, compact = false) {
    const activity = session.activity;
    if (activity === null) return "";
    const phase = activity.phase === "tool" ? `在跑：${activity.label}` : {requesting:"等模型回应", thinking:"思考中", writing:"在写回复", compacting:"压缩中"}[activity.phase];
    const turn = turns.get(session.session_id), now = Date.now();
    return `<span class="activity${compact ? " compact" : ""}" data-activity="${escape(session.session_id)}" role="status"><span class="spin" aria-hidden="true"></span><span class="activity-phase" title="${escape(phase)}">${escape(phase)}</span><span class="activity-elapsed" data-activity-elapsed>${elapsed(turn.startedAt, now)}</span>${activity.phase === "compacting" ? "" : `<span class="activity-tokens" data-activity-tokens>↓ ${tokens(tokenCount(turn, now))} tokens</span>`}</span>`;
  }
  window.CrewActivity = {sync, markup};
})();
(() => {
  const markdown = window.markdownit({ html: false, breaks: true });
  // Same rule as notifications.cjk_bold: "**标题：**正文" is not bold under CommonMark, so move the punctuation outside.
  const BOLD_PUNCT = String.raw`\u2010-\u2027\u3000-\u303f\uff01-\uff0f\uff1a-\uff20\uff3b-\uff40\uff5b-\uff65.,:;!?()\[\]"'`;
  const BOLD_TAIL = new RegExp(`\\*\\*([^*\\n]+?)([${BOLD_PUNCT}]+)\\*\\*(?=[^\\s*${BOLD_PUNCT}])`, 'g');
  const BOLD_HEAD = new RegExp(`(?<=[^\\s*${BOLD_PUNCT}])\\*\\*([${BOLD_PUNCT}]+)([^*\\n]+?)\\*\\*`, 'g');
  const cjkBold = text => text.replace(BOLD_TAIL, '**$1**$2').replace(BOLD_HEAD, '$1**$2**');
  const linkOpen = markdown.renderer.rules.link_open || ((tokens, index, options, env, renderer) => renderer.renderToken(tokens, index, options));
  markdown.renderer.rules.link_open = (tokens, index, options, env, renderer) => {
    tokens[index].attrSet("target", "_blank");
    tokens[index].attrSet("rel", "noopener noreferrer");
    return linkOpen(tokens, index, options, env, renderer);
  };
  const sessions = new Map();
  const compactNotified = new Map();
  let active = null;
  const escape = value => String(value).replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
  const icon = kind => {
    const paths = {
      up: '<path d="m5 11 7-7 7 7M12 4v16"/>',
      down: '<path d="m5 9 7 7 7-7M12 4v12"/>',
      image: '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1.5"/><path d="m3 16 5-5 4 4 3-3 6 6"/>',
      right: '<path d="m9 5 7 7-7 7"/>',
      check: '<path d="m5 12 4 4L19 6"/>',
      close: '<path d="m6 6 12 12M6 18 18 6"/>'
    };
    return `<svg class="i ${kind === "right" ? "xs chev" : "sm"}" viewBox="0 0 24 24" aria-hidden="true">${paths[kind]}</svg>`;
  };
  const current = context => active === context;
  const atBottom = context => context.scroll.scrollHeight - context.scroll.scrollTop - context.scroll.clientHeight < 64;
  const keyFor = context => `crew.draft:${context.state.project.name}:${context.session.session_id}`;
  const query = context => new URLSearchParams({ project: context.state.project.name, session_id: context.session.session_id });

  function nameOf(context, ccName) {
    const person = context.state.leaders.concat(context.state.members).find(item => item.cc_name === ccName && item.name !== null);
    return person ? person.name : ccName;
  }
  function timeOf(at) {
    return at ? new Date(at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }) : "";
  }
  function dayOf(at) {
    const date = new Date(at);
    const today = new Date();
    const yesterday = new Date(today);
    yesterday.setDate(today.getDate() - 1);
    const day = date.toDateString();
    const prefix = day === today.toDateString() ? "今天 · " : day === yesterday.toDateString() ? "昨天 · " : date.getFullYear() === today.getFullYear() ? "" : `${date.getFullYear()}年`;
    return { key: day, label: `${prefix}${date.getMonth() + 1}月${date.getDate()}日 ${["周日", "周一", "周二", "周三", "周四", "周五", "周六"][date.getDay()]}` };
  }
  function persistDraft(context) {
    try {
      localStorage.setItem(keyFor(context), JSON.stringify({ text: context.cache.text, images: context.cache.images }));
    } catch (error) { context.toast(error.message); }
  }
  function saveDraft(context) {
    if (context.readonly) return;
    context.cache.text = context.input.value;
    persistDraft(context);
  }
  function visibleDraft(context) {
    return active && active.cache === context.cache ? active : null;
  }
  function composerState(context) {
    if (context.readonly) return;
    const hasContent = context.input.value.trim().length > 0 || context.cache.images.length > 0;
    context.send.disabled = context.loading || context.busy || context.cache.sending || !hasContent;
    context.hint.textContent = context.busy ? `${context.session.name} 正在回复…` : context.cache.sending ? "正在发送…" : "Enter 发送 · Shift+Enter 换行";
    context.send.title = context.busy ? `${context.session.name} 正在回复…` : "发送";
  }
  function resizeInput(context) {
    context.input.style.height = "auto";
    context.input.style.height = `${Math.min(context.input.scrollHeight, 220)}px`;
  }
  function renderAttachments(context) {
    context.attach.hidden = context.cache.images.length === 0;
    context.attach.innerHTML = context.cache.images.map((image, index) => `<div class="thumb"><img src="data:${escape(image.media_type)};base64,${image.data}" alt="待发送的图片 ${index + 1}"><button type="button" data-remove-image="${index}" aria-label="移除图片 ${index + 1}">${icon("close")}</button></div>`).join("");
    composerState(context);
  }
  function readImage(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve({ media_type: file.type, data: reader.result.slice(reader.result.indexOf(",") + 1) });
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
  }
  async function addImages(context, files) {
    for (const file of files) {
      if (!file.type.startsWith("image/")) { context.toast("请选择图片文件"); continue; }
      if (file.size > 5 * 1024 * 1024) { context.toast("单张图片不能超过 5 MB"); continue; }
      try {
        const image = await readImage(file);
        if (context.cache.images.length === 5) { context.toast("一次最多发送 5 张图片"); break; }
        context.cache.images.push(image);
        context.cache.version += 1;
        const view = visibleDraft(context);
        if (view) renderAttachments(view);
        persistDraft(context);
      } catch (error) { context.toast(error.message); }
    }
  }
  function crewLabel(command) {
    if (/[\r\n;&|]/.test(command)) return null;
    const words = Array.from(command.matchAll(/"[^"]*"|'[^']*'|\S+/g), match => /^['"]/.test(match[0]) ? match[0].slice(1, -1) : match[0]);
    if (words[0] !== "crew") return null;
    const verb = words[1];
    if (verb === "spawn") {
      const nameIndex = words.indexOf("--name");
      const name = words[nameIndex === -1 ? 2 : nameIndex + 1];
      return name !== undefined && !name.startsWith("--") ? `开新下属 ${name}` : null;
    }
    if (verb === "answer" && /^\d+$/.test(words[2])) return `答复 #${words[2]}`;
    if (verb === "withdraw" && /^\d+$/.test(words[2])) return `撤回拍板 #${words[2]}`;
    if ((verb === "open" || verb === "who" || verb === "join") && words[2] !== undefined) {
      const label = {open:"在 VS Code 打开", who:"查看下属", join:"登记下属"}[verb];
      return `${label} ${words[2]}`;
    }
    if (verb === "card") return words.includes("--scope") || words.includes("--done-when") ? "更新任务卡" : "查看任务卡";
    const labels = {status:"查看组员状态", whoami:"查看自己的任务卡", task:"更新当前任务", ask:"发起拍板", decisions:"查看待拍板", report:"汇报进展", reports:"查看进展", claims:"查看文件归属", handoffs:"查看交接", projects:"查看项目"};
    return Object.hasOwn(labels, verb) ? labels[verb] : null;
  }
  function toolLabel(context, block) {
    const name = block.name.split("__").pop();
    if (name === "SendMessage") return `给 ${nameOf(context, block.input.recipient)} 发了消息`;
    if (name === "Bash" || name === "PowerShell") {
      const label = crewLabel(block.input.command);
      return label === null ? block.input.command.slice(0, 60) : label;
    }
    if (name === "Read" || name === "Edit" || name === "Write") return `${{ Read: "读取", Edit: "修改", Write: "写入" }[name]} ${block.input.file_path.split(/[\\/]/).pop()}`;
    return block.name;
  }
  function renderTool(context, block) {
    const result = context.results.get(block.id);
    const foldKey = `tool:${block.id}`;
    const open = context.cache.expanded.has(foldKey);
    const output = result && (typeof result.content === "string" ? result.content : JSON.stringify(result.content, null, 2));
    return `<button type="button" class="tool indent${result ? "" : " live"}${open ? " open" : ""}" data-chat-fold="${escape(foldKey)}" aria-expanded="${open}">${result ? `<span class="${result.is_error ? "chat-error" : "ok"}">${icon("check")}</span>` : '<span class="spin" aria-label="执行中"></span>'}<span class="label">${escape(toolLabel(context, block))}</span>${icon("right")}</button><div class="tool-steps"><div>输入 · ${escape(block.name)}</div><pre>${escape(JSON.stringify(block.input, null, 2))}</pre>${result ? `<div>${result.is_error ? "执行失败" : "结果"}</div><pre>${escape(output.slice(0, 2000))}</pre>${output.length > 2000 ? '<div>仅显示前 2000 字</div>' : ""}` : "<div>正在执行…</div>"}</div>`;
  }
  function assistantText(context, text, item) {
    return `<div class="m-lead"><div class="av round" style="--s:30px;--tint:var(--accent)">${escape(context.session.name.slice(0, 1))}</div><div class="body"><div class="markdown">${markdown.render(cjkBold(text))}</div><div class="m-meta">${escape(context.session.name)}${item.at ? ` · ${timeOf(item.at)}` : ""}</div></div></div>`;
  }
  function itemHTML(context, item) {
    if (item.kind === "compact_request" || item.kind === "compact") return `<div class="context-divider">${item.kind === "compact_request" ? "压缩上下文" : `上下文已压缩：${CrewContext.tokens(item.pre_tokens)} → ${CrewContext.tokens(item.post_tokens)}`}</div>`;
    if (item.kind === "error") return `<div class="chat-error" role="alert">${escape(item.text)}</div>`;
    if (item.kind === "assistant") {
      let html = "";
      if (item.content !== null) {
        for (const block of item.content) {
          if (block.type === "text") html += assistantText(context, block.text, item);
          if (block.type === "tool_use") html += renderTool(context, block);
        }
      }
      if (item.text) html += assistantText(context, item.text, item);
      return html;
    }
    if (item.source === "peer" || item.source === "crew") {
      const sender = item.source === "crew" && !Object.hasOwn(item, "sender") ? "Crew" : item.sender === "user" ? "用户" : nameOf(context, item.sender);
      const foldKey = `peer:${item.id}`;
      const open = context.cache.expanded.has(foldKey);
      return `<button type="button" class="mline indent${open ? " open" : ""}" data-chat-fold="${escape(foldKey)}" aria-expanded="${open}"><span class="who">${escape(sender)} → ${escape(context.session.name)}：</span><span class="txt">${escape(item.text.replace(/\s+/g, " "))}</span>${item.at ? `<span>${timeOf(item.at)}</span>` : ""}${icon("right")}</button><div class="mfull">${escape(item.text).replace(/\n/g, "<br>")}</div>`;
    }
    const images = item.images ? `<div class="message-images">${item.images.map((name, index) => {
      const params = query(context);
      params.set("name", name);
      const url = `/api/chat/file?${params}`;
      return `<a href="${escape(url)}" target="_blank" rel="noopener noreferrer"><img src="${escape(url)}" alt="图片 ${index + 1}" loading="lazy"></a>`;
    }).join("")}</div>` : "";
    return `<div class="m-user"><div class="bubble"><span class="message-text">${escape(item.text).replace(/\n/g, "<br>")}</span>${images}</div><div class="m-meta${item.source === "qq" ? " qq-meta" : ""}">${item.source === "qq" ? "来自 QQ" : ""}${item.at ? `${item.source === "qq" ? " · " : ""}${timeOf(item.at)}` : ""}</div></div>`;
  }
  function render(context) {
    context.frame = null;
    if (!current(context)) return;
    const follow = atBottom(context);
    const desired = [];
    let lastDay = null;
    for (const item of context.items) {
      if (item.at) {
        const day = dayOf(item.at);
        if (day.key !== lastDay) { desired.push({ key: `date:${item.id}`, className: "div date-div", html: escape(day.label) }); lastDay = day.key; }
      }
      if (item.id === context.cache.unreadAnchor) desired.push({ key: "unread", className: "div unread unread-div", html: "以下是你离开后的消息" });
      desired.push({ key: item.id, className: "chat-event", html: itemHTML(context, item) });
    }
    if (!context.readonly && context.busy && context.session.activity !== null) desired.push({key:"activity", className:"chat-activity", html:CrewActivity.markup(context.session)});
    let position = context.inner.firstChild;
    for (const item of desired) {
      let node = context.nodes.get(item.key);
      if (!node) { node = document.createElement("div"); context.nodes.set(item.key, node); }
      node.className = item.className;
      if (node.dataset.html !== item.html) { node.innerHTML = item.html; node.dataset.html = item.html; }
      if (node !== position) context.inner.insertBefore(node, position);
      position = node.nextSibling;
    }
    while (position) { const next = position.nextSibling; position.remove(); position = next; }
    if (context.initialPosition !== null) {
      if (context.initialPosition === "unread") {
        const divider = context.nodes.get("unread");
        context.scroll.scrollTop = divider.getBoundingClientRect().top - context.scroll.getBoundingClientRect().top + context.scroll.scrollTop - 12;
      } else context.scroll.scrollTop = context.initialPosition === "bottom" ? context.scroll.scrollHeight : context.initialPosition;
      context.initialPosition = null;
    } else if (follow) context.scroll.scrollTop = context.scroll.scrollHeight;
    context.jump.classList.toggle("show", !atBottom(context));
    scheduleRead(context);
  }
  function schedule(context) {
    if (context.frame === null) context.frame = requestAnimationFrame(() => render(context));
  }
  function assistant(context, event) {
    let item = context.byId.get(event.id);
    if (!item) {
      item = { id: event.id, kind: "assistant", seq: event.seq, lastSeq: event.seq, at: event.at, text: "", content: null };
      context.items.push(item); context.byId.set(item.id, item);
    }
    item.lastSeq = event.seq;
    return item;
  }
  function setBusy(context, busy, seq) {
    if (seq < context.busySeq || (context.cache.pendingSendSeq !== null && seq <= context.cache.pendingSendSeq)) return;
    context.busy = busy;
    context.busySeq = seq;
    context.cache.pendingSendSeq = null;
  }
  function apply(context, event, incremental) {
    if (event.seq <= context.cursor) return;
    context.cursor = event.seq;
    if (event.type === "message") {
      const item = { ...event, kind: "message", lastSeq: event.seq };
      context.items.push(item); context.byId.set(item.id, item);
    } else if (event.type === "text") assistant(context, event).text += event.text;
    else if (event.type === "assistant") {
      const item = assistant(context, event);
      item.content = event.content; item.text = "";
      for (const block of event.content) if (block.type === "tool_use") context.toolOwners.set(block.id, item);
    }
    else if (event.type === "tool_result") {
      context.results.set(event.tool_use_id, event);
      const owner = context.toolOwners.get(event.tool_use_id);
      if (owner) owner.lastSeq = event.seq;
    }
    else if (event.type === "status") setBusy(context, event.status === "working", event.seq);
    else if (event.type === "compact_request" || event.type === "compact") {
      context.items.push({...event, id:`${event.type}:${event.seq}`, kind:event.type, lastSeq:event.seq});
      if (incremental && event.type === "compact") notifyCompact(context.session.session_id, event, context.toast);
    }
    else if (event.type === "error") context.items.push({ ...event, id: `error:${event.seq}`, kind: "error", lastSeq: event.seq });
    if (incremental) { composerState(context); schedule(context); }
  }
  function scheduleRead(context) {
    if (context.readonly || context.loading || context.reading || context.readTimer !== null || document.visibilityState !== "visible" || !atBottom(context) || context.cursor <= context.readSeq) return;
    context.readTimer = setTimeout(async () => {
      context.readTimer = null;
      if (!current(context) || document.visibilityState !== "visible" || !atBottom(context)) return;
      const seq = context.cursor;
      context.cache.lastReadAt = Date.now(); context.reading = true;
      try {
        const result = await context.api("/api/chat/read", { session_id: context.session.session_id, seq });
        context.readSeq = result.read_seq; context.reading = false;
        if (current(context)) scheduleRead(context);
      } catch (error) { context.reading = false; context.toast(error.message); }
    }, Math.max(0, 1000 - (Date.now() - context.cache.lastReadAt)));
  }
  function connectionError(context, message) {
    context.error.hidden = message === null;
    context.error.textContent = message;
  }
  async function connect(context) {
    if (!current(context)) return;
    context.stream = new AbortController();
    const params = query(context); params.set("after", context.cursor);
    try {
      const headers = { Accept: "text/event-stream" };
      if (context.cursor > 0) headers["Last-Event-ID"] = String(context.cursor);
      const response = await fetch(`/api/chat/events?${params}`, { headers, signal: context.stream.signal });
      if (!response.ok) { const failure = await response.json(); throw new Error(failure.error); }
      if (!current(context)) return;
      connectionError(context, null);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "", data = [];
      while (current(context)) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        let newline;
        while ((newline = buffer.indexOf("\n")) !== -1) {
          const line = buffer.slice(0, newline).replace(/\r$/, ""); buffer = buffer.slice(newline + 1);
          if (line === "") { if (data.length > 0) apply(context, JSON.parse(data.join("\n")), true); data = []; }
          else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
        }
      }
    } catch (error) {
      if (!current(context) || error.name === "AbortError") return;
      connectionError(context, error.message);
    }
    if (current(context)) context.reconnectTimer = setTimeout(() => connect(context), 3000);
  }
  async function load(context) {
    try {
      const history = await context.api(`/api/chat/history?${query(context)}`);
      if (!current(context)) return;
      for (const event of history.events) apply(context, event, false);
      setBusy(context, history.busy, context.cursor);
      context.readSeq = history.read_seq; context.loading = false;
      if (context.cache.first && !context.readonly) {
        const unread = context.items.find(item => item.lastSeq > history.read_seq);
        context.cache.unreadAnchor = unread ? unread.id : null;
        context.initialPosition = unread ? "unread" : "bottom";
      } else context.initialPosition = context.cache.scrollTop === null ? "bottom" : context.cache.scrollTop;
      context.cache.first = false;
      composerState(context); render(context); connect(context);
    } catch (error) {
      if (current(context)) { context.loading = false; connectionError(context, error.message); composerState(context); }
    }
  }
  async function send(context) {
    const text = context.input.value.trim();
    if (context.send.disabled) return;
    saveDraft(context);
    const draftVersion = context.cache.version;
    const previousBusy = context.busy;
    const sendSeq = context.cursor;
    const payload = { session_id: context.session.session_id, text };
    if (context.cache.images.length > 0) payload.images = context.cache.images.slice();
    context.cache.sending = true;
    context.cache.pendingSendSeq = sendSeq;
    context.busy = true;
    composerState(context);
    try {
      await context.api("/api/chat/send", payload);
      if (context.cache.version === draftVersion) {
        context.cache.text = ""; context.cache.images = [];
        const view = visibleDraft(context);
        if (view) { view.input.value = ""; resizeInput(view); renderAttachments(view); }
        persistDraft(context);
      }
    } catch (error) {
      if (context.cache.pendingSendSeq === sendSeq) {
        context.cache.pendingSendSeq = null;
        const view = visibleDraft(context);
        if (view) view.busy = previousBusy;
      }
      context.toast(error.message);
    } finally {
      context.cache.sending = false;
      const view = visibleDraft(context);
      if (view) composerState(view);
    }
  }
  function mount(options) {
    unmount();
    const cacheKey = `${options.state.project.name}:${options.session.session_id}:${Boolean(options.readonly)}`;
    let cache = sessions.get(cacheKey);
    if (!cache) {
      cache = { expanded: new Set(), scrollTop: null, first: true, unreadAnchor: null, text: "", images: [], version: 0, sending: false, pendingSendSeq: null, lastReadAt: 0 };
      sessions.set(cacheKey, cache);
      if (!options.readonly) {
        try {
          const saved = localStorage.getItem(`crew.draft:${options.state.project.name}:${options.session.session_id}`);
          if (saved !== null) { const draft = JSON.parse(saved); cache.text = draft.text; cache.images = draft.images; }
        } catch (error) { options.toast(error.message); }
      }
    }
    const context = { ...options, cache, items: [], byId: new Map(), nodes: new Map(), results: new Map(), toolOwners: new Map(), cursor: 0, readSeq: 0, busySeq: options.readonly ? 0 : options.session.last_seq, readTimer: null, reconnectTimer: null, frame: null, loading: true, busy: cache.pendingSendSeq === null ? options.session.busy : true, reading: false, initialPosition: null, stream: null };
    active = context;
    options.host.classList.add("chat-host");
    options.host.innerHTML = `<div class="chat-error chat-connection-error" role="alert" hidden></div><div class="scroll chat-scroll" tabindex="0" aria-label="${escape(options.session.name)}的对话记录"><div class="col chat-inner"></div></div><button type="button" class="jump" aria-label="回到最新消息">${icon("down")}回到最新</button>${options.readonly ? '<div class="readonly-note">只读记录 · 下属请在 VS Code 里聊</div>' : `<div class="composer-wrap"><form class="composer"><div class="attach" hidden></div><textarea rows="1" aria-label="给 ${escape(options.session.name)} 发消息" placeholder="跟 ${escape(options.session.name)} 说…（可以直接粘贴截图）"></textarea><div class="c-row"><button type="button" class="icon-btn chat-image-button" title="附截图（也可以直接 Ctrl+V）" aria-label="添加图片">${icon("image")}</button><input class="chat-image-file" type="file" accept="image/*" multiple hidden><span class="hint"></span><button type="submit" class="send" aria-label="发送" disabled>${icon("up")}</button></div></form></div>`}`;
    context.scroll = options.host.querySelector(".chat-scroll"); context.inner = options.host.querySelector(".chat-inner");
    context.jump = options.host.querySelector(".jump"); context.error = options.host.querySelector(".chat-connection-error");
    context.jump.onclick = () => { context.scroll.scrollTop = context.scroll.scrollHeight; context.jump.classList.remove("show"); scheduleRead(context); };
    context.scroll.onscroll = () => { context.cache.scrollTop = context.scroll.scrollTop; context.jump.classList.toggle("show", !atBottom(context)); scheduleRead(context); };
    context.inner.onclick = event => {
      const button = event.target.closest("[data-chat-fold]");
      if (!button) return;
      const item = context.items.find(item => context.nodes.get(item.id).contains(button));
      const node = context.nodes.get(item.id);
      const unchanged = node.dataset.html === itemHTML(context, item);
      const key = button.dataset.chatFold;
      const open = !context.cache.expanded.has(key);
      if (open) context.cache.expanded.add(key); else context.cache.expanded.delete(key);
      button.classList.toggle("open", open); button.setAttribute("aria-expanded", String(open));
      if (unchanged) node.dataset.html = itemHTML(context, item);
    };
    context.visibility = () => scheduleRead(context);
    document.addEventListener("visibilitychange", context.visibility);
    if (!context.readonly) {
      context.input = options.host.querySelector("textarea"); context.send = options.host.querySelector(".send");
      context.hint = options.host.querySelector(".hint"); context.attach = options.host.querySelector(".attach");
      context.input.value = cache.text;
      context.input.oninput = () => { context.cache.version += 1; saveDraft(context); resizeInput(context); composerState(context); };
      context.input.onkeydown = event => {
        if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); if (!context.send.disabled) send(context); }
      };
      context.input.onpaste = event => {
        const files = Array.from(event.clipboardData.items).filter(item => item.type.startsWith("image/")).map(item => item.getAsFile());
        if (files.length > 0) { event.preventDefault(); addImages(context, files); }
      };
      const fileInput = options.host.querySelector(".chat-image-file");
      options.host.querySelector(".chat-image-button").onclick = () => fileInput.click();
      fileInput.onchange = () => { addImages(context, Array.from(fileInput.files)); fileInput.value = ""; };
      context.attach.onclick = event => {
        const button = event.target.closest("[data-remove-image]");
        if (!button) return;
        context.cache.images.splice(Number(button.dataset.removeImage), 1); context.cache.version += 1;
        renderAttachments(context); saveDraft(context);
      };
      options.host.querySelector("form").onsubmit = event => { event.preventDefault(); send(context); };
      renderAttachments(context); resizeInput(context);
    }
    load(context);
    return { update, unmount };
  }
  function update(state) {
    if (!active) return;
    active.state = state;
    const session = state.leaders.concat(state.members).find(item => item.session_id === active.session.session_id);
    if (session) {
      active.session = session;
      if (!active.readonly && session.last_seq >= active.cursor) setBusy(active, session.busy, session.last_seq);
    }
    composerState(active);
    if (!active.loading) schedule(active);
  }
  function unmount() {
    if (!active) return;
    const context = active;
    saveDraft(context); context.cache.scrollTop = context.scroll.scrollTop;
    context.stream?.abort(); clearTimeout(context.readTimer); clearTimeout(context.reconnectTimer);
    if (context.frame !== null) cancelAnimationFrame(context.frame);
    document.removeEventListener("visibilitychange", context.visibility);
    active = null;
  }
  function notifyCompact(id, event, toast) {
    if (event.seq <= (compactNotified.has(id) ? compactNotified.get(id) : 0)) return;
    compactNotified.set(id, event.seq);
    toast(`压缩完成：${CrewContext.tokens(event.pre_tokens)} → ${CrewContext.tokens(event.post_tokens)}`);
  }
  async function watchCompact(id, options) {
    const history = await options.api(`/api/chat/history?session_id=${encodeURIComponent(id)}`);
    const params = new URLSearchParams({session_id:id, project:options.state.project.name, after:history.events.length === 0 ? 0 : history.events[history.events.length - 1].seq});
    const stream = new EventSource(`/api/chat/events?${params}`);
    const close = () => stream.close();
    stream.onmessage = message => {
      const event = JSON.parse(message.data);
      if (event.type === "compact") { notifyCompact(id, event, options.toast); close(); }
      else if (event.type === "error" || event.type === "status" && event.status === "offline") close();
    };
    return close;
  }
  window.CrewChat = {mount, update, unmount, watchCompact};
})();
