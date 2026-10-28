"use strict";
// Personal records own their drafts, CAS versions and upload/request receipts.
((root, factory) => {
  const value = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = value;
  else root.CrewPersonalWorkspace = value;
})(typeof window === "undefined" ? globalThis : window, () => {
  const copy = (value) =>
    value == null ? value : JSON.parse(JSON.stringify(value));
  const tags = (text) => [
    ...new Set(
      String(text || "")
        .split(/[,，]/)
        .map((value) => value.trim())
        .filter(Boolean),
    ),
  ];
  function create({
    host,
    client,
    actions,
    navigate,
    toast = () => {},
    projects,
    ui = globalThis.CrewWorkbenchUI,
    conversationFactory = globalThis.CrewConversationEntry?.create,
    environment = globalThis,
  }) {
    const { escape: e, requestId, remember, recall } = ui;
    const conversationEntry = conversationFactory({
      client,
      requestId,
      remember,
      recall,
    });
    let rows = [],
      todos = [],
      taskList = [],
      filter = "",
      visibility = recall("personal.visibility", "open"),
      loading = false,
      generation = 0,
      alive = true,
      editing = recall("personal.editing", null),
      chatSelection = null,
      taskError = "";
    const drafts = new Map(),
      pending = new Set(),
      errors = new Map();
    const find = (list, id) =>
      list.find((row) => String(row.id) === String(id));
    const dataKey = (kind, id) => `personal.${kind}.${id}`;
    const read = (kind, id, fallback = null) =>
      drafts.has(dataKey(kind, id))
        ? drafts.get(dataKey(kind, id))
        : recall(dataKey(kind, id), fallback);
    function save(kind, id, value) {
      drafts.set(dataKey(kind, id), copy(value));
      remember(dataKey(kind, id), copy(value));
    }
    const editItem = (row) =>
      read("edit", row.id) || {
        text: row.text,
        project: row.project || "",
        tags: (row.tags || []).join(", "),
        version: row.version,
      };
    const todoDraft = (row) =>
      read("todo", row.id) || {
        note: recall(`todo.note.${row.id}`, ""),
        version: row.version,
        tags: (row.tags || []).join(", "),
        metadata_version: row.metadata_version || 0,
      };
    const projectOptions = (value) =>
      `<option value="">未分配</option>${projects()
        .map(
          (project) =>
            `<option value="${e(project.name)}" ${project.name === value ? "selected" : ""}>${e(project.display_name || project.name)}</option>`,
        )
        .join("")}`;
    const taskIDs = (row) => [
      ...new Set(
        [
          ...(row.task_ids || []),
          ...(row.task_links || []).map(
            (link) => link.goal_id || link.task_id || link.id,
          ),
        ].filter(Boolean),
      ),
    ];
    const activeTasks = () =>
      taskList.filter((task) => !["archived", "trashed"].includes(task.state));
    function links(row) {
      return taskIDs(row)
        .map((id) => {
          const task = find(taskList, id),
            link = row.task_links?.find(
              (item) =>
                String(item.goal_id || item.task_id || item.id) === String(id),
            );
          return `<button class="wb-link" data-act="personal-task" data-id="${e(id)}" data-project="${e(task?.primary_project || link?.project || row.project || row.projects?.[0] || "")}">${e(task?.title || link?.title || "返回关联任务")} ↗</button>`;
        })
        .join(" ");
    }
    function conflictMarkup(row, draft, kind) {
      const current = kind === "todo" ? row.metadata_version || 0 : row.version,
        base = kind === "todo" ? draft.metadata_version : draft.version;
      if (!draft.conflict && current === base) return "";
      return `<div class="wb-inline-error" role="alert"><p>草稿基于 v${e(base)}，服务器当前为 v${e(current)}。你的修改仍保留。</p><button class="wb-link" data-act="personal-retry">读取最新内容</button>${base !== current ? `<details><summary>核对服务器当前内容</summary><p>${e(kind === "todo" ? (row.tags || []).join(", ") : row.text)}</p></details><button class="wb-link" data-act="${kind === "todo" ? "personal-todo-tags-rebase" : "personal-rebase"}" data-id="${e(row.id)}">已核对，以当前版本继续编辑</button>` : ""}</div>`;
    }
    function itemMarkup(row) {
      const draft = editItem(row),
        id = String(row.id),
        archived = row.state === "archived";
      if (String(editing) === id)
        return `<section class="wb-personal-row editing" data-item="${e(id)}"><label>内容<textarea data-edit-field="text">${e(draft.text)}</textarea></label><div class="wb-form-pair"><label>归属<select data-edit-field="project">${projectOptions(draft.project)}</select></label><label>AI 标签（可修改）<input data-edit-field="tags" value="${e(draft.tags)}" placeholder="逗号分隔"></label></div>${conflictMarkup(row, draft, "item")}<p class="wb-inline-error" role="alert">${e(errors.get("edit:" + id) || "")}</p><div class="wb-row-actions"><button class="wb-button primary" data-act="personal-save" data-id="${e(id)}" ${pending.has("edit:" + id) || draft.version !== row.version || draft.conflict ? "disabled" : ""}>保存</button><button class="wb-button" data-act="personal-cancel">收起</button></div><div class="wb-form-pair"><label>关联已有任务<select data-link-target><option value="">选择任务</option>${activeTasks()
          .filter((task) => !taskIDs(row).includes(task.id))
          .map(
            (task) =>
              `<option value="${e(task.id)}">${e(task.title)} · ${e(task.primary_project)}</option>`,
          )
          .join(
            "",
          )}</select></label><button class="wb-button" data-act="personal-link" data-id="${e(id)}">关联任务</button></div>${taskError ? `<p class="wb-inline-error">任务列表读取失败：${e(taskError)}</p>` : ""}${links(row)}</section>`;
      return `<article class="wb-personal-row"><div class="wb-personal-content">${archived ? '<span class="wb-eyebrow">已归档</span>' : ""}<p>${e(row.text)}</p><div class="wb-meta">${e(row.project || "未分配")} ${(row.tags || []).map((tag) => `<span class="wb-tag">${e(tag)}</span>`).join("")}</div>${links(row)}${
        chatSelection === id
          ? `<label class="wb-field">这条记录关联多个任务，选择要继续的对话<select data-chat-target="${e(id)}"><option value="">选择关联任务</option>${taskIDs(
              row,
            )
              .map(
                (taskID) =>
                  `<option value="${e(taskID)}">${e(find(taskList, taskID)?.title || taskID)}</option>`,
              )
              .join(
                "",
              )}</select></label><button class="wb-button primary" data-act="personal-chat-chosen" data-id="${e(id)}">进入所选任务</button>`
          : ""
      }<p class="wb-inline-error" role="alert">${e(errors.get("item:" + id) || "")}</p></div><div class="wb-row-actions"><button class="wb-link" data-act="personal-edit" data-id="${e(id)}">编辑</button>${archived ? `<button class="wb-link" data-act="personal-restore" data-id="${e(id)}">恢复到清单</button>` : `<button class="wb-link" data-act="personal-item-chat" data-id="${e(id)}">开始聊</button><button class="wb-link" data-act="personal-archive" data-id="${e(id)}">归档</button>`}</div></article>`;
    }
    function todoMarkup(row) {
      const done = row.status && !["open", "pending"].includes(row.status),
        id = String(row.id),
        draft = todoDraft(row),
        uploads = read("uploads", id, []);
      return `<article class="wb-personal-row"><div class="wb-personal-content"><span class="wb-eyebrow">${done ? "已处理" : "需要你动手"}</span><h3>${e(row.title)}</h3><p class="wb-muted">${e(Array.isArray(row.steps) ? row.steps.join("\n") : row.steps || row.description || "")}</p>${row.verification ? `<p class="wb-meta">完成依据：${e(Array.isArray(row.verification) ? row.verification.join(" · ") : row.verification)}</p>` : ""}<div class="wb-meta">${e((row.projects || [row.project]).filter(Boolean).join(" · "))} ${(row.tags || []).map((tag) => `<span class="wb-tag">${e(tag)}</span>`).join("")}</div>${links(row)}${done ? completionMarkup(row) : ""}${editing === `todo:${id}` ? `<div class="wb-todo-form" data-todo="${e(id)}"><label>AI 标签（可修改）<input data-todo-tags value="${e(draft.tags)}" placeholder="逗号分隔"></label>${conflictMarkup(row, draft, "todo")}<button class="wb-link" data-act="personal-todo-tags" data-id="${e(id)}" ${draft.metadata_version !== (row.metadata_version || 0) || draft.conflict ? "disabled" : ""}>保存标签</button>${done ? "" : `<label>补充说明<textarea data-todo-note placeholder="说说处理结果…">${e(draft.note)}</textarea></label><label>附上图片或文件<input type="file" data-todo-files multiple></label>${uploads.length ? `<p class="wb-meta">${uploads.map((file) => `${e(file.filename)} · ${file.attachment ? "已上传，完成重试将复用" : "上传结果待核对"}`).join("<br>")}</p>` : ""}<button class="wb-button primary" data-act="personal-complete" data-id="${e(id)}">完成并反馈</button><button class="wb-button" data-act="personal-unable" data-id="${e(id)}">做不了／取消</button>`}<p class="wb-inline-error" role="alert">${e(errors.get("todo:" + id) || "")}</p></div>` : ""}</div><button class="wb-button" data-act="personal-todo" data-id="${e(id)}">${done ? "编辑标签" : "处理"}</button></article>`;
    }
    function completionMarkup(row) {
      return `${row.note ? `<p class="wb-meta wb-completion-note">处理记录：${e(row.note)}</p>` : ""}<div class="wb-evidence">${(row.evidence || []).map((file) => `<a class="wb-link" target="_blank" rel="noopener" href="${e(client.resourceURL(file.url))}">${e(file.filename)} · ${Math.max(1, Math.ceil(file.size_bytes / 1024))} KB</a>`).join("")}</div>`;
    }
    function render() {
      if (!alive) return;
      const oldScroll =
        host.querySelector(".wb-page")?.scrollTop ??
        recall("personal.scroll", 0);
      const shownRows = rows.filter(
        (row) =>
          row.state !== "trashed" &&
          (visibility === "all" ||
            (row.state === "archived") === (visibility === "archived")),
      );
      const shownTodos =
        visibility === "archived"
          ? []
          : todos.filter(
              (row) =>
                visibility === "all" ||
                ["open", "pending", undefined].includes(row.status),
            );
      const content =
        shownTodos.map(todoMarkup).join("") +
        shownRows.map(itemMarkup).join("");
      host.innerHTML = `<div class="wb-page"><div class="wb-page-inner"><header class="wb-page-heading"><p class="wb-eyebrow">随时记，慢慢理</p><h1>TODO 与灵感</h1><p class="wb-muted">一张清单，留下原话和它所属的工作区。</p></header><form class="wb-capture" id="personal-capture"><label class="sr-only" for="personal-text">记录想法或开始讨论</label><textarea id="personal-text" placeholder="有个想法，先放在这里…" rows="2">${e(recall("personal.capture", ""))}</textarea><div class="wb-capture-footer"><label>归属 <select name="project" aria-label="新事项工作区">${projectOptions(recall("personal.project", filter === "__unassigned" ? "" : filter))}</select></label><div><button type="button" class="wb-button" data-act="personal-capture">记下来</button><button type="button" class="wb-button primary" data-act="personal-discuss">开始聊 ↗</button></div></div><p class="wb-inline-error" role="alert">${e(errors.get("capture") || "")}</p></form><div class="wb-section-heading"><h2>全部事项</h2><label class="wb-muted">范围 <select data-personal-visibility aria-label="事项归档范围">${[
        ["open", "进行中"],
        ["archived", "已归档"],
        ["all", "全部（含已完成）"],
      ]
        .map(
          ([value, label]) =>
            `<option value="${value}" ${visibility === value ? "selected" : ""}>${label}</option>`,
        )
        .join(
          "",
        )}</select></label><label class="wb-muted">筛选 <select data-personal-filter aria-label="筛选工作区"><option value="">全部工作区</option><option value="__unassigned" ${filter === "__unassigned" ? "selected" : ""}>未分配</option>${projects()
        .map(
          (project) =>
            `<option value="${e(project.name)}" ${filter === project.name ? "selected" : ""}>${e(project.display_name || project.name)}</option>`,
        )
        .join(
          "",
        )}</select></label></div><div id="personal-list" aria-busy="${loading}">${errors.get("load") ? `<p class="wb-error" role="alert">${e(errors.get("load"))}</p><button class="wb-button" data-act="personal-retry">重新读取</button>` : loading && !rows.length && !todos.length ? '<p class="wb-empty">正在读取清单…</p>' : content || '<p class="wb-empty">这里还没有事项。</p>'}</div><p class="wb-footnote">新增 TODO 由 Crew 按已启用的 QQ／Lark 渠道通知，结果以任务回执为准。</p></div></div>`;
      const page = host.querySelector(".wb-page");
      if (page) {
        page.scrollTop = oldScroll;
        page.onscroll = (event) =>
          remember("personal.scroll", event.target.scrollTop);
      }
    }
    async function refresh() {
      const ticket = ++generation;
      loading = true;
      errors.delete("load");
      render();
      const query = new URLSearchParams({
        include_done: "1",
        include_inactive: "1",
      });
      if (filter === "__unassigned") query.set("unassigned", "1");
      else if (filter) query.set("project", filter);
      const results = await Promise.allSettled([
        client.request(`/api/personal/items?${query}`),
        client.request("/api/tasks?include_inactive=1"),
      ]);
      if (!alive || ticket !== generation) return;
      if (results[0].status === "fulfilled") {
        rows = results[0].value.items || [];
        todos = results[0].value.todos || [];
      } else errors.set("load", results[0].reason.message);
      if (results[1].status === "fulfilled") {
        taskList = results[1].value.tasks || [];
        taskError = "";
      } else taskError = results[1].reason.message;
      loading = false;
      render();
    }
    async function write(key, path, payload) {
      const prior = recall(`personal.attempt.${key}`, null),
        fingerprint = JSON.stringify(payload);
      const attempt =
        prior?.fingerprint === fingerprint
          ? prior
          : { fingerprint, request_id: requestId() };
      remember(`personal.attempt.${key}`, attempt);
      const result = await client.request(path, {
        ...payload,
        request_id: attempt.request_id,
      });
      if (result?.status === "unknown" || result?.accepted === false)
        throw Object.assign(
          Error(result.error || "操作结果尚未确认，重试会核对同一请求。"),
          { receipt: result },
        );
      remember(`personal.attempt.${key}`, null);
      return result;
    }
    async function run(key, operation) {
      if (pending.has(key)) return;
      pending.add(key);
      errors.delete(key);
      render();
      try {
        return await operation();
      } catch (error) {
        if (alive) errors.set(key, error.message);
        throw error;
      } finally {
        pending.delete(key);
        render();
      }
    }
    async function capture({ text, project, discuss = false }) {
      if (!String(text).trim()) throw Error("请填写内容");
      if (discuss && !project)
        throw Error("选一个工作区，再开始任务对话。也可以先记为未分配。");
      remember("personal.capture", text);
      remember("personal.project", project || "");
      return run("capture", async () => {
        const result = discuss
          ? await conversationEntry.start({
              project,
              text,
              key: "personal.conversation",
            })
          : await write("capture", "/api/personal/capture", {
              text,
              project: project || null,
              tags: [],
            });
        if (recall("personal.capture", "") === text)
          remember("personal.capture", "");
        if (alive) {
          if (discuss)
            navigate({
              view: "task",
              taskId: result.task.id,
              project: result.task.primary_project,
            });
          else {
            toast("已记下原话");
            await refresh();
          }
        }
        return result;
      });
    }
    function setItemDraft(id, changes) {
      const row = find(rows, id);
      if (!row) throw Error("记录已不在清单中");
      save("edit", id, { ...editItem(row), ...changes });
    }
    async function saveItem(id) {
      const row = find(rows, id),
        draft = editItem(row);
      if (draft.conflict || draft.version !== row.version)
        throw Error("请先核对服务器内容，并明确选择以当前版本继续编辑。");
      return run("edit:" + id, async () => {
        try {
          const result = await write("edit:" + id, "/api/personal/update", {
            id: row.id,
            version: draft.version,
            text: draft.text,
            project: draft.project || null,
            tags: tags(draft.tags),
          });
          const latest = read("edit", id);
          const changedWhileSaving =
            latest &&
            ["text", "project", "tags"].some(
              (field) => latest[field] !== draft[field],
            );
          if (changedWhileSaving) {
            save("edit", id, {
              ...latest,
              version: result.item.version,
              conflict: false,
            });
          } else {
            save("edit", id, null);
            editing = null;
            remember("personal.editing", null);
          }
          if (alive) await refresh();
          return result;
        } catch (error) {
          if (error.status === 409)
            save("edit", id, {
              ...(read("edit", id) || draft),
              conflict: true,
            });
          throw error;
        }
      });
    }
    function rebaseItem(id) {
      const row = find(rows, id);
      save("edit", id, {
        ...editItem(row),
        version: row.version,
        conflict: false,
      });
      errors.delete("edit:" + id);
      render();
    }
    async function archiveItem(id, state) {
      const row = find(rows, id);
      return run("item:" + id, async () => {
        const result = await write(
          `state:${id}:${state}`,
          "/api/personal/update",
          { id: row.id, version: row.version, state },
        );
        if (alive) await refresh();
        return result;
      });
    }
    async function linkItem(id, taskID) {
      const row = find(rows, id),
        task = find(activeTasks(), taskID);
      if (!task) throw Error("请选择可用的真实任务");
      return run("edit:" + id, async () => {
        const result = await write("link:" + id, "/api/personal/items/link", {
          id: row.id,
          version: row.version,
          task_id: task.id,
        });
        const draft = read("edit", id);
        if (
          draft &&
          result.item &&
          draft.version === row.version &&
          !draft.conflict
        )
          save("edit", id, { ...draft, version: result.item.version });
        if (alive) await refresh();
        return result;
      });
    }
    async function openItemChat(id, taskID) {
      const row = find(rows, id),
        ids = taskIDs(row);
      if (ids.length > 1 && !taskID) {
        chatSelection = String(id);
        render();
        return { selection_required: true };
      }
      if (taskID && !ids.includes(taskID))
        throw Error("请选择此记录已关联的任务");
      if (!row.project && !ids.length)
        throw Error("请先编辑这条记录并选择工作区，或关联已有任务。");
      return run("item:" + id, async () => {
        const result = await conversationEntry.fromItem(
          row,
          `conversation.item.${row.id}`,
          taskID ? { task_id: taskID } : {},
        );
        if (alive)
          navigate({
            view: "task",
            taskId: result.task.id,
            project: result.task.primary_project,
          });
        return result;
      });
    }
    function setTodoDraft(id, changes) {
      const row = find(todos, id);
      if (!row) throw Error("TODO 已不在清单中");
      save("todo", id, { ...todoDraft(row), ...changes });
    }
    async function saveTodoTags(id) {
      const row = find(todos, id),
        draft = todoDraft(row);
      if (
        draft.conflict ||
        draft.metadata_version !== (row.metadata_version || 0)
      )
        throw Error("请先核对服务器标签，并明确选择以当前版本继续编辑。");
      return run("todo:" + id, async () => {
        try {
          const result = await write(
            "tags:" + id,
            "/api/personal/todos/update",
            {
              id: row.id,
              metadata_version: draft.metadata_version,
              tags: tags(draft.tags),
            },
          );
          save("todo", id, {
            ...(read("todo", id) || draft),
            metadata_version: result.todo.metadata_version,
            conflict: false,
          });
          if (alive) await refresh();
          return result;
        } catch (error) {
          if (error.status === 409)
            save("todo", id, {
              ...(read("todo", id) || draft),
              conflict: true,
            });
          throw error;
        }
      });
    }
    async function filePayload(file, taskID) {
      let bytes;
      if (typeof file.arrayBuffer === "function")
        bytes = new Uint8Array(await file.arrayBuffer());
      else
        bytes = await new Promise((resolve, reject) => {
          const reader = new environment.FileReader();
          reader.onload = () => resolve(new Uint8Array(reader.result));
          reader.onerror = () => reject(reader.error);
          reader.readAsArrayBuffer(file);
        });
      const hash = [
        ...new Uint8Array(
          await environment.crypto.subtle.digest("SHA-256", bytes),
        ),
      ]
        .map((value) => value.toString(16).padStart(2, "0"))
        .join("");
      let raw = "";
      for (const value of bytes) raw += String.fromCharCode(value);
      return {
        fingerprint: JSON.stringify([taskID, file.name, file.type, hash]),
        task_id: taskID,
        filename: file.name,
        mime_type: file.type || "application/octet-stream",
        data: environment.btoa(raw),
      };
    }
    async function completeTodo(id, status, { note, files = [] } = {}) {
      const row = find(todos, id),
        draft = todoDraft(row);
      note ??= draft.note;
      if (status === "cancelled" && !note.trim())
        throw Error("请说明做不了或不做的原因，交给负责人寻找替代办法。");
      setTodoDraft(id, { note });
      return run("todo:" + id, async () => {
        const taskID = taskIDs(row)[0],
          uploads = read("uploads", id, []);
        if (files.length && !taskID)
          throw Error("这条 TODO 没有关联任务，无法归属附件；请回原对话补充。");
        for (const file of files) {
          const payload = await filePayload(file, taskID);
          if (
            !uploads.some(
              (upload) => upload.fingerprint === payload.fingerprint,
            )
          ) {
            uploads.push({ ...payload, request_id: requestId() });
            save("uploads", id, uploads);
          }
        }
        for (const upload of uploads)
          if (!upload.attachment) {
            const result = await client.request("/api/tasks/attachments", {
              task_id: upload.task_id,
              filename: upload.filename,
              mime_type: upload.mime_type,
              data: upload.data,
              request_id: upload.request_id,
            });
            if (!result.attachment?.id)
              throw Error("附件是否保存尚未确认，重试会核对同一上传请求。");
            upload.attachment = {
              goal_id: upload.task_id,
              attachment_id: result.attachment.id,
            };
            delete upload.data;
            save("uploads", id, uploads);
          }
        const result = await write(
          "complete:" + id,
          "/api/personal/todos/complete",
          {
            id: row.id,
            version: draft.version,
            note,
            status,
            attachments: uploads.map((upload) => upload.attachment),
          },
        );
        save("todo", id, null);
        save("uploads", id, []);
        remember(`todo.note.${id}`, "");
        editing = null;
        remember("personal.editing", null);
        if (alive) {
          toast(
            status === "done"
              ? "已提交完成反馈，关联任务按各自状态继续"
              : "已记录，回原任务沟通替代办法",
          );
          await refresh();
        }
        return result;
      });
    }
    const input = (event) => {
      const target = event.target,
        item = target.closest?.("[data-item]"),
        todo = target.closest?.("[data-todo]");
      if (target.id === "personal-text")
        remember("personal.capture", target.value);
      else if (item && target.dataset.editField)
        setItemDraft(item.dataset.item, {
          [target.dataset.editField]: target.value,
        });
      else if (todo && target.hasAttribute("data-todo-note"))
        setTodoDraft(todo.dataset.todo, { note: target.value });
      else if (todo && target.hasAttribute("data-todo-tags"))
        setTodoDraft(todo.dataset.todo, { tags: target.value });
    };
    const change = (event) => {
      input(event);
      const target = event.target;
      if (target.name === "project") remember("personal.project", target.value);
      if (target.hasAttribute("data-personal-filter")) {
        filter = target.value;
        void refresh();
      }
      if (target.hasAttribute("data-personal-visibility")) {
        visibility = target.value;
        remember("personal.visibility", visibility);
        render();
      }
    };
    const submit = (event) => {
      if (event.target.id === "personal-capture") event.preventDefault();
    };
    host.addEventListener("input", input);
    host.addEventListener("change", change);
    host.addEventListener("submit", submit);
    const act = (handler) => async (context) => {
      try {
        await handler(context);
      } catch (error) {
        if (alive) {
          toast(error.message);
          render();
        }
      }
    };
    const captureFromForm = (discuss) => {
      const form = host.querySelector("#personal-capture");
      return capture({
        text: form.querySelector("textarea").value,
        project: form.querySelector("select").value,
        discuss,
      });
    };
    const unregister = actions.register("personal-workspace", {
      "personal-retry": () => refresh(),
      "personal-capture": act(() => captureFromForm(false)),
      "personal-discuss": act(() => captureFromForm(true)),
      "personal-edit": ({ element }) => {
        const row = find(rows, element.dataset.id);
        save("edit", row.id, editItem(row));
        editing = String(row.id);
        remember("personal.editing", editing);
        render();
      },
      "personal-cancel": () => {
        editing = null;
        remember("personal.editing", null);
        render();
      },
      "personal-save": act(({ element }) => saveItem(element.dataset.id)),
      "personal-rebase": ({ element }) => rebaseItem(element.dataset.id),
      "personal-archive": act(({ element }) =>
        archiveItem(element.dataset.id, "archived"),
      ),
      "personal-restore": act(({ element }) =>
        archiveItem(element.dataset.id, "open"),
      ),
      "personal-link": act(({ element }) =>
        linkItem(
          element.dataset.id,
          element.closest("[data-item]").querySelector("[data-link-target]")
            .value,
        ),
      ),
      "personal-item-chat": act(({ element }) =>
        openItemChat(element.dataset.id),
      ),
      "personal-chat-chosen": act(({ element }) => {
        const select =
          element.parentElement.querySelector("[data-chat-target]");
        if (!select.value) throw Error("请选择关联任务");
        return openItemChat(element.dataset.id, select.value);
      }),
      "personal-task": ({ element }) =>
        navigate({
          view: "task",
          taskId: element.dataset.id,
          project: element.dataset.project,
        }),
      "personal-todo": ({ element }) => {
        const row = find(todos, element.dataset.id);
        save("todo", row.id, todoDraft(row));
        editing = `todo:${row.id}`;
        remember("personal.editing", editing);
        render();
      },
      "personal-todo-tags": act(({ element }) =>
        saveTodoTags(element.dataset.id),
      ),
      "personal-todo-tags-rebase": ({ element }) => {
        const row = find(todos, element.dataset.id);
        setTodoDraft(row.id, {
          metadata_version: row.metadata_version || 0,
          conflict: false,
        });
        render();
      },
      ...Object.fromEntries(
        [
          ["personal-complete", "done"],
          ["personal-unable", "cancelled"],
        ].map(([action, status]) => [
          action,
          act(({ element }) => {
            const form = element.closest("[data-todo]");
            return completeTodo(element.dataset.id, status, {
              note: form.querySelector("textarea").value,
              files: [...form.querySelector("input[type=file]").files],
            });
          }),
        ]),
      ),
    });
    return {
      open({ project } = {}) {
        filter = project || "";
        return refresh();
      },
      refresh,
      capture,
      setItemDraft,
      saveItem,
      rebaseItem,
      archiveItem,
      linkItem,
      openItemChat,
      setTodoDraft,
      saveTodoTags,
      completeTodo,
      setVisibility(value) {
        if (!["open", "archived", "all"].includes(value))
          throw Error("事项范围无效");
        visibility = value;
        remember("personal.visibility", value);
        render();
      },
      snapshot: () => ({
        rows: copy(rows),
        todos: copy(todos),
        tasks: copy(taskList),
        filter,
        visibility,
        editing,
        chatSelection,
        loading,
        pending: [...pending],
      }),
      destroy() {
        alive = false;
        generation++;
        unregister();
        host.removeEventListener("input", input);
        host.removeEventListener("change", change);
        host.removeEventListener("submit", submit);
      },
    };
  }
  return { create };
});
