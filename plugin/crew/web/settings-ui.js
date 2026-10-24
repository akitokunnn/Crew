"use strict";
(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const clone = value => JSON.parse(JSON.stringify(value));
  const empty = () => ({runtime:null, model:null, effort:null});
  const tasks = [["digest", "频道摘要", "将交流整理成摘要"], ["git", "Git 管理员", "检查改动、整理提交与合并"], ["operations", "后台事故处理", "按证据分析并验证修复"]];
  const categories = [["ai", "AI 大脑", "载体、模型与任务分配"], ["execution", "执行权限", "文件与网络的访问范围"], ["appearance", "外观", "让工作区更顺手"], ["tools", "工具与更新", "本机执行工具"], ["connections", "QQ 与连接", "离开电脑后的通知"], ["backend", "后台信息", "数据与服务位置"]];
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
      const runtime = catalog.find(item => item.id === (ai[key].runtime || defaultRuntime(null,catalog))), model = runtime?.models?.find(item => item.id === ai[key].model);
      if (ai[key].effort !== null && model && Array.isArray(model.efforts) && !model.efforts.includes(ai[key].effort)) throw Error(`${model.name || model.id} 不支持所填的 effort，请按实际支持情况修改。`);
    }
    return clone(ai);
  }
  function selectionMarkup(key, selection, catalog) {
    const runtime = catalog.find(item => item.id === selection.runtime), model = runtime?.models?.find(item => item.id === selection.model);
    const efforts = model && Array.isArray(model.efforts) ? model.efforts : runtime?.efforts || [];
    const saved = selection.runtime && !runtime ? `<option value="${esc(selection.runtime)}" selected>${esc(selection.runtime)} · 未检测到</option>` : "";
    return `<div class="cs-fields"><label>执行载体<select data-ai-key="${key}" data-ai-field="runtime"><option value="">${catalog.filter(item => item.available).length === 1 ? "自动使用唯一可用载体" : "请选择载体"}</option>${catalog.map(item => `<option value="${esc(item.id)}" ${item.id === selection.runtime ? "selected" : ""} ${item.available ? "" : "disabled"}>${esc(item.name || item.id)}${item.available ? "" : " · 未就绪"}</option>`).join("")}${saved}</select></label><label>模型<input data-ai-key="${key}" data-ai-field="model" list="cs-model-${key}" value="${esc(selection.model)}" placeholder="原生默认" autocomplete="off"><datalist id="cs-model-${key}">${(runtime?.models || []).map(item => `<option value="${esc(item.id)}">${esc(item.name || item.id)}</option>`).join("")}</datalist></label><label>思考强度 · effort<input data-ai-key="${key}" data-ai-field="effort" list="cs-effort-${key}" value="${esc(selection.effort)}" placeholder="原生默认" autocomplete="off"><datalist id="cs-effort-${key}">${efforts.map(item => `<option value="${esc(item)}"></option>`).join("")}</datalist></label></div>${runtime && !runtime.available ? `<p class="cs-warning">${esc(runtime.error || "该载体暂不可用，请检查安装与登录。已保存的选择会保留。")}</p>` : ""}`;
  }
  function create(options) {
    const root = options.host;
    let snapshot = null, draft = null, executionDraft = null, category = "ai", loading = false, saving = false, error = "", note = "", cleanup = null, generation = 0;
    root.classList.add("crew-settings");
    root.innerHTML = `<div class="cs-sidebar"><div class="cs-brand"><span class="cs-mark" aria-hidden="true">C</span><div><strong>设置</strong><small>Crew · 本机偏好</small></div></div><nav aria-label="设置分类">${categories.filter(([id]) => !["appearance", "connections"].includes(id) || options.panels?.[id]).map(([id, label, description], index) => `<button type="button" data-settings-category="${id}"><span class="cs-nav-icon" aria-hidden="true">${["◈", "▣", "◐", "⌘", "↗", "▤"][index]}</span><span>${label}<small>${description}</small></span></button>`).join("")}</nav>${options.onClose ? '<button type="button" class="cs-back" data-settings-action="close">← 返回工作区</button>' : ""}<p class="cs-local">应用于这台电脑上的所有工作区</p></div><div class="cs-body"><header class="cs-header"><div><span class="cs-eyebrow">偏好设置</span><h1 id="crew-settings-title"></h1><p class="cs-subtitle"></p></div>${options.onClose ? '<button type="button" class="cs-close" data-settings-action="close" aria-label="关闭设置">×</button>' : ""}</header><div class="cs-content"></div><footer class="cs-footer"><p class="cs-status" role="status"></p><button type="button" class="cs-button" data-settings-action="reset">撤销更改</button><button type="button" class="cs-button cs-primary" data-settings-action="save">保存设置</button></footer></div>`;
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
      content.innerHTML = `<section class="cs-card cs-default"><div class="cs-card-heading"><div><h2>默认大脑</h2><p>新建 leader 与未单独分配的内部任务使用这里的选择。</p></div><span class="cs-badge">默认</span></div>${selectionMarkup("default", draft.default, catalog)}<p class="cs-hint">模型和 effort 留空时使用原生默认。可从建议中选择，也可填写工具支持的名称。</p></section><div class="cs-section-heading"><h2>任务分配</h2><p>按用途选择大脑，也可以全部跟随默认。</p></div><div class="cs-task-list">${tasks.map(([key, title, description]) => `<section class="cs-card"><div class="cs-card-heading"><div><h3>${title}</h3><p>${description}</p></div><label class="cs-inherit"><input type="checkbox" data-ai-inherit="${key}" ${draft[key] === null ? "checked" : ""}>跟随默认</label></div>${draft[key] === null ? '<p class="cs-follow">使用默认大脑的载体、模型与思考强度。</p>' : selectionMarkup(key, draft[key], catalog)}</section>`).join("")}</div><p class="cs-footnote">保存设置不会运行 AI。新建会话时仍可单独选择；组员沿用 leader 的明确分配。</p>`;
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
        if (field === "runtime" || field === "model") render();
        error = ""; note = ""; controls();
      }
    });
    return {open(next = "ai") { category = next; root.hidden = false; if (!snapshot) void load(); else render(); }, hide() { root.hidden = true; release(); }, reload:load, save, getDraft:() => clone(draft)};
  }
  window.CrewSettings = {create, defaultRuntime, initialAI, validate, selectionMarkup, initialExecution, validateExecution, executionMarkup};
})();
