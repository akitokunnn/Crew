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
        open: false,
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
        `<details class="tw-attention-disclosure" ${cache.open ? "open" : ""}><summary><span>待你处理 <b>${count}</b></span><span>在本任务里沟通与确认</span></summary><div class="tw-attention-content">${nativeCount ? `<section aria-label="原生执行权限">${approvals.chatMarkup(task.leader_session_id, task.id, true)}</section>` : ""}${attention.markup}</div></details>`,
      );
      for (const input of host.querySelectorAll("[data-preserve]"))
        if (
          cache.values[input.dataset.preserve] !== undefined &&
          "value" in input
        )
          input.value = cache.values[input.dataset.preserve];
      for (const detail of host.querySelectorAll("[data-side-decision]"))
        if (cache.expanded.has(detail.dataset.sideDecision)) detail.open = true;
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
      if (event.target.classList.contains("tw-attention-disclosure"))
        cache.open = event.target.open;
      const id = event.target.dataset.sideDecision;
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
      cache.open = true;
      if (decisionId != null) cache.expanded.add(String(decisionId));
      render();
      requestAnimationFrame(() => {
        const target = [...host.querySelectorAll("[data-side-decision]")].find(
          (n) => n.dataset.sideDecision === String(decisionId),
        );
        (target || host.querySelector("summary"))?.scrollIntoView({
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
