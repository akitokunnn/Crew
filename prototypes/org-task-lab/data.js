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
      { id: "e-shi-old", goal_id: "org", session_id: "shi", round: 1, requirement_version: 1, generation: 1, runtime: "codex", status: "completed" },
      { id: "e-chat-zhi", goal_id: "chat", session_id: "zhi", round: 1, requirement_version: 1, generation: 1, runtime: "codex", status: "queued" },
      { id: "e-chat-shi", goal_id: "chat", session_id: "shi", round: 1, requirement_version: 1, generation: 1, runtime: "claude", status: "waiting_user" },
      { id: "e-move", goal_id: "move", session_id: "ting", round: 1, requirement_version: 1, generation: 1, runtime: "codex", status: "paused" },
      { id: "e-nav", goal_id: "nav", session_id: "yan", round: 1, requirement_version: 1, generation: 1, runtime: "claude", status: "completed" },
      { id: "e-search", goal_id: "search", session_id: "wu", round: 1, requirement_version: 1, generation: 1, runtime: "claude", status: "running" }
    ]
  };

  // 上面是细节样例（模型测试依赖它们）。下面按同样字段批量补出多 leader、近百项任务的规模样例。
  // 状态码：R 推进中、W 等你答复、A 待验收、P 已暂停、Q 未开始、C 已完成。成员用组内序号表示。
  const groups = [
    { leader: "lin", members: ["yuan", "ting", "shi"], tasks: [
      ["R", "侧栏导航信息层级重排", "01"], ["W", "顶部标题与常驻属性去重", "2", "顶部状态放标题右侧，还是收进详情？"],
      ["R", "工作台待处理排序规则", "0"], ["Q", "全局助手独立页面的返回路径", ""], ["R", "任务清单搜索与筛选", "12"],
      ["A", "交付与文档入口归位到任务内", "1"], ["Q", "音效设置分项文案", ""], ["R", "长中文名称截断与悬停全名", "2"],
      ["Q", "窄屏抽屉交互", "1"], ["P", "键盘快捷键一览", "0"], ["R", "深色模式对比度复核", "1"],
      ["Q", "草稿与阅读位置保留回归", "1"], ["C", "拍板卡片样式统一", "2"], ["Q", "空状态文案", ""],
      ["W", "通知文案精简", "2", "任务交付通知要不要带验收入口？"], ["Q", "设置页结构整理", ""], ["C", "图标线宽统一", "2"],
      ["Q", "加载骨架屏", ""], ["R", "错误提示给出可操作的下一步", "0"]] },
    { leader: "jian", members: ["wu", "xu"], tasks: [
      ["R", "引用来源去重", "0"], ["Q", "资料摘要长度上限", ""], ["A", "检索结果排序口径", "0"],
      ["Q", "网页快照存档", "1"], ["R", "PDF 表格抽取", "0"], ["Q", "资料过期提醒", ""], ["C", "检索日志脱敏", "0"]] },
    { leader: "wendi", members: ["ruo", "qiwu", "nanx", "zhaolu"], tasks: [
      ["W", "Windows 安装包签名与 SmartScreen 信誉", "02", "签名证书用公司 EV 证书还是个人证书？"], ["R", "自动更新的断点续传", "0"],
      ["R", "托盘图标在高 DPI 下发虚", "1"], ["A", "多窗口位置与大小恢复", "13"], ["R", "退出时后台服务的优雅关闭", "1"],
      ["Q", "便携版数据目录迁移", ""], ["R", "安装路径含中文与空格", "02"], ["Q", "卸载残留清理", "0"],
      ["R", "崩溃日志收集与脱敏", "2"], ["W", "启动白屏的诊断提示", "3", "白屏超过 8 秒就提示重启服务，可以吗？"],
      ["Q", "深色模式跟随系统切换", ""], ["R", "窗口最小尺寸与短屏适配", "3"], ["Q", "快捷键冲突检测", ""],
      ["P", "通知中心与免打扰", "1"], ["Q", "字体回退与生僻字显示", ""], ["R", "文件拖放到对话", "13"],
      ["Q", "剪贴板图片粘贴", ""], ["C", "系统代理与证书", "0"], ["Q", "离线启动与重连提示", ""],
      ["R", "内置浏览器面板权限", "1"], ["Q", "多显示器 DPI 切换", ""], ["C", "应用菜单中文化", "3"],
      ["A", "首次启动引导精简", "3"], ["Q", "资源占用面板", ""], ["R", "安装包体积压缩", "2"],
      ["Q", "旧版本数据导入", ""], ["C", "日志轮转与上限", "2"], ["Q", "ARM64 构建", ""],
      ["R", "打包流水线缓存", "2"], ["Q", "发布说明自动生成", ""]] },
    { leader: "qinglan", members: ["shutong", "baiyu", "linxi"], tasks: [
      ["R", "会话恢复与断线续跑", "1"], ["W", "执行代次与轮次一致性", "12", "旧轮次的执行要不要允许继续查看日志？"],
      ["R", "任务暂停的真实停止确认", "1"], ["Q", "上下文压缩的触发阈值", ""], ["A", "用量统计按执行归属", "2"],
      ["R", "多工作区索引重建", "0"], ["Q", "数据库迁移回滚演练", "0"], ["C", "拍板记录的来源追溯", "2"],
      ["R", "消息去重与幂等写入", "02"], ["Q", "后台服务端口冲突", ""], ["R", "白板快照增量推送", "2"],
      ["P", "长任务心跳与超时", "1"], ["Q", "原生审批弹窗回传", ""], ["C", "错误原因原样上抛", "1"],
      ["R", "并发派工的锁粒度", "0"], ["Q", "历史记录分页加载", ""], ["Q", "存储目录权限检查", ""],
      ["R", "定时任务与唤醒节流", "1"], ["Q", "附件存储去重", ""], ["R", "快照接口字段瘦身", "2"],
      ["Q", "运行时切换不丢上下文", ""], ["C", "日志结构化字段", "0"]] },
    { leader: "carrier", members: ["henian", "mingke", "yuandai", "shiguang"], tasks: [
      ["R", "CC 子代理结果回传", "0"], ["W", "Codex effort 档位映射", "1", "高档位默认给验收任务，还是只给施工任务？"],
      ["R", "模型清单原生发现", "1"], ["Q", "两种载体的权限提示统一", ""], ["R", "长时间运行 8 小时稳定性", "3"],
      ["A", "载体崩溃后的自动接续", "03"], ["R", "工具调用失败原因展示", "0"], ["Q", "载体切换时的草稿保留", ""],
      ["Q", "原生斜杠命令透传", ""], ["R", "回归用例双载体矩阵", "2"], ["Q", "计费与用量口径对齐", ""],
      ["P", "沙箱路径差异处理", "1"], ["Q", "中文路径兼容", ""], ["R", "超长输出截断策略", "02"]] }
  ];
  const STATUS = { R: "running", W: "waiting_user", A: "awaiting_acceptance", P: "paused", Q: "queued", C: "completed" };
  sample.people.push(
    { session_id: "wendi", name: "闻笛", role: "leader", runtime: "claude", summary: "桌面端、安装与打包", tint: "blue" },
    { session_id: "qinglan", name: "青岚", role: "leader", runtime: "codex", summary: "后端服务与运行时", tint: "olive" },
    { session_id: "carrier", name: "多载体适配与回归验证（CC / Codex 原生能力与长时间稳定性）", role: "leader", runtime: "codex", summary: "两种 AI 载体的原生能力适配、长时间运行与回归", tint: "rose" },
    ...[
      ["ruo", "若川", "wendi", "codex", "安装与更新", "sage"], ["qiwu", "栖梧", "wendi", "claude", "窗口与系统集成", "sand"],
      ["nanx", "南星", "wendi", "codex", "打包流水线", "stone"], ["zhaolu", "朝露", "wendi", "claude", "体验细节", "rose"],
      ["shutong", "疏桐", "qinglan", "codex", "存储与迁移", "sand"], ["baiyu", "白榆", "qinglan", "claude", "执行与恢复", "blue"],
      ["linxi", "临溪", "qinglan", "codex", "接口与快照", "sage"], ["henian", "鹤年", "carrier", "claude", "子代理与工具", "olive"],
      ["mingke", "鸣珂", "carrier", "codex", "原生能力", "blue"], ["yuandai", "远黛", "carrier", "codex", "回归矩阵", "stone"],
      ["shiguang", "拾光", "carrier", "claude", "长时间稳定性", "sand"]
    ].map(([session_id, name, leader, runtime, summary, tint]) => ({ session_id, name, role: "member", leader_session_id: leader, runtime, summary, tint }))
  );
  const personById = id => sample.people.find(person => person.session_id === id);
  let code = 31;
  let tick = 0;
  groups.forEach(group => group.tasks.forEach(([letter, title, picks, ask], index) => {
    const id = `${group.leader}-${index + 1}`;
    const status = STATUS[letter];
    const members = [...picks].map(digit => group.members[Number(digit)]);
    tick += 1;
    sample.goals.push({
      id, code: `T-${String(code++).padStart(3, "0")}`, leader_session_id: group.leader, title, status,
      summary: ask || (status === "queued" ? "要求已记录，还没开始。" : `${title}，按任务要求推进。`),
      ask: ask || (status === "awaiting_acceptance" ? "交付已完成，等你验收" : null),
      updated: (tick * 37) % 540 + 3, current_round: 1, requirement_version: 1,
      assignments: [{ session_id: group.leader, task: title, status: "assigned" },
        ...members.map(member => ({ session_id: member, task: personById(member).summary, status: "assigned" }))]
    });
    if (status === "queued") return;
    members.forEach((member, order) => sample.executions.push({
      id: `e-${id}-${member}`, goal_id: id, session_id: member, round: 1, requirement_version: 1, generation: 1,
      runtime: personById(member).runtime,
      status: { running: order ? "idle" : "running", waiting_user: order ? "idle" : "waiting_user", awaiting_acceptance: "completed", paused: "paused", completed: "completed" }[status]
    }));
  }));
  // 细节样例补上待你事项与最近更新时间。
  Object.assign(sample.goals.find(goal => goal.id === "chat"), { ask: "任务切换时的上下文提示，用方案 A 还是 B？", updated: 35 });
  [["org", 4], ["move", 260], ["route", 90], ["nav", 1440], ["search", 12]].forEach(([id, updated]) => { sample.goals.find(goal => goal.id === id).updated = updated; });
  // 总会话里的拍板不属于任何任务，同样进入工作台。
  sample.generalAsks = [{ leader_session_id: "carrier", ask: "长时间稳定性测试要不要占用夜间的两台机器？", updated: 50 }];

  root.CrewOrgSample = sample;
  if (typeof module !== "undefined") module.exports = sample;
})(globalThis);
