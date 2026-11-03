(function () {
  "use strict";

  globalThis.CrewOrgViews = globalThis.CrewOrgViews || {};

  globalThis.CrewOrgViews.graph = function graphView(ctx) {
    const { leader, people, tasks, links, esc, avatar, statusBadge } = ctx;
    const sameId = (a, b) => String(a) === String(b);
    const selectedPerson = people.find(person => sameId(person.id, ctx.selectedMember));
    const personLinks = person => links.filter(link => sameId(link.personId, person.id));
    const currentTaskIds = person => new Set(personLinks(person)
      .filter(link => !link.history)
      .map(link => String(link.taskId)));

    function personNode(link) {
      const person = people.find(item => sameId(item.id, link.personId));
      if (!person) return "";
      const shared = currentTaskIds(person).size;
      const selected = sameId(ctx.selectedMember, person.id);
      return `<button type="button" class="grf-person${selected ? " grf-person-selected" : ""}${link.history ? " grf-person-history" : ""}"
          data-action="member" data-person="${esc(person.id)}" data-task="${esc(link.taskId)}"
          aria-pressed="${selected}">
        ${avatar(person)}
        <span class="grf-person-content">
          <span class="grf-person-heading"><span class="grf-name">${esc(person.name)}</span>
            <span class="org-runtime">${esc(person.runtime)}</span></span>
          <span class="grf-work">${esc(link.work || "工作内容待补充")}</span>
          <span class="grf-person-meta">${statusBadge(link.state)}
            ${sameId(person.id, leader.id) ? '<span class="grf-caption">leader 本人</span>' : ""}
            ${link.history ? '<span class="grf-history-label">历史参与</span>' : shared > 1 ? `<span class="grf-shared">关联 ${shared} 项任务</span>` : ""}
          </span>
        </span>
      </button>`;
    }

    function taskBranch(task) {
      const taskLinks = links.filter(link => sameId(link.taskId, task.id));
      const collapsed = ctx.collapsed.has(String(task.id));
      const selected = sameId(ctx.selectedTask, task.id);
      const connected = selectedPerson && taskLinks.some(link => sameId(link.personId, selectedPerson.id));
      const peopleCount = new Set(taskLinks.map(link => String(link.personId))).size;
      return `<section class="grf-branch${selected ? " grf-branch-selected" : ""}${connected ? " grf-branch-connected" : ""}" aria-label="${esc(task.title)}">
        <div class="grf-task-node">
          <button type="button" class="grf-task-open" data-action="task" data-task="${esc(task.id)}" aria-pressed="${selected}">
            <span class="grf-task-meta"><span class="grf-code">${esc(task.code)}</span>${statusBadge(task.status)}</span>
            <span class="grf-task-title">${esc(task.title)}</span>
            <span class="grf-task-summary">${esc(task.summary || "任务要求待补充")}</span>
          </button>
          <button type="button" class="grf-collapse" data-action="collapse" data-task="${esc(task.id)}"
              aria-expanded="${!collapsed}" aria-label="${collapsed ? "展开" : "收起"}${esc(task.title)}的参与人员">
            <span>${peopleCount} 位参与人员</span><span aria-hidden="true">${collapsed ? "+" : "−"}</span>
          </button>
        </div>
        ${collapsed ? "" : `<div class="grf-branch-people">${taskLinks.length
          ? taskLinks.map(personNode).join("")
          : '<p class="grf-empty-branch">尚未分配成员</p>'}</div>`}
      </section>`;
    }

    const members = people.filter(person => !sameId(person.id, leader.id));
    const unassigned = members.filter(person => personLinks(person).length === 0);
    const historyOnly = members.filter(person => {
      const assignments = personLinks(person);
      return assignments.length > 0 && assignments.every(link => link.history);
    });

    function reserveGroup(title, membersInGroup, className) {
      if (!membersInGroup.length) return "";
      return `<section class="grf-reserve ${className}"><h3>${title}</h3><div class="grf-reserve-people">${membersInGroup.map(person =>
        `<button type="button" class="grf-reserve-person" data-action="member" data-person="${esc(person.id)}">
          ${avatar(person)}<span>${esc(person.name)}</span><span class="org-runtime">${esc(person.runtime)}</span>
        </button>`).join("")}</div></section>`;
    }

    return `<div class="grf-view">
      <div class="grf-leader-row"><button type="button" class="grf-leader" data-action="general" data-person="${esc(leader.id)}">
        ${avatar(leader)}<span class="grf-leader-content"><span class="grf-leader-name">${esc(leader.name)}<span class="grf-leader-role">leader</span></span>
        <span class="grf-leader-summary"><span class="org-runtime">${esc(leader.runtime)}</span> · ${esc(leader.summary || leader.role || "协调本组任务与交付")}</span></span>
        <span class="grf-general-label">总会话 ↗</span>
      </button></div>
      ${selectedPerson ? `<div class="grf-focus" role="status"><span class="grf-focus-marker" aria-hidden="true"></span><span>已高亮 ${esc(selectedPerson.name)} 的任务关系</span></div>` : ""}
      ${tasks.length ? `<div class="grf-stem" aria-hidden="true"></div><div class="grf-viewport" tabindex="0" role="region" aria-label="任务与人员关系图，可横向滚动"><div class="grf-branches">${tasks.map(taskBranch).join("")}</div></div>`
        : '<div class="grf-no-tasks"><h3>当前范围内没有任务</h3><p>可以继续查看 leader 总会话。</p></div>'}
      ${reserveGroup("尚未分配任务", unassigned, "grf-unassigned")}
      ${reserveGroup("仅有历史参与", historyOnly, "grf-historical")}
    </div>`;
  };
}());
