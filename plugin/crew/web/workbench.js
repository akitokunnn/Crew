"use strict";
(() => {
  if (new URLSearchParams(location.search).get("legacy") === "1") return;
  const client = CrewClient.current,
    ui = CrewWorkbenchUI,
    e = ui.escape;
  const conversationEntry = CrewConversationEntry.create({ client, ...ui });
  const root = document.getElementById("app"),
    main = document.getElementById("main"),
    sidebar = document.getElementById("sidebar");
  const actions = CrewUIActions.create({
    onError: (error) => toast(error.message),
  });
  const renderer = CrewDOMRenderer.create({ document });
  let projects = [],
    tasks = [],
    attention = [],
    route = {},
    feature = null,
    loading = false,
    loadError = "",
    generation = 0,
    disposed = false,
    loginVisible = false;
  let refreshTimer = null,
    dataGeneration = 0;
  const scrollPositions = new Map();
  let themeCatalog = null;
  function applyPalette() {
    if (!themeCatalog?.length) return;
    const project = projects.find((item) => item.name === route.project);
    CrewTheme.apply(themeCatalog, project?.theme || null, theme);
  }
  root.className = "wb-shell";
  main.className = "wb-main workbench-main";
  sidebar.className = "wb-sidebar";
  document.body.classList.add("workbench");
  const navMedia = matchMedia("(max-width:760px)");
  const navScrim = document.createElement("button");
  navScrim.className = "wb-nav-scrim";
  navScrim.dataset.act = "wb-menu";
  navScrim.setAttribute("aria-label", "关闭导航遮罩");
  navScrim.hidden = true;
  root.append(navScrim);
  function syncNavigation(focus = false) {
    const open = navMedia.matches && root.classList.contains("wb-menu-open");
    sidebar.inert = navMedia.matches && !open;
    sidebar.setAttribute("aria-hidden", String(navMedia.matches && !open));
    main.inert = open;
    navScrim.hidden = !open;
    if (focus) {
      if (open) sidebar.querySelector("button")?.focus({ preventScroll: true });
      else
        main
          .querySelector('[aria-label="打开导航"]')
          ?.focus({ preventScroll: true });
    }
  }
  navMedia.addEventListener("change", () => syncNavigation());
  [
    "chat-attention",
    "side-task-section",
    "goal-drawer",
    "settings-surface",
  ].forEach((id) => (document.getElementById(id).hidden = true));
  const display = (p) => p.display_name || p.name;
  const routeURL = (value) => {
    const params = new URLSearchParams();
    for (const [key, val] of Object.entries(value))
      if (key !== "view" && val != null && val !== "") params.set(key, val);
    return `#${value.view || "home"}${params.size ? `?${params}` : ""}`;
  };
  function parseRoute() {
    const [view, query = ""] = location.hash.slice(1).split("?");
    const value = {
      view: view || "home",
      ...Object.fromEntries(new URLSearchParams(query)),
    };
    if (!location.hash && new URLSearchParams(location.search).has("project"))
      value.project = new URLSearchParams(location.search).get("project");
    return value;
  }
  function toast(message) {
    const node = document.createElement("div");
    node.className = "toast show";
    node.role = "status";
    node.textContent = message;
    document.getElementById("toast-root").replaceChildren(node);
    setTimeout(() => node.remove(), 6000);
  }
  function navigate(value) {
    const hash = routeURL(value);
    if (location.hash !== hash) history.pushState(null, "", hash);
    return showRoute();
  }
  function navButton(label, value, icon) {
    return `<button class="wb-nav ${route.view === value.view && !value.project ? "active" : ""}" data-act="wb-navigate" data-route="${e(routeURL(value))}">${ui.icon(icon)}<span>${e(label)}</span></button>`;
  }
  function taskButton(task, related = false) {
    return `<div class="wb-nav-task ${route.taskId === String(task.id) ? "active" : ""}"><button data-act="wb-task" data-id="${e(task.id)}" title="${e(task.title)}"><i class="wb-task-dot" data-state="${e(task.state)}"></i><span>${e(task.title)}</span>${task.review_status === "pending" ? '<span class="wb-new-dot" title="有成果待体验"></span>' : ""}${related ? '<span class="wb-related">↗</span>' : ""}</button>${task.pinned ? `<button class="wb-unpin" data-act="wb-pin" data-id="${e(task.id)}" aria-label="取消置顶 ${e(task.title)}" title="取消置顶">${ui.icon("pin")}</button>` : ""}</div>`;
  }
  function renderNav() {
    const prior = sidebar.querySelector(".wb-nav-scroll")?.scrollTop || 0;
    renderer.replace(
      sidebar,
      `<div class="wb-brand"><img src="/logo.svg" alt=""><span>Crew</span><button class="wb-icon-button wb-mobile-close" data-act="wb-menu" aria-label="收起导航">${ui.icon("close")}</button></div>
      <div class="wb-primary-nav">${navButton("首页", { view: "home" }, "home")}${navButton("个人助手", { view: "assistant" }, "chat")}${navButton("TODO 与灵感", { view: "personal" }, "list")}</div>
      <div class="wb-nav-scroll"><section class="wb-nav-group"><p class="wb-nav-label">置顶</p>${
        tasks
          .filter((t) => t.pinned && !["trashed", "archived"].includes(t.state))
          .map((t) => taskButton(t))
          .join("") || '<p class="wb-nav-empty">常用任务可以置顶在这里</p>'
      }</section>
      <div class="wb-nav-label wb-label-action"><span>工作区</span><button class="wb-icon-button" data-act="wb-new-workspace" aria-label="添加工作区">${ui.icon("plus")}</button></div>
      ${projects
        .map((project) => {
          const list = tasks.filter(
            (t) =>
              (t.projects || [t.primary_project]).includes(project.name) &&
              !["archived", "trashed"].includes(t.state),
          );
          return `<section class="wb-nav-group"><div class="wb-workspace-name"><button data-act="wb-project" data-project="${e(project.name)}">${ui.icon("folder")}<span>${e(display(project))}</span></button><a href="${e(ui.legacyURL(project.name, "git"))}" title="${e(display(project))} 的 Git" aria-label="${e(display(project))} 的 Git" class="wb-workspace-git">⑂</a></div>${list.map((task) => taskButton(task, task.primary_project !== project.name)).join("") || '<p class="wb-nav-empty">从一段对话开始</p>'}</section>`;
        })
        .join("")}</div>
      <div class="wb-nav-bottom">${navButton("更多", { view: "more" }, "more")}${navButton("设置", { view: "settings" }, "settings")}<div class="wb-source-label">${client.synthetic ? "样例数据 · 不连接真实 AI" : "已选择真实服务"}</div></div>`,
    );
    sidebar.querySelector(".wb-nav-scroll").scrollTop = prior;
    syncNavigation();
    applyPalette();
  }
  function page(title, subtitle, content) {
    return `<div class="wb-mobile-toolbar"><button class="wb-icon-button" data-act="wb-menu" aria-label="打开导航">${ui.icon("menu")}</button><span>${e(title)}</span></div><div class="wb-page"><div class="wb-page-inner"><header class="wb-page-heading"><p class="wb-eyebrow">${client.synthetic ? "开发用样例 · 所有内容均为虚构" : "你的个人工作台"}</p><h1>${e(title)}</h1>${subtitle ? `<p class="wb-muted">${e(subtitle)}</p>` : ""}</header>${content}</div></div>`;
  }
  function taskCard(task) {
    return `<article class="wb-task-card"><button class="wb-card-main" data-act="wb-task" data-id="${e(task.id)}"><div class="wb-card-top">${ui.status(task)}${task.review_status === "pending" ? '<span class="wb-artifact-badge">有成果待体验</span>' : ""}</div><h3>${e(task.title)}</h3><p>${e(task.summary || task.waiting_reason || task.request || "打开任务继续交流")}</p><div class="wb-meta">${e(task.primary_project)}${task.latest_version ? ` · 成果 ${e(task.latest_version.label || task.latest_version.version || task.latest_version)}` : ""}</div></button><button class="wb-card-pin ${task.pinned ? "is-pinned" : ""}" data-act="wb-pin" data-id="${e(task.id)}" aria-label="${task.pinned ? "取消置顶" : "置顶"} ${e(task.title)}" aria-pressed="${!!task.pinned}">${ui.icon("pin")}</button></article>`;
  }
  function captureMarkup() {
    return `<form class="wb-capture" id="wb-capture"><label class="sr-only" for="wb-new-text">聊聊要做什么，或记下想法</label><textarea id="wb-new-text" placeholder="今天想做点什么？直接聊聊。" rows="2">${e(ui.recall("home.capture", ""))}</textarea><div class="wb-capture-footer"><label>工作区 <select name="project" aria-label="对话所属工作区"><option value="">选择工作区</option>${projects.map((p) => `<option value="${e(p.name)}" ${(route.project || ui.recall("home.project", projects[0]?.name)) === p.name ? "selected" : ""}>${e(display(p))}</option>`).join("")}</select></label><div><button class="wb-button" type="button" data-act="wb-capture">记下来</button><button class="wb-button primary" type="submit">开始聊 ↗</button></div></div><p class="wb-inline-error" id="wb-capture-error" role="alert"></p></form>`;
  }
  function attentionMarkup() {
    const shown = attention
      .filter(
        (item) =>
          !route.project ||
          (item.project || item.primary_project) === route.project,
      )
      .slice(0, 4);
    return shown.length
      ? `<section class="wb-attention"><div class="wb-section-heading"><h2>需要你看一下</h2><span class="wb-muted">回到原对话沟通</span></div>${shown.map((item) => `<button class="wb-attention-row" data-act="wb-attention" data-id="${e(item.task_id || item.goal_id || "")}" data-decision="${e(item.decision_id || "")}" data-session="${e(item.session_id || item.leader_session_id || "")}" data-project="${e(item.project || item.primary_project || "")}"><span class="wb-attention-kind">${e({ todo: "待处理", artifact: "新成果", review: "待体验", blocked: "受阻", decision: "待拍板" }[item.kind] || "待处理")}</span><span>${e(item.summary || item.title || item.question || item.text || "打开原对话处理")}</span><span aria-hidden="true">↗</span></button>`).join("")}</section>`
      : "";
  }
  function renderHome() {
    const current = projects.find((p) => p.name === route.project),
      list = tasks.filter(
        (t) =>
          (!route.project ||
            (t.projects || [t.primary_project]).includes(route.project)) &&
          !["archived", "trashed"].includes(t.state),
      );
    main.innerHTML = page(
      current ? display(current) : "给想法一点空间。",
      current
        ? "这里的任务各自推进，你随时进来聊两句。"
        : "任务在继续。需要你处理的事情，留在这里。",
      `${loadError ? `<div class="wb-error" role="alert">${e(loadError)} <button class="wb-link" data-act="wb-refresh">重新连接</button></div>` : ""}${captureMarkup()}${attentionMarkup()}<section><div class="wb-section-heading"><h2>${current ? "工作区任务" : "正在推进"}</h2><button class="wb-link" data-act="wb-refresh">刷新</button></div>${loading ? '<p class="wb-empty">正在读取工作区…</p>' : list.length ? `<div class="wb-task-list">${list.map(taskCard).join("")}</div>` : `<p class="wb-empty">${projects.length ? "还没有任务。把需求写在上面，开始第一段对话。" : "先添加一个工作区，普通文件夹也可以。"}</p>`}</section>
      ${
        current
          ? `<div class="wb-workspace-footer"><span class="wb-meta">${e(current.root)}</span><button class="wb-link" data-act="wb-folder" data-project="${e(current.name)}">打开文件夹</button><a class="wb-link" href="${e(ui.legacyURL(current.name))}">关系总览与已有会话 ↗</a></div>`
          : `<section class="wb-workspace-summary"><div class="wb-section-heading"><h2>工作区</h2></div>${projects
              .map((p) => {
                const own = tasks.filter((t) =>
                  (t.projects || [t.primary_project]).includes(p.name),
                );
                return `<button class="wb-summary-row" data-act="wb-project" data-project="${e(p.name)}"><span>${ui.icon("folder")}${e(display(p))}</span><span class="wb-meta">${own.filter((t) => t.state === "running").length} 个推进中 · ${own.filter((t) => t.state === "waiting_user").length} 个等你 <b>→</b></span></button>`;
              })
              .join("")}</section>`
      }`,
    );
    const text = main.querySelector("textarea"),
      form = main.querySelector("form");
    text.oninput = () => ui.remember("home.capture", text.value);
    form.querySelector("select").onchange = (event) =>
      ui.remember("home.project", event.target.value);
    form.onsubmit = (event) => {
      event.preventDefault();
      void capture(true);
    };
    restoreScroll();
  }
  function rememberScroll() {
    const node = main.querySelector(".wb-page");
    if (node) scrollPositions.set(routeURL(route), node.scrollTop);
  }
  function restoreScroll() {
    const node = main.querySelector(".wb-page");
    if (node) node.scrollTop = scrollPositions.get(routeURL(route)) || 0;
  }
  async function capture(discuss) {
    const ticket = generation,
      form = main.querySelector("#wb-capture"),
      submitted = form.querySelector("textarea").value,
      text = submitted.trim(),
      project = form.querySelector("select").value;
    if (!text) {
      form.querySelector("textarea").focus();
      return;
    }
    if (discuss && !project) {
      form.querySelector("#wb-capture-error").textContent =
        "先选择一个工作区，再开始聊。";
      return;
    }
    const draftKey = discuss ? "home.create" : "home.note",
      payload = discuss
        ? { project, text }
        : { text, project: project || null, tags: [] },
      prior = ui.recall(draftKey, null);
    const attempt =
      prior && JSON.stringify(prior.payload) === JSON.stringify(payload)
        ? prior
        : { payload, request_id: ui.requestId() };
    ui.remember(draftKey, attempt);
    form.querySelectorAll("button").forEach((b) => (b.disabled = true));
    try {
      const value = discuss
        ? await conversationEntry.start({
            project,
            text,
            key: "home.conversation",
          })
        : await client.request("/api/personal/capture", {
            ...payload,
            request_id: attempt.request_id,
          });
      if (ui.recall("home.capture", "") === submitted)
        ui.remember("home.capture", "");
      ui.remember(draftKey, null);
      if (discuss) {
        await refreshData(false);
        if (ticket === generation && route.view === "home")
          navigate({
            view: "task",
            taskId: value.task.id,
            project: value.task.primary_project,
          });
        else toast("任务已建立，可以从导航继续对话");
      } else {
        toast("已记下来，在 TODO 与灵感中继续整理");
        if (
          ticket === generation &&
          form.isConnected &&
          form.querySelector("textarea").value === submitted
        )
          form.querySelector("textarea").value = "";
      }
    } catch (error) {
      if (ticket === generation && form.isConnected)
        form.querySelector("#wb-capture-error").textContent =
          `${error.message}。输入已保留。`;
      else toast(error.message);
    } finally {
      form.querySelectorAll("button").forEach((b) => (b.disabled = false));
    }
  }
  function renderMore() {
    const project = route.project || projects[0]?.name || "";
    const workspaceId = projects.find(
      (item) => item.name === project,
    )?.workspace_id;
    const links = [
      ["关系总览与已有会话", "all", "保留 leader 总对话和已有目标"],
      ["公共频道", "channel", "成员之间的公开交流"],
      ["Git", "git", "查看所选工作区的变更"],
      ["用量与额度", "usage", "真实用量、配额及记录"],
      ["历史与待收编", "adopt", "查找、收编已有原生会话"],
    ];
    if (workspaceId)
      links.push([
        "完整保存历史",
        "all",
        "已保存的成员、会话与产出",
        `storage:${workspaceId}`,
      ]);
    const linkMarkup = links
      .map(
        ([title, view, description, referenceProject]) =>
          `<a class="wb-more-card" href="${e(ui.legacyURL(referenceProject || project, view))}"><h2>${title} ↗</h2><p>${description}</p></a>`,
      )
      .join("");
    main.innerHTML = page(
      "更多",
      "已有能力都在这里，按工作区进入。",
      `<label class="wb-field">工作区<select id="wb-more-project">${projects.map((p) => `<option value="${e(p.name)}" ${p.name === project ? "selected" : ""}>${e(display(p))}</option>`).join("")}</select></label><div class="wb-more-grid">${linkMarkup}<button class="wb-more-card" data-act="wb-library" data-state="archived"><h2>归档任务</h2><p>完成后收好，需要时再找回来</p></button><button class="wb-more-card" data-act="wb-library" data-state="trashed"><h2>垃圾箱</h2><p>恢复任务记录，继续执行需明确操作</p></button><a class="wb-more-card" href="${e(ui.legacyURL(project, "all", "storage"))}"><h2>数据与历史存储 ↗</h2><p>导入、导出、恢复及历史清理</p></a></div><p class="wb-footnote">文件与交付文档从所属任务的“资料”中查看。</p>`,
    );
    main.querySelector("select").onchange = (event) =>
      navigate({ view: "more", project: event.target.value });
    restoreScroll();
  }
  async function renderLibrary(state) {
    main.innerHTML = page(
      state === "trashed" ? "垃圾箱" : "归档任务",
      "保留已有成果和任务来历。",
      '<p class="wb-empty">正在读取…</p>',
    );
    const ticket = generation;
    try {
      const result = await client.request("/api/tasks?include_inactive=1");
      if (ticket !== generation) return;
      const list = result.tasks.filter((task) => task.state === state);
      main
        .querySelector(".wb-page-inner")
        .insertAdjacentHTML(
          "beforeend",
          list.length
            ? list
                .map(
                  (task) =>
                    `<article class="wb-library-row"><div><h3>${e(task.title)}</h3><p class="wb-meta">${e(task.primary_project)}</p></div><button class="wb-button" data-act="wb-restore" data-id="${e(task.id)}" data-version="${e(task.version)}">恢复记录</button></article>`,
                )
                .join("")
            : '<p class="wb-empty">这里没有任务。</p>',
        );
      main.querySelector(".wb-empty").remove();
    } catch (error) {
      if (ticket === generation)
        main.querySelector(".wb-empty").textContent = error.message;
    }
  }
  async function renderAssistant() {
    main.innerHTML = `<div class="wb-mobile-toolbar"><button class="wb-icon-button" data-act="wb-menu" aria-label="打开导航">${ui.icon("menu")}</button><span>个人助手</span></div><header class="wb-assistant-head"><p class="wb-eyebrow">你的全局助手</p><h1>个人助手</h1><p class="wb-muted">聊进展，整理想法，也可以交代具体事情。</p></header><div class="wb-assistant-chat" id="wb-assistant-chat"><p class="wb-empty">正在连接助手…</p></div>`;
    const ticket = generation;
    try {
      const { assistant } = await client.request("/api/assistant/open", {});
      if (ticket !== generation) return;
      const session = assistant.session || assistant,
        anchor = session.project || assistant.project;
      if (!anchor || !session.session_id)
        throw Error("助手接口缺少固定会话或工作区身份");
      const snapshot = await client.request(
        `/api/state?project=${encodeURIComponent(anchor)}`,
      );
      if (ticket !== generation) return;
      const api = (path, body) => {
        if (path.startsWith("/api/chat/send"))
          return client.request("/api/assistant/send", {
            ...body,
            project: anchor,
          });
        const url = new URL(path, location.origin);
        url.searchParams.set("project", anchor);
        return client.request(url.pathname + url.search, body);
      };
      CrewChat.mount({
        host: main.querySelector("#wb-assistant-chat"),
        session,
        state: snapshot,
        api,
        toast,
        canRead: () => !document.hidden,
        readonly: false,
      });
      feature = { destroy: () => CrewChat.unmount() };
    } catch (error) {
      if (ticket === generation)
        main.querySelector("#wb-assistant-chat").innerHTML =
          `<p class="wb-error">${e(error.message)}</p><button class="wb-button" data-act="wb-route-retry">重试</button>`;
    }
  }
  async function showRoute() {
    rememberScroll();
    generation++;
    feature?.destroy();
    feature = null;
    CrewChat.unmount();
    route = parseRoute();
    loginVisible = false;
    if (
      ![
        "task",
        "home",
        "personal",
        "assistant",
        "settings",
        "remote",
        "appearance",
        "more",
        "library",
      ].includes(route.view)
    )
      route = { view: "home" };
    root.classList.remove("wb-menu-open");
    renderNav();
    document.title = `${{ home: "工作台", personal: "TODO 与灵感", assistant: "个人助手", settings: "设置", remote: "远程连接", appearance: "外观", more: "更多", task: "任务", library: "历史" }[route.view]} · Crew`;
    if (route.view === "home") renderHome();
    else if (route.view === "task") {
      feature = CrewTaskWorkspace.create({
        host: featureHost("任务"),
        client,
        actions,
        navigate,
        toast,
        onTaskChanged: () => refreshData(false),
      });
      await feature.open({
        taskId: route.taskId,
        project: route.project,
        sessionId: route.sessionId,
        decisionId: route.decisionId,
      });
    } else if (route.view === "personal") {
      feature = CrewPersonalWorkspace.create({
        host: featureHost("TODO 与灵感"),
        client,
        actions,
        navigate,
        toast,
        projects: () => projects,
      });
      await feature.open({ project: route.project });
    } else if (route.view === "settings") {
      feature = CrewScopedSettings.create({
        host: settingsHost(),
        client,
        actions,
        toast,
        onLegacySettings: (category) =>
          ["remote", "appearance"].includes(category)
            ? navigate({ view: category, project: route.project })
            : location.assign(
                ui.legacyURL(
                  route.project || projects[0]?.name,
                  "all",
                  category || "ai",
                ),
              ),
      });
      await feature.open({
        project: route.project,
        sessionId: route.sessionId,
        executionId: route.executionId,
      });
    } else if (route.view === "remote") {
      feature = CrewRemoteSettings.create({
        host: featureHost("远程连接"),
        client,
        actions,
        toast,
        onLoggedOut: () => login({ message: "已退出远程会话，请重新登录" }),
      });
      await feature.open();
    } else if (route.view === "appearance") await renderAppearance();
    else if (route.view === "assistant") await renderAssistant();
    else if (route.view === "more") renderMore();
    else await renderLibrary(route.state || "archived");
  }
  function featureHost(title) {
    main.innerHTML = `<div class="wb-mobile-toolbar"><button class="wb-icon-button" data-act="wb-menu" aria-label="打开导航">${ui.icon("menu")}</button><span>${e(title)}</span></div><div class="wb-feature-host"></div>`;
    return main.querySelector(".wb-feature-host");
  }
  async function renderAppearance() {
    main.innerHTML = page(
      "外观",
      "按你的习惯调整显示与反馈。",
      '<div id="wb-appearance"></div>',
    );
    const ticket = generation;
    feature = CrewAppearanceUI.create({
      host: main.querySelector("#wb-appearance"),
      document,
      window,
      renderer,
      actions,
      theme: CrewTheme,
      sound: CrewSound,
      icon: ui.icon,
      notify: toast,
      saveProjectTheme: (project, theme) =>
        client.request("/api/project/theme", { project, theme }),
      onProjectTheme: () => refreshData(false),
      onModeChange: () => {
        theme = feature.mode();
      },
    });
    try {
      const snapshot = await client.request(
        `/api/state${route.project ? `?project=${encodeURIComponent(route.project)}` : ""}`,
      );
      if (ticket !== generation) return;
      if (snapshot.themes?.length) {
        themeCatalog = snapshot.themes;
        feature.sync({
          themes: snapshot.themes,
          project: route.project ? snapshot.project : null,
        });
      }
    } catch (error) {
      if (ticket === generation) toast(error.message);
    }
  }
  function settingsHost() {
    const outer = featureHost("设置"),
      ticket = generation;
    outer.innerHTML = `<div class="wb-settings-context"><label>配置上下文 <select aria-label="设置工作区"><option value="">全局默认</option>${projects.map((project) => `<option value="${e(project.name)}" ${project.name === route.project ? "selected" : ""}>${e(display(project))}</option>`).join("")}</select></label><label ${route.project ? "" : "hidden"}>AI <select aria-label="设置 AI"><option value="">工作区默认</option>${route.sessionId ? `<option value="${e(route.sessionId)}" selected>${e(route.sessionId)}</option>` : ""}</select></label></div><div class="wb-feature-host" id="wb-settings-content"></div>`;
    const projectInput = outer.querySelector('[aria-label="设置工作区"]');
    projectInput.onchange = () =>
      navigate({ view: "settings", project: projectInput.value });
    const sessionInput = outer.querySelector('[aria-label="设置 AI"]');
    sessionInput.onchange = () =>
      navigate({
        view: "settings",
        project: route.project,
        sessionId: sessionInput.value,
      });
    if (route.project)
      void client
        .request(`/api/state?project=${encodeURIComponent(route.project)}`)
        .then((snapshot) => {
          if (ticket !== generation) return;
          const people = [
            ...(snapshot.leaders || []),
            ...(snapshot.members || []),
          ];
          sessionInput.innerHTML = `<option value="">工作区默认</option>${people.map((person) => `<option value="${e(person.session_id)}" ${person.session_id === route.sessionId ? "selected" : ""}>${e(person.name)}${person.leader_session_id ? " · 成员" : " · leader"}</option>`).join("")}`;
          if (
            route.sessionId &&
            !people.some((person) => person.session_id === route.sessionId)
          )
            sessionInput.insertAdjacentHTML(
              "beforeend",
              `<option value="${e(route.sessionId)}" selected>${e(route.sessionId)}</option>`,
            );
        })
        .catch((error) => {
          if (ticket === generation) toast(`AI 列表读取失败：${error.message}`);
        });
    return outer.querySelector("#wb-settings-content");
  }
  async function refreshData(render = true) {
    const ticket = ++dataGeneration;
    const results = await Promise.allSettled([
      client.request("/api/projects"),
      client.request("/api/tasks"),
      client.request("/api/tasks/attention"),
    ]);
    if (disposed || ticket !== dataGeneration) return;
    const auth = results.find(
      (r) => r.status === "rejected" && r.reason.status === 401,
    );
    if (auth) {
      login(auth.reason);
      return;
    }
    const errors = [];
    if (results[0].status === "fulfilled")
      projects = results[0].value.projects || [];
    else errors.push(`工作区：${results[0].reason.message}`);
    if (results[1].status === "fulfilled") tasks = results[1].value.tasks || [];
    else errors.push(`任务：${results[1].reason.message}`);
    if (results[2].status === "fulfilled") {
      const value = results[2].value;
      attention = value.attention || value.items || [];
    } else errors.push(`待处理摘要：${results[2].reason.message}`);
    loadError = errors.join("；");
    loading = false;
    renderNav();
    if (
      render &&
      route.view === "home" &&
      !main.contains(document.activeElement)
    ) {
      rememberScroll();
      renderHome();
    } else if (render && route.view === "task") await feature?.refresh();
  }
  function login(error) {
    feature?.destroy();
    feature = null;
    CrewChat.unmount();
    generation++;
    dataGeneration++;
    loginVisible = true;
    projects = [];
    tasks = [];
    attention = [];
    renderNav();
    main.innerHTML = page(
      "连接 Crew",
      error.message,
      `<form id="wb-login" class="wb-login"><label>访问口令<input name="passphrase" type="password" autocomplete="current-password" required></label><button class="wb-button primary">登录</button><p class="wb-inline-error" role="alert"></p></form>`,
    );
    main.querySelector("form").onsubmit = async (event) => {
      event.preventDefault();
      const form = event.target,
        button = form.querySelector("button");
      button.disabled = true;
      try {
        await client.request("/api/auth/login", {
          passphrase: form.passphrase.value,
        });
        loginVisible = false;
        await refreshData(false);
        if (!loginVisible) await showRoute();
      } catch (failure) {
        form.querySelector("p").textContent = failure.message;
      } finally {
        button.disabled = false;
      }
    };
  }
  function addWorkspace() {
    const dialog = document.createElement("dialog");
    dialog.className = "wb-dialog";
    dialog.innerHTML = `<form><h2>添加工作区</h2><p class="wb-muted">普通文件夹也可以，无需 Git。</p><label>名称<input name="name" required placeholder="例如：我的小工具"></label><label>电脑上的文件夹路径<input name="root" required placeholder="C:\\Projects\\my-tool"></label><p class="wb-inline-error" role="alert"></p><div class="wb-row-actions"><button type="button" class="wb-button" data-close>取消</button><button class="wb-button primary">添加</button></div></form>`;
    document.body.append(dialog);
    dialog.showModal();
    dialog.querySelector("[data-close]").onclick = () => dialog.close();
    dialog.onclose = () => dialog.remove();
    dialog.querySelector("form").onsubmit = async (event) => {
      event.preventDefault();
      const form = event.target,
        button = form.querySelector('button[type="submit"],button.primary');
      button.disabled = true;
      try {
        await client.request("/api/project/init", {
          name: form.elements.name.value,
          root: form.elements.root.value,
        });
        dialog.close();
        await refreshData(false);
        navigate({ view: "home", project: form.elements.name.value });
      } catch (error) {
        form.querySelector("[role=alert]").textContent = error.message;
      } finally {
        button.disabled = false;
      }
    };
  }
  actions.register("workbench", {
    "wb-navigate": ({ element }) => {
      if (location.hash !== element.dataset.route)
        history.pushState(null, "", element.dataset.route);
      return showRoute();
    },
    "wb-task": ({ element }) => {
      const task = tasks.find((t) => String(t.id) === element.dataset.id);
      if (task)
        navigate({
          view: "task",
          taskId: task.id,
          project: task.primary_project,
        });
    },
    "wb-project": ({ element }) =>
      navigate({ view: "home", project: element.dataset.project }),
    "wb-menu": () => {
      root.classList.toggle("wb-menu-open");
      syncNavigation(true);
    },
    "wb-pin": async ({ element }) => {
      const task = tasks.find((t) => String(t.id) === element.dataset.id);
      element.disabled = true;
      try {
        await client.request("/api/tasks/pin", {
          task_id: task.id,
          pinned: !task.pinned,
          request_id: ui.requestId(),
        });
        await refreshData();
      } finally {
        element.disabled = false;
      }
    },
    "wb-refresh": () => refreshData(),
    "wb-route-retry": () => showRoute(),
    "wb-capture": () => capture(false),
    "wb-new-workspace": () => addWorkspace(),
    "wb-attention": ({ element }) => {
      if (element.dataset.id)
        navigate({
          view: "task",
          taskId: element.dataset.id,
          project: element.dataset.project,
          decisionId: element.dataset.decision,
        });
      else if (element.dataset.session)
        location.assign(
          ui.legacyURL(
            element.dataset.project,
            `leader=${encodeURIComponent(element.dataset.session)}`,
          ),
        );
    },
    "wb-folder": async ({ element }) => {
      const workspace = projects.find(
          (p) => p.name === element.dataset.project,
        ),
        result = await client.desktop.openFolder(workspace.root);
      if (result?.opened) toast("已在电脑打开文件夹");
      else {
        try {
          await navigator.clipboard.writeText(workspace.root);
          toast("当前浏览器无法打开本地文件夹，路径已复制");
        } catch (_) {
          toast(`文件夹路径：${workspace.root}`);
        }
      }
    },
    "wb-library": ({ element }) =>
      navigate({ view: "library", state: element.dataset.state }),
    "wb-restore": async ({ element }) => {
      const ticket = generation,
        state = route.state;
      await client.request("/api/tasks/restore", {
        task_id: element.dataset.id,
        version: Number(element.dataset.version),
        request_id: ui.requestId(),
      });
      toast("已恢复任务记录，尚未启动执行");
      await refreshData(false);
      if (ticket === generation && route.view === "library")
        await renderLibrary(state || "archived");
    },
  });
  const click = (event) => actions.handle(event);
  document.addEventListener("click", click);
  window.addEventListener("hashchange", () => void showRoute());
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !event.isComposing) {
      root.classList.remove("wb-menu-open");
      syncNavigation();
    }
  });
  CrewChat.initFiles((path, body) => client.request(path, body), toast);
  let theme = "system";
  try {
    theme = localStorage.getItem("crew.theme") || "system";
    document.documentElement.dataset.reducedMotion = String(
      localStorage.getItem("crew.reducedMotion") === "true",
    );
    const fontScale = Number(localStorage.getItem("crew.fontScale"));
    if (fontScale >= 0.9 && fontScale <= 1.4)
      document.documentElement.style.setProperty("--fs", String(fontScale));
  } catch (_) {}
  CrewTheme.setMode(theme);
  async function start() {
    loading = true;
    route = parseRoute();
    renderNav();
    renderHome();
    await refreshData(false);
    if (!loginVisible) {
      try {
        const snapshot = await client.request("/api/state");
        themeCatalog = snapshot.themes;
      } catch (error) {
        toast(`显示偏好读取失败：${error.message}`);
      }
    }
    if (!loginVisible) await showRoute();
    refreshTimer = setInterval(() => {
      if (!document.hidden && !loginVisible) void refreshData();
    }, 15000);
  }
  window.addEventListener(
    "pagehide",
    (event) => {
      // A cancelled desktop navigation or BFCache entry still needs a live UI.
      if (event.persisted) return;
      disposed = true;
      clearInterval(refreshTimer);
      feature?.destroy();
      actions.destroy();
      renderer.destroy();
      document.removeEventListener("click", click);
    },
  );
  void start();
})();
