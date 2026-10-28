"use strict";
// Compose the existing decision, TODO and native permission owners in a task scope.
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
  const saved = new Map();
  const icon = (name) =>
    `<svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><path d="${name === "check" ? "m5 12 4 4L19 6" : name === "right" ? "m9 5 7 7-7 7" : "m6 9 6 6 6-6"}"/></svg>`;
  function create({
    host,
    actions,
    client,
    task,
    state,
    refresh,
    navigate,
    toast,
  }) {
    const key = `${task.primary_project}:${task.id}`,
      cache = saved.get(key) || {
        values: {},
        todos: {},
        expanded: new Set(),
      };
    saved.set(key, cache);
    let disposed = false,
      feature,
      approvals;
    const renderer = window.CrewDOMRenderer.create();
    const request = (url, body) => {
      const [base, query = ""] = url.split("?");
      const p = new URLSearchParams(query);
      p.set("project", task.primary_project);
      return client.request(base + "?" + p, body);
    };
    function snapshot() {
      const value = state();
      return {
        ...value,
        decisions: (value?.decisions || []).filter(
          (d) => String(d.goal_id) === task.id,
        ),
        todos: (value?.todos || [])
          .filter(
            (t) =>
              String(t.goal_id) === task.id ||
              t.task_ids?.includes(task.id) ||
              t.task_links?.some((l) => l.goal_id === task.id),
          )
          .map((t) => ({ ...t, goal_id: task.id })),
      };
    }
    function preserve() {
      for (const input of host.querySelectorAll("[data-preserve]"))
        if ("value" in input)
          cache.values[input.dataset.preserve] = input.value;
      for (const item of snapshot().todos)
        cache.todos[item.id] = feature?.todoState(item.id);
    }
    function decision(item) {
      const title = item.brief?.title || feature.compactDecisionText(item.question, 180);
      const scope = `data-task-decision="${esc(item.id)}"`;
      if (item.presentation_stage === "ready") {
        return `<section class="tw-decision-ready" ${scope}>${feature.decisionCard(item)}
          <div class="tw-decision-actions"><button class="text-link" data-act="decision-discuss" data-id="${esc(item.id)}">回原对话讨论</button></div></section>`;
      }
      const original = String(item.question || "").trim();
      const expanded = cache.expanded.has(String(item.id));
      return `<article class="tw-decision-question" ${scope}><div class="tw-decision-heading"><h3>${window.CrewChat.renderInline(title, item.session_id)}</h3><span>${item.held ? "已挂起" : esc(item.asker || "负责人")}</span></div>
        ${original && original !== title ? `<details class="tw-decision-original" data-task-decision-original="${esc(item.id)}" ${expanded ? "open" : ""}><summary>完整问题</summary><div class="markdown">${window.CrewChat.renderMarkdown(original, item.session_id)}</div></details>` : ""}
        <div class="tw-decision-actions"><button class="btn ghost" data-act="decision-discuss" data-id="${esc(item.id)}">回原对话讨论</button>
        ${item.presentation_stage === "discussion" ? `<button class="btn" data-act="decision-ready" data-id="${esc(item.id)}">聊清楚了，给我选项</button>` : ""}
        ${item.held ? `<button class="text-link" data-act="decision-unhold" data-id="${esc(item.id)}">取消挂起</button>` : ""}</div></article>`;
    }
    function render() {
      if (disposed || !feature || !approvals) return;
      preserve();
      approvals.sync(state());
      const attention = feature.forChat({
        ownerId: task.leader_session_id,
        goalId: task.id,
      });
      const nativeCount = approvals.countForChat(
        task.leader_session_id,
        task.id,
        true,
      );
      const count = attention.totalWithHeld + nativeCount;
      if (!count) {
        renderer.replace(host, "");
        return;
      }
      renderer.replace(
        host,
        `<section class="tw-task-attention" aria-label="本任务待处理事项"><div class="tw-attention-content">
          ${nativeCount ? `<section class="tw-attention-permissions" aria-label="原生执行权限">${approvals.chatMarkup(task.leader_session_id, task.id, true)}</section>` : ""}
          ${attention.pending.map(decision).join("")}
          ${attention.todos.length ? `<section class="tw-attention-todos"><h3 class="attention-section-title">需要你动手 <span>${attention.todos.length}</span></h3>${attention.todos.map(feature.todoCard).join("")}</section>` : ""}
          ${attention.held.length ? `<details class="tw-attention-held"><summary>已挂起 · ${attention.held.length}</summary>${attention.held.map(decision).join("")}</details>` : ""}
        </div></section>`,
      );
      for (const input of host.querySelectorAll("[data-preserve]"))
        if (
          cache.values[input.dataset.preserve] !== undefined &&
          "value" in input
        )
          input.value = cache.values[input.dataset.preserve];
    }
    feature = window.CrewAttention.create({
      actions,
      state: snapshot,
      api: request,
      notify: toast,
      refresh,
      changed: render,
      navigate: (sessionId, goalId) =>
        navigate({
          view: "task",
          project: task.primary_project,
          taskId: goalId || task.id,
          sessionId,
        }),
      rich: {
        inline: (...args) => window.CrewChat.renderInline(...args),
        markdown: (...args) => window.CrewChat.renderMarkdown(...args),
      },
      ui: {
        icon,
        avatar: (name) =>
          `<span class="tw-avatar">${esc(String(name || "?").slice(0, 1))}</span>`,
        priority: () => "",
        date: (value) =>
          value ? new Date(value).toLocaleString("zh-CN") : "时间未记录",
        ago: () => "",
        empty: (text) => `<p>${esc(text)}</p>`,
      },
    });
    approvals = window.CrewNativeApprovals.create({
      api: request,
      toast,
      refreshState: refresh,
      onChange: render,
    });
    function input() {
      preserve();
    }
    function toggle(event) {
      const id = event.target.dataset.taskDecisionOriginal;
      if (id)
        event.target.open ? cache.expanded.add(id) : cache.expanded.delete(id);
    }
    host.addEventListener("input", input);
    host.addEventListener("toggle", toggle, true);
    // Restore pending TODO notes through the feature's public interface.
    const todoStates = Object.entries(cache.todos);
    for (const [id, value] of todoStates) {
      if (value?.draft) {
        feature.startTodoAction(id, value.draft.status);
        feature.setTodoNote(id, value.draft.note);
      } else if (value?.expanded) feature.toggleTodo(id);
    }
    render();
    function reveal(decisionId) {
      render();
      requestAnimationFrame(() => {
        const target = [...host.querySelectorAll("[data-task-decision]")].find(
          (n) => n.dataset.taskDecision === String(decisionId),
        );
        const held = target?.closest(".tw-attention-held");
        if (held) held.open = true;
        (target || host.querySelector(".tw-task-attention"))?.scrollIntoView({
          block: "nearest",
        });
      });
    }
    return {
      render,
      reveal,
      destroy() {
        if (disposed) return;
        preserve();
        disposed = true;
        feature.destroy();
        approvals.destroy();
        renderer.destroy();
        host.removeEventListener("input", input);
        host.removeEventListener("toggle", toggle, true);
        host.innerHTML = "";
      },
    };
  }
  window.CrewTaskAttention = { create };
})();
