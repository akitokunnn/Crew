"use strict";
(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const clone = value => JSON.parse(JSON.stringify(value));
  function draftSettings(value) { return {...clone(value), port:String(value.port)}; }
  function settingsValue(value) {
    const port = Number(value.port);
    if (!value.port.trim() || !Number.isInteger(port) || port < 0 || port > 65535) throw Error("端口请输入 0 到 65535 的整数；0 代表自动分配。");
    return {...value, port, home:value.home.trim(), backend_directory:value.backend_directory.trim(), service_url:value.service_url.trim(), python:value.python.trim()};
  }
  function create(options) {
    const api = options.api, root = document.createElement("section"); root.className = "cl-settings";
    let active = false, snapshot = null, draft = null, homeAction = "copy", pending = null, error = "", note = "", preview = null, advanced = false, recoveryAvailable = false;
    const pathField = (label, field, kind, placeholder) => `<label>${label}<span class="cl-path-input"><input data-launch-field="${field}" value="${esc(draft[field])}" placeholder="${esc(placeholder)}" autocomplete="off"><button type="button" class="cs-button" data-launch-pick="${kind}" data-launch-pick-field="${field}">选择${kind === "python" ? "程序" : "目录"}</button></span></label>`;
    function changed() { return snapshot && draft && JSON.stringify(draft) !== JSON.stringify(draftSettings(snapshot.settings)); }
    function homeChanged() {
      const normalize = value => value.trim().replace(/\\/g,"/").replace(/\/+$/, "");
      const left = normalize(draft.home), right = normalize(snapshot.current.home);
      return navigator.platform.startsWith("Win") ? left.toLowerCase() !== right.toLowerCase() : left !== right;
    }
    function invalidate() { preview = null; error = ""; note = ""; root.querySelector(".cl-preview")?.replaceChildren(); }
    function controls() {
      for (const element of root.querySelectorAll("input,select,button")) element.disabled = Boolean(pending);
      const check = root.querySelector('[data-launch-action="check"]');
      if (check) { check.disabled = Boolean(pending) || !draft; check.textContent = pending === "check" ? "正在检查…" : "检查连接与路径"; }
      const apply = root.querySelector('[data-launch-action="apply"]');
      if (apply) { apply.disabled = Boolean(pending) || preview?.impact.repair_active === true; apply.textContent = pending === "apply" ? "正在应用并重启…" : preview?.impact.repair_active ? "等待修复结束" : preview?.impact.running_count > 0 || preview?.impact.operations_chat_busy ? "停止会话并应用" : "应用并重新打开"; }
      const reset = root.querySelector('[data-launch-action="reset"]'); if (reset) reset.disabled = Boolean(pending) || !changed();
      const status = root.querySelector(".cl-status");
      if (status) { status.textContent = error || note || (changed() ? "有尚未应用的更改。先检查路径与连接，再确认应用。" : snapshot.reconnect_required ? "当前后台需要重新连接，可检查后按当前配置重新打开。" : "修改后先检查，确认应用时才停止会话与重启。"); status.classList.toggle("co-error", Boolean(error)); }
      const confirmationError = root.querySelector(".cl-confirm-error"); if (confirmationError) { confirmationError.textContent = error; confirmationError.hidden = !error; }
      const homeOptions = root.querySelector(".cl-home-action"); if (homeOptions) homeOptions.hidden = !homeChanged();
      const recovery = root.querySelector(".cl-recovery"); if (recovery) { recovery.hidden = !recoveryAvailable; recovery.querySelector("button").textContent = pending === "recover" ? "正在重新打开…" : "重新打开当前配置"; }
    }
    async function read() {
      if (pending) return; pending = "read"; error = ""; render();
      try { snapshot = await api.get(); if (!draft) draft = draftSettings(snapshot.settings); recoveryAvailable = snapshot.reconnect_required === true; error = snapshot.error || ""; }
      catch (caught) { error = caught.message || String(caught); }
      finally { pending = null; if (active) render(); }
    }
    function payload() { return {settings:settingsValue(draft), home_action:homeAction}; }
    async function check() {
      if (pending || !draft) return;
      let body; try { body = payload(); } catch (caught) { error = caught.message; controls(); return; }
      pending = "check"; invalidate(); controls();
      try { preview = await api.validate(body); }
      catch (caught) { error = caught.message || String(caught); }
      finally { pending = null; if (active) { renderPreview(); controls(); const confirmation = root.querySelector(".cl-confirm"); if (confirmation) { confirmation.scrollIntoView({block:"nearest"}); confirmation.focus({preventScroll:true}); } } }
    }
    async function apply() {
      if (pending || !preview) return;
      pending = "apply"; error = ""; note = ""; controls();
      try {
        const result = await api.apply({settings:clone(preview.settings), home_action:homeAction});
        if (result.ok !== true) throw Error(result.error || "启动设置尚未被确认应用。");
        if (result.restarting === false) { pending = null; preview = null; recoveryAvailable = false; snapshot.reconnect_required = false; note = "设置未变化，无需重启。"; renderPreview(); controls(); return; }
        note = "已应用启动设置，Crew 正在重启。已有会话不会自动唤醒。";
        // Keep controls locked while the application exits, preventing a second migration request.
        controls();
      } catch (caught) { pending = null; recoveryAvailable = true; error = caught.message || String(caught); controls(); }
    }
    async function recover() {
      if (pending || !recoveryAvailable) return;
      pending = "recover"; error = ""; note = ""; controls();
      try {
        const result = await api.apply({settings:clone(snapshot.settings),home_action:"existing"});
        if (result.ok !== true) throw Error(result.error || "当前配置尚未被确认重新打开。");
        if (result.restarting === false) { pending = null; recoveryAvailable = false; snapshot.reconnect_required = false; note = "当前配置连接正常，无需重新打开。正在编辑的草稿已保留。"; }
        else note = "正在按当前配置重新打开 Crew。尚未应用的新目录草稿不会写入启动设置。";
        controls();
      } catch (caught) { pending = null; error = caught.message || String(caught); controls(); }
    }
    async function pick(button) {
      if (pending) return;
      const field = button.dataset.launchPickField;
      pending = "pick"; error = ""; controls();
      try {
        const selected = await api.pick({kind:button.dataset.launchPick, current:draft[field]});
        if (selected.path !== null) { draft[field] = selected.path; invalidate(); }
      } catch (caught) { error = caught.message || String(caught); }
      finally { pending = null; if (active) render(); }
    }
    function renderPreview() {
      const host = root.querySelector(".cl-preview"); if (!host) return; host.replaceChildren();
      if (!preview) return;
      const value = preview.settings, impact = preview.impact, current = snapshot.current, lines = [];
      if (impact.home_changed) lines.push(value.mode === "existing" ? `使用所连后台的数据目录 ${value.home}；当前本机目录 ${current.home} 保留。` : homeAction === "copy" ? preview.inspection?.already_copied ? `使用 ${value.home} 中此前已复制的数据，不会重复复制；原目录 ${current.home} 保留。` : `现有数据将复制到 ${value.home}；原目录 ${current.home} 保留作备份。` : `使用 ${value.home} 中的数据，空目录会从零开始；当前目录 ${current.home} 保留。`);
      if (value.mode === "local" && impact.source_changed) lines.push(value.source === "bundled" ? "后台程序改为随应用提供的版本。" : `后台程序改为 ${value.backend_directory}。`);
      if (impact.connection_changed) lines.push(value.mode === "existing" ? `改为连接已有后台：${value.service_url}。` : `启动本机后台，${value.port === 0 ? "自动分配端口" : "固定端口 " + value.port}。`);
      if (impact.reconnect_required) lines.push("当前后台需要重新连接，将按所选配置重新打开；已暂停的目标保持暂停。");
      if (impact.running_count > 0) lines.push(`停止 ${impact.running_count} 个正在运行的会话。`);
      if (impact.goals_count > 0) lines.push(`暂停 ${impact.goals_count} 个目标，保留任务与对话记录。`);
      if (impact.operations_chat_busy) lines.push("停止当前运维 AI 回复，等待其真实退出后再切换。");
      if (impact.repair_active) lines.push("运维修复正在进行，暂时不能切换。请等待修复结束后重新检查。");
      if (!lines.length) lines.push("路径与连接检查通过，将按以上启动设置重新连接。");
      const sessions = impact.sessions || [];
      host.innerHTML = `<section class="cl-confirm" tabindex="-1" aria-label="应用前确认"><div class="cs-card-heading"><div><h2>${impact.repair_active ? "路径已检查，等待修复结束" : "检查通过，确认后应用"}</h2><p>请核对这次切换的具体影响。</p></div><span class="cs-badge cs-ready">已检查</span></div><ul>${lines.map(line => `<li>${esc(line)}</li>`).join("")}</ul>${sessions.length ? `<details class="cl-sessions" ${sessions.length <= 4 ? "open" : ""}><summary>受影响会话 · ${sessions.length}</summary><div>${sessions.map(session => `<article><strong>${esc(session.name)}</strong><span>${esc(session.project)}</span><small>${session.running ? "运行中，将停止" : "当前未运行"}</small></article>`).join("")}</div></details>` : ""}${preview.service?.home ? `<p class="cl-checked-home">已连接后台的数据目录：${esc(preview.service.home)}</p>` : ""}<p class="cl-impact-note">应用前会停止当前会话并暂停目标，随后重启 Crew。切换后不会自动唤醒 AI。</p><p class="cl-confirm-error co-error" role="alert" hidden></p><div class="cl-confirm-actions"><button type="button" class="cs-button" data-launch-action="edit">返回修改</button><button type="button" class="cs-button cs-primary" data-launch-action="apply">停止会话并应用</button></div></section>`;
    }
    function render() {
      if (!draft) {
        root.innerHTML = `<section class="cs-card"><h2>${pending ? "正在读取连接与目录…" : "启动设置暂时无法读取"}</h2><p class="co-error" role="alert">${esc(error)}</p>${pending ? "" : '<button type="button" class="cs-button" data-launch-action="reload">重新读取</button>'}</section>`; return;
      }
      const local = draft.mode === "local";
      const advancedMarkup = `<details class="co-advanced cl-advanced" ${advanced ? "open" : ""}><summary>高级连接设置</summary><div class="co-form-fields cl-form-fields">${local ? `<label>固定端口<input type="number" min="0" max="65535" data-launch-field="port" value="${esc(draft.port)}"><small>0 为自动分配；需要固定访问地址时再指定端口。</small></label>` : ""}${pathField(local ? "Python 解释器" : "桌面运维 Python", "python", "python", "python 或解释器的完整路径")}${local ? "" : '<p class="cl-inline-hint">供本机运维对话和设置使用，不改变对方后台的 Python 解释器。</p>'}</div></details>`;
      root.innerHTML = `<section class="cs-card"><div class="cs-card-heading"><div><h2>连接与目录</h2><p>决定 Crew 的数据存放位置和连接方式。修改工作区的项目路径，请使用工作区设置。</p></div></div>${snapshot.applying ? '<p class="cl-pending">正在应用启动设置，请等待完成。</p>' : ""}<div class="cl-modes" role="radiogroup" aria-label="后台连接方式">${[["local","本机后台","由 Crew 启动并管理"],["existing","连接已有后台","连接本机已运行的服务"]].map(([id,label,hint]) => `<label class="cl-mode ${draft.mode === id ? "selected" : ""}"><input type="radio" name="crew-launch-mode" value="${id}" data-launch-field="mode" ${draft.mode === id ? "checked" : ""}><span><strong>${label}</strong><small>${hint}</small></span></label>`).join("")}</div>${local ? `<div class="co-form-fields cl-form-fields">${pathField("数据目录 · HOME", "home", "home", "例如 D:\\Crew Data")}<div class="cl-home-action"><span>切换数据目录时</span><label><input type="radio" name="crew-home-action" value="copy" data-launch-home-action ${homeAction === "copy" ? "checked" : ""}><span>复制现有数据，保留原目录作备份</span></label><label><input type="radio" name="crew-home-action" value="existing" data-launch-home-action ${homeAction === "existing" ? "checked" : ""}><span>使用该目录数据（空目录从零开始）</span></label></div><label>后台程序来源<select data-launch-field="source"><option value="bundled" ${draft.source === "bundled" ? "selected" : ""}>随应用提供</option><option value="custom" ${draft.source === "custom" ? "selected" : ""}>指定后台程序目录</option></select></label>${draft.source === "custom" ? pathField("后台程序目录", "backend_directory", "backend", "包含 crew、crew_supervisor.py 的 plugin 目录") : '<p class="cl-inline-hint">后台程序随 Crew 应用版本更新。</p>'}</div>` : `<div class="co-form-fields cl-form-fields"><label>本机后台地址<input type="url" data-launch-field="service_url" value="${esc(draft.service_url)}" placeholder="http://127.0.0.1:7122" autocomplete="off"><small>仅支持本机地址。检查时会核实它是否为可用的 Crew 后台，并读取实际数据目录。</small></label></div>`}${advancedMarkup}</section><div class="cl-check-actions"><p class="cl-status" role="status"></p><div><button type="button" class="cs-button" data-launch-action="reset">撤销更改</button><button type="button" class="cs-button" data-launch-action="check">检查连接与路径</button></div></div><div class="cl-preview"></div><details class="co-config-location"><summary>启动设置保存位置</summary><p>${esc(snapshot.settings_path)}</p></details>`;
      const recovery = document.createElement("div"); recovery.className = "cl-recovery"; recovery.hidden = true;
      recovery.innerHTML = '<p>重新打开使用当前配置，不会应用正在编辑的新目录。</p><button type="button" class="cs-button" data-launch-action="recover">重新打开当前配置</button>';
      root.querySelector(".cl-preview").before(recovery);
      root.querySelector(".cl-advanced")?.addEventListener("toggle", event => { advanced = event.target.open; });
      renderPreview(); controls();
    }
    root.addEventListener("click", event => {
      const button = event.target.closest("button"); if (!button || !root.contains(button)) return;
      if (button.dataset.launchPick) { void pick(button); return; }
      const action = button.dataset.launchAction;
      if (action === "reload") void read();
      if (action === "check") void check();
      if (action === "apply") void apply();
      if (action === "recover") void recover();
      if (action === "edit" && !pending) { invalidate(); controls(); root.querySelector('[data-launch-action="check"]').focus(); }
      if (action === "reset" && !pending) { draft = draftSettings(snapshot.settings); homeAction = "copy"; invalidate(); render(); }
    });
    function change(event) {
      if (pending || !draft) return;
      const field = event.target.dataset.launchField;
      if (field) { draft[field] = event.target.value; invalidate(); if (["mode","source"].includes(field)) render(); else controls(); }
      if (event.target.hasAttribute("data-launch-home-action")) { homeAction = event.target.value; invalidate(); controls(); }
    }
    root.addEventListener("input", change);
    root.addEventListener("change", event => { if (["mode","source"].includes(event.target.dataset.launchField) || event.target.hasAttribute("data-launch-home-action")) change(event); });
    function hide() { active = false; }
    return {mount(host) { active = true; host.append(root); render(); if (!snapshot) void read(); return hide; }, hide,
      getState:() => ({draft:draft && clone(draft), homeAction, pending, error, checked:Boolean(preview)})};
  }
  window.CrewLaunchSettings = {create, draftSettings, settingsValue};
})();
