(function (root) {
  "use strict";

  function mount(element, options = {}) {
    const { esc, avatar, statusBadge, runtimeName } = root.CrewOrgLab.presentation;
    const keyFor = root.CrewOrgModel.conversationKey;
    const id = element.id;
    const narrow = matchMedia("(max-width: 740px)");
    const state = {
      leaderId: "lin",
      personId: "lin",
      taskId: "org",
      panelOpen: !narrow.matches,
      scope: "group",
      expanded: new Set(["org"]),
      mapCollapsed: new Set(),
      selectedPersonId: null,
      selectedTaskId: null,
      drafts: new Map(),
      messages: new Map(),
      scrollPositions: new Map()
    };
    let snapshot = root.openai?.widgetState?.privateContent?.chatOrg;
    if (snapshot && root.CrewOrgSample.people.some(person => person.session_id === snapshot.leaderId && person.role === "leader")) {
      state.leaderId = snapshot.leaderId;
      const initial = root.CrewOrgModel.project(root.CrewOrgSample, state.leaderId);
      if (initial.people.some(person => person.id === snapshot.personId)) state.personId = snapshot.personId;
      else state.personId = state.leaderId;
      state.taskId = initial.tasks.some(task => task.id === snapshot.taskId && !task.history) ? snapshot.taskId : null;
      state.scope = snapshot.scope === "task" && state.taskId ? "task" : "group";
      state.expanded = new Set([state.taskId].filter(Boolean));
      state.selectedPersonId = initial.people.some(person => person.id === snapshot.selectedPersonId) ? snapshot.selectedPersonId : null;
      state.selectedTaskId = initial.tasks.some(task => task.id === snapshot.selectedTaskId && !task.history) ? snapshot.selectedTaskId : null;
    }

    element.classList.add("org-demo", "cd-demo");
    element.innerHTML = `
      <header class="cd-topbar">
        <span class="org-brand"><img class="org-mark" src="${options.logo || "crew.svg"}" alt="">Crew</span>
        <span class="cd-workspace">工作区 / Crew</span>
        <span class="cd-sample">对话右侧架构 · 样例演示 · <a href="chat-light.html">轻量版</a></span>
      </header>
      <div class="cd-workspace-body">
        <nav class="cd-nav" aria-label="小组任务">
          <label for="${id}-leader">负责人</label>
          <select id="${id}-leader" data-chat-control="leader">
            ${root.CrewOrgSample.people.filter(person => person.role === "leader").map(person => `
              <option value="${esc(person.session_id)}">${esc(person.name)} · ${runtimeName(person.runtime)}</option>
            `).join("")}
          </select>
          <div class="cd-nav-items"></div>
          <p class="cd-nav-note">任务与总会话各自保留草稿</p>
        </nav>
        <main class="cd-conversation" aria-label="当前对话">
          <header class="cd-chat-header"></header>
          <div class="cd-messages" role="log" aria-label="样例对话记录" tabindex="0"></div>
          <form class="cd-composer">
            <label class="cd-composer-label" for="${id}-draft"></label>
            <textarea id="${id}-draft" aria-label="当前对话草稿" rows="3" placeholder="继续说说你的想法…"></textarea>
            <div class="cd-compose-footer">
              <span class="cd-compose-status" role="status"></span>
              <button type="submit" class="cd-send">模拟发送</button>
            </div>
          </form>
        </main>
        <div class="cd-panel-slot"></div>
      </div>
      <aside class="cd-panel" aria-label="本组架构图">
        <header class="cd-panel-header">
          <div><h2>本组架构</h2><span class="cd-panel-subtitle"></span></div>
          <div>
            <button type="button" data-chat-action="expand-map" aria-label="展开完整关系图谱">展开</button>
            <button type="button" data-chat-action="close-panel" aria-label="关闭架构图">×</button>
          </div>
        </header>
        <div class="cd-panel-controls" aria-label="架构图范围">
          <button type="button" data-chat-action="panel-scope" data-scope="task" aria-pressed="false">本任务</button>
          <button type="button" data-chat-action="panel-scope" data-scope="group" aria-pressed="true">全组任务</button>
        </div>
        <div class="cd-tree-scroll"></div>
        <div class="cd-selection" aria-live="polite"></div>
      </aside>
      <dialog class="cd-mobile-panel" aria-label="本组架构图"></dialog>
      <dialog class="cd-map-dialog" aria-labelledby="${id}-map-title">
        <header>
          <div>
            <h2 id="${id}-map-title">完整关系图谱</h2>
            <p>当前对话与草稿保留在原处</p>
          </div>
          <button type="button" data-chat-action="close-map" aria-label="关闭完整关系图谱">返回对话 ×</button>
        </header>
        <div class="cd-full-map"></div>
      </dialog>`;

    const panel = element.querySelector(".cd-panel");
    const panelSlot = element.querySelector(".cd-panel-slot");
    const mobileDialog = element.querySelector(".cd-mobile-panel");
    const mapDialog = element.querySelector(".cd-map-dialog");
    const draft = element.querySelector("textarea");
    const transcript = element.querySelector(".cd-messages");
    const tree = element.querySelector(".cd-tree-scroll");
    const group = () => root.CrewOrgModel.project(root.CrewOrgSample, state.leaderId);
    const scopeKey = () => keyFor(state.personId, state.taskId);

    function persist() {
      if (!options.inline || !root.openai?.setWidgetState) return;
      const preferences = {
        leaderId: state.leaderId,
        personId: state.personId,
        taskId: state.taskId,
        scope: state.scope,
        selectedPersonId: state.selectedPersonId,
        selectedTaskId: state.selectedTaskId
      };
      root.openai.setWidgetState({ modelContent: preferences, privateContent: { chatOrg: preferences } }).catch(() => {});
    }

    function rememberConversation() {
      state.drafts.set(scopeKey(), draft.value);
      state.scrollPositions.set(scopeKey(), transcript.scrollTop);
    }

    function context() {
      const data = group();
      const tasks = data.tasks.filter(task => !task.history && (state.scope === "group" || task.id === state.taskId));
      return {
        ...data,
        ...state,
        tasks,
        links: data.links.filter(link => !link.history),
        currentTaskId: state.taskId,
        currentPersonId: state.personId,
        esc,
        avatar,
        statusBadge,
        runtimeName
      };
    }

    function renderNavigation() {
      const data = group();
      element.querySelector('[data-chat-control="leader"]').value = state.leaderId;
      element.querySelector(".cd-nav-items").innerHTML = `
        <button type="button" class="cd-nav-general${state.taskId === null && state.personId === data.leader.id ? " is-current" : ""}" data-chat-action="conversation" data-person="${esc(data.leader.id)}" data-task="">leader 总会话</button>
        <h2>任务</h2>
        ${data.tasks.filter(task => !task.history).map(task => `
          <button type="button" class="cd-nav-task${state.taskId === task.id ? " is-current" : ""}" data-chat-action="conversation" data-person="${esc(data.leader.id)}" data-task="${esc(task.id)}">
            <span>${esc(task.title)}</span>${statusBadge(task.status)}
          </button>`).join("") || '<p class="cd-nav-empty">还没有任务</p>'}`;
    }

    function renderConversation() {
      const data = group();
      const person = data.people.find(item => item.id === state.personId);
      const task = data.tasks.find(item => item.id === state.taskId);
      const link = data.links.find(item => item.personId === person.id && item.taskId === task?.id && !item.history);
      const paused = task?.status === "paused" || task?.status === "pausing";
      const name = task?.title || "总会话";
      element.querySelector(".cd-chat-header").innerHTML = `
        <div class="cd-chat-person">
          ${avatar(person)}
          <div>
            <h1 aria-label="${esc(person.name)}，${esc(name)}">
              <span class="cd-person-name">${esc(person.name)}</span><span class="cd-context-divider" aria-hidden="true">/</span><span class="cd-context-name">${esc(name)}</span>
            </h1>
            <p class="cd-chat-meta">
              <span>${person.role === "leader" ? "leader" : "组员"} · ${esc(runtimeName(link?.runtime || person.runtime))}</span>
              ${task ? statusBadge(task.status) : ""}
            </p>
          </div>
        </div>
        <button type="button" class="cd-open-panel" data-chat-action="toggle-panel" aria-expanded="${state.panelOpen}" aria-controls="${id}-panel-body">本组架构${state.panelOpen ? " −" : " +"}</button>`;
      panel.id = `${id}-panel-body`;
      const stored = state.messages.get(scopeKey()) || [];
      const initialMessages = task
        ? `
          <article class="cd-message cd-user-message">
            <span>你 · 样例</span>
            <p>${esc(task.summary)}</p>
          </article>
          <article class="cd-message">
            <span>${esc(person.name)} · 样例记录</span>
            <p>${esc(link?.executionId ? link.work : "这里还没有该人员的本任务执行记录。")}</p>
            ${link?.executionId ? `
              <div class="cd-message-work">
                ${statusBadge(link.state)}<span>工作记录归属 ${esc(task.code)}</span>
              </div>` : ""}
          </article>`
        : `
          <article class="cd-message cd-user-message">
            <span>你 · 样例</span>
            <p>这组的几个任务都在推进，我想看看成员分别负责什么。</p>
          </article>
          <article class="cd-message">
            <span>${esc(person.name)} · 样例记录</span>
            <p>${person.role === "leader" ? "我会统筹本组任务。每个任务的分工和执行记录都可以从右侧查看。" : "我的任务分工可以从右侧查看，也可以在这里讨论新的安排。"}</p>
          </article>`;
      transcript.innerHTML = `
        <div class="cd-transcript-inner">
          ${initialMessages}
          ${stored.map(text => `
            <article class="cd-message cd-user-message">
              <span>你 · 模拟消息</span><p>${esc(text)}</p>
            </article>`).join("")}
        </div>`;
      transcript.scrollTop = state.scrollPositions.get(scopeKey()) || 0;
      draft.value = state.drafts.get(scopeKey()) || "";
      draft.readOnly = paused;
      element.querySelector(".cd-send").disabled = paused;
      element.querySelector(".cd-composer-label").textContent = `${person.name} / ${name}`;
      element.querySelector(".cd-compose-status").textContent = paused ? "任务已暂停 · 当前可查看记录" : "仅在本演示中保存消息";
      renderNavigation();
    }

    function renderSelection() {
      const target = element.querySelector(".cd-selection");
      const data = group();
      const person = data.people.find(item => item.id === state.selectedPersonId);
      const task = data.tasks.find(item => item.id === state.selectedTaskId);
      if (!person && !task) {
        target.hidden = true;
        target.innerHTML = "";
        return;
      }
      target.hidden = false;
      const link = person && task && data.links.find(item => item.personId === person.id && item.taskId === task.id && !item.history);
      const related = person ? data.links.filter(item => item.personId === person.id && !item.history) : [];
      const canOpenTask = task && (person?.role === "leader" || link);
      target.innerHTML = `
        <div class="cd-selection-head">
          <strong>${person ? esc(person.name) : esc(task.title)}</strong>
          <button type="button" data-chat-action="clear-selection" aria-label="关闭选中分工">×</button>
        </div>
        ${person ? `<p>${esc(task?.title || (person.role === "leader" ? "统筹本组任务" : "当前未分配任务"))}</p>` : `<p>${esc(task.summary)}</p>`}
        ${link ? `
          <p class="cd-selection-work">${esc(link.work)}</p>
          <div>${statusBadge(link.state)} <span class="org-runtime">${esc(runtimeName(link.runtime))}</span></div>` : ""}
        ${person && related.length > 1 ? `
          <div class="cd-related-tasks" aria-label="该成员的其他任务">
            ${related.map(item => {
              const assignmentTask = data.tasks.find(entry => entry.id === item.taskId);
              return `
                <button type="button" data-panel-action="select-person" data-person="${esc(person.id)}" data-task="${esc(item.taskId)}" aria-pressed="${item.taskId === state.selectedTaskId}">
                  ${esc(assignmentTask.code)} · ${esc(assignmentTask.title)}
                </button>`;
            }).join("")}
          </div>` : ""}
        <div class="cd-selection-actions">
          ${canOpenTask || !person ? `<button type="button" data-chat-action="conversation" data-person="${esc(person?.id || data.leader.id)}" data-task="${esc(task.id)}">${person ? "本任务对话" : "leader 任务对话"} ↗</button>` : ""}
          ${person ? `<button type="button" data-chat-action="conversation" data-person="${esc(person.id)}" data-task="">本人总会话 ↗</button>` : ""}
        </div>`;
    }

    function renderPanel() {
      const ctx = context();
      const scrollTop = tree.scrollTop;
      element.querySelector(".cd-panel-subtitle").textContent = `${ctx.leader.name} · ${ctx.people.length - 1} 位成员`;
      element.querySelectorAll('[data-chat-action="panel-scope"]').forEach(button => {
        button.setAttribute("aria-pressed", String(button.dataset.scope === state.scope));
        button.disabled = button.dataset.scope === "task" && state.taskId === null;
      });
      tree.innerHTML = root.CrewChatOrgView(ctx);
      tree.scrollTop = scrollTop;
      renderSelection();
    }

    function syncPanel() {
      if (narrow.matches) {
        mobileDialog.append(panel);
        panel.hidden = false;
        if (state.panelOpen && !mobileDialog.open) mobileDialog.showModal();
        if (!state.panelOpen && mobileDialog.open) mobileDialog.close();
      } else {
        if (mobileDialog.open) mobileDialog.close();
        panelSlot.append(panel);
        panel.hidden = !state.panelOpen;
      }
      panelSlot.hidden = narrow.matches || !state.panelOpen;
      element.classList.toggle("cd-panel-open", !narrow.matches && state.panelOpen);
      element.querySelector(".cd-open-panel")?.setAttribute("aria-expanded", String(state.panelOpen));
    }

    function switchConversation(personId, taskId) {
      rememberConversation();
      state.personId = personId;
      state.taskId = taskId;
      if (taskId) state.expanded.add(taskId);
      else state.scope = "group";
      if (narrow.matches) state.panelOpen = false;
      renderConversation();
      renderPanel();
      syncPanel();
      persist();
    }

    function graphContext() {
      const data = group();
      const people = data.people.map(person => ({ ...person, runtime: runtimeName(person.runtime) }));
      return {
        ...data,
        people,
        leader: people.find(person => person.id === state.leaderId),
        tasks: data.tasks.filter(task => !task.history),
        links: data.links.filter(link => !link.history),
        selectedTask: state.selectedTaskId || state.taskId,
        selectedMember: state.selectedPersonId,
        collapsed: state.mapCollapsed,
        esc,
        avatar,
        statusBadge,
        runtimeName
      };
    }

    element.addEventListener("click", event => {
      const button = event.target.closest("button");
      if (!button || !element.contains(button)) return;
      const panelAction = button.dataset.panelAction;
      const action = button.dataset.chatAction;
      if (panelAction === "toggle-task") {
        const taskId = button.dataset.task;
        state.expanded.has(taskId) ? state.expanded.delete(taskId) : state.expanded.add(taskId);
        renderPanel();
        Array.from(tree.querySelectorAll('[data-panel-action="toggle-task"]')).find(item => item.dataset.task === taskId)?.focus();
      }
      if (panelAction === "select-person") {
        state.selectedPersonId = button.dataset.person;
        state.selectedTaskId = button.dataset.task || null;
        renderPanel();
        persist();
      }
      if (panelAction === "chat" || action === "conversation") switchConversation(button.dataset.person, button.dataset.task || null);
      if (action === "toggle-panel") { state.panelOpen = !state.panelOpen; syncPanel(); }
      if (action === "close-panel") { state.panelOpen = false; syncPanel(); element.querySelector(".cd-open-panel").focus(); }
      if (action === "panel-scope") { state.scope = button.dataset.scope; renderPanel(); persist(); }
      if (action === "clear-selection") { state.selectedPersonId = null; state.selectedTaskId = null; renderPanel(); persist(); }
      if (action === "expand-map") {
        element.querySelector(".cd-full-map").innerHTML = root.CrewOrgViews.graph(graphContext());
        mapDialog.showModal();
      }
      if (action === "close-map") mapDialog.close();
      if (mapDialog.contains(button) && button.dataset.action) {
        const graphAction = button.dataset.action;
        if (graphAction === "general") { switchConversation(button.dataset.id || button.dataset.person, null); mapDialog.close(); }
        if (graphAction === "member" || graphAction === "task") {
          state.selectedPersonId = graphAction === "member" ? button.dataset.person : null;
          state.selectedTaskId = button.dataset.task || null;
          if (state.selectedTaskId) state.expanded.add(state.selectedTaskId);
          state.scope = "group";
          state.panelOpen = true;
          mapDialog.close(); renderPanel(); syncPanel();
          persist();
        }
        if (graphAction === "collapse") {
          const taskId = button.dataset.task;
          state.mapCollapsed.has(taskId) ? state.mapCollapsed.delete(taskId) : state.mapCollapsed.add(taskId);
          element.querySelector(".cd-full-map").innerHTML = root.CrewOrgViews.graph(graphContext());
        }
      }
    });
    element.addEventListener("change", event => {
      if (event.target.dataset.chatControl === "leader") {
        rememberConversation();
        state.leaderId = event.target.value;
        const data = group();
        state.personId = data.leader.id;
        state.taskId = data.tasks.find(task => !task.history)?.id || null;
        state.selectedPersonId = null; state.selectedTaskId = null;
        state.expanded = new Set([state.taskId].filter(Boolean));
        state.scope = "group";
        renderConversation(); renderPanel(); persist();
      }
    });
    draft.addEventListener("input", () => state.drafts.set(scopeKey(), draft.value));
    element.querySelector("form").addEventListener("submit", event => {
      event.preventDefault();
      const text = draft.value.trim();
      if (!text || draft.readOnly) return;
      const messages = state.messages.get(scopeKey()) || [];
      state.messages.set(scopeKey(), [...messages, text]);
      state.drafts.set(scopeKey(), "");
      renderConversation();
      transcript.scrollTop = transcript.scrollHeight;
      element.querySelector(".cd-compose-status").textContent = "模拟消息已加入当前对话";
    });
    mobileDialog.addEventListener("close", () => {
      if (narrow.matches && panel.parentNode === mobileDialog) { state.panelOpen = false; syncPanel(); }
    });
    mobileDialog.addEventListener("click", event => { if (event.target === mobileDialog) mobileDialog.close(); });
    narrow.addEventListener("change", syncPanel);
    root.addEventListener("openai:set_globals", event => {
      const next = event.detail?.globals?.widgetState?.privateContent?.chatOrg;
      if (!next || JSON.stringify(next) === JSON.stringify(snapshot)) return;
      snapshot = next;
      const leader = root.CrewOrgSample.people.find(person => person.session_id === next.leaderId && person.role === "leader");
      if (!leader) return;
      if (state.leaderId === next.leaderId && state.personId === next.personId && state.taskId === next.taskId
          && state.scope === next.scope && state.selectedPersonId === next.selectedPersonId && state.selectedTaskId === next.selectedTaskId) return;
      rememberConversation();
      state.leaderId = next.leaderId;
      const data = group();
      state.personId = data.people.some(person => person.id === next.personId) ? next.personId : leader.session_id;
      state.taskId = data.tasks.some(task => task.id === next.taskId && !task.history) ? next.taskId : null;
      state.scope = next.scope === "task" && state.taskId ? "task" : "group";
      state.selectedPersonId = data.people.some(person => person.id === next.selectedPersonId) ? next.selectedPersonId : null;
      state.selectedTaskId = data.tasks.some(task => task.id === next.selectedTaskId && !task.history) ? next.selectedTaskId : null;
      if (state.taskId) state.expanded.add(state.taskId);
      renderConversation(); renderPanel();
    });
    renderConversation(); renderPanel(); syncPanel();
    if (options.inline && root.Tweak) {
      const design = { panelWidth: 332 };
      const tweak = new Tweak({ container: element, onChange: () => element.style.setProperty("--cd-panel-width", `${design.panelWidth}px`) });
      tweak.addSlider(design, "panelWidth", { label: "右侧面板宽度", min: 300, max: 400, step: 4, unit: "px" });
    }
    return { state };
  }

  root.CrewChatOrgDemo = { mount };
  const standalone = document.getElementById("crew-chat-org-demo");
  if (standalone) mount(standalone);
})(globalThis);
