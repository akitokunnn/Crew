(function () {
  "use strict";

  const views = globalThis.CrewOrgViews ||= {};

  function currentTaskCount(ctx, personId) {
    return new Set(ctx.links.filter(link => link.personId === personId && !link.history).map(link => link.taskId)).size;
  }

  function personName(ctx, person) {
    return `${ctx.avatar(person)}<span class="grp-person-name">${ctx.esc(person.name)}</span><span class="org-runtime">${ctx.esc(person.runtime)}</span>`;
  }

  function linkRow(ctx, link) {
    const person = ctx.people.find(item => item.id === link.personId);
    if (!person) return "";
    const shared = currentTaskCount(ctx, person.id);
    const selected = ctx.selectedMember === person.id;
    return `<button type="button" class="grp-member${selected ? " is-selected" : ""}${link.history ? " is-history" : ""}" data-action="member" data-person="${ctx.esc(person.id)}" data-task="${ctx.esc(link.taskId)}" aria-pressed="${selected}">
      <span class="grp-person-line">${personName(ctx, person)}${person.role === "leader" ? '<span class="grp-role">leader</span>' : ""}${shared > 1 ? `<span class="grp-shared">关联 ${shared} 项任务</span>` : ""}</span>
      <span class="grp-work-line"><span class="grp-work">${ctx.esc(link.work || "工作内容待补充")}</span><span class="grp-link-state">${link.history ? '<span class="grp-history-label">历史</span>' : ""}${ctx.statusBadge(link.state)}</span></span>
    </button>`;
  }

  views.lanes = function (ctx) {
    const idle = ctx.people.filter(person => person.role !== "leader" && !ctx.links.some(link => link.personId === person.id && !link.history));
    const rows = ctx.tasks.map(task => {
      const links = ctx.links.filter(link => link.taskId === task.id);
      const peopleCount = new Set(links.filter(link => !link.history).map(link => link.personId)).size;
      const historyCount = new Set(links.filter(link => link.history).map(link => link.personId)).size;
      const participation = peopleCount ? `${peopleCount} 人参与${historyCount ? " · 含历史记录" : ""}` : historyCount ? `${historyCount} 人历史参与` : "0 人参与";
      const folded = ctx.collapsed.has(task.id);
      const selected = ctx.selectedTask === task.id;
      const bodyId = `${ctx.idPrefix || "org"}-grp-body-${encodeURIComponent(task.id)}`;
      return `<section class="grp-task${selected ? " is-selected" : ""}" aria-label="${ctx.esc(task.title)}">
        <div class="grp-task-meta">
          <div class="grp-task-topline"><span class="grp-code">${ctx.esc(task.code || task.id)}</span>${ctx.statusBadge(task.status)}<button class="grp-collapse" type="button" data-action="collapse" data-task="${ctx.esc(task.id)}" aria-expanded="${!folded}" aria-controls="${ctx.esc(bodyId)}" aria-label="${folded ? "展开" : "收起"}${ctx.esc(task.title)}">${folded ? "+" : "−"}</button></div>
          <button type="button" class="grp-task-title" data-action="task" data-task="${ctx.esc(task.id)}" aria-pressed="${selected}">${ctx.esc(task.title)}<span aria-hidden="true">↗</span></button>
          <p class="grp-summary">${ctx.esc(task.summary || "任务内容待补充")}</p>
          <div class="grp-task-foot"><span>${ctx.esc(ctx.leader.name)}统筹</span><span>${participation}</span></div>
        </div>
        <div class="grp-task-body" id="${ctx.esc(bodyId)}"${folded ? " hidden" : ""}>
          ${links.length ? links.map(link => linkRow(ctx, link)).join("") : '<div class="grp-task-empty">尚未分配成员<span>任务保留在这里，方便查看后续安排。</span></div>'}
        </div>
        ${folded ? `<div class="grp-folded">已收起成员 · ${participation}</div>` : ""}
      </section>`;
    }).join("");

    return `<div class="grp-view" aria-label="按任务分组的组织架构">
      <div class="grp-columns"><span>任务与负责人</span><span>参与人员与当前工作</span></div>
      ${rows || '<div class="grp-empty">当前没有符合条件的任务。</div>'}
      <section class="grp-standby" aria-label="未分配当前任务的成员"><div class="grp-standby-heading"><h2>组内待命</h2><span>${idle.length} 人</span></div>
        <div class="grp-idle-list">${idle.length ? idle.map(person => `<button type="button" class="grp-idle-person${ctx.selectedMember === person.id ? " is-selected" : ""}" data-action="member" data-person="${ctx.esc(person.id)}" aria-pressed="${ctx.selectedMember === person.id}">${personName(ctx, person)}<span class="grp-idle-label">当前未分配任务</span></button>`).join("") : '<span class="grp-idle-label">成员均已有任务安排。</span>'}</div>
      </section>
    </div>`;
  };

  function matrixCell(ctx, person, task) {
    const links = ctx.links.filter(link => link.personId === person.id && link.taskId === task.id);
    const highlighted = ctx.selectedMember === person.id || ctx.selectedTask === task.id;
    if (!links.length) return `<td class="mtx-cell mtx-unassigned${highlighted ? " is-highlighted" : ""}"><span aria-label="无任务参与记录">—</span></td>`;
    return `<td class="mtx-cell${highlighted ? " is-highlighted" : ""}">${links.map(link => `<button type="button" class="mtx-assignment${link.history ? " is-history" : ""}" data-action="member" data-person="${ctx.esc(person.id)}" data-task="${ctx.esc(task.id)}" aria-label="查看${ctx.esc(person.name)}在${ctx.esc(task.title)}中的工作">${link.history ? '<span class="mtx-history-label">历史参与</span>' : ""}${ctx.statusBadge(link.state)}<span class="mtx-work">${ctx.esc(link.work || "工作内容待补充")}</span></button>`).join("")}</td>`;
  }

  views.matrix = function (ctx) {
    return `<div class="mtx-view" aria-label="成员与任务的参与矩阵">
      <div class="mtx-intro"><span>每一行是一个人，每一列是一个任务。</span><span>— 无参与记录</span></div>
      <div class="mtx-scroll" tabindex="0" role="region" aria-label="可左右滚动的成员任务矩阵">
        <table class="mtx-table">
          <caption class="mtx-caption">${ctx.esc(ctx.leader.name)}组的成员与任务关系，任务状态与成员执行状态分别显示。</caption>
          <thead><tr><th class="mtx-person-heading" scope="col">组内人员<span>${ctx.people.length} 人</span></th>${ctx.tasks.map(task => `<th scope="col" class="mtx-task-heading${ctx.selectedTask === task.id ? " is-selected" : ""}"><span class="mtx-task-code">${ctx.esc(task.code || task.id)}</span><button type="button" class="mtx-task-name" data-action="task" data-task="${ctx.esc(task.id)}" aria-pressed="${ctx.selectedTask === task.id}">${ctx.esc(task.title)}</button>${ctx.statusBadge(task.status)}</th>`).join("")}${ctx.tasks.length ? "" : '<th class="mtx-no-tasks" scope="col">当前没有符合条件的任务</th>'}</tr></thead>
          <tbody>${ctx.people.map(person => {
            const count = currentTaskCount(ctx, person.id);
              return `<tr class="mtx-person-row${ctx.selectedMember === person.id ? " is-selected" : ""}"><th class="mtx-person-cell" scope="row"><button type="button" class="mtx-person-button" data-action="member" data-person="${ctx.esc(person.id)}" aria-pressed="${ctx.selectedMember === person.id}">${ctx.avatar(person)}<span class="mtx-person-info"><span class="mtx-person-name">${ctx.esc(person.name)}${person.role === "leader" ? '<span class="mtx-leader-label">leader</span>' : ""}</span><span class="mtx-person-meta"><span class="org-runtime">${ctx.esc(person.runtime)}</span><span>${count ? `关联 ${count} 项任务` : person.role === "leader" ? "统筹本组任务" : "当前未分配任务"}</span></span></span></button></th>${ctx.tasks.map(task => matrixCell(ctx, person, task)).join("")}${ctx.tasks.length ? "" : '<td class="mtx-cell mtx-unassigned">—</td>'}</tr>`;
          }).join("")}</tbody>
        </table>
      </div>
    </div>`;
  };
})();
