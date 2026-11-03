(function (root) {
  "use strict";

  const sample = {
    people: [
      { session_id: "lin", name: "林序", role: "leader", runtime: "codex", summary: "产品交付与体验", tint: "sage" },
      { session_id: "jian", name: "见川", role: "leader", runtime: "claude", summary: "资料整理与检索", tint: "sand" },
      { session_id: "shou", name: "守望", role: "leader", runtime: "codex", summary: "新建小组", tint: "stone" },
      { session_id: "zhi", name: "知衡", role: "member", leader_session_id: "lin", runtime: "codex", summary: "前端实现", tint: "sage" },
      { session_id: "yuan", name: "远舟", role: "member", leader_session_id: "lin", runtime: "claude", summary: "接口与数据", tint: "sand" },
      { session_id: "ting", name: "听澜", role: "member", leader_session_id: "lin", runtime: "codex", summary: "交互与回归验证", tint: "blue" },
      { session_id: "shi", name: "时雨", role: "member", leader_session_id: "lin", runtime: "claude", summary: "界面与信息设计", tint: "rose" },
      { session_id: "qing", name: "青禾", role: "member", leader_session_id: "lin", runtime: "codex", summary: "工程支持", tint: "olive" },
      { session_id: "yan", name: "砚川", role: "member", leader_session_id: "lin", runtime: "claude", summary: "文案与整理", tint: "stone" },
      { session_id: "wu", name: "雾杉", role: "member", leader_session_id: "jian", runtime: "claude", summary: "材料归档", tint: "sage" },
      { session_id: "xu", name: "序白", role: "member", leader_session_id: "jian", runtime: "codex", summary: "检索验证", tint: "blue" }
    ],
    goals: [
      { id: "org", code: "T-024", leader_session_id: "lin", title: "组织架构与任务归属", status: "running", summary: "让每个组员的分工、任务和执行状态对应起来。", current_round: 2, requirement_version: 2,
        assignments: [
          { session_id: "lin", task: "梳理接入与最终验收", status: "assigned" },
          { session_id: "zhi", task: "组织视图与成员交互", status: "assigned" },
          { session_id: "yuan", task: "核对任务与执行接口", status: "assigned" },
          { session_id: "ting", task: "等待界面后验证跨任务跳转", status: "assigned" },
          { session_id: "shi", task: "初稿信息设计，已交接给知衡", status: "released" }
        ] },
      { id: "chat", code: "T-025", leader_session_id: "lin", title: "对话与任务上下文", status: "waiting_user", summary: "等待你确认任务切换时的上下文提示。", current_round: 1, requirement_version: 1,
        assignments: [
          { session_id: "lin", task: "协调上下文设计与验收", status: "assigned" },
          { session_id: "zhi", task: "确认设计后接入对话顶部", status: "assigned" },
          { session_id: "shi", task: "两版提示方案等待拍板", status: "assigned" }
        ] },
      { id: "move", code: "T-021", leader_session_id: "lin", title: "普通文件夹工作区迁移与历史接续", status: "paused", summary: "用户已暂停，现有工作记录保留。", current_round: 1, requirement_version: 1,
        assignments: [{ session_id: "lin", task: "协调迁移与验收", status: "assigned" }, { session_id: "ting", task: "迁移后历史与草稿回归", status: "assigned" }] },
      { id: "route", code: "T-026", leader_session_id: "lin", title: "消息归属回归", status: "queued", summary: "要求已记录，成员尚未分配。", current_round: 1, requirement_version: 1, assignments: [{ session_id: "lin", task: "协调消息归属回归", status: "assigned" }] },
      { id: "nav", code: "T-018", leader_session_id: "lin", title: "导航文案整理", status: "completed", summary: "已交付，分工与执行记录留作历史。", current_round: 1, requirement_version: 1,
        assignments: [{ session_id: "lin", task: "协调导航文案验收", status: "assigned" }, { session_id: "yan", task: "完成导航文案与使用说明", status: "assigned" }] },
      { id: "search", code: "T-030", leader_session_id: "jian", title: "任务资料检索", status: "running", summary: "整理任务内资料，校验引用来源。", current_round: 1, requirement_version: 1,
        assignments: [{ session_id: "jian", task: "协调资料整理与验证", status: "assigned" }, { session_id: "wu", task: "整理材料来源", status: "assigned" }, { session_id: "xu", task: "等待领取检索验证", status: "assigned" }] }
    ],
    executions: [
      { id: "e-lin", goal_id: "org", session_id: "lin", round: 2, requirement_version: 2, generation: 1, runtime: "codex", status: "running" },
      { id: "e-zhi-old", goal_id: "org", session_id: "zhi", round: 2, requirement_version: 2, generation: 1, runtime: "codex", status: "failed" },
      { id: "e-zhi", goal_id: "org", session_id: "zhi", round: 2, requirement_version: 2, generation: 2, runtime: "codex", status: "running" },
      { id: "e-yuan", goal_id: "org", session_id: "yuan", round: 2, requirement_version: 2, generation: 1, runtime: "claude", status: "running" },
      { id: "e-ting", goal_id: "org", session_id: "ting", round: 2, requirement_version: 2, generation: 1, runtime: "codex", status: "idle" },
      { id: "e-shi-old", goal_id: "org", session_id: "shi", round: 1, requirement_version: 1, generation: 1, runtime: "claude", status: "completed" },
      { id: "e-chat-zhi", goal_id: "chat", session_id: "zhi", round: 1, requirement_version: 1, generation: 1, runtime: "codex", status: "queued" },
      { id: "e-chat-shi", goal_id: "chat", session_id: "shi", round: 1, requirement_version: 1, generation: 1, runtime: "claude", status: "waiting_user" },
      { id: "e-move", goal_id: "move", session_id: "ting", round: 1, requirement_version: 1, generation: 1, runtime: "codex", status: "paused" },
      { id: "e-nav", goal_id: "nav", session_id: "yan", round: 1, requirement_version: 1, generation: 1, runtime: "claude", status: "completed" },
      { id: "e-search", goal_id: "search", session_id: "wu", round: 1, requirement_version: 1, generation: 1, runtime: "claude", status: "running" }
    ]
  };

  root.CrewOrgSample = sample;
  if (typeof module !== "undefined") module.exports = sample;
})(globalThis);
