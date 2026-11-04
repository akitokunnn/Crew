"use strict";
// Builds the starting messages of a conversation from the fixed sample data.
(() => {
  const L = window.LeaderLabState;
  const D = L.D;

  function general(leaderId) {
    const tasks = L.tasksOf(leaderId);
    const waiting = tasks.filter(L.needsYou).sort((a, b) => b.waitMin - a.waitMin);
    const running = tasks.filter(item => item.status === "running" && !L.needsYou(item)).length;
    const queued = tasks.filter(item => item.status === "queued").length;
    const [ask, answer] = D.generalPrompts[leaderId];
    const overview = {
      role: "leader",
      text: `这一组现在有 ${tasks.length} 项任务：${waiting.length} 项在等你，${running} 项推进中，${queued} 项待规划。每项任务都有自己的对话，这里只商量整体安排。`,
      list: waiting.slice(0, 3).map(item => `${item.title}：${item.decision ? "待你拍板" : D.labels[item.status]}`)
    };
    const out = [overview, {role: "user", text: ask}, {role: "leader", text: answer}];
    if (L.leader(leaderId).generalDecision) out.push({role: "decision", scope: "general"});
    return out;
  }

  function forTask(item, index) {
    if (D.rich[item.id]) return D.rich[item.id].map(msg => ({...msg}));
    const done = D.progress[item.leaderId][index % 3];
    const opener = {role: "user", text: `「${item.title}」先摸清现状，有需要我定的再叫我。`};
    switch (item.status) {
      case "queued": return [];
      case "running":
      case "pausing":
      case "waiting_user": {
        const out = [opener, {role: "leader", text: `${done}。目前的判断是：这项的瓶颈不在模型本身，而在前后环节的衔接。我先把可以确定的部分做完，再把需要取舍的地方单独列出来。`}];
        if (item.status === "pausing") out.push({role: "leader", text: "收到暂停请求，正在把当前这一轮测量收尾，完成后停下。"});
        if (item.decision) out.push({role: "decision", scope: "task"});
        return out;
      }
      case "awaiting_acceptance": return [opener, {role: "leader", text: `${done}。${item.acceptance}`}, {role: "acceptance"}];
      case "paused": return [opener, {role: "leader", text: `${done}。按你的要求先暂停，等上游结论出来再继续。`}];
      case "completed": return [opener, {role: "leader", text: `已完成。${done}，结论和数据都写进了交付记录。`}, {role: "user", text: "收到，验收通过。"}];
      case "failed": return [opener, {role: "leader", text: `这一轮未达标：${done}，但关键指标差 12%，原因写在交付记录里。是否重开由你决定。`}];
      case "cancelled": return [opener, {role: "leader", text: "这项已取消，相关工作并入了其他任务。"}];
      default: throw new Error(`未知任务状态 ${item.status}`);
    }
  }

  window.LeaderLabSeed = key => {
    const {leaderId, taskId} = L.parse(key);
    if (!taskId) return general(leaderId);
    const list = L.tasksOf(leaderId);
    const item = L.task(taskId);
    return forTask(item, list.indexOf(item));
  };
})();
