"use strict";
// Owns scoped settings, editable Crew prompts and the evidence shown for one
// execution. Reading or saving here never starts or reconfigures a native AI.
(() => {
  const esc = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (char) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[char],
    );
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const PURPOSES = [
    ["default", "通用默认"],
    ["leader", "任务负责人"],
    ["member", "成员"],
    ["assistant", "个人助手"],
    ["digest", "摘要"],
    ["git", "Git"],
    ["operations", "运维"],
  ];
  const PROMPT_NAMES = {
    common: "通用工作原则",
    leader: "任务负责人",
    member: "成员",
    assistant: "个人助手",
    report: "报告",
    context: "摘要与上下文交接",
  };
  const PERMISSIONS = {
    codex: [
      ["read-only", "只读"],
      ["workspace-write", "工作区访问"],
      ["danger-full-access", "完全访问"],
    ],
    claude: [
      ["default", "原生默认"],
      ["acceptEdits", "允许编辑"],
      ["bypassPermissions", "完全访问"],
    ],
  };
  const LEGACY = [
    ["tools", "工具与更新"],
    ["connections", "QQ / Lark 与连接"],
    ["appearance", "外观"],
    ["storage", "数据与备份"],
    ["remote", "远程访问"],
    ["backend", "本机后台"],
  ];
  // Route changes may destroy the renderer. Retain only non-secret editor
  // drafts and view positions in memory, independently of a mounted instance.
  const retainedDrafts = new Map(),
    retainedViews = new Map(),
    retainedPositions = new Map();
  const requestId = () =>
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `settings-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  const query = (path, values) => {
    const fields = Object.entries(values).filter(
      ([, value]) => value !== null && value !== undefined && value !== "",
    );
    return (
      path +
      (fields.length
        ? "?" +
          fields
            .map(
              ([key, value]) =>
                `${encodeURIComponent(key)}=${encodeURIComponent(value)}`,
            )
            .join("&")
        : "")
    );
  };
  const statusOf = (error) => Number(error?.status) || 0;
  const reason = (error) => error?.message || String(error);
  const labelSource = (source) => {
    if (!source) return "来源未确认";
    const scope =
      {
        default: "Crew 内置默认",
        "builtin-role-preference": "内置角色偏好",
        global: "全局默认",
        project: "工作区",
        session: "这个 AI 的专属设置",
      }[source.scope] || source.scope;
    return `${scope}${source.scope === "project" ? ` · ${source.identity}` : ""}${source.purpose && source.purpose !== "default" ? ` · ${PURPOSES.find(([id]) => id === source.purpose)?.[1] || source.purpose}` : ""}`;
  };
  const options = (choices, selected) =>
    choices
      .map(
        ([id, label]) =>
          `<option value="${esc(id)}" ${id === selected ? "selected" : ""}>${esc(label)}</option>`,
      )
      .join("");

  function create({
    host,
    client,
    actions,
    toast = () => {},
    onLegacySettings,
  }) {
    if (
      !host ||
      typeof client?.request !== "function" ||
      typeof actions?.register !== "function"
    )
      throw Error(
        "Scoped settings requires host, client.request and actions.register",
      );
    let destroyed = false,
      generation = 0,
      extraGeneration = 0;
    let context = { project: null, sessionId: null, executionId: null };
    const drafts = retainedDrafts,
      writes = new Set();
    let paintedView = null,
      paintedReady = false;
    let catalog = null,
      catalogError = "",
      catalogPending = null;
    const state = {
      tab: "ai",
      scope: "global",
      purpose: "default",
      key: "common",
      loading: false,
      error: "",
      note: "",
      scopeRecord: null,
      effective: null,
      promptRecord: null,
      promptKeys: [],
      extra: null,
      extraLoading: false,
      extraError: "",
      extraData: null,
    };
    host.classList.add("crew-scoped-settings");
    const identity = () =>
      state.scope === "global"
        ? "global"
        : state.scope === "project"
          ? context.project
          : context.sessionId;
    const scopeKey = () => JSON.stringify([state.scope, identity()]);
    const draftKey = () =>
      JSON.stringify([
        state.tab,
        state.scope,
        identity(),
        state.tab === "prompts"
          ? state.key
          : state.tab === "ai"
            ? state.purpose
            : "execution",
      ]);
    const viewKey = () =>
      JSON.stringify([
        context.project,
        context.sessionId,
        context.executionId,
        state.tab,
        state.scope,
        identity(),
        state.purpose,
        state.key,
      ]);
    const contextKey = () =>
      JSON.stringify([context.project, context.sessionId, context.executionId]);
    const rememberView = () =>
      retainedViews.set(contextKey(), {
        tab: state.tab,
        scope: state.scope,
        purpose: state.purpose,
        key: state.key,
        extra: state.extra,
      });
    const selectionContext = () => ({
      project: state.scope === "global" ? null : context.project,
      session_id: state.scope === "session" ? context.sessionId : null,
    });
    const scopeQuery = () => ({ scope: state.scope, identity: identity() });
    const currentVersion = () =>
      state.tab === "prompts"
        ? state.promptRecord?.version
        : state.scopeRecord?.version;
    const currentValue = () =>
      state.tab === "prompts"
        ? (state.promptRecord?.effective?.content ?? "")
        : state.tab === "ai"
          ? state.effective?.ai
          : state.effective?.execution;
    const currentDraft = () => drafts.get(draftKey());
    const busy = () => writes.has(draftKey());
    const conflict = () => {
      const draft = currentDraft();
      return Boolean(
        draft?.dirty && (draft.conflict || draft.version !== currentVersion()),
      );
    };
    const canSave = () =>
      Boolean(
        !state.loading &&
          !busy() &&
          currentVersion() !== undefined &&
          currentDraft()?.dirty &&
          !conflict(),
      );
    const scopeLabel = () =>
      state.scope === "global"
        ? "所有工作区"
        : state.scope === "project"
          ? `工作区「${context.projectName || context.project}」`
          : context.sessionName || "当前 AI";
    const sourceLabel = source => labelSource(source?.scope === "project" && source.identity === context.project ? {...source,identity:context.projectName || context.project} : source);
    const saveLabel = () => state.scope === "global" ? "保存为全局默认" : state.scope === "project" ? "保存到此工作区" : "保存到这个 AI";

    function ensureDraft() {
      const key = draftKey();
      const existing = drafts.get(key);
      if (
        (!existing || (!existing.dirty && !writes.has(key))) &&
        currentVersion() !== undefined &&
        currentValue() !== null &&
        currentValue() !== undefined
      ) {
        drafts.set(key, {
          value: clone(currentValue()),
          version: currentVersion(),
          dirty: false,
          conflict: false,
          attempt: null,
        });
      }
      return drafts.get(key);
    }
    function updateDraft(field, value) {
      const draft = ensureDraft();
      if (!draft || busy()) return;
      let runtimeChanged = false;
      if (state.tab === "prompts") draft.value = value;
      else {
        const next = value.trim() || null;
        if (
          state.tab === "ai" &&
          field === "runtime" &&
          draft.value.runtime !== next
        ) {
          // Model and effort belong to the previous carrier. Clear them in the
          // same scoped draft when the carrier changes, before another edit or
          // save; later explicit choices must remain untouched.
          draft.value = { runtime: next, model: null, effort: null };
          runtimeChanged = true;
        } else draft.value[field] = next;
      }
      draft.dirty =
        JSON.stringify(draft.value) !== JSON.stringify(currentValue());
      draft.attempt = null;
      state.note = "";
      controls();
      return runtimeChanged;
    }
    function controls() {
      const save = host.querySelector?.('[data-act="scoped-settings-save"]');
      if (save) {
        save.disabled = !canSave();
        save.textContent = busy() ? "正在保存…" : saveLabel();
      }
      const note = host.querySelector?.("[data-ss-draft-note]");
      if (note)
        note.textContent = currentDraft()?.dirty ? "草稿已保留，尚未保存" : "";
    }
    function scopeToolbar(withPurpose = false) {
      const scopes = [
        ["global", "所有工作区 · 全局默认"],
        ...(context.project
          ? [["project", `仅 ${context.projectName || context.project}`]]
          : []),
        ...(context.sessionId ? [["session", `仅 ${context.sessionName || "当前 AI"}`]] : []),
      ];
      const mainPurposes = PURPOSES.filter(([key]) => ["default","leader","member","assistant"].includes(key));
      const otherPurposes = PURPOSES.filter(([key]) => !mainPurposes.some(([main]) => main === key));
      return `<div class="ss-toolbar"><label>这些设置改谁<select data-ss-change="scope" aria-label="设置作用范围">${options(scopes, state.scope)}</select></label>${withPurpose ? `<label>哪一类 AI<select data-ss-change="purpose" aria-label="AI用途"><optgroup label="常用角色">${options(mainPurposes,state.purpose)}</optgroup><optgroup label="按需工作">${options(otherPurposes,state.purpose)}</optgroup></select></label>` : ""}</div>`;
    }
    function inheritance() {
      return `<details class="ss-guidance"><summary>继承与生效时间</summary><p>正在修改：${esc(scopeLabel())}。没有单独设置时，AI 使用工作区设置；工作区没有设置时，再使用全局默认。</p><p>${state.tab === "prompts" ? "保存后，Crew 会在下一轮对话注入新规则。当前执行收到了哪些内容，可在实际加载证据中查看。载体自己的私有提示词、Skill 和 MCP 沿用原生配置。" : "下一次启动 AI 时使用新设置，当前对话继续保持正在使用的配置。"}</p></details>`;
    }
    function conflictMarkup() {
      if (!conflict()) return "";
      const version = currentVersion(),
        draft = currentDraft();
      const latest =
        state.tab === "prompts"
          ? currentValue()
          : JSON.stringify(currentValue(), null, 2);
      return `<section class="ss-conflict" role="alert"><strong>服务器版本已变化，草稿仍保留</strong><p>草稿基于 v${esc(draft.version)}，当前读取到 v${esc(version)}。先读取最新内容，再明确选择。</p><button type="button" data-act="scoped-settings-reload">读取最新版本</button>${version !== draft.version ? `<details><summary>查看服务器当前内容</summary><pre>${esc(latest)}</pre></details><div class="ss-conflict-actions"><button type="button" data-act="scoped-settings-rebase">保留草稿，基于 v${esc(version)} 继续</button><button type="button" data-act="scoped-settings-use-server">使用服务器内容</button></div>` : ""}</section>`;
    }
    function footer() {
      return `<div class="ss-form-footer"><div><span data-ss-draft-note>${currentDraft()?.dirty ? "有未保存修改，草稿已保留" : ""}</span><button class="ss-link" type="button" data-act="scoped-settings-reset" ${state.loading || busy() || currentVersion() === undefined ? "disabled" : ""}>${state.scope === "global" ? "恢复默认" : "改为跟随上级"}</button></div><button class="ss-primary" type="button" data-act="scoped-settings-save" ${canSave() ? "" : "disabled"}>${busy() ? "正在保存…" : saveLabel()}</button></div>`;
    }
    function aiMarkup() {
      const draft = ensureDraft(),
        selection = draft?.value || state.effective.ai;
      const runtime = catalog?.find((item) => item.id === selection.runtime);
      const runtimeChoices = [
        ["", "原生默认"],
        ...(catalog || []).map((item) => [
          item.id,
          `${item.name || item.id}${item.available ? "" : " · 未就绪"}`,
        ]),
      ];
      if (
        selection.runtime &&
        !runtimeChoices.some(([id]) => id === selection.runtime)
      )
        runtimeChoices.push([
          selection.runtime,
          `${selection.runtime} · 目录未确认`,
        ]);
      const model = runtime?.models?.find(
        (item) => item.id === selection.model,
      );
      const efforts = Array.isArray(model?.efforts)
        ? model.efforts
        : runtime?.efforts || [];
      const preference = state.effective.role_preference;
      const sources = ["runtime","model","effort"].map(field => sourceLabel(state.effective.sources?.ai?.[field]));
      const sharedSource = sources.every(value => value === sources[0]) ? sources[0] : "各项来自不同设置";
      return `${scopeToolbar(true)}<div class="ss-editing-summary"><p>正在修改 <strong>${esc(scopeLabel())}</strong> 的${state.purpose === "default" ? "通用默认" : esc(PURPOSES.find(([key]) => key === state.purpose)?.[1] || state.purpose)}。</p><span>下一次启动生效</span></div>
        <div class="ss-ai-fields"><label>使用的工具<select data-ss-field="runtime" ${busy() ? "disabled" : ""}>${options(runtimeChoices, selection.runtime || "")}</select></label>
        <label>模型<input data-ss-field="model" list="ss-model-options" value="${esc(selection.model)}" placeholder="原生默认，也可填写模型名" autocomplete="off" ${busy() ? "disabled" : ""}><datalist id="ss-model-options">${(runtime?.models || []).map((item) => `<option value="${esc(item.id)}">${esc(item.name || item.id)} · ${esc({ configured: "配置项", discovered: "原生发现", observed: "原生回报" }[item.status] || item.status || "来源待确认")}</option>`).join("")}</datalist></label>
        <label>思考强度<input data-ss-field="effort" list="ss-effort-options" value="${esc(selection.effort)}" placeholder="原生默认" autocomplete="off" ${busy() ? "disabled" : ""}><datalist id="ss-effort-options">${efforts.map((value) => `<option value="${esc(value)}"></option>`).join("")}</datalist></label></div>
        <details class="ss-guidance ss-source-details"><summary>当前来源：${esc(sharedSource)}</summary><dl>${["工具","模型","思考强度"].map((label,index) => `<div><dt>${label}</dt><dd>${esc(sources[index])}</dd></div>`).join("")}</dl>${preference ? `<p>角色默认偏好 ${esc({claude:"Claude Code",codex:"Codex"}[preference.runtime] || preference.runtime)}，启动时按可用工具选择。</p>` : ""}</details>
        ${catalogError ? `<div class="ss-error" role="alert">模型目录读取失败：${esc(catalogError)}。已保存值仍保留；可显式重试。<button type="button" data-act="scoped-settings-catalog">重新读取目录</button></div>` : ""}
        <details class="ss-guidance"><summary>模型列表与原生配置</summary><p>列表来自工具发现、已有配置或运行回报。没有列出的模型和思考强度，也可以明确填写。</p>${runtime ? `<p>${esc(runtime.discovery?.error || runtime.error || `目录状态：${runtime.discovery?.status || "未确认"}`)}</p>${(runtime.configuration_errors || []).map((error) => `<p>${esc(error)}</p>`).join("")}` : ""}</details>${inheritance()}${conflictMarkup()}${footer()}`;
    }
    function executionMarkup() {
      const draft = ensureDraft(),
        value = draft?.value || state.effective.execution;
      return `${scopeToolbar()}<div class="ss-editing-summary"><p>设置 <strong>${esc(scopeLabel())}</strong> 中工具的访问权限。</p><span>下一次启动生效</span></div><div class="ss-permissions">${[
        ["claude", "Claude Code"],
        ["codex", "Codex"],
      ]
        .map(
          ([runtime, name]) =>
            `<label><span><strong>${name}</strong><small>来自${esc(sourceLabel(state.effective.sources?.execution?.[runtime]))}</small></span><select data-ss-field="${runtime}" aria-label="${name}执行权限" ${busy() ? "disabled" : ""}>${options(PERMISSIONS[runtime], value[runtime])}</select></label>`,
        )
        .join("")}</div><p class="ss-effective-note">任务里的“全自动决定”仍由各任务单独切换。</p>${inheritance()}${conflictMarkup()}${footer()}`;
    }
    function promptMarkup() {
      const record = state.promptRecord,
        draft = ensureDraft();
      return `<div class="ss-prompt-tools"><label>工作规则<select data-ss-change="prompt" aria-label="提示词类型">${options(
        state.promptKeys.map((key) => [key, PROMPT_NAMES[key] || key]),
        state.key,
      )}</select></label>${scopeToolbar()}</div>
        <section class="ss-editor"><div class="ss-editor-heading"><div><h2>${esc(PROMPT_NAMES[record.key] || record.key)}</h2><p>来自${esc(sourceLabel(record.effective.source))}${typeof record.effective.version === "number" ? ` · v${esc(record.effective.version)}` : ""}</p></div><span>${record.overridden ? "单独设置" : "跟随上级"}</span></div><label class="ss-sr" for="crew-scoped-prompt">提示词内容</label><textarea id="crew-scoped-prompt" data-ss-field="prompt" spellcheck="false" ${busy() ? "readonly" : ""}>${esc(draft?.value ?? record.effective.content)}</textarea></section>
        <div class="ss-prompt-secondary"><button class="ss-link" type="button" data-act="scoped-settings-applied">查看实际加载</button><button class="ss-link" type="button" data-act="scoped-settings-history">修改历史</button><span>下一轮对话使用新规则</span></div>${inheritance()}${conflictMarkup()}${footer()}${extraMarkup()}`;
    }
    function extraMarkup() {
      if (!state.extra) return "";
      const title =
        state.extra === "history" ? "提示词修改历史" : "实际加载证据";
      let body = "";
      if (state.extraLoading) body = '<p role="status">正在读取…</p>';
      else if (state.extraError)
        body = `<p class="ss-error" role="alert">${esc(state.extraError)}</p>`;
      else if (state.extra === "applied") {
        const data = state.extraData;
        if (!context.executionId)
          body =
            "<p>未确认：当前没有明确的执行身份。配置内容不能作为实际加载证据。</p>";
        else if (!data) body = "<p>加载未确认。</p>";
        else
          body = `<p class="ss-evidence-state">${data.loaded === "confirmed" ? "已有原生加载确认" : "加载未确认"} · 执行 ${esc(data.execution_id)}</p>${(data.applications || []).map((item) => `<article class="ss-application"><h3>${esc({ prepared: "已准备，尚未送达", sent: "宿主已发送，加载待确认", confirmed: "原生已确认加载" }[item.status] || item.status)}</h3><p>${esc(item.created_at)}${item.evidence ? ` · ${esc(item.evidence)}` : ""}</p>${(item.manifest?.prompts || []).map((prompt) => `<details><summary>${esc(PROMPT_NAMES[prompt.key] || prompt.key)} · v${esc(prompt.version)} · ${esc(labelSource(prompt.source))}</summary><pre>${esc(prompt.content)}</pre></details>`).join("")}</article>`).join("") || "<p>尚无宿主注入记录，加载未确认。</p>"}`;
      } else {
        const entries = state.extraData?.history || [];
        body = entries.length
          ? entries
              .map(
                (item) =>
                  `<article class="ss-history-entry"><h3>v${esc(item.version)} · ${item.action === "reset" ? "恢复继承 / 默认" : "保存修改"}</h3><p>${esc(item.created_at)} · ${esc(item.actor)}</p><details><summary>查看该次内容</summary><pre>${esc(item.effective_content ?? item.content)}</pre></details></article>`,
              )
              .join("")
          : "<p>这个范围还没有修改记录。</p>";
        if (state.extraData?.next_before)
          body += "<p>当前显示最近一页记录。</p>";
      }
      return `<section class="ss-extra"><header><h2>${title}</h2><button class="ss-link" type="button" data-act="scoped-settings-extra-close">收起</button></header>${body}</section>`;
    }
    function legacyMarkup() {
      return `<p class="ss-section-intro">外观、通知、工具与数据。</p><div class="ss-legacy-list">${LEGACY.map(([id, label]) => `<button type="button" data-act="scoped-settings-legacy" data-category="${id}" ${onLegacySettings ? "" : "disabled"}><span>${label}</span><span aria-hidden="true">↗</span></button>`).join("")}</div>${onLegacySettings ? "" : '<p class="ss-error">当前入口未提供完整设置跳转。</p>'}`;
    }
    function paint() {
      if (destroyed) return;
      if (paintedView && paintedReady)
        retainedPositions.set(paintedView, host.scrollTop || 0);
      rememberView();
      const title = {
        ai: "AI 配置",
        execution: "执行权限",
        prompts: "提示词",
        more: "完整设置",
      }[state.tab];
      let content = "";
      if (state.tab === "more") content = legacyMarkup();
      else if (state.loading)
        content = '<p class="ss-loading" role="status">正在读取真实设置…</p>';
      else if (
        state.tab === "prompts"
          ? state.promptRecord
          : state.scopeRecord && state.effective
      )
        content =
          state.tab === "prompts"
            ? promptMarkup()
            : state.tab === "ai"
              ? aiMarkup()
              : executionMarkup();
      else content = '<p class="ss-empty">尚未取得设置。请显式重试。</p>';
      host.innerHTML = `<div class="ss-shell"><header class="ss-header"><h1>设置</h1><nav aria-label="设置分类">${[
        ["ai", "AI 配置"],
        ["execution", "执行权限"],
        ["prompts", "提示词"],
        ["more", "更多设置"],
      ]
        .map(
          ([id, label]) =>
            `<button type="button" data-act="scoped-settings-tab" data-tab="${id}" ${state.tab === id ? 'aria-current="page"' : ""}>${label}</button>`,
        )
        .join(
          "",
        )}</nav></header><section class="ss-body" aria-label="${title}">${state.error ? `<div class="ss-error" role="alert"><p>${esc(state.error)}</p><button type="button" data-act="scoped-settings-reload">重新读取（保留草稿）</button></div>` : ""}${state.note ? `<p class="ss-note" role="status">${esc(state.note)}</p>` : ""}${content}</section></div>`;
      paintedView = JSON.stringify([viewKey(), state.extra]);
      paintedReady = !state.loading && !state.extraLoading;
      if (paintedReady)
        host.scrollTop = retainedPositions.get(paintedView) || 0;
    }
    async function readCatalog(force = false) {
      if (catalogPending) return catalogPending;
      if (catalog && !force) return catalog;
      catalogPending = client
        .request("/api/runtimes")
        .then((data) => {
          if (!Array.isArray(data.runtimes))
            throw Error("原生目录返回格式无效");
          if (!destroyed) {
            catalog = data.runtimes;
            catalogError = "";
          }
          return data.runtimes;
        })
        .catch((error) => {
          if (!destroyed) catalogError = reason(error);
          return null;
        })
        .finally(() => {
          catalogPending = null;
        });
      return catalogPending;
    }
    async function load() {
      const seq = ++generation;
      let captured = viewKey();
      ++extraGeneration;
      state.loading = state.tab !== "more";
      state.error = "";
      state.extra = null;
      state.extraData = null;
      state.extraError = "";
      state.scopeRecord = null;
      state.effective = null;
      state.promptRecord = null;
      paint();
      if (state.tab === "more") return;
      const isCurrent = () =>
        !destroyed && generation === seq && captured === viewKey();
      try {
        if (state.tab === "prompts") {
          const manifest = await client.request(
            query("/api/prompts", selectionContext()),
          );
          if (!isCurrent()) return;
          if (!Array.isArray(manifest.prompts) || !manifest.prompts.length)
            throw Error("Crew 提示词清单返回格式无效");
          state.promptKeys = manifest.prompts.map((item) => item.key);
          if (!state.promptKeys.includes(state.key)) {
            state.key = state.promptKeys[0];
            captured = viewKey();
          }
          const record = await client.request(
            query("/api/prompts", { ...scopeQuery(), key: state.key }),
          );
          if (!isCurrent()) return;
          if (
            typeof record.version !== "number" ||
            typeof record.effective?.content !== "string"
          )
            throw Error("Crew 提示词作用范围返回格式无效");
          state.promptRecord = record;
        } else {
          const [scope, effective] = await Promise.all([
            client.request(query("/api/settings/scope", scopeQuery())),
            client.request(
              query("/api/settings/effective", {
                ...selectionContext(),
                purpose: state.purpose,
              }),
            ),
            readCatalog(),
          ]);
          if (!isCurrent()) return;
          if (
            typeof scope.version !== "number" ||
            !effective.ai ||
            !effective.execution
          )
            throw Error("设置作用范围返回格式无效");
          state.scopeRecord = scope;
          state.effective = effective;
        }
      } catch (error) {
        if (isCurrent()) state.error = reason(error);
      } finally {
        if (!destroyed && generation === seq) {
          state.loading = false;
          paint();
        }
      }
    }
    async function loadExtra(kind) {
      if (destroyed || state.loading || state.tab !== "prompts") return;
      if (state.extra === kind) {
        state.extra = null;
        ++extraGeneration;
        paint();
        return;
      }
      const seq = ++extraGeneration,
        captured = viewKey();
      state.extra = kind;
      state.extraLoading = true;
      state.extraError = "";
      state.extraData = null;
      paint();
      if (kind === "applied" && !context.executionId) {
        state.extraLoading = false;
        paint();
        return;
      }
      try {
        const path =
          kind === "history"
            ? query("/api/prompts/history", { ...scopeQuery(), key: state.key })
            : query("/api/prompts/applied", {
                execution_id: context.executionId,
              });
        const result = await client.request(path);
        if (!destroyed && extraGeneration === seq && captured === viewKey())
          state.extraData = result;
      } catch (error) {
        if (!destroyed && extraGeneration === seq && captured === viewKey())
          state.extraError = `${kind === "history" && statusOf(error) === 404 ? "后端暂未提供历史接口：" : ""}${reason(error)}`;
      } finally {
        if (!destroyed && extraGeneration === seq && captured === viewKey()) {
          state.extraLoading = false;
          paint();
        }
      }
    }
    function validateAI(value) {
      for (const field of ["model", "effort"])
        if (
          value[field] !== null &&
          (!value[field].trim() ||
            value[field].startsWith("-") ||
            /[\x00-\x1f\x7f]/.test(value[field]))
        )
          throw Error(`${field} 必须为有效文本或原生默认`);
      const runtime = catalog?.find((item) => item.id === value.runtime),
        model = runtime?.models?.find((item) => item.id === value.model);
      if (
        value.effort &&
        Array.isArray(model?.efforts) &&
        !model.efforts.includes(value.effort)
      )
        throw Error("所选模型的原生目录不包含这个 effort，请核对实际支持值。");
    }
    async function mutate(reset = false) {
      if (
        destroyed ||
        state.loading ||
        busy() ||
        currentVersion() === undefined ||
        (!reset && !canSave())
      )
        return;
      const key = draftKey(),
        captured = viewKey(),
        draft = ensureDraft(),
        tab = state.tab;
      if (!draft) return;
      const body = {
        ...scopeQuery(),
        version: reset ? currentVersion() : draft.version,
      };
      let path = "/api/settings/scope";
      try {
        if (tab === "prompts") {
          path = `/api/prompts/${reset ? "reset" : "save"}`;
          body.key = state.key;
          if (!reset) {
            if (!draft.value.trim()) throw Error("提示词不能为空");
            body.text = draft.value;
          }
        } else if (tab === "ai") {
          if (!reset) validateAI(draft.value);
          body.ai = { [state.purpose]: reset ? null : clone(draft.value) };
        } else
          body.execution = reset
            ? Object.fromEntries(
                Object.keys(PERMISSIONS).map((runtime) => [runtime, null]),
              )
            : clone(draft.value);
      } catch (error) {
        state.error = reason(error);
        paint();
        return;
      }
      const signature = JSON.stringify([path, body]);
      const submittedValue = JSON.stringify(draft.value);
      if (draft.attempt?.signature !== signature)
        draft.attempt = { signature, id: requestId() };
      body.request_id = draft.attempt.id;
      writes.add(key);
      state.error = "";
      state.note = "";
      paint();
      try {
        const result = await client.request(path, body);
        if (
          drafts.get(key) === draft &&
          JSON.stringify(draft.value) === submittedValue
        )
          drafts.delete(key);
        if (destroyed || captured !== viewKey()) return;
        state.note = `${reset ? "已恢复本项默认 / 继承" : "修改已保存"}。${tab === "prompts" ? "下一轮宿主注入生效，实际加载请查看证据。" : "下一次启动生效，当前执行保持原配置。"}`;
        toast(state.note);
        if (tab === "prompts") {
          state.promptRecord = result;
          state.extra = null;
        } else {
          writes.delete(key);
          await load();
        }
      } catch (error) {
        if (statusOf(error) === 409) draft.conflict = true;
        if (!destroyed && captured === viewKey())
          state.error =
            statusOf(error) === 409
              ? `${reason(error)}。草稿保留，请读取最新版本后核对。`
              : `${statusOf(error) ? "保存未完成" : "保存结果未确认"}：${reason(error)}。草稿保留，不会自动重试。`;
      } finally {
        writes.delete(key);
        if (!destroyed && captured === viewKey()) paint();
      }
    }
    function chooseServer(keep) {
      const draft = ensureDraft();
      if (!draft || currentVersion() === undefined) return;
      if (!keep) {
        draft.value = clone(currentValue());
        draft.dirty = false;
      }
      draft.version = currentVersion();
      draft.conflict = false;
      draft.attempt = null;
      state.error = "";
      state.note = keep
        ? "已保留草稿并采用最新版本依据；核对后再保存。"
        : "已使用服务器当前内容。";
      paint();
    }
    const unregister = actions.register("scoped-settings", {
      "scoped-settings-tab": ({ element }) => {
        state.tab = element.dataset.tab;
        state.note = "";
        return load();
      },
      "scoped-settings-reload": () => load(),
      "scoped-settings-save": () => mutate(false),
      "scoped-settings-reset": () => mutate(true),
      "scoped-settings-rebase": () => chooseServer(true),
      "scoped-settings-use-server": () => chooseServer(false),
      "scoped-settings-applied": () => loadExtra("applied"),
      "scoped-settings-history": () => loadExtra("history"),
      "scoped-settings-extra-close": () => {
        ++extraGeneration;
        state.extra = null;
        paint();
      },
      "scoped-settings-catalog": async () => {
        await readCatalog(true);
        if (!destroyed) paint();
      },
      "scoped-settings-legacy": ({ element }) =>
        onLegacySettings?.(element.dataset.category),
    });
    function onInput(event) {
      const field = event.target.dataset?.ssField;
      if (field && updateDraft(field, event.target.value)) paint();
    }
    function onChange(event) {
      const element = event.target,
        change = element.dataset?.ssChange;
      if (change === "scope") {
        state.scope = element.value;
        state.note = "";
        void load();
      } else if (change === "purpose") {
        state.purpose = element.value;
        state.note = "";
        void load();
      } else if (change === "prompt") {
        state.key = element.value;
        state.note = "";
        void load();
      } else if (element.dataset?.ssField) {
        updateDraft(element.dataset.ssField, element.value);
        if (element.dataset.ssField !== "prompt") paint();
      }
    }
    host.addEventListener("input", onInput);
    host.addEventListener("change", onChange);
    return {
      async open(next = {}) {
        if (destroyed) throw Error("Scoped settings was destroyed");
        const priorTarget = JSON.stringify([context.project,context.sessionId]);
        context = {
          project:
            typeof next.project === "object"
              ? next.project?.name || null
              : next.project || null,
          projectName: next.projectName || next.project?.display_name || (typeof next.project === "string" ? next.project : next.project?.name) || null,
          sessionId: next.sessionId || null,
          sessionName: next.sessionName || null,
          sessionRole: next.sessionRole || null,
          executionId: next.executionId || null,
          taskId: next.taskId || null,
        };
        const previous = retainedViews.get(contextKey());
        if (previous) Object.assign(state, previous);
        if (!previous || priorTarget !== JSON.stringify([context.project,context.sessionId])) {
          const targetScope = context.sessionId ? "session" : context.project ? "project" : "global";
          if (!previous || state.scope !== targetScope) state.extra = null;
          state.scope = targetScope;
          if (context.sessionId && ["leader","member","assistant"].includes(context.sessionRole)) state.purpose = context.sessionRole;
        }
        if (
          (state.scope === "session" && !context.sessionId) ||
          (state.scope === "project" && !context.project)
        )
          state.scope = "global";
        host.hidden = false;
        const extra = state.extra;
        await load();
        if (!destroyed && extra && state.tab === "prompts")
          await loadExtra(extra);
      },
      destroy() {
        if (destroyed) return;
        if (paintedView && paintedReady)
          retainedPositions.set(paintedView, host.scrollTop || 0);
        rememberView();
        destroyed = true;
        ++generation;
        ++extraGeneration;
        unregister();
        host.removeEventListener("input", onInput);
        host.removeEventListener("change", onChange);
        host.classList.remove("crew-scoped-settings");
      },
    };
  }
  const exported = { create };
  if (typeof module !== "undefined" && module.exports)
    module.exports = exported;
  else window.CrewScopedSettings = exported;
})();
