/* Isolated design prototype. This file never calls Crew or a native AI runtime. */
(() => {
  "use strict";
  const STORAGE_KEY = "crew-design-settings-v1";
  const defaults = {
    general:
      "先理解用户真正想达成的结果。需求明确时主动推进，只询问会影响结果的重要取舍。保留用户原话与已有修改，复用已经通过的验证。普通交流直接回答，阶段交付时更新报告。",
    leader:
      "你负责本任务的整体结果。先自己推进，确有必要时再启动成员。维护任务目标、约定、完成标准与下一步。协调相关工作区，保留并行工作的修改。需要用户拍板时，回到原任务对话说明影响。",
    member:
      "围绕明确的任务说明施工。读取相关而精简的上下文，保留现有约定与其他人的修改。报告实际完成、验证结果和未解决的问题。收到影响整体任务的要求时，将原始输入同步给 leader。",
    assistant:
      "你是 Crew 的个人助手，帮助用户了解各项目进展、整理 TODO 和灵感、找到相关任务与成果。需要信息时先读取相关记录。明确区分讨论、记下来和开始执行；收到明确操作指令时说明结果与作用范围。",
    report:
      "第一段直接给当前结果或状态。按实质进展持续更新，保留用户改过的内容。用表格表达比较，用图示表达结构。阶段成果明确版本号、体验入口和验证范围。报告更新不代表任务结束。",
    summary:
      "按需整理公共频道和协作交流。优先保留决定、阻塞、实际成果与下一步，减少重复汇报。摘要附原始对话入口，区分事实和建议。",
    context:
      "交接保留目标、约定、用户拍板、已完成结果、未解决问题及下一步。给成员只装载相关上下文。记录来源和时间，排除已移入垃圾箱的活动上下文。",
  };
  const roles = [
    ["general", "通用工作原则", "所有 AI 共同遵循"],
    ["leader", "Leader", "理解需求与协调交付"],
    ["member", "成员", "具体施工与验证"],
    ["assistant", "个人助手", "日常整理与交流"],
    ["report", "报告", "结构、图表与更新方式"],
    ["summary", "摘要", "频道与协作信息整理"],
    ["context", "上下文交接", "压缩、记忆装载与交接"],
  ];
  const sections = [
    ["ai", "AI 默认", "◉"],
    ["prompts", "提示词", "≡"],
    ["permissions", "执行权限", "◇"],
    ["appearance", "外观与动态", "◐"],
    ["channels", "连接与通知", "↗"],
    ["native", "原生能力", "⌘"],
    ["local", "本机与数据", "▤"],
  ];
  const fresh = () => ({
    overrides: {},
    history: [],
    drafts: {},
    formDrafts: {},
    views: {},
    loaded: {},
    appearance: { theme: "light", motion: "system", font: "standard" },
    channels: { qq: true, lark: true },
    permissions: { cc: "full", codex: "full" },
    permissionOverrides: {},
    ai: {},
  });
  function read() {
    try {
      const value = JSON.parse(localStorage.getItem(STORAGE_KEY));
      if (!value || typeof value !== "object") return fresh();
      const initial = fresh();
      return {
        ...initial,
        ...value,
        appearance: { ...initial.appearance, ...value.appearance },
      };
    } catch {
      return fresh();
    }
  }
  function applyAppearance() {
    const appearance = read().appearance;
    const darkSystem =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.dataset.theme =
      appearance.theme === "system"
        ? darkSystem
          ? "dark"
          : "light"
        : appearance.theme;
    document.documentElement.dataset.motion = appearance.motion;
    document.documentElement.dataset.fontSize = appearance.font;
    return { ...appearance };
  }
  function toggleTheme() {
    const saved = read();
    const current =
      document.documentElement.dataset.theme ||
      (applyAppearance(), document.documentElement.dataset.theme);
    saved.appearance.theme = current === "dark" ? "light" : "dark";
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
    } catch {
      /* A local prototype can still change appearance in memory. */
    }
    document.documentElement.dataset.theme = saved.appearance.theme;
    if (
      typeof window.dispatchEvent === "function" &&
      typeof CustomEvent === "function"
    )
      window.dispatchEvent(
        new CustomEvent("crew-prototype-appearance", {
          detail: saved.appearance,
        }),
      );
    return saved.appearance.theme;
  }
  function render(host, api) {
    const esc =
      api.escape ||
      ((value) =>
        String(value).replace(
          /[&<>"']/g,
          (c) =>
            ({
              "&": "&amp;",
              "<": "&lt;",
              ">": "&gt;",
              '"': "&quot;",
              "'": "&#39;",
            })[c],
        ));
    const saved = read();
    const workspaceName = api.workspaceName || "Crew";
    const workspaceId = encodeURIComponent(String(api.workspaceId || "crew"));
    const taskId = encodeURIComponent(String(api.taskId || "demo"));
    const taskTitle = api.taskTitle || "当前任务";
    const workspaceScope = `workspace:${workspaceId}`;
    const members =
      Array.isArray(api.members) && api.members.length
        ? api.members.map((member, index) => [
            String(member.id || `member-${index}`),
            member.name || `成员 ${index + 1}`,
          ])
        : [["member", "成员（样例）"]];
    const aiTargets = [
      ["leader", api.leaderName || "Leader"],
      ...members.filter(([id]) => id !== "leader"),
    ];
    const viewKey = `view:${workspaceId}:${taskId}`;
    const previous = saved.views[viewKey] || {};
    const state = {
      section: "ai",
      scope: "global",
      role: "general",
      aiRole: "leader",
      aiTarget: "leader",
      extra: null,
      nativeOpen: false,
      status: "",
      ...previous,
      drafts: saved.drafts,
    };
    if (!sections.some(([id]) => id === state.section)) state.section = "ai";
    if (!["global", "workspace", "ai"].includes(state.scope))
      state.scope = "global";
    if (!roles.some(([id]) => id === state.role)) state.role = "general";
    if (!aiTargets.some(([id]) => id === state.aiTarget))
      state.aiTarget = "leader";
    const targetName = () =>
      aiTargets.find(([id]) => id === state.aiTarget)?.[1] || "Leader";
    const aiScopeFor = (id) =>
      `ai:${workspaceId}:${taskId}:${encodeURIComponent(id)}`;
    const aiScope = () => aiScopeFor(state.aiTarget);
    const scopeNames = () => ({
      global: "全局默认",
      workspace: `工作区 · ${workspaceName}`,
      ai: `单 AI · ${targetName()} · ${taskTitle}`,
    });
    const scopeKey = () =>
      state.scope === "global"
        ? "global"
        : state.scope === "workspace"
          ? workspaceScope
          : aiScope();
    const sourceName = (scope) =>
      scope === "global"
        ? "全局默认"
        : scope.startsWith("workspace:")
          ? `工作区 · ${workspaceName}`
          : `${aiTargets.find(([id]) => encodeURIComponent(id) === scope.split(":").at(-1))?.[1] || "AI"} · ${taskTitle}`;
    const configuredAIRole = () =>
      state.scope === "ai"
        ? state.aiTarget === "leader"
          ? "leader"
          : "member"
        : state.aiRole;
    const editorKey = () => `${scopeKey()}:${state.role}`;
    const aiFormKey = () => `ai-form:${scopeKey()}:${configuredAIRole()}`;
    const permissionsFormKey = () => `permissions-form:${scopeKey()}`;
    function effective(scope, role) {
      const explicit = saved.overrides[`${scope}:${role}`];
      if (typeof explicit === "string")
        return { text: explicit, source: sourceName(scope) };
      if (scope.startsWith("ai:")) return effective(workspaceScope, role);
      if (scope.startsWith("workspace:")) return effective("global", role);
      return { text: defaults[role], source: "Crew 内置默认（样例）" };
    }
    function effectiveAI(scope, role) {
      const own = saved.ai[`${scope}:${role}`];
      if (own) return { ...own, source: sourceName(scope) };
      if (scope.startsWith("ai:")) return effectiveAI(workspaceScope, role);
      if (scope.startsWith("workspace:")) return effectiveAI("global", role);
      return {
        carrier: "native",
        effort: "native",
        source: "原生默认（样例）",
      };
    }
    function effectivePermissions(scope) {
      const own = saved.permissionOverrides[scope];
      if (own) return { ...own, source: sourceName(scope) };
      if (scope.startsWith("ai:")) return effectivePermissions(workspaceScope);
      if (scope.startsWith("workspace:")) return effectivePermissions("global");
      return { ...saved.permissions, source: "Crew 内置默认（样例）" };
    }
    function persist(message) {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
      } catch {
        api.toast("浏览器存储不可用，本次修改仅保留在页面中");
        return;
      }
      if (message) api.toast(message);
    }
    function stashView() {
      const { drafts, status, ...view } = state;
      saved.views[viewKey] = view;
      persist();
    }
    function buildSnapshot(id, reason) {
      const role = id === "leader" ? "leader" : "member";
      const scope = aiScopeFor(id);
      const promptKeys = ["general", role, "report", "summary", "context"];
      return {
        registeredAt: new Date().toLocaleString("zh-CN"),
        reason,
        prompts: Object.fromEntries(
          promptKeys.map((key) => [key, effective(scope, key)]),
        ),
        ai: effectiveAI(scope, role),
        permissions: effectivePermissions(scope),
      };
    }
    for (const [id] of aiTargets)
      if (!saved.loaded[aiScopeFor(id)])
        saved.loaded[aiScopeFor(id)] = buildSnapshot(id, "样例启动快照");
    function rememberEditor() {
      const editor = host.querySelector("#prototype-prompt-editor");
      if (!editor) return;
      if (editor.value === effective(scopeKey(), state.role).text)
        delete state.drafts[editorKey()];
      else state.drafts[editorKey()] = editor.value;
      persist();
    }
    const pill = (text) => `<span class="ps-pill">${esc(text)}</span>`;
    const row = (title, description, control) =>
      `<div class="ps-setting-row"><div><h3>${title}</h3><p>${description}</p></div><div class="ps-control">${control}</div></div>`;
    const select = (id, label, options, value) =>
      `<label class="ps-sr" for="${id}">${label}</label><select id="${id}" aria-label="${label}">${options.map(([key, text]) => `<option value="${key}" ${key === value ? "selected" : ""}>${esc(text)}</option>`).join("")}</select>`;
    const toggle = (name, value, label) =>
      `<button class="ps-switch ${value ? "is-on" : ""}" type="button" role="switch" aria-checked="${value}" aria-label="${label}" data-ps="toggle-${name}"><span></span></button>`;
    const scopePicker = () =>
      `<div class="ps-scope-select"><label for="ps-scope">编辑范围</label>${select("ps-scope", "设置作用范围", Object.entries(scopeNames()), state.scope)}${state.scope === "ai" ? select("ps-ai-target", "选择单个 AI", aiTargets, state.aiTarget) : ""}</div>`;
    function pageHeader(title, description) {
      return `<header class="ps-content-header"><span class="ps-eyebrow">偏好与工作方式</span><h2>${title}</h2><p>${description}</p></header>`;
    }
    function aiPanel() {
      const ai = {
        ...effectiveAI(scopeKey(), configuredAIRole()),
        ...saved.formDrafts[aiFormKey()],
      };
      return `${pageHeader("AI 默认", "先设置偏好，leader 再按任务需要选择实际可用的能力。")}
        ${scopePicker()}<div class="ps-scope-line"><span>当前来源</span>${pill(ai.source)}<span class="ps-muted">生效范围：${esc(scopeNames()[state.scope])}</span></div>
        ${
          state.scope === "ai"
            ? `<p class="ps-muted">正在配置 ${esc(taskTitle)} → ${esc(targetName())}，不影响同任务的其他 AI。</p>`
            : `<div class="ps-role-segments" aria-label="AI 角色">${[
                ["leader", "Leader"],
                ["member", "成员"],
                ["assistant", "个人助手"],
              ]
                .map(
                  ([key, label]) =>
                    `<button type="button" data-ps="ai-role" data-value="${key}" class="${state.aiRole === key ? "is-active" : ""}" aria-pressed="${state.aiRole === key}">${label}</button>`,
                )
                .join("")}</div>`
        }
        <section class="ps-card">
          ${row(
            "优先载体",
            "当前展示原生载体类型；本轮只保存样例偏好。",
            select(
              "ps-carrier",
              "优先载体",
              [
                ["native", "跟随原生默认"],
                ["cc", "Claude Code"],
                ["codex", "Codex"],
              ],
              ai.carrier,
            ),
          )}
          ${row("模型", "正式产品从原生发现中读取，不将某台机器的清单固定在 Crew。", '<span class="ps-source-label">原生发现 · 样例占位</span>')}
          ${row(
            "推理强度",
            "可选项以实际载体及模型能力为准。",
            select(
              "ps-effort",
              "推理强度",
              [
                ["native", "跟随原生默认"],
                ["preferred", "用户偏好（样例）"],
              ],
              ai.effort,
            ),
          )}
        </section>
        <div class="ps-callout"><strong>设置与当前执行分开</strong><p>未保存的选择作为草稿保留。保存后供新会话使用。现有会话保留当前配置，主动应用时才模拟通知对应 AI；保存不会启动任务。</p></div>
        <div class="ps-footer"><button class="ps-text-button" type="button" data-ps="reset-ai">${state.scope === "global" ? "恢复原生默认" : "取消覆盖，恢复继承"}</button><button class="ps-button ps-primary" type="button" data-ps="save-ai">保存样例偏好</button></div>`;
    }
    function promptsPanel() {
      const current = effective(scopeKey(), state.role);
      const own = Object.hasOwn(saved.overrides, editorKey());
      const text = state.drafts[editorKey()] ?? current.text;
      const role = roles.find((item) => item[0] === state.role);
      return `${pageHeader("提示词", "Crew 注入的工作方式都在这里。能读、能改，也能看清实际来源。")}
        ${scopePicker()}
        <div class="ps-prompt-grid"><div class="ps-prompt-list" aria-label="提示词类型">${roles.map(([key, label, note]) => `<button type="button" data-ps="prompt-role" data-value="${key}" class="${state.role === key ? "is-active" : ""}" aria-pressed="${state.role === key}"><strong>${label}</strong><span>${note}</span></button>`).join("")}</div>
        <section class="ps-editor-card"><div class="ps-editor-heading"><h3>${role[1]}</h3>${pill(Object.hasOwn(state.drafts, editorKey()) ? "未保存草稿" : own ? "此范围已覆盖" : "继承中")}</div><p class="ps-inheritance">当前来源：<strong>${esc(current.source)}</strong> · 草稿离开后保留，保存后才更改配置</p>
          <label class="ps-sr" for="prototype-prompt-editor">${role[1]}提示词内容</label><textarea id="prototype-prompt-editor" spellcheck="false">${esc(text)}</textarea>
          <div class="ps-editor-meta"><span>生效范围：${esc(scopeNames()[state.scope])}${state.scope === "global" ? "及未覆盖的下级" : ""}</span><button class="ps-text-button" type="button" data-ps="reset-prompt">${state.scope === "global" ? "恢复内置默认" : "取消覆盖，恢复继承"}</button></div>
        </section></div>
        <div class="ps-prompt-actions"><button class="ps-button" type="button" data-ps="loaded">查看实际加载内容</button><button class="ps-button" type="button" data-ps="history">修改历史${saved.history.length ? ` · ${saved.history.length}` : ""}</button><span class="ps-action-spacer"></span><button class="ps-button ps-primary" type="button" data-ps="save-prompt">保存提示词</button></div>
        <div class="ps-callout"><strong>新会话使用新设置</strong><p>正在运行的 AI 保留启动时配置。${esc(taskTitle)}的 ${esc(targetName())} 可单独应用，任务进度连续保留。</p><button type="button" class="ps-text-button" data-ps="apply-prompt">主动应用到 ${esc(targetName())} →</button></div>
        ${extraPanel()}`;
    }
    function extraPanel() {
      if (state.extra === "history")
        return `<section class="ps-extra"><div class="ps-extra-heading"><h3>修改历史 <small>本浏览器样例记录</small></h3><button type="button" class="ps-text-button" data-ps="hide-extra">收起</button></div>${
          saved.history.length
            ? saved.history
                .slice()
                .reverse()
                .map(
                  (item) =>
                    `<article class="ps-history-item"><div><strong>${esc(item.role)}</strong>${pill(item.scope)}</div><p>${esc(item.time)} · ${esc(item.action)}</p><details><summary>查看该次内容</summary><pre>${esc(item.text)}</pre></details></article>`,
                )
                .join("")
            : '<p class="ps-muted">保存一次提示词后，修改会出现在这里。</p>'
        }</section>`;
      if (state.extra === "loaded") {
        const snapshot = saved.loaded[aiScope()];
        return `<section class="ps-extra"><div class="ps-extra-heading"><h3>${esc(targetName())} 的加载快照 <small>样例 · ${esc(taskTitle)}</small></h3><button type="button" class="ps-text-button" data-ps="hide-extra">收起</button></div><p class="ps-muted">${esc(snapshot.reason)} · 登记于 ${esc(snapshot.registeredAt)}。这是单独保存的样例会话内容，未读取原生会话。保存配置不会改变此快照；主动应用才更新所选 AI，其他 AI 保持原快照。</p>${Object.entries(
          snapshot.prompts,
        )
          .map(
            ([key, item]) =>
              `<details class="ps-loaded"><summary>${roles.find((role) => role[0] === key)[1]} <span>来源：${esc(item.source)}</span></summary><pre>${esc(item.text)}</pre></details>`,
          )
          .join("")}</section>`;
      }
      return "";
    }
    function permissionsPanel() {
      const permissions = {
        ...effectivePermissions(scopeKey()),
        ...saved.formDrafts[permissionsFormKey()],
      };
      return `${pageHeader("执行权限", "控制原生载体可以访问什么。每个任务的决策模式在任务顶部切换。")}
        ${scopePicker()}<p class="ps-muted">当前来源：${esc(permissions.source)} · 生效范围：${esc(scopeNames()[state.scope])}</p>
        <div class="ps-callout"><strong>此处全部为样例</strong><p>不会修改 Claude Code、Codex 或操作系统的实际权限。</p></div>
        <section class="ps-card">${[
          ["cc", "Claude Code"],
          ["codex", "Codex"],
        ]
          .map(([key, label]) =>
            row(
              label,
              `来源：${esc(permissions.source)} · 新会话生效`,
              select(
                `ps-permission-${key}`,
                `${label}样例执行权限`,
                [
                  ["full", "完全访问（样例）"],
                  ["workspace", "工作区访问（样例）"],
                  ["ask", "按原生规则询问（样例）"],
                ],
                permissions[key],
              ),
            ),
          )
          .join("")}</section>
        <p class="ps-fineprint">任务的「AI 全自动决定」只决定本次工作中的关键取舍。切换任务模式不会更改这里的访问权限。</p>
        <div class="ps-footer"><button class="ps-text-button" type="button" data-ps="reset-permissions">${state.scope === "global" ? "恢复默认权限" : "取消覆盖，恢复继承"}</button><button class="ps-button ps-primary" data-ps="save-permissions" type="button">保存样例权限</button></div>`;
    }
    function appearancePanel() {
      return `${pageHeader("外观与动态", "温和的颜色、清晰的层次，让长时间工作更从容。")}
        <section class="ps-card">${row(
          "主题",
          "改变当前原型的外观。",
          select(
            "ps-theme",
            "主题",
            [
              ["light", "浅色 · 纸与苔绿"],
              ["dark", "深色 · 夜间工作"],
              ["system", "跟随系统"],
            ],
            saved.appearance.theme,
          ),
        )}
        ${row(
          "动态效果",
          "也会尊重系统的减少动态效果设置。",
          select(
            "ps-motion",
            "动态效果",
            [
              ["system", "跟随系统"],
              ["reduced", "减少动态效果"],
            ],
            saved.appearance.motion,
          ),
        )}
        ${row(
          "文字大小",
          "调整阅读密度，保持内容层级。",
          select(
            "ps-font",
            "文字大小",
            [
              ["standard", "标准"],
              ["large", "稍大"],
            ],
            saved.appearance.font,
          ),
        )}</section>
        <div class="ps-appearance-preview"><span class="ps-preview-dot"></span><div><strong>工作持续推进</strong><p>新成果 v0.3 已准备好，可以随时回来看看。</p></div>${pill("新成果")}</div>`;
    }
    function channelsPanel() {
      return `${pageHeader("连接与通知", "需要你行动时提醒，普通进度留在任务里。")}
        <section class="ps-card">${row("QQ", "样例渠道 · 新 TODO、待处理问题与任务故障提醒。", toggle("qq", saved.channels.qq, "QQ样例通知"))}${row("Lark / 飞书", "样例渠道 · 通知与页面引用同一条 TODO。", toggle("lark", saved.channels.lark, "Lark样例通知"))}</section>
        <div class="ps-callout"><strong>页面与渠道只引用一件事</strong><p>同一人工操作被多个任务需要时，合并为一条 TODO，保留任务关联。完成后通知相关任务；手动停止的任务保持停止。</p></div>
        <div class="ps-footer"><span>没有连接真实账号，也不会发送消息。</span><button class="ps-button" type="button" data-ps="test-notice">模拟一条通知</button></div>${state.status ? `<div class="ps-notice-preview" role="status"><span>通知预览 · 样例</span><strong>需要你：确认测试设备可用</strong><p>${esc(state.status)}</p><small>点击正式通知会回到原任务。</small></div>` : ""}`;
    }
    function nativePanel() {
      return `${pageHeader("原生能力", "继续使用 Claude Code 和 Codex 原生的 Skill、MCP 与项目说明。")}
        <div class="ps-callout"><strong>按实际载体装载</strong><p>正式产品展示来源与实际加载结果。本页为能力清单的阅读样例，没有扫描你的电脑。</p></div>
        <section class="ps-card">${row("项目说明", "AGENTS.md / CLAUDE.md · 来源：工作区（样例）", pill("随载体加载"))}${row("Skills", "保留原生能力及其配置来源（样例）", pill("2 项样例"))}${row("MCP", "读取原生配置，不在 Crew 重建完整编辑器。", pill("按原生配置"))}</section>
        <button class="ps-button" type="button" data-ps="native-detail">${state.nativeOpen ? "收起加载来源" : "查看加载来源"}</button>
        ${state.nativeOpen ? '<section class="ps-extra"><h3>加载来源 · 样例</h3><dl class="ps-definition"><dt>项目说明</dt><dd>工作区 / AGENTS.md</dd><dt>Claude Code</dt><dd>原生用户配置 → 项目配置</dd><dt>Codex</dt><dd>原生用户配置 → 项目配置</dd><dt>读取时机</dt><dd>会话启动与原生重载时</dd></dl><p class="ps-muted">PC 正式版提供打开原配置入口；手机可阅读来源。</p></section>' : ""}`;
    }
    function localPanel() {
      return `${pageHeader("本机与数据", "后台独立运行，桌面和网页都是回到工作的入口。")}
        <section class="ps-card">${row("后台", "本地交互原型 · 未连接 Crew 后台", pill("未连接"))}${row("样例存储", "仅本浏览器的 crew-design-* 键。", pill("浏览器本地"))}${row("工具与更新", "Claude Code、Codex 与 Crew 的版本及来源。", '<button type="button" class="ps-text-button" data-ps="tool-details">查看样例详情 →</button>')}${row("运维对话", "故障发生且需要分析时，再按需进入。", '<button type="button" class="ps-text-button" data-ps="operations">查看运维入口 →</button>')}</section>
        <p class="ps-fineprint">关闭本页不影响正式 Crew。项目文件夹、正式数据库与原生 AI 配置均未被本原型读取或修改。</p>${state.status ? `<div class="ps-callout" role="status"><strong>${esc(state.status)}</strong><p>正式入口沿用已有能力。本轮仅体验位置与信息层次。</p></div>` : ""}`;
    }
    function paint() {
      stashView();
      const panels = {
        ai: aiPanel,
        prompts: promptsPanel,
        permissions: permissionsPanel,
        appearance: appearancePanel,
        channels: channelsPanel,
        native: nativePanel,
        local: localPanel,
      };
      host.innerHTML = `<div class="prototype-settings ${api.embedded ? "ps-embedded" : ""}"><header class="ps-top"><button type="button" class="ps-back" data-ps="close">← <span>返回工作台</span></button><div><strong>设置</strong><span>样例配置</span></div></header><div class="ps-layout"><nav class="ps-nav" aria-label="设置分组">${sections.map(([key, label, mark]) => `<button type="button" data-ps="section" data-value="${key}" class="${state.section === key ? "is-active" : ""}" ${state.section === key ? 'aria-current="page"' : ""}><span aria-hidden="true">${mark}</span>${label}</button>`).join("")}<p>所有修改只作用于<br>这个设计原型。</p></nav><div class="ps-content">${panels[state.section]()}</div></div></div>`;
    }
    function onClick(event) {
      const button = event.target.closest("[data-ps]");
      if (!button || !host.contains(button)) return;
      const action = button.dataset.ps;
      rememberEditor();
      if (action === "close") {
        stashView();
        api.close();
        return;
      }
      if (action === "section") {
        state.section = button.dataset.value;
        state.status = "";
        state.extra = null;
      }
      if (action === "prompt-role") {
        state.role = button.dataset.value;
        state.extra = null;
      }
      if (action === "ai-role") state.aiRole = button.dataset.value;
      if (action === "save-ai") {
        saved.ai[`${scopeKey()}:${configuredAIRole()}`] = {
          carrier: host.querySelector("#ps-carrier").value,
          effort: host.querySelector("#ps-effort").value,
        };
        delete saved.formDrafts[aiFormKey()];
        persist("样例 AI 偏好已保存，供新会话使用");
      }
      if (action === "reset-ai") {
        delete saved.ai[`${scopeKey()}:${configuredAIRole()}`];
        delete saved.formDrafts[aiFormKey()];
        persist("已恢复样例 AI 默认与继承来源");
      }
      if (action === "save-prompt") {
        const text =
          state.drafts[editorKey()] ?? effective(scopeKey(), state.role).text;
        if (!text.trim()) {
          api.toast("提示词不能为空，可以恢复默认内容");
          return;
        }
        saved.overrides[editorKey()] = text;
        delete state.drafts[editorKey()];
        saved.history.push({
          role: roles.find((item) => item[0] === state.role)[1],
          scope: scopeNames()[state.scope],
          scopeKey: scopeKey(),
          text,
          time: new Date().toLocaleString("zh-CN"),
          action: "保存修改，供新会话使用",
        });
        saved.history = saved.history.slice(-20);
        persist("样例提示词已保存 · 新会话生效");
      }
      if (action === "reset-prompt") {
        delete saved.overrides[editorKey()];
        delete state.drafts[editorKey()];
        const next = effective(scopeKey(), state.role);
        saved.history.push({
          role: roles.find((item) => item[0] === state.role)[1],
          scope: scopeNames()[state.scope],
          scopeKey: scopeKey(),
          text: next.text,
          time: new Date().toLocaleString("zh-CN"),
          action: "恢复默认或继承",
        });
        saved.history = saved.history.slice(-20);
        persist("已恢复样例默认与继承来源");
      }
      if (action === "history")
        state.extra = state.extra === "history" ? null : "history";
      if (action === "loaded")
        state.extra = state.extra === "loaded" ? null : "loaded";
      if (action === "hide-extra") state.extra = null;
      if (action === "apply-prompt") {
        saved.loaded[aiScope()] = buildSnapshot(
          state.aiTarget,
          "用户主动应用后的样例快照",
        );
        persist(
          `已模拟应用已保存配置到 ${taskTitle} 的 ${targetName()}；未保存草稿不会生效，其他 AI 保持原快照`,
        );
      }
      if (action === "save-permissions") {
        saved.permissionOverrides[scopeKey()] = {
          cc: host.querySelector("#ps-permission-cc").value,
          codex: host.querySelector("#ps-permission-codex").value,
        };
        delete saved.formDrafts[permissionsFormKey()];
        persist("样例权限已保存，未修改原生访问权限");
      }
      if (action === "reset-permissions") {
        delete saved.permissionOverrides[scopeKey()];
        delete saved.formDrafts[permissionsFormKey()];
        persist("已恢复样例权限默认与继承来源");
      }
      if (action === "toggle-qq" || action === "toggle-lark") {
        const channel = action.slice(7);
        saved.channels[channel] = !saved.channels[channel];
        persist("样例通知偏好已保存");
      }
      if (action === "test-notice") {
        const targets = [
          saved.channels.qq && "QQ",
          saved.channels.lark && "Lark",
        ].filter(Boolean);
        state.status = targets.length
          ? `已模拟送达 ${targets.join(" 与 ")}，同一事项保留原任务入口。`
          : "当前样例渠道全部关闭，事项仍保留在 TODO 中。";
      }
      if (action === "native-detail") state.nativeOpen = !state.nativeOpen;
      if (action === "tool-details")
        state.status = "工具版本与更新来源 · 样例占位，未执行检测";
      if (action === "operations")
        state.status = "按需运维对话 · 样例入口，未启动 AI";
      paint();
    }
    function onChange(event) {
      const input = event.target;
      if (input.id === "ps-scope") {
        rememberEditor();
        state.scope = input.value;
        state.extra = null;
        paint();
      }
      if (input.id === "ps-ai-target") {
        rememberEditor();
        state.aiTarget = input.value;
        state.extra = null;
        paint();
      }
      if (input.id === "ps-carrier" || input.id === "ps-effort") {
        saved.formDrafts[aiFormKey()] = {
          carrier: host.querySelector("#ps-carrier").value,
          effort: host.querySelector("#ps-effort").value,
        };
        persist();
      }
      if (
        input.id === "ps-permission-cc" ||
        input.id === "ps-permission-codex"
      ) {
        saved.formDrafts[permissionsFormKey()] = {
          cc: host.querySelector("#ps-permission-cc").value,
          codex: host.querySelector("#ps-permission-codex").value,
        };
        persist();
      }
      if (input.id === "ps-theme") {
        saved.appearance.theme = input.value;
        persist("原型主题已更新");
        applyAppearance();
      }
      if (input.id === "ps-motion") {
        saved.appearance.motion = input.value;
        persist("原型动态偏好已更新");
        applyAppearance();
      }
      if (input.id === "ps-font") {
        saved.appearance.font = input.value;
        persist("原型文字大小已更新");
        applyAppearance();
      }
    }
    function onInput(event) {
      if (event.target.id === "prototype-prompt-editor") rememberEditor();
    }
    function syncAppearance(event) {
      saved.appearance = { ...saved.appearance, ...event.detail };
      const themeSelect = host.querySelector("#ps-theme");
      if (themeSelect) themeSelect.value = saved.appearance.theme;
    }
    host.addEventListener("click", onClick);
    host.addEventListener("change", onChange);
    host.addEventListener("input", onInput);
    window.addEventListener?.("crew-prototype-appearance", syncAppearance);
    paint();
    return () => {
      rememberEditor();
      stashView();
      host.removeEventListener("click", onClick);
      host.removeEventListener("change", onChange);
      host.removeEventListener("input", onInput);
      window.removeEventListener?.("crew-prototype-appearance", syncAppearance);
    };
  }
  window.PrototypeSettings = { render, applyAppearance, toggleTheme };
})();
