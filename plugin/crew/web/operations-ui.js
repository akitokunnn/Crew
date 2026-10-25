"use strict";
(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const clone = value => JSON.parse(JSON.stringify(value));
  const commandsText = value => JSON.stringify(value, null, 2);
  function parseCommands(text, label) {
    let value;
    try { value = JSON.parse(text); }
    catch (error) { throw Error(`${label}格式不正确：${error.message}`); }
    if (!Array.isArray(value) || value.some(command => !Array.isArray(command) || !command.length || command.some(arg => typeof arg !== "string" || !arg.trim()))) throw Error(`${label}需要填写命令列表，每条命令包含程序名和参数。`);
    return value;
  }
  function policyDraft(policy) {
    return {...clone(policy), timeout_seconds:String(policy.timeout_seconds), reproduce:commandsText(policy.reproduce), verify:commandsText(policy.verify)};
  }
  function policyValue(draft) {
    return {...draft, timeout_seconds:Number(draft.timeout_seconds), reproduce:parseCommands(draft.reproduce, "复现命令"), verify:parseCommands(draft.verify, "验证命令")};
  }
  function create(options) {
    const api = options.api, root = document.createElement("section");
    root.className = "co-workspace";
    root.innerHTML = `<div class="co-toolbar"><div class="co-tabs" role="tablist" aria-label="后台与运维">${[["chat","对话"],["status","状态"],["config","自动修复"]].map(([id,label]) => `<button type="button" role="tab" data-operations-tab="${id}" aria-controls="co-operations-panel" aria-selected="${id === "chat"}" tabindex="${id === "chat" ? 0 : -1}">${label}</button>`).join("")}</div><button type="button" class="co-brain" data-operations-action="brain">运维 AI 大脑 <span aria-hidden="true">↗</span></button></div><div class="co-panel" id="co-operations-panel" role="tabpanel"></div>`;
    const panel = root.querySelector(".co-panel");
    let tab = "chat", active = false, timer = null, healthCleanup = null, snapshot = null;
    let chat = null, chatLoaded = false, chatError = "", chatReadError = "", chatRead = 0, chatNote = "", chatDraft = "", pending = null, messageId = null;
    let config = null, configDraft = null, configError = "", configNote = "", configLoading = false, configSaving = false, advanced = false;
    let chatScroll = 0, chatPinned = true, messageSignature = "";
    function clearTimer() { clearTimeout(timer); timer = null; }
    function unmountHealth() { if (healthCleanup) { healthCleanup(); healthCleanup = null; } }
    function shown() { return active && !document.hidden; }
    function schedule() {
      clearTimer();
      if (shown() && tab === "chat") timer = setTimeout(() => { timer = null; void readChat(); }, chatReadError ? 15000 : chat?.busy ? 1200 : 4000);
    }
    function chatControls() {
      const send = panel.querySelector('[data-operations-action="send"]');
      if (!send) return;
      const busy = Boolean(chat?.busy), status = panel.querySelector(".co-chat-status");
      send.disabled = Boolean(pending) || busy || !chatLoaded || !chatDraft.trim();
      send.textContent = pending === "send" ? "正在发送…" : "发送";
      const stop = panel.querySelector('[data-operations-action="interrupt"]');
      stop.hidden = !busy; stop.disabled = Boolean(pending); stop.textContent = pending === "interrupt" ? "正在停止…" : "停止";
      const brain = panel.querySelector(".co-last-brain");
      brain.hidden = !chat?.brain;
      if (chat?.brain) brain.textContent = "上次：" + [({claude:"Claude Code",codex:"Codex"})[chat.brain.runtime] || chat.brain.runtime, chat.brain.model || "原生默认"].filter(Boolean).join(" · ");
      status.textContent = chatError || chatReadError || chat?.error || chatNote || (busy ? "运维 AI 正在回复…" : "发消息才调用 AI；打开此页不会唤醒模型。");
      status.classList.toggle("co-error", Boolean(chatError || chatReadError || chat?.error));
      panel.querySelector('[data-operations-action="retry-chat"]').hidden = !chatReadError;
    }
    function messages() {
      const list = panel.querySelector(".co-messages");
      if (!list) return;
      const signature = JSON.stringify([chatLoaded, chat?.older_count, chat?.messages || []]);
      if (messageSignature !== signature) {
        const nearBottom = list.scrollHeight - list.clientHeight - list.scrollTop < 60;
        const top = list.scrollTop;
        list.innerHTML = !chatLoaded ? '<div class="co-chat-empty"><h2>正在读取运维对话…</h2></div>' : !chat?.messages?.length ? '<div class="co-chat-empty"><span class="co-ops-mark" aria-hidden="true">⌘</span><h2>和运维 AI 聊聊</h2><p>后台连不上、会话卡住、设置不清楚，都可以直接问。</p><button type="button" data-operations-example="帮我检查一下 Crew 当前的连接和运行状态，告诉我有没有需要处理的问题。">检查当前运行状态 <span aria-hidden="true">↗</span></button></div>' : `${chat.older_count > 0 ? `<p class="co-history-count">显示最近 ${chat.messages.length} 条，更早记录已保留。</p>` : ""}${chat.messages.map(item => `<article class="co-message co-message-${item.role === "user" ? "user" : "assistant"}"><div class="co-message-label">${item.role === "user" ? "你" : "运维 AI"}${item.at ? `<time>${esc(new Date(item.at).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"}))}</time>` : ""}</div><div class="co-message-text">${esc(item.text)}</div></article>`).join("")}`;
        messageSignature = signature;
        list.scrollTop = nearBottom && chatPinned ? list.scrollHeight : top;
      }
      chatControls();
    }
    async function readChat() {
      clearTimer();
      if (pending) { schedule(); return; }
      const request = ++chatRead;
      try { const value = await api.snapshot(); if (request !== chatRead) return; chat = value; chatLoaded = true; chatReadError = ""; }
      catch (error) { if (request !== chatRead) return; chatReadError = error.message || String(error); }
      if (active && tab === "chat") { messages(); schedule(); }
    }
    async function send() {
      if (pending || chat?.busy || !chatLoaded || !chatDraft.trim()) return;
      pending = "send"; ++chatRead; chatError = ""; chatNote = "";
      const text = chatDraft, id = messageId || crypto.randomUUID(); messageId = id; chatControls();
      try {
        chat = await api.send({text, message_id:id}); chatLoaded = true;
        // Editing the composer while an IPC request is pending must preserve the new text.
        if (chatDraft === text) { chatDraft = ""; const input = panel.querySelector(".co-composer textarea"); if (input) input.value = ""; }
        messageId = null; chatPinned = true;
      } catch (error) { chatError = error.message || String(error); }
      finally { pending = null; if (active && tab === "chat") { messages(); schedule(); } }
    }
    async function interrupt() {
      if (pending || !chat?.busy) return;
      pending = "interrupt"; ++chatRead; chatError = ""; chatControls();
      try { chat = await api.interrupt(); chatNote = "已请求停止，正在等待当前回复结束。"; }
      catch (error) { chatError = error.message || String(error); }
      finally { pending = null; if (active && tab === "chat") { messages(); schedule(); } }
    }
    function configControls() {
      const save = panel.querySelector('[data-operations-action="save-config"]');
      if (!save) return;
      const dirty = config && JSON.stringify(configDraft) !== JSON.stringify(policyDraft(config.policy));
      save.disabled = configLoading || configSaving || !configDraft || !dirty; save.textContent = configSaving ? "正在保存…" : "保存运维配置";
      panel.querySelector('[data-operations-action="reset-config"]').disabled = configLoading || configSaving || !dirty;
      const status = panel.querySelector(".co-config-status");
      for (const input of panel.querySelectorAll("[data-operations-field]")) input.disabled = configSaving;
      status.textContent = configError || configNote || (dirty ? "有尚未保存的运维配置" : "保存后用于下一次事故处理，不会启动修复或重启后台。");
      status.classList.toggle("co-error", Boolean(configError));
    }
    async function readConfig() {
      if (configLoading || configSaving) return;
      configLoading = true; configError = ""; if (tab === "config") renderConfig();
      try {
        config = await api.configGet();
        if (!configDraft) configDraft = policyDraft(config.policy);
        configError = config.error || "";
      } catch (error) { configError = error.message || String(error); }
      finally { configLoading = false; if (active && tab === "config") renderConfig(); }
    }
    async function saveConfig() {
      if (configSaving || !configDraft) return;
      configError = ""; configNote = "";
      let value;
      try { value = policyValue(configDraft); }
      catch (error) { configError = error.message; configControls(); return; }
      configSaving = true; configControls();
      try { config = await api.configSave(value); configDraft = policyDraft(config.policy); configNote = "运维配置已保存，下一次事故处理生效。"; }
      catch (error) { configError = error.message || String(error); }
      finally { configSaving = false; if (active && tab === "config") renderConfig(); }
    }
    function renderChat() {
      panel.innerHTML = `<section class="co-chat" aria-label="运维 AI 对话"><div class="co-chat-heading"><div><h2>运维 AI</h2><p>所有工作区共用。对话用于诊断；自动修复遵循已保存策略。</p><p class="co-last-brain" hidden></p></div><span class="cs-badge">按需响应</span></div><div class="co-messages" tabindex="0" aria-label="聊天记录"></div><form class="co-composer"><label class="co-sr-only" for="co-chat-input">发给运维 AI</label><textarea id="co-chat-input" rows="3" maxlength="8000" placeholder="例如：为什么这个工作区连不上？">${esc(chatDraft)}</textarea><div class="co-compose-footer"><span>Enter 发送 · Shift + Enter 换行</span><div><button type="button" class="cs-button" data-operations-action="interrupt" hidden>停止</button><button type="submit" class="cs-button cs-primary" data-operations-action="send">发送</button></div></div></form><div class="co-status-row"><p class="co-chat-status" role="status"></p><button type="button" class="co-retry" data-operations-action="retry-chat" hidden>重新连接</button></div></section>`;
      const list = panel.querySelector(".co-messages"); messageSignature = "";
      messages(); list.scrollTop = chatPinned ? list.scrollHeight : chatScroll;
      list.addEventListener("scroll", () => { chatScroll = list.scrollTop; chatPinned = list.scrollHeight - list.clientHeight - list.scrollTop < 60; });
    }
    function renderConfig() {
      if (!configDraft) {
        panel.innerHTML = `<section class="cs-card"><h2>${configLoading ? "正在读取运维配置…" : "运维配置尚未读取"}</h2><p class="co-error" role="alert">${esc(configError)}</p>${configLoading ? "" : '<button type="button" class="cs-button" data-operations-action="retry-config">重新读取</button>'}</section>`;
        return;
      }
      const value = configDraft;
      panel.innerHTML = `<form class="co-config"><section class="cs-card"><div class="cs-card-heading"><div><h2>故障自动修复</h2><p>正常巡检不调用模型。开启后，故障按已配置的复现与验证命令，在独立分支处理。</p></div><label class="co-toggle"><input type="checkbox" data-operations-field="enabled" ${value.enabled ? "checked" : ""}>启用</label></div><div class="co-form-fields"><label> Crew 源码目录<input data-operations-field="repository" value="${esc(value.repository)}" placeholder="Crew 的 Git 仓库根目录" autocomplete="off"><small>填写 Crew 本身的源码位置，和各个业务工作区分开。</small></label><div class="co-form-pair"><label>每次处理时限 · 秒<input type="number" min="1" max="600" data-operations-field="timeout_seconds" value="${esc(value.timeout_seconds)}"></label><label>项目合入分支<input data-operations-field="merge_ref" value="${esc(value.merge_ref)}" placeholder="例如 main；不设置可留空" autocomplete="off"></label></div></div><details class="co-advanced" ${advanced ? "open" : ""}><summary>复现与验证命令 <span>高级配置</span></summary><p>先确认问题确实存在，再检查修复是否有效。每条命令写成程序名和参数的列表；下面只是格式示例，请换成项目实际使用的命令。</p><label>复现命令<textarea data-operations-field="reproduce" rows="5" spellcheck="false" placeholder='[["python", "-m", "unittest", "tests.test_connection"]]'>${esc(value.reproduce)}</textarea></label><label>验证命令<textarea data-operations-field="verify" rows="5" spellcheck="false" placeholder='[["python", "-m", "unittest", "discover", "-s", "plugin/tests"]]'>${esc(value.verify)}</textarea></label><small>修复先独立验证，提交与合并仍遵守项目规则。</small></details></section><section class="co-config-bottom"><p class="co-config-status" role="status"></p><div><button type="button" class="cs-button" data-operations-action="reset-config">撤销更改</button><button type="submit" class="cs-button cs-primary" data-operations-action="save-config">保存运维配置</button></div></section><details class="co-config-location"><summary>配置保存位置</summary><p>${esc(config.policy_path)}</p></details></form>`;
      panel.querySelector(".co-advanced").addEventListener("toggle", event => { advanced = event.target.open; });
      configControls();
    }
    function renderStatus() {
      panel.innerHTML = `<section class="cs-card co-status-facts"><h2>当前启动位置</h2><p class="cs-hint">这些是实际运行位置，随启动方式确定。工作区路径可在“工作区设置”中修改；运维修复策略在此页“自动修复”中修改。</p><dl class="cs-paths"><dt>数据目录 · HOME</dt><dd>${esc(snapshot?.home || "尚未从后台读取")}</dd><dt>后台程序目录</dt><dd>${esc(snapshot?.backend || "尚未从后台读取")}</dd>${options.service ? `<dt>连接地址</dt><dd>${esc(options.service())}</dd>` : ""}</dl></section><div class="co-native-health"></div>`;
      if (options.mountHealth) healthCleanup = options.mountHealth(panel.querySelector(".co-native-health"), snapshot) || null;
    }
    function render() {
      clearTimer(); unmountHealth();
      for (const button of root.querySelectorAll("[data-operations-tab]")) { const selected = button.dataset.operationsTab === tab; button.setAttribute("aria-selected", String(selected)); button.tabIndex = selected ? 0 : -1; if (selected) panel.setAttribute("aria-label", button.textContent); }
      if (tab === "chat") { renderChat(); if (active) void readChat(); }
      if (tab === "status") renderStatus();
      if (tab === "config") { renderConfig(); if (!config && active) void readConfig(); }
    }
    root.addEventListener("click", event => {
      const button = event.target.closest("button"); if (!button || !root.contains(button)) return;
      if (button.dataset.operationsTab) { tab = button.dataset.operationsTab; render(); return; }
      if (button.dataset.operationsExample) { chatDraft = button.dataset.operationsExample; messageId = null; const input = panel.querySelector("textarea"); input.value = chatDraft; input.focus(); chatControls(); return; }
      const action = button.dataset.operationsAction;
      if (action === "brain") options.onBrain();
      if (action === "retry-chat") void readChat();
      if (action === "interrupt") void interrupt();
      if (action === "retry-config") void readConfig();
      if (action === "reset-config" && !configSaving) { configDraft = policyDraft(config.policy); configError = ""; configNote = ""; renderConfig(); }
    });
    root.addEventListener("input", event => {
      if (event.target.matches(".co-composer textarea")) { chatDraft = event.target.value; messageId = null; chatError = ""; chatControls(); }
      const field = event.target.dataset.operationsField;
      if (field && configDraft && !configSaving) { configDraft[field] = field === "enabled" ? event.target.checked : event.target.value; configError = ""; configNote = ""; configControls(); }
    });
    root.addEventListener("change", event => {
      if (event.target.dataset.operationsField === "enabled" && configDraft && !configSaving) { configDraft.enabled = event.target.checked; configError = ""; configNote = ""; configControls(); }
    });
    root.addEventListener("submit", event => { event.preventDefault(); if (event.target.matches(".co-composer")) void send(); if (event.target.matches(".co-config")) void saveConfig(); });
    root.addEventListener("keydown", event => {
      if (event.target.dataset.operationsTab && ["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) {
        event.preventDefault(); const tabs = ["chat","status","config"], index = tabs.indexOf(tab);
        tab = event.key === "Home" ? tabs[0] : event.key === "End" ? tabs[2] : tabs[(index + (event.key === "ArrowRight" ? 1 : 2)) % tabs.length]; render(); root.querySelector(`[data-operations-tab="${tab}"]`).focus();
      }
      if (event.target.matches(".co-composer textarea") && event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); void send(); }
    });
    const visibility = () => { if (!shown()) clearTimer(); else if (tab === "chat") void readChat(); };
    document.addEventListener("visibilitychange", visibility);
    function hide() { active = false; clearTimer(); unmountHealth(); }
    return {mount(host, value) { snapshot = value; active = true; host.append(root); render(); return hide; }, hide,
      getState:() => ({tab, chatDraft, configDraft:configDraft && clone(configDraft), chatLoaded, chatError:chatError || chatReadError})};
  }
  window.CrewOperations = {create, parseCommands, policyDraft, policyValue};
})();
