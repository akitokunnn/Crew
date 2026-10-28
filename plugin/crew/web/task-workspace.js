"use strict";
// A task owns its conversation, members and material drawer. The shell owns routes.
(() => {
  const esc = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const states = {
    discussion: "讨论中",
    queued: "已排队",
    running: "推进中",
    waiting_user: "等待你",
    stopped: "已停止",
    blocked: "受阻",
    completed: "已完成",
    archived: "已归档",
    trashed: "垃圾箱",
  };
  const executionLabel = (value) =>
    ({
      working: "推进中",
      running: "推进中",
      starting: "启动中",
      idle: "空闲",
      waiting: "等待中",
      waiting_user: "等待你",
      paused: "已停止",
      pausing: "停止中",
      stopped: "已停止",
      completed: "已完成",
      failed: "失败",
      offline: "离线",
      cancelled: "已取消",
      queued: "已排队",
      accepted: "已接收",
      unknown: "尚未确认",
    })[value] ||
    value ||
    "无当前执行";
  const runtimeLabel = (value) =>
    ({ claude: "Claude Code", codex: "Codex" })[value] || value || "载体未确认";
  const uuid = () =>
    globalThis.crypto?.randomUUID?.() ||
    `task-ui-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const nameOf = (person) =>
    person?.name || person?.crew_name || person?.session_id || "负责人";
  const drafts = new Map(),
    views = new Map();
  function create({
    host,
    client,
    actions,
    navigate,
    toast = () => {},
    onTaskChanged = () => {},
  }) {
    let scope = null,
      detail = null,
      snapshot = null,
      person = null,
      panel = null,
      lastPanel = "details";
    let disposed = false,
      generation = 0,
      refreshSequence = 0,
      pending = false,
      chat = null,
      artifacts = null,
      attention = null,
      timer = null,
      wideMaterial = false,
      wideChatScroll = null,
      chatReadOnly = false,
      editingDetails = false;
    const operationIDs = new Map();
    let stopConfirmation = null;
    function confirmTaskOperation(operation) {
      if (stopConfirmation) return stopConfirmation.promise;
      const dialog = document.createElement("dialog");
      const focused = document.activeElement;
      const title =
        operation === "trash"
          ? "移入垃圾箱"
          : "归档任务";
      dialog.className = "wb-dialog tw-stop-confirm";
      dialog.setAttribute("aria-labelledby", "task-stop-title");
      dialog.innerHTML = `<h2 id="task-stop-title">${title}？</h2><p class="wb-muted">本任务的负责人、成员和待派发工作都会停止，已有文件与成果保留。之后可以从工具与历史恢复任务记录。</p><div class="wb-row-actions"><button type="button" class="wb-button" data-stop-cancel>取消</button><button type="button" class="wb-button primary" data-stop-confirm>${title}</button></div>`;
      const promise = new Promise((resolve) => {
        dialog.onclose = () => {
          const confirmed = dialog.returnValue === "stop";
          dialog.remove();
          stopConfirmation = null;
          if (!disposed) focused?.focus?.({ preventScroll: true });
          resolve(confirmed);
        };
        dialog.querySelector("[data-stop-cancel]").onclick = () =>
          dialog.close("cancel");
        dialog.querySelector("[data-stop-confirm]").onclick = () =>
          dialog.close("stop");
      });
      stopConfirmation = { dialog, promise };
      document.body.append(dialog);
      dialog.showModal();
      dialog.querySelector("[data-stop-cancel]").focus();
      return promise;
    }
    const projectName = (value) =>
      typeof value === "string" ? value : value?.name;
    const projectLabel = (name) => {
      const project = snapshot?.project?.name === name
        ? snapshot.project
        : snapshot?.projects?.find((item) => item.name === name);
      return project?.display_name || name;
    };
    const path = (
      url,
      project = detail?.task.primary_project || scope?.project,
    ) => {
      const [base, query = ""] = url.split("?");
      const params = new URLSearchParams(query);
      if (project) params.set("project", project);
      return base + (params.size ? "?" + params : "");
    };
    const api = (url, body, options) =>
      client.request(path(url), body, options);
    const scopeKey = () =>
      `${detail?.task.primary_project || scope?.project}:${scope?.taskId}`;
    const viewKey = () =>
      `${scopeKey()}:${scope?.sessionId || detail?.task.leader_session_id}`;
    const draftKey = () => `crew.task-details:${scopeKey()}`;
    const alive = (token) => !disposed && token === generation;
    const selectedExecution = () =>
      (detail?.executions || [])
        .filter((e) => e.session_id === person?.session_id)
        .at(-1);
    function draft() {
      const key = scopeKey();
      if (!drafts.has(key)) {
        let saved;
        try {
          saved = JSON.parse(localStorage.getItem(draftKey()));
        } catch (_) {
          /* No persisted draft. */
        }
        drafts.set(
          key,
          saved?.dirty ? saved : {
            version: detail.task.version,
            title: detail.task.title,
            request: detail.task.request,
            acceptance: detail.task.acceptance
              .map((a) => (typeof a === "string" ? a : a.title))
              .join("\n"),
            constraints: detail.task.constraints.join("\n"),
            projects: detail.task.projects
              .filter((p) => p !== detail.task.primary_project)
              .join("\n"),
            dirty: false,
          },
        );
      }
      return drafts.get(key);
    }
    function keepDetails() {
      const form = host.querySelector(".tw-details-form");
      if (!form || !detail) return;
      const value = draft();
      for (const key of [
        "title",
        "request",
        "acceptance",
        "constraints",
        "projects",
      ])
        value[key] = form.elements.namedItem(key).value;
      try {
        localStorage.setItem(draftKey(), JSON.stringify(value));
      } catch (error) {
        toast(error.message);
      }
    }
    function saveView() {
      keepDetails();
      if (detail) {
        const previous = views.get(viewKey()) || { positions: {} };
        views.set(viewKey(), {
          panel,
          lastPanel,
          editingDetails,
          positions: {
            ...previous.positions,
            ...(panel
              ? {
                  [panel]:
                    host.querySelector(".tw-drawer-body")?.scrollTop || 0,
                }
              : {}),
          },
        });
      }
    }
    function stopRendering() {
      clearTimeout(timer);
      timer = null;
      saveView();
      chat?.unmount();
      chat = null;
      artifacts?.destroy();
      artifacts = null;
      attention?.destroy();
      attention = null;
    }
    function header() {
      const t = detail.task,
        member = person.session_id !== t.leader_session_id;
      const leader = allPeople().find(
        (p) => p.session_id === t.leader_session_id,
      );
      const crumbs = member
        ? `<div class="tw-heading"><nav class="tw-crumbs" aria-label="对话来历"><button data-act="tw-leader" title="返回任务：${esc(t.title)}">${esc(t.title)}</button><span>›</span><button data-act="tw-leader">负责人 · ${esc(nameOf(leader))}</button><span>›</span><span>成员</span></nav><h1>${esc(nameOf(person))}的对话</h1></div>`
        : `<div class="tw-heading"><span>${esc(projectLabel(t.primary_project))}</span><h1>${esc(t.title)}</h1></div>`;
      const stopped = ["stopped", "blocked", "waiting_user"].includes(t.state);
      const operation = !t.authorized ? "start" : stopped ? "resume" : "stop";
      const control =
        operation === "start"
          ? "开始执行"
          : operation === "resume"
            ? "继续执行"
            : "停止任务";
      const controlling =
        pending ||
        Boolean(t.lifecycle_pending) ||
        t.execution_status === "pausing";
      const inactive = ["archived", "trashed"].includes(t.state);
      const nextAction = inactive ? "restore" : operation;
      const currentStatus = selectedExecution()?.status || person.status;
      const controlTitle = {
        start: "授权负责人和成员开始本任务",
        stop: "停止整个任务、成员与待派发工作",
        restore: "恢复任务记录，不自动执行",
        resume: "授权负责人和成员继续本任务",
      }[nextAction];
      const controlLabel = controlling
        ? "正在处理…"
        : inactive
          ? "恢复任务记录"
          : t.state === "completed"
            ? "任务已完成"
            : control;
      const materialLinks = [
        ["members", "成员"],
        ["deliverables", "成果"],
        ["details", "详情"],
      ]
        .map(([id, label]) => `<button class="tw-material-toggle" data-act="tw-panel"
          data-panel="${id}" data-toggle="true" aria-expanded="${panel === id}">
          ${label}${id === "deliverables" && t.review_status === "pending" ? '<span class="tw-review">待体验</span>' : ""}</button>`)
        .join("");
      return `<div class="tw-top">
          <div class="tw-title">${crumbs}</div>
          <div class="tw-primary-actions">
            <button class="tw-mode" data-act="tw-mode" role="switch" aria-label="本任务全自动取舍"
              aria-checked="${t.mode === "automatic"}"
              title="${t.mode === "automatic" ? "本任务的关键取舍交给 AI" : "本任务的关键取舍由你拍板"}；执行权限在设置中配置"
              ${pending || inactive ? "disabled" : ""}>${t.mode === "automatic" ? "全自动" : "你来拍板"}<span aria-hidden="true">⇄</span></button>
            <button class="tw-main-action ${nextAction === "stop" ? "tw-stop-action" : ""}"
              data-act="tw-control" data-operation="${nextAction}" title="${controlTitle}"
              ${controlling || t.state === "completed" ? "disabled" : ""}>${controlLabel}</button>
            <details class="tw-controls"><summary aria-label="更多任务操作">···</summary>
              <div class="tw-control-menu">
                <button data-act="tw-pin" ${pending ? "disabled" : ""}>${t.pinned ? "取消置顶" : "置顶任务"}</button>
                <button data-act="tw-settings">当前 AI 的设置与提示词</button>
                ${operation !== "stop" && !["stopped", "archived", "trashed", "completed"].includes(t.state) ? `<button data-act="tw-control" data-operation="stop" ${controlling ? "disabled" : ""}>停止整个任务及成员</button>` : ""}
                ${!inactive ? `<button data-act="tw-control" data-operation="archive" ${controlling ? "disabled" : ""}>归档任务</button><button data-act="tw-control" data-operation="trash" ${controlling ? "disabled" : ""}>移入垃圾箱</button>` : ""}
              </div>
            </details>
          </div>
        </div>
        <div class="tw-statusbar">
          <div class="tw-scope-line"><span class="tw-state ${esc(t.state)}">任务 · ${esc(states[t.state] || t.state)}</span><span>${member ? `当前成员 · ${esc(executionLabel(currentStatus))}` : `负责人 · ${esc(nameOf(person))}`}</span></div>
          <nav class="tw-material-nav" aria-label="当前任务内容">${materialLinks}</nav>
        </div>
        ${t.lifecycle_pending || t.execution_status === "pausing" ? '<div class="tw-notice" role="status">已请求停止，正在等待实际执行退出；现有成果保留。</div>' : ""}
        ${t.waiting_reason ? `<div class="tw-notice">${esc(t.waiting_reason)}</div>` : ""}`;
    }
    function allPeople() {
      const people = [
        ...(snapshot?.leaders || []),
        ...(snapshot?.members || []),
        ...(detail?.members || []),
      ];
      return people.filter(
        (p, i) =>
          people.findIndex((other) => other.session_id === p.session_id) === i,
      );
    }
    function resolvePerson() {
      const id = scope.sessionId || detail.task.leader_session_id;
      const allowed = new Set([
        detail.task.leader_session_id,
        ...detail.members.map((p) => p.session_id),
        ...detail.executions.map((e) => e.session_id),
      ]);
      const selected =
        allowed.has(id) && allPeople().find((p) => p.session_id === id);
      if (!selected)
        throw Error("此人员不在当前任务记录中；请返回任务负责人。");
      person = {
        ...selected,
        name: nameOf(selected),
        activity: selected.activity || null,
        last_seq: selected.last_seq || 0,
      };
    }
    function renderHeader() {
      const slot = host.querySelector(".tw-header");
      if (slot) {
        const opened = slot.querySelector(".tw-controls")?.open;
        slot.innerHTML = header();
        if (opened && slot.querySelector(".tw-controls"))
          slot.querySelector(".tw-controls").open = true;
      }
    }
    function mountChat() {
      const target = host.querySelector(".tw-chat");
      chatReadOnly = ["archived", "trashed"].includes(detail.task.state);
      chat = window.CrewChat.mount({
        host: target,
        client,
        api,
        toast,
        session: person,
        goalId: detail.task.id,
        readonly: chatReadOnly,
        readonlyReason:
          "此任务已收起为历史记录。恢复任务记录后可以继续对话；恢复不会自动启动执行。",
        canRead: () =>
          !disposed &&
          !wideMaterial &&
          !(panel && matchMedia("(max-width:1200px)").matches) &&
          !host.closest?.(".wb-menu-open") &&
          !document.querySelector("dialog[open]"),
        state: {
          ...snapshot,
          project: { ...snapshot?.project, name: detail.task.primary_project },
          leaders: allPeople().filter(
            (p) => p.session_id === detail.task.leader_session_id,
          ),
          members: allPeople().filter(
            (p) => p.session_id !== detail.task.leader_session_id,
          ),
        },
        memberConversation: person.session_id !== detail.task.leader_session_id,
        onRouted: (taskId) =>
          navigate({
            view: "task",
            project: detail.task.primary_project,
            taskId,
          }),
        onQueue: async ({ text, sessionId, requestId }) => {
          const token = generation;
          let result;
          try {
            result = await api("/api/tasks/schedule", {
              task_id: detail.task.id,
              version: detail.task.version,
              session_id: sessionId,
              text,
              evidence: { type: "explicit-user-queue", text },
              request_id: requestId,
            });
          } catch (error) {
            if (error.status === 409) await refresh();
            throw error;
          }
          if (
            result.receipt?.status === "unknown" ||
            result.receipt?.accepted === "unknown" ||
            result.receipt?.accepted === false
          )
            throw Error("排队请求结果尚未确认；文字和同一请求编号已保留。");
          if (!alive(token)) return result;
          if (result.task) detail.task = result.task;
          onTaskChanged(result.task || detail.task);
          renderHeader();
        },
        onExecutionChange: () => {
          if (!timer)
            timer = setTimeout(() => {
              timer = null;
              void refresh();
            }, 350);
        },
      });
    }
    function detailsMarkup() {
      const d = draft(),
        t = detail.task;
      if (!editingDetails) {
        const list = (items) => items.length
          ? `<ul>${items.map((item) => `<li>${esc(typeof item === "string" ? item : item.title)}</li>`).join("")}</ul>`
          : '<p class="tw-summary-empty">随对话补充</p>';
        return `<section class="tw-details-summary">
          <div class="tw-detail-toolbar"><p class="tw-context">随对话更新 · 版本 ${t.version}</p><button class="tw-text-button" data-act="tw-edit-details">${d.dirty ? "继续编辑草稿" : "编辑详情"}</button></div>
          ${d.dirty ? '<p class="tw-draft-note">有尚未保存的详情草稿</p>' : ""}
          <section><h3>当前目标</h3><p class="tw-summary-copy">${esc(t.request || "继续和负责人聊聊要完成什么。")}</p></section>
          <section><h3>完成标准</h3>${list(t.acceptance)}</section>
          ${t.constraints.length ? `<section><h3>已定条件</h3>${list(t.constraints)}</section>` : ""}
          <section><h3>工作区</h3><p>${esc(projectLabel(t.primary_project))} <span class="tw-summary-empty">主归属</span></p>${t.projects.filter((p) => p !== t.primary_project).map((p) => `<p>${esc(projectLabel(p))} <span class="tw-summary-empty">关联</span></p>`).join("")}</section>
        </section>`;
      }
      return `<form class="tw-details-form"><p class="tw-context">${esc(projectLabel(t.primary_project))} · AI 随对话维护，你也可以修改。</p>
      ${d.version !== t.version && d.dirty ? `<div class="tw-conflict" role="alert">任务已更新到版本 ${t.version}，你的版本 ${d.version} 草稿仍保留。<button type="button" data-act="tw-rebase">核对后，以当前版本提交这份草稿</button></div>` : ""}
      <label>任务标题<input name="title" value="${esc(d.title)}" required></label><label>当前目标<textarea name="request" rows="4" required>${esc(d.request)}</textarea></label><label>完成标准 · 每行一项<textarea name="acceptance" rows="4">${esc(d.acceptance)}</textarea></label><label>已定条件 · 每行一项<textarea name="constraints" rows="3">${esc(d.constraints)}</textarea></label><label>关联工作区 · 每行一个已登记名称<textarea name="projects" rows="2">${esc(d.projects)}</textarea></label><p class="tw-context">主归属：${esc(t.primary_project)}。关联入口指向同一个任务，负责人不变。</p><div class="tw-detail-actions"><button class="btn primary" type="submit" ${pending ? "disabled" : ""}>保存详情</button><button class="tw-text-button" type="button" data-act="tw-read-details">返回阅读</button></div><div class="tw-form-feedback" role="status"></div></form>`;
    }
    function membersMarkup() {
      const t = detail.task,
        ids = new Set([
          t.leader_session_id,
          ...detail.members.map((p) => p.session_id),
        ]);
      return `<p class="tw-context">${esc(t.title)} · 一个负责人，按需邀请成员</p>${allPeople()
        .filter((p) => ids.has(p.session_id))
        .map((p) => {
          const e = detail.executions
            .filter((e) => e.session_id === p.session_id)
            .at(-1);
          return `<button class="tw-person" data-act="tw-person" data-session-id="${esc(p.session_id)}" ${p.session_id === person.session_id ? 'aria-current="true"' : ""}><span class="tw-avatar">${esc(nameOf(p).slice(0, 1))}</span><span><strong>${esc(nameOf(p))}<span class="tw-person-role">${p.session_id === t.leader_session_id ? "负责人" : "成员"}</span></strong>${p.task ? `<small>${esc(p.task)}</small>` : ""}<small>${esc(runtimeLabel(e?.runtime || p.runtime))} · ${esc(executionLabel(e?.status || p.status))}</small></span><span>${p.session_id === person.session_id ? "当前" : "›"}</span></button>`;
        })
        .join(
          "",
        )}<details><summary>执行与通知记录</summary>${detail.executions.map((e) => `<div class="tw-record"><strong>${esc(nameOf(allPeople().find((p) => p.session_id === e.session_id)))}</strong><span>${esc(executionLabel(e.status))} · ${esc(runtimeLabel(e.runtime))}</span><small>${esc(e.id)}</small></div>`).join("")}${(detail.notifications?.receipts || []).map((r) => `<div class="tw-record">${esc(r.channel)} · ${esc({ sent: "已发送", failed: "发送失败", unknown: "结果未知", not_configured: "未配置" }[r.status] || r.status)}${r.error ? " · " + esc(r.error) : ""}</div>`).join("")}</details>`;
    }
    function renderPanel() {
      artifacts?.destroy();
      artifacts = null;
      const drawer = host.querySelector(".tw-drawer");
      drawer.hidden = !panel;
      host
        .querySelector(".tw-layout")
        .classList.toggle("has-material", Boolean(panel));
      if (!panel || panel !== "deliverables") wideMaterial = false;
      host
        .querySelector(".tw-layout")
        .classList.toggle("reading-material", wideMaterial);
      if (!panel) {
        syncOverlay();
        return;
      }
      drawer.innerHTML = `<header><div><h2>${{details:"任务详情",members:"成员对话",deliverables:"成果与文档"}[panel]}</h2><p>${esc(detail.task.title)}</p></div><button data-act="tw-panel" data-panel="close" aria-label="返回对话">×</button></header><nav aria-label="任务内容">${[
        ["details", "详情"],
        ["members", "成员"],
        ["deliverables", "成果与文档"],
      ]
        .map(
          ([id, label]) =>
            `<button data-act="tw-panel" data-panel="${id}" aria-pressed="${panel === id}">${label}</button>`,
        )
        .join(
          "",
        )}</nav><div class="tw-drawer-body">${panel === "details" ? detailsMarkup() : panel === "members" ? membersMarkup() : '<div class="tw-deliverables"></div>'}</div>`;
      const body = drawer.querySelector(".tw-drawer-body");
      body.scrollTop = views.get(viewKey())?.positions?.[panel] || 0;
      const currentPanel = panel,
        currentKey = viewKey();
      body.addEventListener(
        "scroll",
        () => {
          const old = views.get(currentKey) || {};
          views.set(currentKey, {
            ...old,
            panel,
            lastPanel,
            positions: { ...old.positions, [currentPanel]: body.scrollTop },
          });
        },
        { passive: true },
      );
      if (panel === "deliverables") {
        if (!window.CrewTaskDeliverables)
          body.textContent = "成果组件尚未加载，请重新打开页面。";
        else
          artifacts = window.CrewTaskDeliverables.create({
            host: body.querySelector(".tw-deliverables"),
            client,
            project: detail.task.primary_project,
            taskId: detail.task.id,
            sessionId: person.session_id,
            taskTitle: detail.task.title,
            documents: detail.documents,
            reports: detail.reports,
            root: snapshot?.project?.root,
            toast,
            expanded: wideMaterial,
            onExpand: expandReport,
            onChanged: () => {
              void refresh();
            },
          });
      }
      syncOverlay();
    }
    function expandReport(value) {
      const body = host.querySelector(".tw-drawer-body"),
        top = body.getBoundingClientRect().top;
      const transcript = host.querySelector(".tw-chat .chat-scroll");
      if (value && transcript) wideChatScroll = transcript.scrollTop;
      const anchor = [
        ...body.querySelectorAll(
          ".td-report h1,.td-report h2,.td-report p,.td-report table",
        ),
      ].find((n) => n.getBoundingClientRect().bottom > top);
      const offset = anchor?.getBoundingClientRect().top;
      wideMaterial = value;
      host
        .querySelector(".tw-layout")
        .classList.toggle("reading-material", value);
      artifacts?.setExpanded(value);
      requestAnimationFrame(() => {
        if (anchor?.isConnected)
          body.scrollTop += anchor.getBoundingClientRect().top - offset;
        if (!value && transcript && wideChatScroll !== null) {
          transcript.scrollTop = wideChatScroll;
          wideChatScroll = null;
        }
      });
    }
    function syncOverlay() {
      const overlay = matchMedia("(max-width:760px)").matches && Boolean(panel);
      const main = host.querySelector(".tw-conversation");
      if (main) main.inert = overlay;
      const drawer = host.querySelector(".tw-drawer");
      if (drawer) {
        drawer.setAttribute("role", overlay ? "dialog" : "complementary");
        if (overlay) drawer.setAttribute("aria-modal", "true");
        else drawer.removeAttribute("aria-modal");
      }
    }
    async function refresh() {
      if (!scope || disposed) return;
      const token = generation,
        sequence = ++refreshSequence,
        wanted = { ...scope };
      try {
        const result = await client.request(
          path(
            `/api/task?task_id=${encodeURIComponent(wanted.taskId)}`,
            wanted.project,
          ),
        );
        if (!alive(token) || sequence !== refreshSequence) return;
        const state = await client.request(
          path("/api/state", result.task.primary_project),
        );
        if (!alive(token) || sequence !== refreshSequence) return;
        detail = result;
        snapshot = state;
        resolvePerson();
        if (
          chat &&
          chatReadOnly !== ["archived", "trashed"].includes(detail.task.state)
        )
          stopRendering();
        const existing = drafts.get(scopeKey());
        if (
          existing &&
          !existing.dirty &&
          existing.version !== detail.task.version
        )
          drafts.delete(scopeKey());
        if (!chat) {
          host.innerHTML =
            '<section class="tw-layout"><div class="tw-conversation"><div class="tw-header"></div><div class="tw-attention"></div><div class="tw-chat"></div></div><aside class="tw-drawer" hidden></aside></section>';
          const remembered = views.get(viewKey());
          panel = scope.initialPanel || remembered?.panel || null;
          lastPanel = panel || remembered?.lastPanel || "details";
          editingDetails = remembered?.editingDetails || false;
          renderHeader();
          mountChat();
          renderPanel();
          if (window.CrewTaskAttention) {
            attention = window.CrewTaskAttention.create({
              host: host.querySelector(".tw-attention"),
              actions,
              client,
              task: detail.task,
              state: () => snapshot,
              refresh,
              navigate,
              toast,
            });
            if (scope.decisionId) attention.reveal(scope.decisionId);
          }
        } else {
          renderHeader();
          chat.update({
            ...snapshot,
            leaders: snapshot.leaders || [],
            members: snapshot.members || [],
          });
          attention?.render();
          if (panel === "members" || (panel === "details" && !editingDetails))
            renderPanel();
          artifacts?.updateContext({
            documents: detail.documents,
            reports: detail.reports,
            taskTitle: detail.task.title,
          });
          void artifacts?.refresh();
        }
        onTaskChanged(detail.task);
      } catch (error) {
        if (!alive(token) || sequence !== refreshSequence) return;
        if (!chat)
          host.innerHTML = `<section class="tw-load-error" role="alert"><h2>任务没有读取成功</h2><p>${esc(error.message)}</p><button class="btn" data-act="tw-refresh">重新读取</button></section>`;
        else toast(error.message);
      }
    }
    async function mutate(operation, fields = {}) {
      if (pending || !detail) return;
      const token = generation,
        body = {
          task_id: detail.task.id,
          version: detail.task.version,
          ...fields,
        };
      const fingerprint = JSON.stringify([operation, body]);
      if (!operationIDs.has(fingerprint)) operationIDs.set(fingerprint, uuid());
      pending = true;
      renderHeader();
      try {
        const result = await api(`/api/tasks/${operation}`, {
          ...body,
          request_id: operationIDs.get(fingerprint),
        });
        if (!alive(token)) return;
        operationIDs.delete(fingerprint);
        detail.task = result.task;
        onTaskChanged(result.task);
        renderHeader();
        const receipt = result.receipt;
        toast(
          receipt && receipt.accepted !== true && receipt.status !== "accepted"
            ? "操作回执尚未确认，请查看原对话；没有重复发送。"
            : operation === "stop"
              ? "停止请求已提交；实际退出状态由后台确认。"
              : "任务设置已保存。",
        );
        await refresh();
      } catch (error) {
        if (alive(token)) {
          toast(error.message);
          if (error.status === 409) await refresh();
        }
      } finally {
        if (alive(token)) {
          pending = false;
          renderHeader();
        }
      }
    }
    function openPerson(sessionId) {
      saveView();
      const targetKey = `${scopeKey()}:${sessionId}`;
      views.set(targetKey, {
        ...views.get(targetKey),
        panel: null,
      });
      if (sessionId === person.session_id) {
        panel = null;
        renderHeader();
        renderPanel();
        return;
      }
      navigate({
        view: "task",
        taskId: detail.task.id,
        project: detail.task.primary_project,
        sessionId,
      });
    }
    const handlers = {
      "tw-refresh": () => refresh(),
      "tw-leader": () => openPerson(detail.task.leader_session_id),
      "tw-person": ({ element }) => openPerson(element.dataset.sessionId),
      "tw-panel": ({ element }) => {
        saveView();
        const requested = element.dataset.panel;
        panel =
          requested === "close" ||
          (requested === "toggle" && panel) ||
          (element.dataset.toggle && requested === panel)
            ? null
            : requested === "toggle"
              ? lastPanel
              : requested;
        if (panel) lastPanel = panel;
        renderHeader();
        renderPanel();
        requestAnimationFrame(() =>
          panel
            ? host.querySelector(".tw-drawer button")?.focus()
            : host.querySelector(`.tw-header [data-panel="${lastPanel}"]`)?.focus(),
        );
      },
      "tw-edit-details": () => {
        editingDetails = true;
        renderPanel();
        host.querySelector('.tw-details-form [name="title"]')?.focus();
      },
      "tw-read-details": () => {
        keepDetails();
        editingDetails = false;
        renderPanel();
      },
      "tw-mode": () =>
        mutate("mode", {
          mode: detail.task.mode === "automatic" ? "manual" : "automatic",
        }),
      "tw-pin": () => mutate("pin", { pinned: !detail.task.pinned }),
      "tw-control": async ({ element }) => {
        const operation = element.dataset.operation;
        if (
          ["archive", "trash"].includes(operation) &&
          !(await confirmTaskOperation(operation))
        )
          return;
        if (disposed) return;
        return mutate(operation);
      },
      "tw-settings": () => {
        saveView();
        navigate({
          view: "settings",
          project: detail.task.primary_project,
          sessionId: person.session_id,
          executionId: selectedExecution()?.id,
        });
      },
      "tw-rebase": () => {
        keepDetails();
        draft().version = detail.task.version;
        keepDetails();
        renderPanel();
      },
    };
    const unregister = actions.register("task-workspace", handlers);
    function input(event) {
      if (!event.target.closest(".tw-details-form")) return;
      editingDetails = true;
      draft().dirty = true;
      draft().revision = (draft().revision || 0) + 1;
      keepDetails();
    }
    async function submit(event) {
      if (!event.target.matches(".tw-details-form")) return;
      event.preventDefault();
      if (pending) return;
      keepDetails();
      const d = draft(),
        token = generation,
        t = detail.task,
        editRevision = d.revision || 0,
        savedScope = scopeKey(),
        savedStorage = draftKey();
      const lines = (value) =>
        value
          .split("\n")
          .map((s) => s.trim())
          .filter(Boolean);
      const body = {
        task_id: t.id,
        version: d.version,
        title: d.title.trim(),
        request: d.request,
        acceptance: lines(d.acceptance).map((title, i) => ({
          id: t.acceptance[i]?.id || uuid(),
          title,
        })),
        constraints: lines(d.constraints),
        projects: [
          t.primary_project,
          ...lines(d.projects).filter((p) => p !== t.primary_project),
        ],
        request_id: uuid(),
      };
      pending = true;
      try {
        const result = await api("/api/tasks/update", body);
        if ((drafts.get(savedScope)?.revision || 0) === editRevision) {
          drafts.delete(savedScope);
          if (alive(token)) editingDetails = false;
          try {
            localStorage.removeItem(savedStorage);
          } catch (error) {
            if (alive(token))
              toast(`详情已保存，本地草稿清理失败：${error.message}`);
          }
        }
        if (!alive(token)) return;
        detail.task = result.task;
        onTaskChanged(result.task);
        renderHeader();
        renderPanel();
        toast("任务详情已保存。");
      } catch (error) {
        if (!alive(token)) return;
        const feedback = host.querySelector(".tw-form-feedback");
        if (feedback)
          feedback.textContent = `${error.message}。你的输入已保留。`;
        if (error.status === 409) {
          await refresh();
          if (alive(token)) renderPanel();
        }
      } finally {
        if (alive(token)) {
          pending = false;
          const button = host.querySelector('.tw-details-form [type="submit"]');
          if (button) button.disabled = false;
        }
      }
    }
    function keydown(event) {
      if (event.key === "Escape" && panel) {
        saveView();
        panel = null;
        renderHeader();
        renderPanel();
        host.querySelector(`.tw-header [data-panel="${lastPanel}"]`)?.focus();
      }
      if (
        event.key !== "Tab" ||
        !panel ||
        !matchMedia("(max-width:760px)").matches
      )
        return;
      const items = [
        ...host.querySelectorAll(
          ".tw-drawer button,.tw-drawer a[href],.tw-drawer input,.tw-drawer textarea,.tw-drawer select,.tw-drawer summary",
        ),
      ].filter((e) => !e.disabled && e.getClientRects().length);
      if (event.shiftKey && document.activeElement === items[0]) {
        event.preventDefault();
        items.at(-1)?.focus();
      } else if (!event.shiftKey && document.activeElement === items.at(-1)) {
        event.preventDefault();
        items[0]?.focus();
      }
    }
    host.addEventListener("input", input);
    host.addEventListener("submit", submit);
    host.addEventListener("keydown", keydown);
    const media = matchMedia("(max-width:760px)");
    media.addEventListener("change", syncOverlay);
    function open({ taskId, project, sessionId, decisionId, panel: initialPanel }) {
      const next = {
        taskId,
        project: projectName(project),
        sessionId: sessionId || null,
        decisionId: decisionId || null,
        initialPanel:
          initialPanel === "artifacts"
            ? "deliverables"
            : ["details", "members", "deliverables"].includes(initialPanel)
              ? initialPanel
              : null,
      };
      if (scope && JSON.stringify(scope) === JSON.stringify(next) && chat)
        return refresh();
      stopRendering();
      generation++;
      scope = next;
      detail = null;
      snapshot = null;
      person = null;
      panel = null;
      editingDetails = false;
      pending = false;
      host.classList.add("crew-task-workspace");
      host.innerHTML =
        '<p class="tw-loading" role="status">正在读取任务与对话…</p>';
      return refresh();
    }
    function destroy() {
      if (disposed) return;
      stopConfirmation?.dialog.close("cancel");
      stopRendering();
      disposed = true;
      generation++;
      unregister();
      host.removeEventListener("input", input);
      host.removeEventListener("submit", submit);
      host.removeEventListener("keydown", keydown);
      media.removeEventListener("change", syncOverlay);
      host.classList.remove("crew-task-workspace");
      host.innerHTML = "";
    }
    return { open, refresh, destroy };
  }
  window.CrewTaskWorkspace = { create };
})();
