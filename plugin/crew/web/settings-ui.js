"use strict";
(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const clone = value => JSON.parse(JSON.stringify(value));
  const empty = () => ({runtime:null, model:null, effort:null});
  const tasks = [["digest", "频道摘要", "将交流整理成摘要"], ["git", "Git 管理员", "检查改动、整理提交与合并"], ["operations", "后台事故处理", "按证据分析并验证修复"]];
  const categories = [["ai", "AI 大脑", "载体、模型与任务分配"], ["execution", "执行权限", "文件与网络的访问范围"], ["appearance", "外观", "让工作区更顺手"], ["tools", "工具与更新", "本机执行工具"], ["connections", "QQ 与连接", "离开电脑后的通知"], ["backend", "后台信息", "数据与服务位置"]];
  const icons = {
    ai:'<rect x="6" y="6" width="12" height="12" rx="3"/><path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4"/><rect x="9" y="9" width="6" height="6" rx="1"/>',
    execution:'<path d="m12 3 8 3v6c0 4-4 7-8 9-4-2-8-5-8-9V6l8-3Z"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
    appearance:'<circle cx="12" cy="12" r="9"/><path d="M12 3v18"/><path d="M12 3a9 9 0 0 1 0 18Z" fill="currentColor" stroke="none"/>',
    tools:'<path d="M14.8 6.2a5 5 0 0 0-6.6 6.6L3 18a2.1 2.1 0 0 0 3 3l5.2-5.2a5 5 0 0 0 6.6-6.6L14 13l-3-3 3.8-3.8Z"/>',
    connections:'<path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l1.8-4.2A8.5 8.5 0 1 1 21 11.5Z"/><path d="M8 11h8"/>',
    backend:'<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.01M7 17.5h.01M11 6.5h6m-6 11h6"/>',
    back:'<path d="m10 6-6 6 6 6m-6-6h16"/>',
    close:'<path d="m6 6 12 12M6 18 18 6"/>'
  };
  const icon = name => `<svg class="cs-icon" viewBox="0 0 24 24" aria-hidden="true">${icons[name]}</svg>`;
  const permissions = {codex:[["read-only","只读"],["workspace-write","项目沙箱"],["danger-full-access","完全访问"]], claude:[["default","原生默认"],["acceptEdits","允许编辑"],["bypassPermissions","完全访问"]]};
  function initialExecution(snapshot) { return {codex:snapshot.execution?.codex || "workspace-write", claude:snapshot.execution?.claude || "default"}; }
  function validateExecution(value) {
    for (const runtime of Object.keys(permissions)) if (!permissions[runtime].some(([id]) => id === value[runtime])) throw Error("请选择有效的执行权限。");
    return clone(value);
  }
  function executionMarkup(value) {
    return `<div class="cs-task-list">${[["codex","Codex","只读用于查看与分析；项目沙箱允许在当前项目内修改。"],["claude","Claude Code","原生默认沿用工具的权限流程；允许编辑可执行文件编辑。"]].map(([id,title,description]) => `<section class="cs-card"><div class="cs-card-heading"><div><h2>${title}</h2><p>${description}</p></div></div><div class="cs-fields cs-permission-options"><label>权限范围<select data-execution-runtime="${id}">${permissions[id].map(([mode,label]) => `<option value="${mode}" ${value[id] === mode ? "selected" : ""}>${label}</option>`).join("")}</select></label></div></section>`).join("")}</div><p class="cs-warning">完全访问可操作项目外文件和网络。请按任务需要明确选择。</p><p class="cs-footnote">控制交互会话；摘要与后台修复保持各自的隔离范围。下一次启动载体时生效，不主动重启或唤醒，不修改工具全局配置。</p>`;
  }
  function defaultRuntime(ai, catalog) {
    if (ai?.default?.runtime) return ai.default.runtime;
    const available = catalog.filter(item => item.available);
    return available.length === 1 ? available[0].id : "";
  }
  function initialAI(snapshot) {
    const ai = {default:empty(), digest:null, git:null, operations:null, ...clone(snapshot.ai || {})};
    if (!ai.default.runtime) ai.default.runtime = defaultRuntime(ai, snapshot.runtimes || []) || null;
    return ai;
  }
  function validate(ai, catalog) {
    if (!defaultRuntime(ai, catalog)) throw Error(catalog.some(item => item.available) ? "请先选择默认载体。" : "没有可用的执行工具，请先安装并登录，再重新检测。");
    for (const key of ["default", ...tasks.map(item => item[0])]) {
      if (ai[key] === null) continue;
      for (const field of ["model", "effort"]) if (ai[key][field] !== null && !ai[key][field].trim()) throw Error("模型和 effort 为空时请选择原生默认。");
      const selectedRuntime = ai[key].runtime || defaultRuntime(null,catalog);
      if (!selectedRuntime) throw Error(`${key === "default" ? "默认大脑" : tasks.find(item => item[0] === key)[1]}：请选择执行载体，或使用“跟随默认”。`);
      const runtime = catalog.find(item => item.id === selectedRuntime), model = runtime?.models?.find(item => item.id === ai[key].model);
      if (ai[key].effort !== null && model && Array.isArray(model.efforts) && !model.efforts.includes(ai[key].effort)) throw Error(`${model.name || model.id} 不支持所填的 effort，请按实际支持情况修改。`);
    }
    return clone(ai);
  }
  function choiceOptions(value, choices) {
    return `<option value="">原生默认</option>${choices.map(item => `<option value="${esc(item.id)}" ${item.id === value ? "selected" : ""}>${esc(item.name || item.id)}</option>`).join("")}${value && !choices.some(item => item.id === value) ? `<option value="${esc(value)}" selected>自定义 · ${esc(value)}</option>` : ""}`;
  }
  function choiceMarkup(key, field, value, choices, custom) {
    const attrs = `data-ai-key="${key}" data-ai-field="${field}"`;
    const input = custom ? `<input ${attrs} value="${esc(value)}" placeholder="输入工具支持的名称" autocomplete="off">` : `<select ${attrs}>${choiceOptions(value, choices)}</select>`;
    return `<div class="cs-choice">${input}<button type="button" data-ai-custom="${key}:${field}">${custom ? "返回列表" : "自定义输入"}</button></div>`;
  }
  function selectionMarkup(key, selection, catalog, customFields = new Set()) {
    const runtime = catalog.find(item => item.id === (selection.runtime || defaultRuntime(null,catalog))), model = runtime?.models?.find(item => item.id === selection.model);
    const efforts = model && Array.isArray(model.efforts) ? model.efforts : runtime?.efforts || [];
    const saved = selection.runtime && !runtime ? `<option value="${esc(selection.runtime)}" selected>${esc(selection.runtime)} · 未检测到</option>` : "";
    return `<div class="cs-fields"><label>执行载体<select data-ai-key="${key}" data-ai-field="runtime"><option value="">${catalog.filter(item => item.available).length === 1 ? "自动使用唯一可用载体" : "请选择载体"}</option>${catalog.map(item => `<option value="${esc(item.id)}" ${item.id === selection.runtime ? "selected" : ""} ${item.available ? "" : "disabled"}>${esc(item.name || item.id)}${item.available ? "" : " · 未就绪"}</option>`).join("")}${saved}</select></label><label>模型${choiceMarkup(key, "model", selection.model, runtime?.models || [], customFields.has(key+":model"))}</label><label>思考强度 · effort${choiceMarkup(key, "effort", selection.effort, efforts.map(id => ({id})), customFields.has(key+":effort"))}</label></div>${runtime && !runtime.available ? `<p class="cs-warning">${esc(runtime.error || "该载体暂不可用，请检查安装与登录。已保存的选择会保留。")}</p>` : ""}`;
  }
  function create(options) {
    const root = options.host;
    const customFields = new Set();
    let snapshot = null, draft = null, executionDraft = null, category = "ai", loading = false, saving = false, error = "", note = "", cleanup = null, generation = 0;
    root.classList.add("crew-settings");
    root.innerHTML = `<div class="cs-sidebar"><div class="cs-brand"><span class="cs-mark" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M14.26 8.54a4.9 4.9 0 1 0 0 6.92" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"/><circle cx="17.4" cy="16.4" r="1.75" fill="currentColor"/></svg></span><div><strong>设置</strong><small>Crew · 本机偏好</small></div></div><nav aria-label="设置分类">${categories.filter(([id]) => !["appearance", "connections"].includes(id) || options.panels?.[id]).map(([id, label, description]) => `<button type="button" data-settings-category="${id}"><span class="cs-nav-icon">${icon(id)}</span><span>${label}<small>${description}</small></span></button>`).join("")}</nav>${options.onClose ? `<button type="button" class="cs-back" data-settings-action="close">${icon("back")}返回工作区</button>` : ""}<p class="cs-local">本机偏好 · 所有工作区共用<span class="cs-font-credit">HarmonyOS Sans · 华为</span></p></div><div class="cs-body"><header class="cs-header"><div><span class="cs-eyebrow">偏好设置</span><h1 id="crew-settings-title"></h1><p class="cs-subtitle"></p></div>${options.onClose ? `<button type="button" class="cs-close" data-settings-action="close" aria-label="关闭设置">${icon("close")}</button>` : ""}</header><div class="cs-content"></div><footer class="cs-footer"><p class="cs-status" role="status"></p><button type="button" class="cs-button" data-settings-action="reset">撤销更改</button><button type="button" class="cs-button cs-primary" data-settings-action="save">保存设置</button></footer></div>`;
    const content = root.querySelector(".cs-content"), footer = root.querySelector(".cs-footer"), status = root.querySelector(".cs-status");
    function release() { if (cleanup) { cleanup(); cleanup = null; } }
    function changed() { return snapshot && (JSON.stringify(draft) !== JSON.stringify(snapshot.ai) || JSON.stringify(executionDraft) !== JSON.stringify(initialExecution(snapshot))); }
    function controls() {
      const dirty = changed();
      status.textContent = error || note || (dirty ? "有尚未保存的设置" : "保存后用于新任务；已有会话保留各自的设置。");
      status.classList.toggle("cs-error", Boolean(error));
      root.querySelector('[data-settings-action="save"]').disabled = !snapshot || loading || saving || !dirty;
      root.querySelector('[data-settings-action="save"]').textContent = saving ? "正在保存…" : "保存设置";
      root.querySelector('[data-settings-action="reset"]').disabled = !snapshot || saving || !dirty;
    }
    function renderAI() {
      const catalog = snapshot.runtimes || [];
      content.innerHTML = `<section class="cs-card cs-default"><div class="cs-card-heading"><div><h2>默认大脑</h2><p>新建 leader 与未单独分配的内部任务使用这里的选择。</p></div><span class="cs-badge">默认</span></div>${selectionMarkup("default", draft.default, catalog, customFields)}<p class="cs-hint">下拉选择本机检测到的模型与强度；也可自定义输入。选择“原生默认”沿用工具配置。</p></section><div class="cs-section-heading"><h2>任务分配</h2><p>按用途选择大脑，也可以全部跟随默认。</p></div><div class="cs-task-list">${tasks.map(([key, title, description]) => `<section class="cs-card"><div class="cs-card-heading"><div><h3>${title}</h3><p>${description}</p></div><label class="cs-inherit"><input type="checkbox" data-ai-inherit="${key}" ${draft[key] === null ? "checked" : ""}>跟随默认</label></div>${draft[key] === null ? '<p class="cs-follow">使用默认大脑的载体、模型与思考强度。</p>' : selectionMarkup(key, draft[key], catalog, customFields)}</section>`).join("")}</div><p class="cs-footnote">保存设置不会运行 AI。新建会话时仍可单独选择；组员沿用 leader 的明确分配。</p>`;
    }
    function renderTools() {
      content.innerHTML = `<section class="cs-card"><div class="cs-card-heading"><div><h2>执行工具</h2><p>以下信息来自本机实际检测。</p></div><button type="button" class="cs-button" data-settings-action="reload">重新检测</button></div>${(snapshot.runtimes || []).map(item => `<div class="cs-tool-row"><div><strong>${esc(item.name || item.id)}</strong><p>${esc(item.error || (item.available ? "已就绪" : "未就绪"))}</p></div><span class="cs-badge ${item.available ? "cs-ready" : ""}">${item.available ? "可用" : "需配置"}</span></div>`).join("")}</section><p class="cs-footnote">安装、登录与工具更新可在 Crew 桌面首页的设置中管理。</p>`;
    }
    function render() {
      release();
      for (const button of root.querySelectorAll("[data-settings-category]")) { const active = button.dataset.settingsCategory === category; button.classList.toggle("active", active); button.setAttribute("aria-current", active ? "page" : "false"); }
      const info = categories.find(item => item[0] === category);
      root.querySelector("#crew-settings-title").textContent = info[1];
      root.querySelector(".cs-subtitle").textContent = info[2];
      footer.hidden = !["ai", "execution"].includes(category);
      if (options.panels?.[category]) { content.replaceChildren(); cleanup = options.panels[category](content, snapshot) || null; }
      else if (!snapshot) content.innerHTML = `<div class="cs-empty"><h2>${loading ? "正在读取设置…" : "设置暂时无法读取"}</h2><p>${esc(error)}</p>${loading ? "" : '<button type="button" class="cs-button" data-settings-action="reload">重试</button>'}</div>`;
      else if (category === "ai") renderAI();
      else if (category === "execution") content.innerHTML = executionMarkup(executionDraft);
      else if (category === "tools") renderTools();
      else if (category === "backend") content.innerHTML = `<section class="cs-card"><h2>本机后台</h2><p class="cs-hint">设置保存在 HOME 数据目录中，所有工作区共用。</p><dl class="cs-paths"><dt>数据目录 · HOME</dt><dd>${esc(snapshot.home)}</dd><dt>后台程序目录</dt><dd>${esc(snapshot.backend)}</dd>${options.service ? `<dt>连接地址</dt><dd>${esc(options.service())}</dd>` : ""}</dl></section>`;
      controls();
    }
    async function load() {
      if (loading || saving) return;
      const request = ++generation, edited = changed(); loading = true; error = ""; note = ""; render();
      try { const value = await options.read(); if (request !== generation) return; snapshot = value; if (!edited) { draft = initialAI(value); executionDraft = initialExecution(value); } options.onSnapshot?.(value); }
      catch (caught) { if (request === generation) error = caught.message || String(caught); }
      finally { if (request === generation) { loading = false; render(); } }
    }
    async function save() {
      if (!snapshot || saving) return;
      try {
        const ai = JSON.stringify(draft) === JSON.stringify(snapshot.ai) ? clone(draft) : validate(draft, snapshot.runtimes || []), execution = validateExecution(executionDraft); saving = true; error = ""; note = ""; controls();
        const value = await options.save({ai,execution}); snapshot = value; draft = initialAI(value); executionDraft = initialExecution(value); options.onSnapshot?.(value); note = "已保存。后续新任务使用这些设置。";
      } catch (caught) { error = caught.message || String(caught); }
      finally { saving = false; controls(); }
    }
    root.addEventListener("click", event => {
      const button = event.target.closest("button"); if (!button || !root.contains(button)) return;
      if (button.dataset.aiCustom && !saving) {
        const name = button.dataset.aiCustom, [key, field] = name.split(":");
        if (customFields.has(name)) customFields.delete(name); else customFields.add(name);
        render(); content.querySelector(`[data-ai-key="${key}"][data-ai-field="${field}"]`).focus(); return;
      }
      if (button.dataset.settingsCategory) { category = button.dataset.settingsCategory; render(); return; }
      const action = button.dataset.settingsAction;
      if (action === "close") options.onClose?.();
      if (action === "reload") void load();
      if (action === "reset" && !saving) { draft = initialAI(snapshot); executionDraft = initialExecution(snapshot); error = ""; note = ""; render(); }
      if (action === "save") void save();
    });
    root.addEventListener("change", event => {
      if (!draft || saving) return;
      const element = event.target, key = element.dataset.aiKey, field = element.dataset.aiField;
      if (element.dataset.executionRuntime) { executionDraft[element.dataset.executionRuntime] = element.value; error = ""; note = ""; controls(); }
      else if (element.dataset.aiInherit) { const task = element.dataset.aiInherit; draft[task] = element.checked ? null : clone(draft.default); render(); }
      else if (key && field) {
        draft[key][field] = element.value.trim() || null;
        if (field === "runtime") { draft[key].model = null; draft[key].effort = null; }
        // A model change never silently invents or replaces an explicit effort.
        if (field === "runtime") {
          const focused = document.activeElement === element;
          render();
          if (focused) content.querySelector(`[data-ai-key="${key}"][data-ai-field="${field}"]`).focus();
        }
        if (field === "model") {
          const runtime = snapshot.runtimes.find(item => item.id === (draft[key].runtime || defaultRuntime(null,snapshot.runtimes))), model = runtime?.models?.find(item => item.id === draft[key].model);
          const efforts = model && Array.isArray(model.efforts) ? model.efforts : runtime?.efforts || [];
          const picker = content.querySelector(`[data-ai-key="${key}"][data-ai-field="effort"]`);
          if (picker.tagName === "SELECT") picker.innerHTML = choiceOptions(draft[key].effort, efforts.map(id => ({id})));
        }
        error = ""; note = ""; controls();
      }
    });
    return {open(next = "ai") { category = next; root.hidden = false; if (!snapshot) void load(); else render(); }, hide() { root.hidden = true; release(); }, reload:load, save, getDraft:() => clone(draft)};
  }
  window.CrewSettings = {create, defaultRuntime, initialAI, validate, selectionMarkup, initialExecution, validateExecution, executionMarkup};
})();
