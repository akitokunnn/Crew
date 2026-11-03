(function (root) {
  "use strict";

  const terminalStatuses = new Set(["completed", "failed", "cancelled"]);
  const executionStates = {
    running: "working", starting: "queued", queued: "queued", waiting_user: "waiting",
    idle: "idle", offline: "offline", pausing: "pausing", paused: "paused",
    awaiting_acceptance: "awaiting_acceptance", completed: "completed", failed: "failed", cancelled: "cancelled"
  };

  function project(sample, leaderId) {
    const persons = sample.people.filter(person => person.session_id === leaderId || person.leader_session_id === leaderId);
    const people = persons.map(person => ({ ...person, id: person.session_id, leaderId: person.leader_session_id }));
    const tasks = sample.goals.filter(goal => goal.leader_session_id === leaderId).map(goal => ({ ...goal, leaderId: goal.leader_session_id, history: terminalStatuses.has(goal.status) }));
    const links = tasks.flatMap(task => task.assignments.map((assignment, index) => {
      const history = assignment.status === "released" || task.history;
      const executions = sample.executions.filter(execution => execution.goal_id === task.id && execution.session_id === assignment.session_id);
      const current = executions.filter(execution => execution.round === task.current_round && execution.requirement_version === task.requirement_version);
      const execution = (history ? executions : current).slice().sort((a, b) => b.round - a.round || b.requirement_version - a.requirement_version || b.generation - a.generation)[0];
      return {
        id: `${task.id}-${assignment.session_id}-${index}`,
        personId: assignment.session_id,
        taskId: task.id,
        work: assignment.task,
        state: execution ? executionStates[execution.status] || "unknown" : "planned",
        runtime: execution?.runtime || people.find(person => person.id === assignment.session_id)?.runtime,
        executionId: execution?.id || null,
        coordinationOnly: assignment.session_id === leaderId && !execution,
        history,
        released: assignment.status === "released"
      };
    })).filter(link => !link.coordinationOnly);
    return { leader: people.find(person => person.id === leaderId), people, tasks, links };
  }

  function conversationKey(personId, taskId = null) {
    return JSON.stringify([personId, taskId]);
  }

  root.CrewOrgModel = { project, conversationKey };
  if (typeof module !== "undefined") module.exports = root.CrewOrgModel;
})(globalThis);
