"use strict";
// Department compaction and current-workspace cleanup dialog. The backend owns
// every result; this module only previews, submits once and shows stored state.
(() => {
  const ITEM = {pending:"未开始", running:"处理中", completed:"已完成", failed:"失败", interrupted:"已中断"};
  const JOB = {running:"后台进行中", completed:"全部完成", partial:"部分完成", failed:"没有完成", interrupted:"已中断"};
  const KIND = {busy:"正在工作", external:"外部运行", identity_in_use:"正在处理其他工作", ownership_unknown:"运行状态未知",
    no_history:"没有原生历史", no_web_record:"缺少启动记录", launch_failed:"恢复失败", native_error:"原生压缩失败",
    process_exited:"进程退出", unconfirmed:"未收到完成事件", timeout:"超时", stop_failed:"停止未确认", interrupted:"中断",
    update_paused:"更新暂停", scope_changed:"范围已变化", preview_changed:"预览已变化", blocked:"没有执行", not_confirmed:"未确认取消"};
  const RUNTIME = {claude:"Claude", codex:"Codex"};
  const GOAL = {queued:"待开始", running:"进行中", pausing:"暂停中", paused:"已暂停", waiting_user:"等你回复",
    awaiting_acceptance:"待你体验", completed:"已完成", failed:"未完成", cancelled:"已取消"};
  const POLL_MS = 1200;
  const size = value => value === null || value === undefined ? "未知" : value >= 10000 ? `${(value / 10000).toFixed(1)} 万` : String(value);
  const when = value => { const date = new Date(value); return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", {hour12:false}); };
  const reason = item => item.error_type ? `${KIND[item.error_type] || item.error_type}：${item.error || ""}` : item.error || "";

  function el(tag, props = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "text") node.textContent = value;
      else if (key === "class") node.className = value;
      else if (key === "data") for (const [name, data] of Object.entries(value)) node.dataset[name] = data;
      else if (["disabled", "checked", "type", "value"].includes(key)) node[key] = value;
      else node.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of [].concat(children)) {
      if (child === null || child === undefined || child === false) continue;
      node.append(typeof child === "string" ? document.createTextNode(child) : child);
    }
    return node;
  }
  const button = (action, text, extra = {}) => el("button", {type:"button", data:{maintenanceAction:action, focusKey:extra.key || action}, class:extra.class, disabled:extra.disabled, "aria-describedby":extra.describedby}, text);

  function create(options) {
    const host = options.host || document.body;
    const dialog = el("dialog", {class:"maintenance-dialog", "aria-labelledby":"maintenance-title", "aria-describedby":"maintenance-scope"});
    host.append(dialog);
    let mode = null, project = null, label = null, leader = null, view = null, preview = null, job = null, history = [], cleanups = 0;
    let archive = null, back = null, error = null, note = "", noteFailed = false, submitting = false, generation = 0, timer = null, returnTo = null;
    const selected = new Set(), cleaned = new Set();
    let renderedView = null;

    function setNote(text, failed = false) { note = text; noteFailed = failed; render(); }
    function stopPolling() { clearTimeout(timer); timer = null; }
    async function api(path, body) { return options.api(path, body); }
    const query = values => new URLSearchParams(values).toString();

    function title() {
      if (view === "archive") return archive.kind === "channel" ? "清理前的交流（只读）" : "清理前的交接（只读）";
      return mode === "department" ? `压缩整个部门「${leader.name}」` : `清理当前工作区「${label || project}」`;
    }
    function scope() {
      if (mode === "department") return "整理这个 leader 和成员的对话与任务记忆，保留现有团队。";
      return "范围：仅当前工作区。其他工作区、全局规则和账号设置不受影响。";
    }

    function itemRow(item) {
      const detail = item.detail || {};
      const facts = [];
      if (detail.pre_tokens !== undefined || detail.post_tokens !== undefined) facts.push(`压缩前 ${size(detail.pre_tokens)} → 压缩后 ${size(detail.post_tokens)}`);
      if (detail.process === "temporary") facts.push("压缩完成，会话已停止");
      if (detail.process === "existing") facts.push("压缩完成，保留在线状态");
      return el("li", {class:`mt-item is-${item.status}`, data:{itemStatus:item.status}}, [
        el("span", {class:"mt-item-label", text:item.label}),
        el("span", {class:"mt-badge", text:mode === "department" && item.status === "completed" ? "已压缩" : ITEM[item.status] || item.status}),
        item.status === "failed" || item.status === "interrupted" ? el("p", {class:"mt-reason", text:reason(item)}) : null,
        facts.length ? el("p", {class:"mt-facts", text:facts.join("；")}) : null]);
    }

    function jobView() {
      const counts = job.counts || {};
      const outcome = job.status === "completed" ? (mode === "department" ? "所选项已完成" : "清理完成") : JOB[job.status] || job.status;
      const summary = `${outcome} · 完成 ${counts.completed || 0} / 失败 ${(counts.failed || 0) + (counts.interrupted || 0)} / 处理中 ${counts.running || 0} / 未开始 ${counts.pending || 0}`;
      const parts = [el("p", {class:"mt-summary", role:"status", text:summary})];
      if (job.status === "running") parts.push(el("p", {class:"mt-hint", text:"在后台执行。可以关闭窗口，之后重新打开查看结果。"}));
      if (job.error) parts.push(el("p", {class:"mt-error", role:"alert", text:reason(job)}));
      if (mode === "workspace" && job.status === "completed") parts.push(el("p", {class:"mt-success", text:"已归档旧工作。新的 leader 不会收到旧交流、旧交接或旧上下文，可以重新开始。"}));
      if (mode === "workspace" && job.status !== "running" && job.status !== "completed") parts.push(el("p", {class:"mt-hint", text:"清理没有完成：已经停止或取消的部分保持现状，没有建立清理边界。处理失败原因后请重新预览。"}));
      parts.push(el("ol", {class:"mt-items"}, (job.items || []).map(itemRow)));
      if (mode === "workspace" && job.detail?.archived_groups?.length) parts.push(el("details", {class:"mt-section"}, [
        el("summary", {text:"查看归档的部门备注"}), ...job.detail.archived_groups.map(group => el("section", {}, [
          el("h3", {text:group.name}), el("pre", {class:"mt-text", text:group.note})]))]));
      if (mode === "workspace" && job.detail?.boundary) parts.push(archiveButtons(job));
      return parts;
    }

    function archiveButtons(entry) {
      return el("div", {class:"mt-archive-actions"}, [
        button("archive-channel", "查看清理前的交流", {key:`archive-channel-${entry.id}`}),
        button("archive-handoffs", "查看清理前的交接", {key:`archive-handoffs-${entry.id}`})]);
    }

    function departmentPreview() {
      const parts = [el("p", {class:"mt-intro", text:"选择要压缩的记忆，压缩后保留摘要。聊天记录、文件和交付都会保留；正在工作的会话稍后再处理。"})];
      if (preview.total === 0) parts.push(el("p", {class:"mt-empty", text:"部门里还没有使用过的上下文，没有需要压缩的内容。"}));
      for (const person of preview.people) {
        if (!person.contexts.length) continue;
        parts.push(el("fieldset", {class:"mt-person"}, [
          el("legend", {text:`${person.name}（${person.role === "leader" ? "leader" : "成员"}）`}),
          ...person.contexts.map(context => {
            const id = `maintenance-context-${context.target}`;
            return el("div", {class:`mt-context ${context.available ? "" : "is-unavailable"}`}, [
              el("input", {type:"checkbox", id, checked:selected.has(context.target), disabled:!context.available || submitting,
                data:{maintenanceTarget:context.target, focusKey:id}, "aria-describedby":`${id}-state`}),
              el("label", {for:id}, [el("span", {text:context.context}), el("span", {class:"mt-chip", text:RUNTIME[context.runtime] || context.runtime}),
                context.goal_status ? el("span", {class:"mt-chip", text:GOAL[context.goal_status] || context.goal_status}) : null]),
              el("p", {id:`${id}-state`, class:context.available ? "mt-state" : "mt-reason", text:context.available ? context.reason : `${KIND[context.kind] || context.kind}：${context.reason}`})]);
          })]));
      }
      if (history.length) parts.push(el("section", {class:"mt-history"}, [el("h3", {text:"上次结果"}),
        el("p", {text:`${when(history[0].created_at)} · ${JOB[history[0].status] || history[0].status} · 完成 ${history[0].counts.completed} / 共 ${history[0].items.length}`}),
        button("show-job", "查看上次结果", {key:"show-last-job"})]));
      return parts;
    }

    function list(titleText, entries) {
      return el("section", {class:"mt-section"}, [el("h3", {text:titleText}), el("ul", {}, entries.filter(Boolean).map(text => el("li", {text})))]);
    }
    function cleanupPreview() {
      const p = preview, parts = [];
      parts.push(el("p", {class:"mt-intro", text:"归档旧部门和工作内容，取消未结束的任务，重新开始。项目文件、交付和历史记录保留。"}));
      const warnings = [];
      if (p.empty) warnings.push("当前工作区没有可清理的旧工作。");
      if (p.blocked) warnings.push(p.blocked);
      if (p.running.external) warnings.push(`有 ${p.running.external} 个身份在 VS Code 或终端中运行。Crew 不会强停外部进程：请先在原工具中结束，否则清理会失败，并保留已经停止的部分。`);
      if (p.running.unknown) warnings.push("无法确认外部进程状态：" + p.running.unknown);
      if (warnings.length) parts.push(el("div", {class:"mt-warning", role:"alert"}, warnings.map(text => el("p", {text}))));
      parts.push(list("将归档", [...p.departments.map(item => `部门「${item.name}」：leader 与 ${item.members} 名成员`),
        `共 ${p.identities.total} 名人员：leader ${p.identities.leaders}、成员 ${p.identities.members}、待收编 ${p.identities.pending}`,
        p.identities.git_admin ? "Git 管理员（关闭 Git 管理，不执行 Git 命令、不改动分支）" : null]));
      parts.push(list("将停止", [`Crew 中在线的会话 ${p.running.processes} 个，其中 ${p.running.busy} 个正在工作`,
        ...p.people.filter(item => item.running || item.external).map(item => `${item.name}：${item.external ? "外部运行，需在原工具中停止" : item.busy ? "正在工作" : "运行中，空闲"}`)]));
      parts.push(list("将取消或撤下", [`未完成目标 ${p.goals.unfinished.length} 个` + (p.goals.unfinished.length ? `：${p.goals.unfinished.map(goal => goal.title).join("、")}` : ""),
        `开放待办 ${p.todos} 项、待拍板 ${p.decisions} 项、文件声明 ${p.claims} 项、设备租约 ${p.leases} 项`]));
      parts.push(list("退出新团队的工作记忆", [`交流区发言 ${p.channel.messages} 条、要点卡 ${p.channel.digests} 张`, `交接 ${p.handoffs} 条`, `项目、部门和任务记忆 ${p.context} 条`]));
      parts.push(list("保留", [`项目文件与目录：${p.project.root}`, `交付文档 ${p.kept.documents} 份、目标报告 ${p.kept.reports} 份、已结束目标 ${p.kept.finished_goals} 个`,
        "原始对话与原生历史、归档记录", "账号、连接与模型设置", `全局规则 ${p.kept.crew_rules} 条`]));
      parts.push(historySection());
      return parts;
    }
    function historySection() {
      return el("section", {class:"mt-history"}, [el("h3", {text:`清理记录（已清理 ${cleanups} 次）`}),
        history.length ? el("ol", {class:"mt-history-list"}, history.map(entry => el("li", {}, [
          el("span", {text:`${when(entry.created_at)} · ${JOB[entry.status] || entry.status}`}),
          button("show-job", "查看结果", {key:`show-job-${entry.id}`, class:"mt-link"}),
          entry.detail?.boundary ? archiveButtons(entry) : null]))) : el("p", {class:"mt-hint", text:"还没有清理记录。"})]);
    }

    function archiveView() {
      const parts = [el("p", {class:"mt-hint", text:"只读的历史记录，不会再注入给任何会话。"})];
      if (archive.kind === "channel") {
        if (archive.digest) parts.push(el("section", {class:"mt-section"}, [el("h3", {text:`清理前最后一张要点卡（${when(archive.digest.created_at)}）`}), el("pre", {class:"mt-text", text:archive.digest.body})]));
        parts.push(el("ol", {class:"mt-archive"}, archive.messages.map(message => el("li", {}, [
          el("p", {class:"mt-meta", text:`${when(message.created_at)} · ${message.speaker_name}${message.mentions.length ? " → " + message.mentions.map(item => "@" + item.name).join("、") : ""}`}),
          el("pre", {class:"mt-text", text:message.body})]))));
        if (!archive.messages.length) parts.push(el("p", {class:"mt-empty", text:"这次清理前没有交流发言。"}));
      } else {
        parts.push(el("ol", {class:"mt-archive"}, archive.handoffs.map(item => el("li", {}, [
          el("p", {class:"mt-meta", text:`${when(item.created_at)} · ${item.from_name || item.from_session} → ${item.to_target === "*" ? "所有人" : item.to_target}`}),
          el("pre", {class:"mt-text", text:item.body})]))));
        if (!archive.handoffs.length) parts.push(el("p", {class:"mt-empty", text:"这次清理前没有交接。"}));
      }
      if (archive.has_more) parts.push(button("archive-more", "加载更早的记录"));
      return parts;
    }

    function footer() {
      const actions = [];
      if (view === "archive") actions.push(button("back", "返回"));
      if (view === "error") actions.push(button("retry", "重试"));
      if (view === "preview" && mode === "department") actions.push(button("start", submitting ? "正在提交…" : `压缩所选 ${selected.size} 项`, {class:"mt-primary", disabled:submitting || selected.size === 0}));
      if (view === "preview" && mode === "workspace") actions.push(button("start", submitting ? "正在提交…" : "归档旧工作并重新开始", {class:"mt-danger", disabled:submitting || preview.empty || Boolean(preview.blocked)}));
      if (view === "job" && job.status !== "running") actions.push(button("refresh", "重新预览"));
      actions.unshift(button("close", view === "preview" ? "取消" : "关闭"));
      return actions;
    }

    function render() {
      const scroll = renderedView === view ? (dialog.querySelector(".mt-body")?.scrollTop ?? 0) : 0;
      const inside = dialog.contains(document.activeElement), focused = inside ? document.activeElement.dataset?.focusKey : undefined;
      let body;
      if (view === "loading") body = [el("p", {class:"mt-loading", role:"status", text:"正在读取…"})];
      else if (view === "error") body = [el("p", {class:"mt-error", role:"alert", text:error})];
      else if (view === "job") body = jobView();
      else if (view === "archive") body = archiveView();
      else body = mode === "department" ? departmentPreview() : cleanupPreview();
      dialog.replaceChildren(
        el("header", {class:"mt-header"}, [el("div", {}, [el("h2", {id:"maintenance-title", tabindex:"-1", text:title()}), el("p", {id:"maintenance-scope", text:scope()})]),
          el("button", {type:"button", class:"mt-close", "aria-label":"关闭", data:{maintenanceAction:"close", focusKey:"close-x"}}, "×")]),
        el("div", {class:"mt-body"}, body),
        el("footer", {class:"mt-footer"}, [el("p", {class:`mt-note ${noteFailed ? "is-error" : ""}`, role:noteFailed ? "alert" : "status", text:note}), el("div", {class:"mt-actions"}, footer())]));
      dialog.querySelector(".mt-body").scrollTop = scroll;
      renderedView = view;
      if (!inside) return;
      // Replacing the content drops focus; keep it on the same control, otherwise on the title.
      const target = focused ? dialog.querySelector(`[data-focus-key="${focused}"]`) : null;
      (target && !target.disabled ? target : dialog.querySelector("#maintenance-title")).focus({preventScroll:true});
    }

    async function guarded(task) {
      const requested = generation;
      try { await task(requested); }
      catch (failure) { if (requested === generation) { submitting = false; setNote(failure.message, true); } }
    }

    function poll() {
      stopPolling();
      if (!job || job.status !== "running" || !dialog.open) return;
      const requested = generation;
      timer = setTimeout(() => guarded(async () => {
        const next = await readJob(job.id);
        if (requested !== generation) return;
        job = next; finished(); render(); poll();
      }), POLL_MS);
    }
    async function readJobs() {
      const params = {project, kind:mode === "department" ? "department_compact" : "workspace_cleanup"};
      if (mode === "department") params.leader_session_id = leader.session_id;
      const result = await api(`/api/maintenance/jobs?${query(params)}`);
      history = result.jobs; cleanups = result.cleanups;
      return result.jobs;
    }
    async function readJob(id) {
      const jobs = await readJobs();
      const found = jobs.find(entry => entry.id === id);
      if (!found) throw Error("维护记录已不在最近列表中，请重新打开查看");
      return found;
    }
    function finished() {
      if (mode === "workspace" && job.status === "completed" && !cleaned.has(job.id)) { cleaned.add(job.id); options.onCleaned?.(project); }
    }

    async function load(requested) {
      view = "loading"; preview = null; job = null; archive = null; note = ""; noteFailed = false; render();
      try {
        const jobs = await readJobs();
        if (requested !== generation) return;
        const active = jobs.find(entry => entry.status === "running");
        if (active) { job = active; view = "job"; render(); poll(); return; }
        const next = mode === "department"
          ? await api("/api/maintenance/department/preview", {project, leader_session_id:leader.session_id})
          : await api("/api/maintenance/cleanup/preview", {project});
        if (requested !== generation) return;
        preview = next; selected.clear();
        if (mode === "department") for (const person of next.people) for (const context of person.contexts) if (context.available) selected.add(context.target);
        view = "preview"; render();
      } catch (failure) {
        if (requested !== generation) return;
        error = failure.message; view = "error"; render();
      }
    }

    async function start() {
      if (submitting || view !== "preview") return;
      submitting = true; setNote(mode === "department" ? "正在提交压缩…" : "正在提交清理…");
      await guarded(async requested => {
        const next = mode === "department"
          ? await api("/api/maintenance/department/compact", {project, leader_session_id:leader.session_id, preview_id:preview.preview_id, targets:[...selected]})
          : await api("/api/maintenance/cleanup", {project, preview_id:preview.preview_id, confirm:true});
        if (requested !== generation) return;
        submitting = false; job = next; view = "job"; note = "已提交，后台执行中。"; noteFailed = false;
        finished(); render(); poll();
      });
    }

    async function openArchive(kind, jobId, before = null) {
      await guarded(async requested => {
        const params = {project, job_id:jobId, kind};
        if (before !== null) params.before = before;
        const next = await api(`/api/maintenance/archive?${query(params)}`);
        if (requested !== generation) return;
        if (before !== null && archive) {
          if (kind === "channel") next.messages = [...next.messages, ...archive.messages];
          else next.handoffs = [...next.handoffs, ...archive.handoffs];
          next.digest = archive.digest;
        } else back = {view, job};
        archive = {...next, kind, job_id:jobId}; view = "archive"; note = ""; noteFailed = false; render();
        if (before === null) dialog.querySelector("#maintenance-title").focus();
      });
    }

    function close() {
      stopPolling(); generation += 1; submitting = false;
      if (dialog.open) dialog.close();
      options.onClose?.();
      if (returnTo?.isConnected) returnTo.focus({preventScroll:true});
    }

    dialog.addEventListener("cancel", event => { event.preventDefault(); close(); });
    dialog.addEventListener("change", event => {
      const target = event.target.dataset?.maintenanceTarget;
      if (target === undefined || submitting) return;
      if (event.target.checked) selected.add(target); else selected.delete(target);
      render();
    });
    dialog.addEventListener("click", event => {
      const control = event.target.closest?.("[data-maintenance-action]");
      if (!control || control.disabled) return;
      const action = control.dataset.maintenanceAction, key = control.dataset.focusKey || "";
      if (action === "close") close();
      else if (action === "retry" || action === "refresh") void load(generation);
      else if (action === "start") void start();
      else if (action === "back") { view = back.view; job = back.job; archive = null; render(); dialog.querySelector("#maintenance-title").focus(); }
      else if (action === "show-job") {
        const id = key.startsWith("show-job-") ? key.slice("show-job-".length) : history[0].id;
        job = history.find(entry => entry.id === id); view = "job"; render(); dialog.querySelector("#maintenance-title").focus(); poll();
      }
      else if (action === "archive-channel" || action === "archive-handoffs") void openArchive(action === "archive-channel" ? "channel" : "handoffs", key.split("-").slice(2).join("-"));
      else if (action === "archive-more") {
        const entries = archive.kind === "channel" ? archive.messages : archive.handoffs;
        void openArchive(archive.kind, archive.job_id, entries[0].id);
      }
    });

    function open(nextMode, nextProject, values) {
      if (dialog.open && submitting) return Promise.resolve();
      stopPolling();
      returnTo = dialog.open ? returnTo : document.activeElement;
      generation += 1; mode = nextMode; project = nextProject; ({leader = null, label = null} = values); view = "loading"; history = []; cleanups = 0; back = null; submitting = false;
      if (!dialog.open) dialog.showModal();
      render(); dialog.querySelector("#maintenance-title").focus();
      return load(generation);
    }
    return {
      openDepartment(projectName, item) { return open("department", projectName, {leader:{session_id:item.session_id, name:item.name}}); },
      openCleanup(projectName, displayName) { return open("workspace", projectName, {label:displayName}); },
      close, isOpen:() => dialog.open, element:dialog
    };
  }
  window.CrewMaintenance = {create, ITEM, JOB, KIND};
})();
