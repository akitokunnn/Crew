"use strict";
(() => {
  const CONTEXT_WARN = 0.5, CONTEXT_DANGER = 0.8;
  const tokens = value => value >= 10000 ? `${(value / 10000).toFixed(1)} 万` : String(Math.round(value));
  const escape = value => String(value).replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
  function ring(context, target) {
    if (context === null) return "";
    const ratio = context.used / context.max, percent = Math.round(ratio * 100);
    const maximum = context.max >= 10000 ? `${Number((context.max / 10000).toFixed(1))} 万` : tokens(context.max);
    const hint = target.phase === "queued" ? "已排队，本轮结束后压缩" : target.phase === "submitting" ? "正在请求压缩…" : target.phase ? "正在压缩上下文…" : target.external ? "点击查看原工具压缩方式" : target.busy ? "点击在本轮结束后压缩" : "点击压缩上下文";
    const label = `${target.name} · 上下文已用 ${tokens(context.used)} / ${maximum}（${percent}%） · ${hint}`;
    return `<button type="button" class="context-ring ${ratio >= CONTEXT_DANGER ? "danger" : ratio >= CONTEXT_WARN ? "warn" : "calm"}${target.phase ? " is-compacting" : ""}" data-act="context-compact" data-id="${escape(target.id)}" title="${escape(label)}" aria-label="${escape(label)}" ${target.phase ? 'aria-busy="true" disabled' : ""}><svg viewBox="0 0 20 20" aria-hidden="true"><circle class="context-track" cx="10" cy="10" r="7.5"/><circle class="context-arc" cx="10" cy="10" r="7.5" pathLength="100" stroke-dasharray="${Math.min(100, ratio * 100)} 100"/></svg><span class="context-pct" aria-hidden="true">${percent}%</span></button>`;
  }
  // A freshly resumed Codex thread reports no size before compaction.
  const compactSize = value => value === null ? "未知" : tokens(value);
  window.CrewContext = {tokens, ring, compactSize};
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
  const markdown = window.markdownit({ html: false, breaks: true, linkify: true });
  // Same rule as notifications.cjk_bold: "**标题：**正文" is not bold under CommonMark, so move the punctuation outside.
  const BOLD_PUNCT = String.raw`\u2010-\u2027\u3000-\u303f\uff01-\uff0f\uff1a-\uff20\uff3b-\uff40\uff5b-\uff65.,:;!?()\[\]"'`;
  const BOLD_TAIL = new RegExp(`\\*\\*([^*\\n]+?)([${BOLD_PUNCT}]+)\\*\\*(?=[^\\s*${BOLD_PUNCT}])`, 'g');
  const BOLD_HEAD = new RegExp(`(?<=[^\\s*${BOLD_PUNCT}])\\*\\*([${BOLD_PUNCT}]+)([^*\\n]+?)\\*\\*`, 'g');
  const cjkBold = text => (text ?? "").replace(BOLD_TAIL, '**$1**$2').replace(BOLD_HEAD, '$1**$2**');
  const localPath = value => !value.startsWith("#") && (!/^[a-z][a-z0-9+.-]*:/i.test(value) || /^[a-z]:[\\/]/i.test(value) || /^file:/i.test(value));
  const pathPattern = /(?:[A-Za-z]:[\\/][^\r\n<>"'`|?*\uFF0C\u3002\uFF1B\uFF01\uFF1F\uFF08\uFF09\u3010\u3011]*?|(?:\.\.?[\\/]|[\p{L}\p{N}_-]+[\\/])[^\s<>"'`|?*\uFF0C\u3002\uFF1B\uFF01\uFF1F\uFF08\uFF09\u3010\u3011]*?)\.[A-Za-z0-9]+(?=$|[\s<>"'`|\uFF0C\u3002\uFF1B\uFF01\uFF1F\uFF08\uFF09\u3010\u3011)\]#:])/gu;
  const fileAttrs = (path, env) => `href="#" data-file-path="${escape(path)}" data-file-session="${escape(env.sessionId)}"${env.base === null ? "" : ` data-file-base="${escape(env.base)}"`}`;
  function linkedText(value, env) {
    let output = "", end = 0;
    for (const match of value.matchAll(pathPattern)) {
      const start = match.index;
      if (/https?:\/\/[^\s]*$/.test(value.slice(0, start))) continue;
      output += escape(value.slice(end, start)) + `<a ${fileAttrs(match[0], env)}>${escape(match[0])}</a>`;
      end = start + match[0].length;
    }
    return output + escape(value.slice(end));
  }
  const renderMarkdown = (text, sessionId = "", base = null) => markdown.render(cjkBold(text), {sessionId, base});
  const renderInline = (text, sessionId) => markdown.renderInline(cjkBold(text), {sessionId, base:null});
  markdown.renderer.rules.text = (tokens, index, options, env) => tokens[index].meta === "inside-link" ? escape(tokens[index].content) : linkedText(tokens[index].content, env);
  markdown.renderer.rules.code_inline = (tokens, index, options, env) => {
    const token = tokens[index], value = token.content;
    const wholePath = /[\\/]/.test(value) && /\.[A-Za-z0-9]+$/.test(value) && localPath(value);
    const content = token.meta === "inside-link" ? escape(value) : wholePath ? `<a ${fileAttrs(value, env)}>${escape(value)}</a>` : linkedText(value, env);
    return `<code>${content}</code>`;
  };
  markdown.core.ruler.after("linkify", "file-link-context", state => {
    for (const block of state.tokens) {
      if (block.children === null) continue;
      let inside = false;
      for (const token of block.children) {
        if (token.type === "link_open") inside = true;
        else if (token.type === "link_close") inside = false;
        else if (inside && (token.type === "text" || token.type === "code_inline")) token.meta = "inside-link";
      }
    }
  });
  const originalImage = markdown.renderer.rules.image;
  markdown.renderer.rules.image = (tokens, index, options, env, renderer) => {
    const token = tokens[index], src = decodeURIComponent(token.attrGet("src"));
    if (localPath(src)) token.attrSet("src", fileContentUrl(src, env.sessionId, env.base, true));
    return originalImage(tokens, index, options, env, renderer);
  };
  const linkOpen = markdown.renderer.rules.link_open || ((tokens, index, options, env, renderer) => renderer.renderToken(tokens, index, options));
  markdown.renderer.rules.link_open = (tokens, index, options, env, renderer) => {
    const token = tokens[index], href = decodeURIComponent(token.attrGet("href"));
    if (localPath(href)) {
      token.attrSet("href", "#");
      token.attrSet("data-file-path", href.replace(/^file:\/\/\//i, ""));
      token.attrSet("data-file-session", env.sessionId);
      if (env.base !== null) token.attrSet("data-file-base", env.base);
    }
    tokens[index].attrSet("target", "_blank");
    tokens[index].attrSet("rel", "noopener noreferrer");
    return linkOpen(tokens, index, options, env, renderer);
  };
  function fileContentUrl(path, sessionId, base, raw = false, documentId = null) {
    const params = new URLSearchParams(location.search);
    if (documentId !== null) {
      params.delete("path"); params.delete("session_id"); params.delete("base"); params.set("document_id", documentId);
    } else {
      params.set("path", path);
      if (sessionId !== "") params.set("session_id", sessionId);
      if (base !== null) params.set("base", base);
    }
    if (raw) params.set("raw", "1");
    return "/api/files/content?" + params;
  }
  function initFiles(api, toast) {
    let request = 0;
    const panel = document.createElement("section");
    panel.id = "file-preview";
    panel.className = "card file-preview";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "\u6587\u4ef6\u9884\u89c8");
    document.body.append(panel);
    document.addEventListener("click", async event => {
      const link = event.target.closest("[data-file-path]");
      if (link === null) {
        if (event.target.closest('a[target="_blank"]') !== null) event.stopImmediatePropagation();
        return;
      }
      event.preventDefault();
      event.stopImmediatePropagation();
      const sequence = ++request;
      const documentLink = link.hasAttribute("data-document-id");
      if (documentLink) panel.classList.remove("show");
      const rawDocumentId = link.dataset.documentId;
      if (documentLink && (!/^[1-9]\d*$/.test(rawDocumentId || "") || !Number.isSafeInteger(Number(rawDocumentId)))) { toast("文档编号无效，请重新读取文档列表。"); return; }
      const data = documentLink ? {document_id:Number(rawDocumentId)} : {path:link.dataset.filePath, session_id:link.dataset.fileSession};
      if (!documentLink && link.hasAttribute("data-file-base")) data.base = link.dataset.fileBase;
      try {
        const info = await api("/api/files/resolve", data);
        if (sequence !== request) return;
        if (info.error !== null) throw Error(info.error);
        const renderingSession = documentLink ? (typeof info.session_id === "string" ? info.session_id : "") : data.session_id;
        const resolved = documentLink ? {document_id:data.document_id} : {path:info.path, session_id:data.session_id};
        if (info.type === "external" && info.open_with === "default") { await api("/api/files/open", resolved); return; }
        let content;
        if (info.type === "preview" && info.kind !== "image") {
          const response = await fetch(fileContentUrl(info.path, data.session_id || "", null, false, documentLink ? data.document_id : null));
          content = await response.json();
          if (!response.ok) throw Error(content.error);
        }
        if (sequence !== request) return;
        const openLabel = info.open_with === "vscode" ? "在 VS Code 打开" : "用默认程序打开";
        const openButton = info.open_with === "blocked" ? "" : `<button class="btn file-open">${openLabel}</button>`;
        panel.innerHTML = `<div class="card-head"><div class="file-title">${escape(info.path)}</div><button class="icon-btn file-close" aria-label="\u5173\u95ed">&times;</button></div><div class="card-actions">${openButton}<button class="btn file-reveal">\u5728\u6587\u4ef6\u5939\u4e2d\u663e\u793a</button></div><div class="card-body file-body"></div>`;
        const body = panel.querySelector(".file-body");
        if (info.open_with === "blocked") body.textContent = "这类文件不直接打开，请用‘在文件夹中显示’";
        else if (info.kind === "image") body.innerHTML = `<img class="file-image" alt="${escape(info.path)}" src="${escape(fileContentUrl(info.path, data.session_id || "", null, true, documentLink ? data.document_id : null))}">`;
        else {
          if (content.truncated) body.innerHTML = `<p class="note">\u6587\u4ef6\u8f83\u5927\uff0c\u4ec5\u663e\u793a${escape(content.portion)} 256 KiB</p>`;
          body.innerHTML += info.kind === "markdown" ? `<div class="markdown">${renderMarkdown(content.content, renderingSession, info.path)}</div>` : `<pre class="file-text">${escape(content.content)}</pre>`;
        }
        panel.querySelector(".file-close").onclick = () => { request++; panel.classList.remove("show"); };
        for (const action of (info.open_with === "blocked" ? ["reveal"] : ["open", "reveal"])) panel.querySelector(".file-" + action).onclick = async () => {
          try { await api("/api/files/" + action, resolved); } catch (error) { toast(error.message); }
        };
        panel.classList.add("show");
      } catch (error) { toast(error.message); }
    }, true);
    document.addEventListener("keydown", event => { if (event.key === "Escape" && panel.classList.contains("show")) { event.stopImmediatePropagation(); request++; panel.classList.remove("show"); } });
  }
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
      close: '<path d="m6 6 12 12M6 18 18 6"/>',
      handoff: '<path d="M4 12h15m-5-5 5 5-5 5"/>'
    };
    return `<svg class="i ${kind === "right" ? "xs chev" : "sm"}" viewBox="0 0 24 24" aria-hidden="true">${paths[kind]}</svg>`;
  };
  const current = context => active === context;
  const atBottom = context => context.scroll.scrollHeight - context.scroll.scrollTop - context.scroll.clientHeight < 64;
  const keyFor = context => `crew.draft:${context.state.project.name}:${context.session.session_id}${context.goalId == null ? "" : `:goal:${context.goalId}`}`;
  const goalPayload = context => context.goalId == null ? {} : {goal_id:context.goalId};
  const query = context => new URLSearchParams({ project: context.state.project.name, session_id: context.session.session_id, ...goalPayload(context) });

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
    return active && !active.readonly && active.cache === context.cache ? active : null;
  }
  function composerState(context) {
    if (context.readonly) return;
    const hasContent = context.input.value.trim().length > 0 || context.cache.images.length > 0;
    context.send.disabled = context.loading || context.cache.sending || !hasContent;
    const external = context.goalId == null && context.session.where === "vscode";
    context.hint.textContent = context.busy || context.cache.sending ? "" : external ? "会话仍在原工具中；退出并交给 Crew 后才能接话" : "Enter 发送 · Shift+Enter 换行";
    context.send.hidden = context.busy && !hasContent;
    context.send.title = context.busy ? "工作中插话" : "发送";
    const stopReason = context.goalId != null ? "" : external ? "回复在 VS Code 或终端中运行，请在原工具中断。" : context.session.where !== "bg" && !context.session.running ? "正在等待 Crew 托管会话，稍后可停止。" : "";
    context.stop.hidden = !context.busy;
    context.stop.disabled = !context.busy || context.interrupting || context.confirmingStop || Boolean(stopReason);
    const stopLabel = context.confirmingStop ? "等待确认…" : context.interrupting ? "正在停止…" : context.goalId == null ? "停止运行" : "停止当前回复";
    context.stop.setAttribute("aria-label", stopLabel);
    context.stop.setAttribute("aria-busy", String(context.confirmingStop || context.interrupting));
    context.stop.title = stopReason || (context.confirmingStop || context.interrupting ? stopLabel : context.goalId == null ? "停止运行，暂停关联任务，保留记录和草稿" : "停止当前回复，保留会话和草稿");
    context.stopStatus.textContent = context.interruptError || (context.busy && stopReason ? stopReason : "");
    context.stopStatus.hidden = !context.stopStatus.textContent;
    context.stopStatus.classList.toggle("failed", Boolean(context.interruptError));
    context.deliveryStatus.textContent = context.cache.deliveryError || "";
    context.deliveryStatus.hidden = !context.cache.deliveryError;
  }
  async function interrupt(context) {
    if (!current(context) || context.stop.disabled) return;
    if (context.goalId == null && context.beforeStop) { context.confirmingStop = true; composerState(context); schedule(context); let allowed; try { allowed = await context.beforeStop(); } finally { context.confirmingStop = false; if (current(context)) { composerState(context); schedule(context); } } if (!allowed) return; }
    if (!current(context)) return;
    context.interrupting = true; context.interruptError = ""; context.interruptConfirmed = false;
    composerState(context); schedule(context);
    try {
      const result = await context.api(context.goalId == null ? "/api/session/stop" : "/api/chat/interrupt", {session_id:context.session.session_id, ...goalPayload(context)});
      if (!result.ok) throw new Error("中断请求未被确认，请重试。");
      if (context.goalId == null && current(context)) { context.cache.pendingSendSeq = null; context.busy = false; context.interrupting = false; context.interruptConfirmed = true; composerState(context); schedule(context); }
      // Goal interruption is acknowledged before exit; its journal confirms the actual stop.
    } catch (error) {
      if (!current(context) || context.interruptConfirmed) return;
      context.interrupting = false;
      context.interruptError = `中断未确认：${error.message || "连接失败"}。可以重试。`;
      composerState(context); schedule(context);
    }
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
    if (verb === "answer" && /^\d+$/.test(words[2])) return `答复拍板：${words.slice(3).join(" ")}`;
    if (verb === "withdraw" && /^\d+$/.test(words[2])) return "撤回一道拍板";
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
    const stopped = context.toolStops.get(block.id);
    const ended = stopped && ({handoff:["已转交执行", "当前会话已转交执行，原工具调用未返回结果。"],
      interrupted:["本轮已中断", "本轮已中断，未收到此工具的结果。"],
      failed:["本轮失败", "本轮执行失败，未收到此工具的结果。"],
      offline:["会话已离线", "会话已离线，未收到此工具的结果，结果未确认。"],
      ended:["本轮已结束", "本轮已结束，未收到此工具的结果。"]})[stopped];
    const goalId = stopped === "handoff" && block.name === "crew_goal" && typeof block.input.goal_id === "string" ? block.input.goal_id : null;
    const foldKey = `${context.historyPrefix || ""}tool:${block.id}`;
    const open = context.cache.expanded.has(foldKey);
    const output = result && (typeof result.content === "string" ? result.content : JSON.stringify(result.content, null, 2));
    return `<button type="button" class="tool indent${result || ended ? "" : " live"}${open ? " open" : ""}" data-chat-fold="${escape(foldKey)}" aria-expanded="${open}">${result ? `<span class="${result.is_error ? "chat-error" : "ok"}">${icon("check")}</span>` : ended ? icon(stopped === "handoff" ? "handoff" : "close") : '<span class="spin" aria-label="执行中"></span>'}<span class="label">${escape(toolLabel(context, block))}</span>${!result && ended ? `<span class="tool-state">${ended[0]}</span>` : ""}${icon("right")}</button><div class="tool-steps"><div>输入 · ${escape(block.name)}</div><pre>${escape(JSON.stringify(block.input, null, 2))}</pre>${result ? `<div>${result.is_error ? "执行失败" : "结果"}</div><pre>${escape(output.slice(0, 2000))}</pre>${output.length > 2000 ? '<div>仅显示前 2000 字</div>' : ""}` : ended ? `<div>${ended[1]}</div>${goalId && context.onRouted ? `<button type="button" class="text-link" data-chat-goal="${escape(goalId)}">查看任务 →</button>` : ""}` : "<div>正在执行…</div>"}</div>`;
  }
  function assistantText(context, text, item) {
    return `<div class="m-lead"><div class="av round" style="--s:30px;--tint:var(--accent)">${escape(context.session.name.slice(0, 1))}</div><div class="body"><div class="markdown">${renderMarkdown(text, context.session.session_id)}</div><div class="m-meta">${escape(context.session.name)}${item.at ? ` · ${timeOf(item.at)}` : ""}</div></div></div>`;
  }
  function itemHTML(context, item) {
    if (item.kind === "compact_request" || item.kind === "compact") return `<div class="context-divider">${item.kind === "compact_request" ? "压缩上下文" : `上下文已压缩：${CrewContext.compactSize(item.pre_tokens)} → ${CrewContext.tokens(item.post_tokens)}`}</div>`;
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
      // The sender may have been ended or archived since; its relative paths then resolve against the project root.
      const peer = item.source === "peer" ? context.state.leaders.concat(context.state.members).find(person => person.cc_name === item.sender) : undefined;
      const pathSession = item.source !== "peer" ? context.session.session_id : peer === undefined ? "" : peer.session_id;
      const foldKey = `${context.historyPrefix || ""}peer:${item.id}`;
      const open = context.cache.expanded.has(foldKey);
      return `<button type="button" class="mline indent${open ? " open" : ""}" data-chat-fold="${escape(foldKey)}" aria-expanded="${open}"><span class="who">${escape(sender)} → ${escape(context.session.name)}：</span><span class="txt">${renderInline(item.text.replace(/\s+/g, " "), pathSession)}</span>${item.at ? `<span>${timeOf(item.at)}</span>` : ""}${icon("right")}</button><div class="mfull">${renderMarkdown(item.text, pathSession)}</div>`;
    }
    const images = item.images ? `<div class="message-images">${item.images.map((name, index) => {
      const params = query(context);
      params.set("name", name);
      if (context.goalId != null && context.executionId) params.set("execution_id", context.executionId);
      const url = `/api/chat/file?${params}`;
      return `<a href="${escape(url)}" target="_blank" rel="noopener noreferrer"><img src="${escape(url)}" alt="图片 ${index + 1}" loading="lazy"></a>`;
    }).join("")}</div>` : "";
    const remoteSource = {qq:"QQ", lark:"Lark / 飞书"}[item.source];
    return `<div class="m-user"><div class="bubble"><span class="message-text">${escape(item.text).replace(/\n/g, "<br>")}</span>${images}</div><div class="m-meta${remoteSource ? " qq-meta" : ""}">${remoteSource ? `来自 ${remoteSource}` : ""}${item.at ? `${remoteSource ? " · " : ""}${timeOf(item.at)}` : ""}</div></div>`;
  }
  function* timeline(context) {
    for (const scope of [...(context.previous || []), context]) for (const item of scope.items) {
      yield {scope, item, key:`${scope.historyPrefix || ""}${item.id}`};
    }
  }
  function render(context) {
    context.frame = null;
    if (!current(context)) return;
    const follow = atBottom(context);
    const desired = [];
    let lastDay = null;
    for (const {scope, item, key} of timeline(context)) {
      if (item.at) {
        const day = dayOf(item.at);
        if (day.key !== lastDay) { desired.push({ key: `date:${key}`, className: "div date-div", html: escape(day.label) }); lastDay = day.key; }
      }
      if (scope === context && item.id === context.cache.unreadAnchor) desired.push({ key: "unread", className: "div unread unread-div", html: "以下是你离开后的消息" });
      desired.push({ key, className: "chat-event", html: itemHTML(scope, item) });
    }
    if (!context.readonly && context.busy) {
      const phase = context.confirmingStop ? "等待停止确认" : context.interrupting ? "正在停止…" : context.cache.sending ? "正在发送…" : "等模型回应";
      const html = !context.confirmingStop && !context.interrupting && context.session.activity !== null ? CrewActivity.markup(context.session) : `<span class="activity" role="status"><span class="spin" aria-hidden="true"></span><span class="activity-phase">${phase}</span></span>`;
      desired.push({key:"activity", className:"chat-activity", html});
    }
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
      item = { id: event.id, kind: "assistant", seq: event.seq, lastSeq: event.seq, at: event.at, text: "", content: null, replyText:"", replySeq:0 };
      context.items.push(item); context.byId.set(item.id, item);
    }
    item.lastSeq = event.seq;
    return item;
  }
  function trackReply(item, event) {
    const text = (item.content || []).filter(block => block.type === "text").map(block => block.text).join("") + item.text;
    if (text !== item.replyText) { item.replyText = text; item.replySeq = event.seq; }
  }
  function setBusy(context, busy, seq) {
    if (seq < context.busySeq || (context.cache.pendingSendSeq !== null && seq <= context.cache.pendingSendSeq)) return;
    if (busy && context.settledSeq !== null && seq <= context.settledSeq) return;
    context.busy = busy;
    if (!busy) {
      if (context.interrupting) context.interruptConfirmed = true;
      context.interrupting = false; context.interruptError = "";
    }
    context.busySeq = seq;
    context.cache.pendingSendSeq = null;
  }
  function apply(context, event, incremental) {
    if (event.seq <= context.cursor) return;
    context.cursor = event.seq;
    if (event.type === "message") {
      const item = { ...event, kind: "message", lastSeq: event.seq };
      context.items.push(item); context.byId.set(item.id, item);
    } else if (event.type === "text") {
      const item = assistant(context, event);
      item.text += event.text; trackReply(item, event);
    }
    else if (event.type === "assistant") {
      const item = assistant(context, event);
      item.content = event.content; item.text = "";
      trackReply(item, event);
      for (const block of event.content) if (block.type === "tool_use") context.toolOwners.set(block.id, item);
    }
    else if (event.type === "tool_result") {
      context.results.set(event.tool_use_id, event);
      const owner = context.toolOwners.get(event.tool_use_id);
      if (owner) owner.lastSeq = event.seq;
    }
    else if (event.type === "identity_handoff" || event.type === "turn_result") {
      const stopped = event.type === "identity_handoff" || event.reason === "identity_handoff" ? "handoff" : event.status === "interrupted" ? "interrupted" : event.status === "failed" ? "failed" : "ended";
      for (const [id, owner] of context.toolOwners) {
        if (!context.results.has(id) && !context.toolStops.has(id)) {
          context.toolStops.set(id, stopped); owner.lastSeq = event.seq;
        }
      }
      context.settledSeq = event.seq;
      setBusy(context, false, event.seq);
    }
    else if (event.type === "status") {
      // Historical journals may predate terminal-turn events. Offline means the
      // carrier is gone; it does not establish a tool's success or failure.
      if (event.status === "offline") for (const [id, owner] of context.toolOwners) {
        if (!context.results.has(id) && !context.toolStops.has(id)) {
          context.toolStops.set(id, "offline"); owner.lastSeq = event.seq;
        }
      }
      if (event.status !== "working") context.settledSeq = event.seq;
      setBusy(context, event.status === "working", event.seq); if (incremental && context.goalId != null) context.onExecutionChange?.();
    }
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
        const result = await context.api("/api/chat/read", { session_id: context.session.session_id, seq, ...goalPayload(context) });
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
    const generation = context.connectionGeneration;
    context.stream = new AbortController();
    const params = query(context); params.set("after", context.cursor);
    try {
      const headers = { Accept: "text/event-stream" };
      if (context.cursor > 0) headers["Last-Event-ID"] = String(context.cursor);
      const response = await fetch(`/api/chat/events?${params}`, { headers, signal: context.stream.signal });
      if (!response.ok) { const failure = await response.json(); throw new Error(failure.error); }
      if (!current(context) || generation !== context.connectionGeneration) return;
      connectionError(context, null);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "", data = [];
      while (current(context) && generation === context.connectionGeneration) {
        const chunk = await reader.read();
        if (chunk.done || generation !== context.connectionGeneration) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        let newline;
        while ((newline = buffer.indexOf("\n")) !== -1) {
          const line = buffer.slice(0, newline).replace(/\r$/, ""); buffer = buffer.slice(newline + 1);
          if (line === "") { if (data.length > 0) apply(context, JSON.parse(data.join("\n")), true); data = []; }
          else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
        }
      }
    } catch (error) {
      if (!current(context) || generation !== context.connectionGeneration || error.name === "AbortError") return;
      connectionError(context, error.message);
    }
    if (current(context) && generation === context.connectionGeneration) context.reconnectTimer = setTimeout(() => connect(context), 3000);
  }
  async function load(context) {
    if (!current(context)) return;
    const generation = ++context.loadGeneration;
    clearTimeout(context.historyRetryTimer); context.historyRetryTimer = null;
    context.loading = true; composerState(context);
    if (context.historyFailed) connectionError(context, "正在重新连接对话记录…");
    try {
      const history = await context.api(`/api/chat/history?${query(context)}`);
      if (!current(context) || generation !== context.loadGeneration) return;
      if ((history.execution_id ?? null) !== context.executionId) {
        // Keep the visible transcript until the replacement history arrives;
        // a failed read must not leave an accepted continuation looking empty.
        context.items = []; context.previous = []; context.byId.clear(); context.results.clear();
        context.toolOwners.clear(); context.toolStops.clear(); context.cursor = 0; context.busySeq = 0; context.settledSeq = null;
        context.cache.pendingSendSeq = null;
      }
      context.executionId = history.execution_id ?? null;
      context.historyPrefix = context.goalId != null && context.executionId ? `history:${context.executionId}:` : "";
      context.previous = (history.previous_executions || []).map(value => {
        const scope = {...context, executionId:value.execution_id, historyPrefix:`history:${value.execution_id}:`,
          items:[], byId:new Map(), results:new Map(), toolOwners:new Map(), toolStops:new Map(), cursor:0, busySeq:0, settledSeq:null,
          cache:{...context.cache, pendingSendSeq:null}};
        for (const event of value.events) apply(scope, event, false);
        return scope;
      });
      for (const event of history.events) apply(context, event, false);
      setBusy(context, history.busy, context.cursor);
      context.readSeq = history.read_seq; context.loading = false; context.historyFailed = false;
      connectionError(context, null);
      if (context.cache.first && !context.readonly) {
        const unread = context.items.find(item => item.kind === "assistant" && item.replyText.trim() && item.replySeq > history.read_seq);
        context.cache.unreadAnchor = unread ? unread.id : null;
        context.initialPosition = unread ? "unread" : "bottom";
      } else context.initialPosition = context.cache.scrollTop === null ? "bottom" : context.cache.scrollTop;
      context.cache.first = false;
      composerState(context); render(context); connect(context);
    } catch (error) {
      if (current(context) && generation === context.loadGeneration) {
        context.historyFailed = true;
        connectionError(context, `对话记录暂时无法读取，3 秒后自动重试。${error.message || "请检查连接。"}`);
        composerState(context);
        context.historyRetryTimer = setTimeout(() => { context.historyRetryTimer = null; void load(context); }, 3000);
      }
    }
  }
  async function send(context) {
    const text = context.input.value.trim();
    if (context.send.disabled) return;
    context.interruptConfirmed = false; context.interruptError = "";
    saveDraft(context);
    const draftVersion = context.cache.version;
    const previousBusy = context.busy;
    const sendSeq = context.cursor;
    // Keep this ID with the exact draft until acceptance, including an uncertain network failure.
    const fingerprint = JSON.stringify([text, context.cache.images]);
    if (context.cache.pendingMessage?.fingerprint !== fingerprint) {
      const id = globalThis.crypto?.randomUUID?.() || `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
      context.cache.pendingMessage = {fingerprint, id};
    }
    const payload = { session_id: context.session.session_id, text, message_id:context.cache.pendingMessage.id, ...goalPayload(context) };
    if (context.cache.images.length > 0) payload.images = context.cache.images.slice();
    context.cache.sending = true;
    context.cache.pendingSendSeq = sendSeq;
    context.busy = true;
    context.cache.deliveryError = "";
    composerState(context); schedule(context);
    try {
      const result = await context.api("/api/chat/send", payload);
      if (result.accepted === false || result.accepted === "unknown" || result.delivery === "unknown") throw Error(result.error || "无法确认当前执行是否已接收，请先核对消息记录，保留同一消息编号。");
      context.cache.pendingMessage = null;
      if (context.cache.version === draftVersion) {
        context.cache.text = ""; context.cache.images = [];
        const view = visibleDraft(context);
        if (view) { view.input.value = ""; resizeInput(view); renderAttachments(view); }
        persistDraft(context);
      }
      const view = visibleDraft(context);
      if (view && current(context) && context.goalId == null && result.goal_id != null) context.onRouted?.(result.goal_id);
      if (view && context.goalId != null && result.execution_id != null && result.execution_id !== view.executionId) {
        // First send and context rotation create a different journal. A history read never creates one.
        view.stream?.abort(); view.connectionGeneration++; clearTimeout(view.reconnectTimer); clearTimeout(view.readTimer);
        if (view.frame !== null) { cancelAnimationFrame(view.frame); view.frame = null; }
        view.cache.pendingSendSeq = null; view.cache.unreadAnchor = null; view.cache.scrollTop = null;
        view.readTimer = null; view.reconnectTimer = null; void load(view);
      }
    } catch (error) {
      if (context.cache.pendingSendSeq === sendSeq) {
        context.cache.pendingSendSeq = null;
        const view = visibleDraft(context);
        if (view) view.busy = previousBusy;
      }
      context.cache.deliveryError = `${error.message}（草稿已保留）`;
      context.toast(error.message);
    } finally {
      context.cache.sending = false;
      const view = visibleDraft(context);
      if (view) { composerState(view); schedule(view); }
    }
  }
  function mount(options) {
    unmount();
    const cacheKey = `${options.state.project.name}:${options.session.session_id}:${options.goalId ?? ""}`;
    let cache = sessions.get(cacheKey);
    if (!cache) {
      cache = { expanded: new Set(), scrollTop: null, first: true, unreadAnchor: null, text: "", images: [], version: 0, sending: false, pendingSendSeq: null, lastReadAt: 0 };
      sessions.set(cacheKey, cache);
      try {
        const saved = localStorage.getItem(keyFor(options));
        if (saved !== null) { const draft = JSON.parse(saved); cache.text = draft.text; cache.images = draft.images; }
      } catch (error) { options.toast(error.message); }
    }
    const context = { ...options, session:options.goalId == null ? options.session : {...options.session,activity:null}, cache, items: [], byId: new Map(), nodes: new Map(), results: new Map(), toolOwners: new Map(), toolStops: new Map(), cursor: 0, readSeq: 0, busySeq: options.readonly || options.goalId != null ? 0 : options.session.last_seq, settledSeq:null, executionId:null, connectionGeneration:0, loadGeneration:0, readTimer: null, reconnectTimer: null, historyRetryTimer: null, historyFailed: false, interrupting: false, interruptError: "", interruptConfirmed: false, frame: null, loading: true, busy: cache.pendingSendSeq === null ? options.goalId == null && options.session.busy : true, reading: false, initialPosition: null, stream: null };
    active = context;
    options.host.classList.add("chat-host");
    options.host.innerHTML = `<div class="chat-error chat-connection-error" role="alert" hidden></div><div class="scroll chat-scroll" tabindex="0" aria-label="${escape(options.session.name)}的对话记录"><div class="col chat-inner"></div></div><button type="button" class="jump" aria-label="回到最新消息">${icon("down")}回到最新</button>${options.readonly ? `<div class="readonly-note">${escape(options.readonlyReason || "此会话尚未由 Crew 托管。请先在原工具退出，再从会话操作中接管；历史记录保留。")}</div>` : `<div class="composer-wrap"><form class="composer"><div class="attach" hidden></div><textarea rows="1" aria-label="给 ${escape(options.session.name)} 发消息" placeholder="跟 ${escape(options.session.name)} 说…（可以直接粘贴截图）"></textarea><div class="c-row"><button type="button" class="icon-btn chat-image-button" title="附截图（也可以直接 Ctrl+V）" aria-label="添加图片">${icon("image")}</button><input class="chat-image-file" type="file" accept="image/*" multiple hidden><span class="hint"></span><button type="button" class="chat-stop" aria-label="停止当前回复" hidden><svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none"/></svg></button><button type="submit" class="send" aria-label="发送" disabled>${icon("up")}</button></div><div class="chat-interrupt-status" role="status" aria-live="polite" hidden></div><div class="chat-delivery-status" role="alert" hidden></div></form></div>`}`;
    context.scroll = options.host.querySelector(".chat-scroll"); context.inner = options.host.querySelector(".chat-inner");
    context.jump = options.host.querySelector(".jump"); context.error = options.host.querySelector(".chat-connection-error");
    context.jump.onclick = () => { context.scroll.scrollTop = context.scroll.scrollHeight; context.jump.classList.remove("show"); scheduleRead(context); };
    context.scroll.onscroll = () => { if (!current(context)) return; context.cache.scrollTop = context.scroll.scrollTop; context.jump.classList.toggle("show", !atBottom(context)); scheduleRead(context); };
    context.inner.onclick = async event => {
      const goalButton = event.target.closest("[data-chat-goal]");
      if (goalButton) {
        try { await context.onRouted(goalButton.dataset.chatGoal); }
        catch (error) { context.toast("打开任务失败：" + error.message); }
        return;
      }
      const button = event.target.closest("[data-chat-fold]");
      if (!button) return;
      const entry = [...timeline(context)].find(({key}) => context.nodes.get(key)?.contains(button));
      if (!entry) return;
      const {scope, item, key:nodeKey} = entry;
      const node = context.nodes.get(nodeKey);
      const unchanged = node.dataset.html === itemHTML(scope, item);
      const key = button.dataset.chatFold;
      const open = !context.cache.expanded.has(key);
      if (open) context.cache.expanded.add(key); else context.cache.expanded.delete(key);
      button.classList.toggle("open", open); button.setAttribute("aria-expanded", String(open));
      if (unchanged) node.dataset.html = itemHTML(scope, item);
    };
    context.visibility = () => scheduleRead(context);
    document.addEventListener("visibilitychange", context.visibility);
    if (!context.readonly) {
      context.input = options.host.querySelector("textarea"); context.send = options.host.querySelector(".send");
      context.stop = options.host.querySelector(".chat-stop"); context.stopStatus = options.host.querySelector(".chat-interrupt-status");
      context.deliveryStatus = options.host.querySelector(".chat-delivery-status");
      context.stop.onclick = () => { void interrupt(context); };
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
      active.session = active.goalId == null ? session : {...session,activity:null};
      // Identity state spans goals. Only the selected execution's history/SSE can set its busy state.
      if (!active.readonly && active.goalId == null && session.last_seq >= active.cursor) setBusy(active, session.busy, session.last_seq);
    }
    composerState(active);
    if (!active.loading) schedule(active);
  }
  function unmount() {
    if (!active) return;
    const context = active;
    saveDraft(context); context.cache.scrollTop = context.scroll.scrollTop;
    context.stream?.abort(); clearTimeout(context.readTimer); clearTimeout(context.reconnectTimer); clearTimeout(context.historyRetryTimer);
    if (context.frame !== null) cancelAnimationFrame(context.frame);
    document.removeEventListener("visibilitychange", context.visibility);
    active = null;
  }
  function notifyCompact(id, event, toast) {
    if (event.seq <= (compactNotified.has(id) ? compactNotified.get(id) : 0)) return;
    compactNotified.set(id, event.seq);
    toast(`压缩完成：${CrewContext.compactSize(event.pre_tokens)} → ${CrewContext.tokens(event.post_tokens)}`);
  }
  async function watchCompact(id, options) {
    const history = await options.api(`/api/chat/history?session_id=${encodeURIComponent(id)}`);
    const params = new URLSearchParams({session_id:id, project:options.state.project.name, after:history.events.length === 0 ? 0 : history.events[history.events.length - 1].seq});
    const stream = new EventSource(`/api/chat/events?${params}`);
    const close = () => stream.close();
    const finish = event => { close(); options.onFinish?.(event); };
    stream.onmessage = message => {
      const event = JSON.parse(message.data);
      if (event.type === "compact") { notifyCompact(id, event, options.toast); finish(event); }
      else if (event.type === "error" || event.type === "compact_cancelled" || event.type === "status" && event.status === "offline") finish(event);
    };
    stream.onerror = () => { if (stream.readyState === EventSource.CLOSED) finish({type:"error", text:"压缩结果连接已关闭，请检查会话状态"}); };
    return close;
  }
  window.CrewChat = {mount, update, unmount, watchCompact, renderMarkdown, renderInline, initFiles};
})();
