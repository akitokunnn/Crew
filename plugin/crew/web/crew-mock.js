"use strict";
// Contract-shaped, explicitly synthetic data. This module never fetches,
// launches a carrier, opens a path or touches Crew's database.
((root, factory) => {
  const exported = factory();
  if (typeof module !== "undefined" && module.exports)
    module.exports = exported;
  else root.CrewMock = exported;
})(typeof window === "undefined" ? globalThis : window, () => {
  const NAMESPACE = "crew.synthetic.frontend.v1";
  const TASK = "bde33c12-fd33-4eac-a8f4-8544359b2f89";
  const LEADER = "84aff42c-e1f7-4239-b0e9-a7aee5c45965";
  const OTHER = "e3e6e54b-036f-428c-ae15-548c90dbd915";
  const MEMBER = "13f44fe1-e3d3-4337-9b72-d76ccf96c617";
  const ASSISTANT = "32d3603a-0283-41b0-a541-fc145e7ac784";
  const clone = (value) =>
    value == null ? value : JSON.parse(JSON.stringify(value));
  const stamp = () => new Date().toISOString();
  const fail = (message, status = 400, details = {}) => {
    const error = new Error(message);
    error.status = status;
    error.details = { error: message, ...details };
    return error;
  };
  const PROMPTS = {
    common:
      "保留用户原意、真实错误和已确认约束。相关验证通过后继续推进，普通交流直接回答。",
    leader:
      "理解需求并维护任务详情。按需启动成员，协调修改和交付，重要取舍按本任务模式处理。",
    member:
      "只处理收到的独立任务。重要补充保留原文并同步 leader，报告真实结果与阻碍。",
    assistant:
      "帮助用户查询进展、整理 TODO 和灵感。明确区分记下来与开始聊，收到明确指令才执行操作。",
    report:
      "第一段说明结果，分节更新实质进展，保留用户编辑、来源和版本，冲突时保留双方内容。",
    context:
      "交接包含有效需求、决定、未完成工作、真实错误及成果来源；成员仅收到相关上下文。",
  };
  function person(id, name, project, owner = null) {
    return {
      session_id: id,
      cc_name: id,
      name,
      project,
      runtime: owner ? "codex" : "claude",
      model: null,
      effort: null,
      applied: null,
      role: owner ? "member" : "leader",
      leader_session_id: owner,
      group: owner ? "声纹负责人" : name,
      task: owner ? "验证延迟与准确度" : null,
      goal: owner ? "验证延迟与准确度" : "推进当前项目",
      status: "idle",
      where: "bg",
      running: false,
      busy: false,
      activity: null,
      context: null,
      codex: [],
      codex_older: 0,
      last_seq: 0,
      last_reply_id: null,
      unread: 0,
      pending: 0,
      priority: null,
      active_goal_id: null,
      active_goal_ids: [],
      member_defaults: null,
      active_execution_id: null,
      runtime_switch: null,
      is_git_admin: false,
      web_chat: true,
      read_seq: 0,
      notices_pending: 0,
      member_ids: [],
      title: null,
      status_note: null,
      scope: null,
      done_when: null,
      branch: null,
      worktree: null,
      cwd: `C:\\Samples\\${project}`,
      progress: [],
      leases: [],
      claims: [],
      trust: { enabled: false, goal: "", updated_at: null },
      synthetic: true,
    };
  }
  function task(id, title, project, leader, state) {
    return {
      id,
      title,
      primary_project: project,
      projects: [project],
      workspace_references: [],
      leader_session_id: leader,
      request: title,
      acceptance: [{ id: "a1", title: "完成相关验证并提供可体验成果" }],
      constraints: ["保留已有修改"],
      mode: "manual",
      authorized: state !== "discussion",
      state,
      execution_status: state === "discussion" ? "queued" : "running",
      review_status: "none",
      lifecycle_pending: null,
      waiting_reason: null,
      version: 1,
      requirement_version: 1,
      current_round: 1,
      pinned: false,
      pinned_at: null,
      created_at: stamp(),
      updated_at: stamp(),
      synthetic: true,
    };
  }
  function seed(empty) {
    const leaders = [
      person(LEADER, "声纹负责人", "voice-demo"),
      person("crew-leader", "界面负责人", "Crew"),
    ];
    const members = [person(MEMBER, "效果验证", "voice-demo", LEADER)];
    const first = task(TASK, "落地声纹识别", "voice-demo", LEADER, "running");
    first.projects.push("evaluation-demo");
    first.pinned = true;
    first.pinned_at = stamp();
    first.request = "比较本地声纹方案，在保持准确度的同时降低响应延迟。";
    first.review_status = "pending";
    const second = task(
      OTHER,
      "让 Crew 的交互更从容",
      "Crew",
      "crew-leader",
      "waiting_user",
    );
    second.waiting_reason = "需要体验任务切换与资料展开的效果";
    return {
      schema: 1,
      synthetic: true,
      tasks: empty ? [] : [first, second],
      projects: empty
        ? []
        : [
            {
              name: "voice-demo",
              display_name: "声纹实验室",
              root: "C:\\Samples\\voice-demo",
              workspace_id: "synthetic-voice",
              theme: "jade",
              synthetic: true,
            },
            {
              name: "Crew",
              display_name: "Crew",
              root: "C:\\Samples\\Crew",
              workspace_id: "synthetic-crew",
              theme: "jade",
              synthetic: true,
            },
            {
              name: "evaluation-demo",
              display_name: "效果评估",
              root: "C:\\Samples\\evaluation-demo",
              workspace_id: "synthetic-evaluation",
              theme: "jade",
              synthetic: true,
            },
          ],
      leaders: empty ? [] : leaders,
      members: empty ? [] : members,
      items: empty
        ? []
        : [
            {
              id: "idea-1",
              kind: "idea",
              text: "把体验反馈放在同一个任务里",
              project: "Crew",
              tags: ["交互"],
              state: "open",
              version: 1,
              task_id: null,
              created_at: stamp(),
              updated_at: stamp(),
              synthetic: true,
            },
          ],
      todos: empty
        ? []
        : [
            {
              id: 1,
              kind: "todo",
              title: "体验声纹识别 v0.3",
              project: "voice-demo",
              projects: ["voice-demo", "evaluation-demo"],
              task_ids: [TASK],
              task_links: [{ goal_id: TASK }],
              session_id: LEADER,
              leader_session_id: LEADER,
              group: "声纹负责人",
              proposer_name: "声纹负责人",
              steps: ["打开样例成果并检查延迟"],
              verification: ["在原任务反馈体验"],
              tags: ["待体验"],
              status: "open",
              version: 1,
              metadata_version: 0,
              evidence: [],
              operation_key: "demo-experience",
              blocking: false,
              created_at: stamp(),
              synthetic: true,
            },
          ],
      reports: empty
        ? {}
        : {
            [TASK]: {
              goal_id: TASK,
              version: 2,
              markdown:
                "# 声纹识别 · 阶段成果\n\n样例方案已完成延迟验证，等待实际体验。这是明确标注的样例内容。\n\n## 当前结果\n\n| 项目 | v0.2 | v0.3 |\n| --- | --- | --- |\n| 首次响应 | 820ms | 410ms |\n| 样例准确率 | 94% | 96% |\n\n## 下一步\n\n请在原任务反馈体验；以上数值仅用于界面演示。",
              updated_at: stamp(),
              synthetic: true,
            },
          },
      pending: {},
      attachments: {},
      versions: empty
        ? {}
        : {
            [TASK]: [
              {
                id: "version-03",
                goal_id: TASK,
                version: 3,
                label: "v0.3 · 延迟优化",
                files: [
                  {
                    path: "README.md",
                    content: "样例 v0.3 内容",
                    delivered_sha256: "synthetic-v03",
                    delivered_size: 21,
                    exists: true,
                  },
                ],
                created_at: stamp(),
                synthetic: true,
              },
              {
                id: "version-02",
                goal_id: TASK,
                version: 2,
                label: "v0.2 · 初步识别",
                files: [
                  {
                    path: "README.md",
                    content: "样例 v0.2 内容",
                    delivered_sha256: "synthetic-v02",
                    delivered_size: 21,
                    exists: true,
                  },
                ],
                created_at: stamp(),
                synthetic: true,
              },
            ],
          },
      journals: {},
      receipts: {},
      scopes: {},
      prompts: {},
      promptHistory: [],
      settings: {
        ai: {
          default: { runtime: "claude", model: null, effort: null },
          leader: null,
          member: { runtime: "codex", model: null, effort: null },
          digest: null,
          git: null,
          operations: null,
        },
        execution: { claude: "default", codex: "workspace-write" },
      },
      assistant: {
        ...person(ASSISTANT, "个人助手", "voice-demo"),
        role: "assistant",
      },
      decisions: empty
        ? []
        : [
            {
              id: 1,
              goal_id: OTHER,
              session_id: "crew-leader",
              leader_session_id: "crew-leader",
              asker: "界面负责人",
              question: "任务切换的动画是否舒适？",
              options: ["保留当前速度", "再轻一点"],
              recommendation: null,
              presentation_stage: "discussion",
              held: false,
              brief: null,
              created_at: stamp(),
              synthetic: true,
            },
          ],
      channel: { messages: [], unread: 0, latest_id: 0 },
      notifications: { away: false, manual_away: false, error: null },
      auth: { authenticated: true, local: true, origin: null },
      remote: {
        version: 0,
        enabled: false,
        origins: [],
        password_configured: false,
        session_seconds: 86400,
        transport: "loopback-reverse-proxy",
        deployed: false,
        synthetic: true,
      },
    };
  }

  function create({
    storage = null,
    scenario = "",
    environment = globalThis,
    replyDelay = 220,
    namespace = NAMESPACE,
  } = {}) {
    if (!["", "conflict", "offline", "unknown", "empty"].includes(scenario))
      throw fail("未知样例场景", 400);
    const key = namespace + (scenario ? ":" + scenario : "");
    let state;
    try {
      const saved = storage?.getItem(key);
      state = saved ? JSON.parse(saved) : seed(scenario === "empty");
    } catch (error) {
      throw fail(`样例存储读取失败：${error.message}`, 500);
    }
    if (state?.schema !== 1 || state.synthetic !== true)
      throw fail("样例存储格式无效，请显式重置样例数据。", 500);
    const listeners = new Set(),
      runs = new Map(),
      timers = new Set();
    const set = environment.setTimeout?.bind(environment) || setTimeout;
    const clear = environment.clearTimeout?.bind(environment) || clearTimeout;
    let closed = false;
    const ResponseType = environment.Response || globalThis.Response;
    const StreamType = environment.ReadableStream || globalThis.ReadableStream;
    const Encoder = environment.TextEncoder || globalThis.TextEncoder;
    const uuid = () =>
      environment.crypto?.randomUUID?.() ||
      `synthetic-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    const persist = () => {
      if (storage) storage.setItem(key, JSON.stringify(state));
    };
    const later = (handler, delay) => {
      const id = set(() => {
        timers.delete(id);
        if (!closed) handler();
      }, delay);
      timers.add(id);
      return id;
    };
    const found = (items, id, name) => {
      const item = items.find(
        (entry) => String(entry.id ?? entry.session_id) === String(id),
      );
      if (!item) throw fail(`${name}不存在`, 404);
      return item;
    };
    const taskBy = (id) => found(state.tasks, id, "任务");
    const checkVersion = (item, version) => {
      if (scenario === "conflict" || item.version !== version)
        throw fail("内容已变化，请保留草稿并重新检查。", 409, {
          current: clone(item),
        });
    };
    const update = (item) => {
      item.version += 1;
      item.updated_at = stamp();
      return item;
    };
    const journalKey = (project, goal, session) =>
      `${project || ""}:${goal || ""}:${session || ""}`;
    function journal(project, goal, session) {
      const identity = journalKey(project, goal, session);
      if (!state.journals[identity]) {
        const events = [],
          execution = "synthetic-execution-" + uuid();
        // Only the fixed fixtures have a pre-existing transcript. Creating a
        // discussion task does not deliver input; chat/send owns its first turn.
        if ([TASK, OTHER].includes(goal) && state.tasks.some((item) => item.id === goal)) {
          events.push({
            type: "message",
            id: "seed-user-" + identity,
            text: taskBy(goal).request,
            seq: 1,
            at: stamp(),
            source: "human",
            synthetic: true,
            execution_id: execution,
          });
          events.push({
            type: "assistant",
            id: "seed-assistant-" + identity,
            content: [
              {
                type: "text",
                text:
                  session === MEMBER
                    ? "样例：我负责验证效果，重要结论会同步给 leader。"
                    : "样例：已经整理当前目标。你可以继续补充、查看成员和成果，所有操作只影响浏览器样例。",
              },
            ],
            seq: 2,
            at: stamp(),
            synthetic: true,
            execution_id: execution,
          });
          events.push({
            type: "turn_result",
            status: "completed",
            seq: 3,
            at: stamp(),
            synthetic: true,
            execution_id: execution,
          });
        }
        state.journals[identity] = {
          project,
          goal_id: goal,
          session_id: session,
          execution_id: execution,
          events,
          busy: false,
          running: false,
          read_seq: 0,
        };
      }
      return state.journals[identity];
    }
    function append(current, event) {
      const value = {
        ...event,
        seq: (current.events.at(-1)?.seq || 0) + 1,
        at: stamp(),
        execution_id: current.execution_id,
        synthetic: true,
      };
      current.events.push(value);
      if (value.type === "status") current.busy = value.status === "working";
      if (value.type === "turn_result") current.busy = false;
      persist();
      for (const listener of listeners)
        if (listener.current === current) listener.deliver(value);
      return value;
    }
    function interrupt(current) {
      runs.set(current.execution_id, (runs.get(current.execution_id) || 0) + 1);
      append(current, { type: "turn_result", status: "interrupted" });
      append(current, { type: "status", status: "idle" });
    }
    function drain(current) {
      const value = state.tasks.find((item) => item.id === current.goal_id);
      if (
        !value?.authorized ||
        !["running", "waiting_user"].includes(value.state) ||
        current.busy
      )
        return;
      const queued = value.queue?.find(
        (item) =>
          item.status === "queued" && item.session_id === current.session_id,
      );
      if (!queued) return;
      queued.status = "accepted";
      queued.accepted_at = stamp();
      persist();
      reply(current, queued.text, queued.id, false);
    }
    function reply(current, text, messageId, important) {
      append(current, {
        type: "message",
        id: messageId,
        text,
        source: "human",
      });
      append(current, {
        type: "input_delivery",
        message_id: messageId,
        status: "accepted",
      });
      append(current, { type: "status", status: "working" });
      const generation = (runs.get(current.execution_id) || 0) + 1;
      runs.set(current.execution_id, generation);
      later(() => {
        if (runs.get(current.execution_id) !== generation) return;
        append(current, {
          type: "assistant",
          id: uuid(),
          content: [
            {
              type: "text",
              text: `样例回复：已收到「${text}」。${important ? "这条补充已保留原文并同步给任务负责人。" : "你可以切换任务或成员，稍后返回继续。"}此处没有调用真实 AI。`,
            },
          ],
        });
        append(current, { type: "turn_result", status: "completed" });
        append(current, { type: "status", status: "idle" });
        drain(current);
      }, replyDelay);
    }
    function runtimeSnapshot() {
      return ["claude", "codex"].map((id) => ({
        id,
        name: id === "claude" ? "Claude Code" : "Codex",
        available: true,
        error: null,
        version: "synthetic",
        source: "configured",
        discovery: { status: "synthetic", confirmed: false },
        models: [
          {
            id: `synthetic-${id}`,
            name: "样例模型（非原生发现）",
            efforts: ["low", "medium", "high"],
            source: "configured",
            status: "configured",
          },
        ],
        efforts: ["low", "medium", "high"],
        synthetic: true,
      }));
    }
    function settingsSnapshot() {
      return {
        ...clone(state.settings),
        runtimes: runtimeSnapshot(),
        home: "样例数据 · 浏览器 localStorage",
        backend: "未连接后台",
        synthetic: true,
      };
    }
    function scopeValue(scope, identity) {
      if (!["global", "project", "session"].includes(scope))
        throw fail("设置范围无效");
      identity = scope === "global" ? "global" : identity;
      if (!identity) throw fail("请指定设置范围身份");
      return (
        state.scopes[`${scope}:${identity}`] || {
          scope,
          identity,
          version: 0,
          ai: scope === "global" ? clone(state.settings.ai) : {},
          execution: scope === "global" ? clone(state.settings.execution) : {},
          applies: "next_launch",
          synthetic: true,
        }
      );
    }
    function effective(params) {
      const project = params.get("project"),
        session = params.get("session_id"),
        purpose = params.get("purpose") || "default";
      let ai = clone(state.settings.ai[purpose] || state.settings.ai.default),
        execution = clone(state.settings.execution);
      const source = {
        scope: "global",
        identity: "global",
        purpose: state.settings.ai[purpose] ? purpose : "default",
      };
      let sources = {
        ai: Object.fromEntries(Object.keys(ai).map((field) => [field, source])),
        execution: Object.fromEntries(
          Object.keys(execution).map((field) => [
            field,
            { scope: "global", identity: "global" },
          ]),
        ),
      };
      const scopes = [scopeValue("global")];
      for (const [scope, identity] of [
        ["project", project],
        ["session", session],
      ])
        if (identity) {
          const value = scopeValue(scope, identity);
          scopes.push(value);
          const selection = value.ai[purpose] || value.ai.default;
          if (selection) {
            ai = clone(selection);
            sources.ai = Object.fromEntries(
              Object.keys(ai).map((field) => [
                field,
                { scope, identity, purpose },
              ]),
            );
          }
          for (const [runtime, mode] of Object.entries(value.execution)) {
            execution[runtime] = mode;
            sources.execution[runtime] = { scope, identity };
          }
        }
      return {
        ai,
        execution,
        sources,
        scopes,
        project,
        session_id: session,
        purpose,
        applies: "next_launch",
        current_execution_changed: false,
        native_configuration: {
          status: "not_inspected",
          loaded: "not_confirmed",
        },
        synthetic: true,
      };
    }
    function promptResolved(key, project, session) {
      if (!(key in PROMPTS)) throw fail("未知提示词类别");
      let value = {
        key,
        content: PROMPTS[key],
        version: "builtin-v1",
        source: { scope: "default", identity: null },
      };
      for (const [scope, identity] of [
        ["global", "global"],
        ["project", project],
        ["session", session],
      ]) {
        const entry = state.prompts[`${scope}:${identity}:${key}`];
        if (identity && entry?.content != null)
          value = {
            key,
            content: entry.content,
            version: entry.version,
            source: { scope, identity },
          };
      }
      return { ...value, sha256: "synthetic", synthetic: true };
    }
    function promptWritable(key, scope = "global", identity) {
      identity = scope === "global" ? "global" : identity;
      const entry = state.prompts[`${scope}:${identity}:${key}`];
      return {
        key,
        scope,
        identity,
        version: entry?.version || 0,
        content: entry?.content ?? null,
        overridden: entry?.content != null,
        default: PROMPTS[key],
        effective: promptResolved(
          key,
          scope === "project" ? identity : null,
          scope === "session" ? identity : null,
        ),
        applies: "next_turn",
        synthetic: true,
      };
    }
    // Complete the same read projection for fresh and already-persisted sample
    // records. These fields are supplied by workspace_snapshot in the real API.
    function projectPerson(value) {
      return {
        ...person(
          value.session_id,
          value.name,
          value.project,
          value.leader_session_id,
        ),
        ...value,
      };
    }
    function getState(project) {
      const active = state.projects.find((item) => item.name === project) ||
        state.projects[0] || {
          name: project || "",
          root: "",
          display_name: "未选择工作区",
          theme: "jade",
        };
      const tasks = state.tasks.filter(
        (item) => !project || item.projects.includes(project),
      );
      const leaders = state.leaders
        .filter((item) => !project || item.project === project)
        .map(projectPerson);
      const members = state.members
        .filter((item) => !project || item.project === project)
        .map(projectPerson);
      for (const leader of leaders)
        leader.member_ids = members
          .filter((member) => member.leader_session_id === leader.session_id)
          .map((member) => member.session_id);
      const people = new Set(
        [...leaders, ...members].map((item) => item.session_id),
      );
      const todos = state.todos.filter(
        (item) =>
          item.status === "open" &&
          (!project || item.projects.includes(project)),
      );
      const decisions = state.decisions.filter(
        (item) =>
          !project ||
          people.has(item.leader_session_id) ||
          people.has(item.session_id),
      );
      const runtimes = runtimeSnapshot();
      const goals = tasks.map((item) => ({
        ...item,
        status: item.execution_status,
        report_available: Boolean(state.reports[item.id]),
        assignments: members
          .filter(
            (member) => member.leader_session_id === item.leader_session_id,
          )
          .map((member) => ({
            session_id: member.session_id,
            status: "assigned",
          })),
      }));
      const channels = ["qq", "lark"].map((id) => ({
        id,
        name: id === "qq" ? "QQ" : "Lark",
        state: "unconfigured",
        detail: "样例模式 · 未连接",
        error: null,
        synthetic: true,
      }));
      return {
        project: active,
        projects: state.projects,
        leaders,
        members,
        goals,
        tasks,
        todos,
        todo_count: todos.length,
        decisions,
        attention: [],
        native_approvals: [],
        now: stamp(),
        agents_error: null,
        vscode_error: null,
        runtimes,
        ai: clone(state.settings.ai),
        models: runtimes[0].models,
        efforts: runtimes[0].efforts,
        candidates: { running: [], others: [] },
        limits: { claude: { seven_day: null, five_hour: null }, codex: null },
        notification: {
          ...state.notifications,
          channels,
          channel: channels[0],
        },
        channel: {
          digesting: false,
          digest_error: null,
          digest_id: null,
          ...state.channel,
        },
        themes: [
          {
            id: "jade",
            name: "雾绿",
            light: {
              "--accent": "#438378",
              "--accent-text": "#356b62",
              "--on-accent": "#fff",
            },
            dark: {
              "--accent": "#88c4b5",
              "--accent-text": "#9dd5c7",
              "--on-accent": "#152b25",
            },
          },
        ],
        errors: [],
        synthetic: true,
      };
    }
    function taskDetail(item) {
      const members = state.members
        .filter((person) => person.leader_session_id === item.leader_session_id)
        .map(projectPerson);
      const executions = [
        found(state.leaders, item.leader_session_id, "负责人"),
        ...members,
      ].map((person) => {
        const current = journal(
          item.primary_project,
          item.id,
          person.session_id,
        );
        return {
          id: current.execution_id,
          execution_id: current.execution_id,
          session_id: person.session_id,
          goal_id: item.id,
          runtime: person.runtime,
          model: person.model,
          effort: person.effort,
          round: item.current_round,
          requirement_version: item.requirement_version,
          generation: 1,
          status: current.busy ? "running" : "idle",
          synthetic: true,
        };
      });
      return {
        task: item,
        executions,
        members,
        reports: state.reports[item.id]
          ? [
              {
                id: "report-" + item.id,
                goal_id: item.id,
                title: item.title + " · 阶段报告",
                round: 1,
                requirement_version: item.requirement_version,
                version: state.reports[item.id].version,
                status: "awaiting_acceptance",
                url: `/api/goals/report?project=${encodeURIComponent(item.primary_project)}&goal_id=${encodeURIComponent(item.id)}&report_id=${encodeURIComponent("report-" + item.id)}`,
                created_at: state.reports[item.id].updated_at,
                synthetic: true,
              },
            ]
          : [],
        documents: [],
        context: { items: [], manifest: {} },
        acceptance_review: null,
        scheduler: {
          state: item.state,
          queue: item.queue || [],
          synthetic: true,
        },
        notifications: { receipts: [] },
        user_forwards: [],
        synthetic: true,
      };
    }
    function route(path, data, method) {
      const url = new URL(path, "http://synthetic.invalid"),
        name = url.pathname,
        q = url.searchParams;
      const project = q.get("project") || data?.project;
      const post = method === "POST",
        body = data || {};
      const taskID =
        q.get("task_id") || q.get("goal_id") || body.task_id || body.goal_id;
      if (
        state.auth?.authenticated === false &&
        !["/api/auth/session", "/api/auth/login"].includes(name)
      ) {
        throw fail("样例会话已退出，请重新登录样例。", 401, {
          code: "unauthorized",
          synthetic: true,
        });
      }
      if (name === "/api/projects" && !post)
        return { projects: state.projects, synthetic: true };
      if (name === "/api/project/init" && post) {
        if (!body.name?.trim() || !body.root?.trim())
          throw fail("请填写工作区名称和文件夹路径");
        if (state.projects.some((item) => item.name === body.name))
          throw fail("工作区名称已存在", 409);
        const value = {
          name: body.name.trim(),
          display_name: body.name.trim(),
          root: body.root.trim(),
          workspace_id: uuid(),
          theme: "jade",
          synthetic: true,
        };
        state.projects.push(value);
        return { project: value, synthetic: true };
      }
      if (name === "/api/state" && !post) return getState(project);
      if (name === "/api/project/theme" && post) {
        const item = state.projects.find((item) => item.name === body.project);
        if (!item) throw fail("工作区不存在", 404);
        if (body.theme !== "jade") throw fail("样例中没有这个主题", 400);
        item.theme = body.theme;
        return { theme: item.theme, synthetic: true };
      }
      if (
        post &&
        (name === "/api/answer" || name.startsWith("/api/decision/"))
      ) {
        const decision = found(state.decisions, body.id, "拍板事项");
        const task = taskBy(decision.goal_id);
        if (name === "/api/answer") {
          if (!body.text?.trim()) throw fail("请填写你的决定");
          state.decisions = state.decisions.filter(
            (item) => item.id !== decision.id,
          );
          state.answers ||= [];
          state.answers.push({
            ...decision,
            answer: body.text,
            answered_at: stamp(),
            synthetic: true,
          });
          task.state = task.authorized ? "running" : "discussion";
          task.waiting_reason = null;
          update(task);
          return { routed_to: null, synthetic: true };
        }
        if (name.endsWith("/hold")) decision.held = true;
        else if (name.endsWith("/unhold")) decision.held = false;
        else if (name.endsWith("/discuss") || name.endsWith("/clarify"))
          decision.presentation_stage = "discussion";
        else if (name.endsWith("/ready")) {
          decision.presentation_stage = "ready";
          decision.brief = {
            context: "样例：请体验任务切换和资料展开后，在原任务里作出选择。",
            why_user: "动画舒适度需要你的实际体验。",
            recommendation_reason: "当前时长较短，切换过程保持对话来历。",
            options: decision.options.map((label) => ({
              label,
              consequence: label.includes("轻")
                ? "缩短切换动画，保留淡入反馈。"
                : "维持当前切换时长。",
            })),
          };
        } else throw fail("此样例未实现该拍板动作", 501);
        return { conversation_goal_id: decision.goal_id, synthetic: true };
      }
      if (name === "/api/runtimes" && !post)
        return { runtimes: runtimeSnapshot(), synthetic: true };
      if (name === "/api/tasks" && !post)
        return {
          tasks: state.tasks.filter(
            (item) =>
              (!project || item.projects.includes(project)) &&
              (q.get("include_inactive") === "1" ||
                !["archived", "trashed"].includes(item.state)),
          ),
          synthetic: true,
        };
      if (name === "/api/tasks/attention" && !post) {
        const items = state.tasks
          .filter(
            (item) =>
              (!project || item.projects.includes(project)) &&
              (["waiting_user", "blocked"].includes(item.state) ||
                item.review_status === "pending"),
          )
          .map((item) => ({
            id:
              (item.state === "blocked"
                ? "blocked:"
                : item.review_status === "pending"
                  ? "review:"
                  : "decision:") + item.id,
            task_id: item.id,
            goal_id: item.id,
            task_ids: [item.id],
            decision_id: state.decisions.find(
              (decision) => decision.goal_id === item.id,
            )?.id,
            session_id: item.leader_session_id,
            project: item.primary_project,
            created_at: item.created_at,
            title: item.title,
            summary: item.waiting_reason || "有阶段成果待体验",
            kind:
              item.state === "blocked"
                ? "blocked"
                : item.review_status === "pending"
                  ? "review"
                  : "decision",
            synthetic: true,
          }));
        for (const todo of state.todos)
          if (
            todo.status === "open" &&
            (!project || todo.projects.includes(project))
          ) {
            items.push({
              id: "todo:" + todo.id,
              task_id: todo.task_ids[0],
              goal_id: todo.task_ids[0],
              task_ids: todo.task_ids,
              session_id: todo.leader_session_id || todo.session_id,
              created_at: todo.created_at,
              todo_id: todo.id,
              project: todo.project,
              title: todo.title,
              summary: "需要你亲自处理",
              kind: "todo",
              synthetic: true,
            });
          }
        const offset = Math.max(0, Number(q.get("offset")) || 0),
          limit = Math.min(100, Math.max(1, Number(q.get("limit")) || 50));
        return {
          items: items.slice(offset, offset + limit),
          total: items.length,
          offset,
          limit,
          next_offset: offset + limit < items.length ? offset + limit : null,
          synthetic: true,
        };
      }
      if (name === "/api/task" && !post) return taskDetail(taskBy(taskID));
      if (name === "/api/tasks/create" && post) {
        if (!project || !body.text?.trim())
          throw fail("请提供工作区和讨论内容");
        const owner =
          body.leader_session_id ||
          state.leaders.find((item) => item.project === project)?.session_id ||
          uuid();
        if (!state.leaders.some((item) => item.session_id === owner))
          state.leaders.push(person(owner, "任务负责人", project));
        const value = task(
          uuid(),
          body.title || body.text.trim().slice(0, 24),
          project,
          owner,
          "discussion",
        );
        Object.assign(value, {
          request: body.text,
          projects: [...new Set([project, ...(body.projects || [])])],
          acceptance: body.acceptance || [],
          constraints: body.constraints || [],
        });
        state.tasks.push(value);
        return { task: value, synthetic: true };
      }
      if (
        /^\/api\/tasks\/(update|mode|pin|start|stop|resume|archive|trash|restore|schedule)$/.test(
          name,
        ) &&
        post
      ) {
        const value = taskBy(taskID),
          action = name.split("/").at(-1);
        if (action !== "pin") checkVersion(value, body.version);
        if (action === "update") {
          for (const field of [
            "title",
            "request",
            "acceptance",
            "constraints",
            "projects",
          ])
            if (field in body) value[field] = clone(body[field]);
          value.projects = [
            ...new Set([value.primary_project, ...value.projects]),
          ];
          value.requirement_version += 1;
        } else if (action === "mode") {
          if (!["manual", "automatic"].includes(body.mode))
            throw fail("任务模式无效");
          value.mode = body.mode;
        } else if (action === "pin") {
          value.pinned = Boolean(body.pinned);
          value.pinned_at = value.pinned ? stamp() : null;
        } else if (action === "start" || action === "resume") {
          value.authorized = true;
          value.state = "running";
          value.execution_status = "running";
        } else if (action === "schedule") {
          if (!body.text?.trim()) throw fail("请提供排队内容");
          if (
            !value.authorized ||
            !["running", "waiting_user"].includes(value.state)
          )
            throw fail("请先明确开始或恢复任务，再排队发送。", 409);
          (value.queue ||= []).push({
            id: uuid(),
            text: body.text,
            session_id: body.session_id || value.leader_session_id,
            status: "queued",
            evidence: body.evidence,
            synthetic: true,
          });
          later(
            () =>
              drain(
                journal(
                  value.primary_project,
                  value.id,
                  body.session_id || value.leader_session_id,
                ),
              ),
            0,
          );
        } else {
          for (const queued of value.queue || [])
            if (queued.status === "queued") queued.status = "cancelled";
          for (const current of Object.values(state.journals))
            if (current.goal_id === value.id) interrupt(current);
          value.state = {
            stop: "stopped",
            archive: "archived",
            trash: "trashed",
            restore: "stopped",
          }[action];
          value.execution_status = "paused";
          value.lifecycle_pending = null;
        }
        if (action !== "pin") update(value);
        return {
          task: value,
          receipt: { status: "accepted", synthetic: true },
          synthetic: true,
        };
      }
      if (name === "/api/chat/history" && !post) {
        const current = journal(project, q.get("goal_id"), q.get("session_id"));
        return { ...current, previous_executions: [], synthetic: true };
      }
      if (name === "/api/chat/read" && post) {
        const current = journal(project, body.goal_id, body.session_id);
        current.read_seq = body.seq;
        return { read_seq: current.read_seq, synthetic: true };
      }
      if (["/api/chat/send", "/api/assistant/send"].includes(name) && post) {
        if (!body.text?.trim() && !body.images?.length)
          throw fail("请提供文字或图片");
        if (!body.message_id) throw fail("请保留稳定 message_id");
        if (scenario === "unknown")
          return {
            message_id: body.message_id,
            accepted: false,
            status: "unknown",
            delivery: "unknown",
            synthetic: true,
          };
        const isAssistant = name === "/api/assistant/send";
        const current = journal(
          isAssistant ? state.assistant.project : project,
          isAssistant ? null : body.goal_id,
          isAssistant ? ASSISTANT : body.session_id,
        );
        reply(
          current,
          body.text || "[样例图片]",
          body.message_id,
          body.affects_task,
        );
        return {
          accepted: true,
          status: "accepted",
          message_id: body.message_id,
          execution_id: current.execution_id,
          synthetic: true,
        };
      }
      if (name === "/api/chat/interrupt" && post) {
        const current = journal(project, body.goal_id, body.session_id);
        interrupt(current);
        return { accepted: true, status: "interrupted", synthetic: true };
      }
      if (name === "/api/assistant/open" && post)
        return { assistant: state.assistant, synthetic: true };
      if (name === "/api/assistant/action" && post) {
        if (!body.instruction?.trim()) throw fail("请提供明确指令");
        const actions = {
          "task.create": "/api/tasks/create",
          "task.stop": "/api/tasks/stop",
          capture: "/api/personal/capture",
          "item.capture": "/api/personal/capture",
        };
        if (!actions[body.action])
          throw fail("此样例未实现该助手动作", 501, { action: body.action });
        const payload = { ...body.data, request_id: body.request_id };
        if (body.action === "task.stop")
          payload.version ??= taskBy(payload.task_id).version;
        return route(actions[body.action], payload, "POST");
      }
      if (name === "/api/personal/items" && !post)
        return {
          items: state.items.filter(
            (item) =>
              (!project || item.project === project) &&
              (q.get("unassigned") !== "1" || !item.project) &&
              (q.get("include_inactive") === "1" || item.state === "open"),
          ),
          todos: state.todos.filter(
            (item) =>
              (!project || item.projects.includes(project)) &&
              (q.get("include_done") === "1" || item.status === "open"),
          ),
          synthetic: true,
        };
      if (name === "/api/personal/capture" && post) {
        if (!body.text?.trim()) throw fail("请输入内容");
        const item = {
          id: uuid(),
          kind: "idea",
          text: body.text,
          project: body.project || null,
          tags: body.tags || [],
          state: "open",
          version: 1,
          created_at: stamp(),
          updated_at: stamp(),
          synthetic: true,
        };
        state.items.push(item);
        return { item, synthetic: true };
      }
      if (name === "/api/personal/update" && post) {
        const item = found(state.items, body.id, "记录");
        checkVersion(item, body.version);
        for (const field of ["text", "project", "tags", "state"])
          if (field in body) item[field] = clone(body[field]);
        update(item);
        return { item, synthetic: true };
      }
      if (
        ["/api/personal/items/chat", "/api/personal/items/link"].includes(
          name,
        ) &&
        post
      ) {
        const item = found(state.items, body.id, "记录");
        checkVersion(item, body.version);
        let taskID =
            body.task_id ||
            (!body.new_task && item.task_ids?.length === 1
              ? item.task_ids[0]
              : null),
          created = false;
        if (!taskID && name.endsWith("chat")) {
          const value = route(
            "/api/tasks/create",
            {
              project: project || item.project,
              text: item.text,
              title: body.title,
            },
            "POST",
          );
          taskID = value.task.id;
          created = true;
        }
        const target = taskBy(taskID);
        if (["archived", "trashed"].includes(target.state))
          throw fail("请先恢复任务记录", 409);
        if (!item.task_ids?.includes(taskID)) {
          item.task_ids = [...(item.task_ids || []), taskID];
          update(item);
        }
        return {
          item,
          task: target,
          created,
          conversation: {
            project: target.primary_project,
            goal_id: target.id,
            session_id: target.leader_session_id,
            text: item.text,
          },
          synthetic: true,
        };
      }
      if (name === "/api/personal/todos" && post) {
        let item = state.todos.find(
          (entry) =>
            body.operation_key &&
            entry.operation_key === body.operation_key &&
            entry.status === "open",
        );
        if (item) {
          item.task_ids = [
            ...new Set([...item.task_ids, ...(body.task_ids || [])]),
          ];
          update(item);
        } else {
          item = {
            ...body,
            id:
              state.todos.reduce(
                (max, entry) => Math.max(max, Number(entry.id) || 0),
                0,
              ) + 1,
            kind: "todo",
            project: project || taskBy(body.task_ids?.[0]).primary_project,
            projects: [project || taskBy(body.task_ids?.[0]).primary_project],
            status: "open",
            version: 1,
            metadata_version: 0,
            tags: body.tags || [],
            evidence: [],
            created_at: stamp(),
            synthetic: true,
          };
          delete item.request_id;
          state.todos.push(item);
        }
        return { todo: item, synthetic: true };
      }
      if (name === "/api/personal/todos/update" && post) {
        const item = found(state.todos, body.id, "TODO");
        if (
          scenario === "conflict" ||
          (item.metadata_version || 0) !== body.metadata_version
        )
          throw fail("TODO 标签已变化，请保留草稿并重新核对。", 409, {
            current: clone(item),
          });
        if (
          !Array.isArray(body.tags) ||
          body.tags.some((tag) => typeof tag !== "string")
        )
          throw fail("标签必须是字符串数组");
        item.tags = [...new Set(body.tags)];
        item.metadata_version = (item.metadata_version || 0) + 1;
        return { todo: item, synthetic: true };
      }
      if (
        ["/api/personal/todos/complete", "/api/todo/resolve"].includes(name) &&
        post
      ) {
        const item = found(state.todos, body.id, "TODO");
        checkVersion(item, body.version ?? body.expected_version);
        if (body.status === "cancelled" && !body.note?.trim())
          throw fail("请说明做不了或不做的原因");
        item.status = body.status || "done";
        item.note = body.note || "";
        item.evidence = clone(body.attachments || []);
        update(item);
        return {
          todo: item,
          scheduled: item.task_ids.filter(
            (id) =>
              !["stopped", "trashed", "archived"].includes(taskBy(id).state),
          ),
          synthetic: true,
        };
      }
      if (name === "/api/settings" || name === "/api/project/settings") {
        if (post) {
          if (body.ai) state.settings.ai = clone(body.ai);
          if (body.execution) state.settings.execution = clone(body.execution);
        }
        return name === "/api/settings"
          ? settingsSnapshot()
          : {
              project: state.projects.find((item) => item.name === project),
              sessions: [...state.leaders, ...state.members].filter(
                (item) => item.project === project,
              ),
              revision: 1,
              goals_count: state.tasks.filter((item) =>
                item.projects.includes(project),
              ).length,
              synthetic: true,
            };
      }
      if (name === "/api/settings/effective" && !post) return effective(q);
      if (name === "/api/settings/scope") {
        const scope = body.scope || q.get("scope") || "global",
          identity = body.identity || q.get("identity");
        const value = clone(scopeValue(scope, identity));
        if (post) {
          checkVersion(value, body.version);
          if (body.reset) {
            value.ai = scope === "global" ? seed(true).settings.ai : {};
            value.execution =
              scope === "global" ? seed(true).settings.execution : {};
          } else {
            for (const field of ["ai", "execution"])
              for (const [name, setting] of Object.entries(body[field] || {})) {
                if (setting === null) delete value[field][name];
                else value[field][name] = clone(setting);
              }
          }
          value.version += 1;
          state.scopes[`${scope}:${value.identity}`] = value;
          if (scope === "global")
            state.settings = {
              ai: clone(value.ai),
              execution: clone(value.execution),
            };
        }
        return value;
      }
      if (name === "/api/prompts" && !post) {
        if (q.has("key"))
          return promptWritable(
            q.get("key"),
            q.get("scope") || "global",
            q.get("identity"),
          );
        return {
          project,
          session_id: q.get("session_id"),
          prompts: Object.keys(PROMPTS).map((key) =>
            promptResolved(key, project, q.get("session_id")),
          ),
          applies: "next_turn",
          native_private_instructions: "not_controlled",
          synthetic: true,
        };
      }
      if (["/api/prompts/save", "/api/prompts/reset"].includes(name) && post) {
        const value = promptWritable(body.key, body.scope, body.identity);
        checkVersion(value, body.version);
        const identity = `${value.scope}:${value.identity}:${body.key}`;
        state.prompts[identity] = {
          version: value.version + 1,
          content: name.endsWith("reset") ? null : body.text,
        };
        state.promptHistory.push({
          key: body.key,
          scope: value.scope,
          identity: value.identity,
          version: value.version + 1,
          content: state.prompts[identity].content,
          synthetic: true,
        });
        return promptWritable(body.key, body.scope, body.identity);
      }
      if (name === "/api/prompts/history" && !post)
        return {
          history: state.promptHistory.filter(
            (item) => item.key === q.get("key"),
          ),
          next_before: null,
          synthetic: true,
        };
      if (name === "/api/prompts/applied" && !post)
        return {
          execution_id: q.get("execution_id"),
          applications: [],
          loaded: "not_confirmed",
          synthetic: true,
        };
      if (name === "/api/tasks/report") {
        const value = state.reports[taskID] || {
          goal_id: taskID,
          version: 0,
          markdown: "",
          updated_at: null,
          synthetic: true,
        };
        if (!post)
          return {
            report: value,
            pending: state.pending[taskID] || [],
            synthetic: true,
          };
        if (scenario === "conflict" || body.version !== value.version) {
          const pending = {
            id: uuid(),
            pending_id: uuid(),
            markdown: body.markdown,
            sections: body.sections,
            synthetic: true,
          };
          (state.pending[taskID] ||= []).push(pending);
          persist();
          throw fail("报告已更新，双方内容已保留。", 409, {
            conflict: true,
            pending_id: pending.pending_id,
            report: value,
          });
        }
        if (body.markdown !== undefined) value.markdown = body.markdown;
        else if (body.sections) {
          for (const [title, content] of Object.entries(body.sections)) {
            const heading = "## " + title,
              lines = value.markdown.split("\n"),
              start = lines.indexOf(heading);
            if (start < 0) value.markdown += `\n\n${heading}\n\n${content}`;
            else {
              let end = start + 1;
              while (end < lines.length && !lines[end].startsWith("## ")) end++;
              lines.splice(start, end - start, heading, "", content, "");
              value.markdown = lines.join("\n");
            }
          }
        } else throw fail("请提供报告内容或分节更新");
        update(value);
        state.reports[taskID] = value;
        return { ok: true, report: value, synthetic: true };
      }
      if (name === "/api/tasks/attachments") {
        taskBy(taskID);
        if (!post)
          return {
            attachments: state.attachments[taskID] || [],
            synthetic: true,
          };
        if (
          !body.filename ||
          typeof body.data !== "string" ||
          !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(
            body.data,
          )
        )
          throw fail("附件必须提供文件名和有效 base64");
        const item = {
          id: uuid(),
          goal_id: taskID,
          filename: body.filename,
          mime_type: body.mime_type || "application/octet-stream",
          data: body.data,
          size_bytes:
            Math.floor((body.data.length * 3) / 4) -
            (body.data.endsWith("==") ? 2 : body.data.endsWith("=") ? 1 : 0),
          sha256: "synthetic",
          created_at: stamp(),
          synthetic: true,
        };
        item.url = `/api/tasks/attachment?task_id=${encodeURIComponent(taskID)}&attachment_id=${encodeURIComponent(item.id)}`;
        (state.attachments[taskID] ||= []).push(item);
        return { attachment: item, synthetic: true };
      }
      if (name === "/api/tasks/versions") {
        taskBy(taskID);
        if (!post)
          return { versions: state.versions[taskID] || [], synthetic: true };
        const item = {
          id: uuid(),
          goal_id: taskID,
          version: (state.versions[taskID]?.length || 0) + 1,
          label: body.label || "样例版本",
          files: (body.files || []).map((path) => ({
            path,
            exists: true,
            content: "样例内容",
            delivered_sha256: "synthetic",
            delivered_size: 12,
          })),
          created_at: stamp(),
          synthetic: true,
        };
        (state.versions[taskID] ||= []).unshift(item);
        return { version: item, synthetic: true };
      }
      if (
        ["/api/tasks/versions/preview", "/api/tasks/versions/restore"].includes(
          name,
        ) &&
        post
      ) {
        const value = found(
          state.versions[taskID] || [],
          body.version_id,
          "成果版本",
        );
        const expected = Object.fromEntries(
          value.files.map((file) => [file.path, "synthetic-current"]),
        );
        if (name.endsWith("restore")) {
          if (
            scenario === "conflict" ||
            JSON.stringify(body.expected_files) !== JSON.stringify(expected)
          )
            throw fail("文件在预览后已变化，未覆盖样例内容。", 409, {
              conflict: true,
              expected_files: expected,
            });
          taskBy(taskID).restored_version_id = value.id;
          return {
            ok: true,
            restored: true,
            version_id: value.id,
            files: value.files,
            synthetic: true,
          };
        }
        return {
          goal_id: taskID,
          version_id: value.id,
          version: value.version,
          expected_files: expected,
          files: value.files.map((file) => ({
            path: file.path,
            action:
              scenario === "conflict"
                ? "conflict"
                : file.delivered_sha256
                  ? "write"
                  : "delete",
            current_sha256: "synthetic-current",
            current_size: 21,
            target_sha256: file.delivered_sha256 || null,
            target_size: file.delivered_size || 0,
            reason: scenario === "conflict" ? "样例：文件有后续修改" : null,
          })),
          can_restore: scenario !== "conflict",
          synthetic: true,
        };
      }
      if (name === "/api/remote" && !post) return state.remote;
      if (name === "/api/remote/configure" && post) {
        checkVersion(state.remote, body.version);
        if (typeof body.enabled !== "boolean" || !Array.isArray(body.origins))
          throw fail("请提供 enabled 和 HTTPS origin 列表");
        const origins = body.origins.map((value) => {
          let parsed;
          try {
            parsed = new URL(value);
          } catch (_) {
            throw fail("远程入口必须是完整 HTTPS origin");
          }
          if (
            parsed.protocol !== "https:" ||
            parsed.origin !== value ||
            parsed.username ||
            parsed.password
          )
            throw fail("远程入口必须是完整 HTTPS origin");
          return parsed.origin;
        });
        if (
          body.passphrase &&
          (typeof body.passphrase !== "string" || body.passphrase.length < 12)
        )
          throw fail("口令至少需要 12 个字符");
        const configured =
          state.remote.password_configured || Boolean(body.passphrase);
        if (body.enabled && (!configured || origins.length === 0))
          throw fail("启用需要 HTTPS origin 与访问口令");
        state.remote = {
          ...state.remote,
          enabled: body.enabled,
          origins: [...new Set(origins)],
          version: state.remote.version + 1,
          password_configured: configured,
          deployed: false,
          status: "synthetic_only",
          note: "仅保存浏览器样例配置；没有配置代理、口令认证或实际公网服务。",
        };
        return state.remote;
      }
      if (name === "/api/auth/session" && !post)
        return {
          ...(state.auth || { authenticated: true, local: true, origin: null }),
          synthetic: true,
        };
      if (name === "/api/auth/login" && post) {
        if (typeof body.passphrase !== "string" || !body.passphrase.trim())
          throw fail("请输入样例口令", 400);
        state.auth = {
          authenticated: true,
          local: false,
          origin: "https://synthetic.invalid",
        };
        return {
          authenticated: true,
          expires_in: 86400,
          status: "synthetic_only",
          note: "样例登录，没有建立真实认证会话。",
          synthetic: true,
        };
      }
      if (name === "/api/auth/logout" && post) {
        state.auth = {
          authenticated: false,
          local: false,
          origin: "https://synthetic.invalid",
        };
        return { authenticated: false, synthetic: true };
      }
      if (name === "/api/admin/service" && !post)
        return {
          managed: false,
          can_stop: false,
          state: "synthetic",
          url: null,
          supervisor_pid: null,
          service_pid: null,
          instance_id: null,
          reason: "样例模式未连接 Crew 后台，不能停止或重启正式服务。",
          synthetic: true,
        };
      if (name === "/api/admin/service/stop" && post)
        throw fail("样例模式没有可停止的真实后台。", 501, { synthetic: true });
      if (name === "/api/notifications/away" && post) {
        state.notifications.manual_away = Boolean(body.away);
        state.notifications.away = Boolean(body.away);
        return { ...state.notifications, synthetic: true };
      }
      throw fail(`样例尚未实现接口：${method} ${name}`, 501, {
        synthetic: true,
      });
    }
    function binary(path) {
      const url = new URL(path, "http://synthetic.invalid"),
        q = url.searchParams,
        id = q.get("task_id");
      if (url.pathname === "/api/tasks/attachment") {
        const item = found(
          state.attachments[id] || [],
          q.get("attachment_id"),
          "附件",
        );
        const decode = environment.atob || globalThis.atob;
        return {
          content: Uint8Array.from(decode(item.data), (char) =>
            char.charCodeAt(0),
          ),
          mime: item.mime_type,
        };
      }
      if (url.pathname === "/api/tasks/version-file") {
        const version = found(
          state.versions[id] || [],
          q.get("version_id"),
          "版本",
        );
        const file = version.files.find((item) => item.path === q.get("path"));
        if (!file) throw fail("版本文件不存在", 404);
        return { content: file.content, mime: "text/plain; charset=utf-8" };
      }
      if (url.pathname === "/api/goals/report") {
        const report = state.reports[q.get("goal_id")];
        if (!report) throw fail("报告不存在", 404);
        const escaped = report.markdown.replace(
          /[&<>]/g,
          (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[char],
        );
        return {
          content: `<!doctype html><meta charset="utf-8"><style>body{font:16px/1.8 system-ui;padding:24px;max-width:780px;margin:auto;white-space:pre-wrap}</style><strong>明确样例 · 未连接 Crew</strong><article>${escaped}</article>`,
          mime: "text/html; charset=utf-8",
        };
      }
      return null;
    }
    function eventSource(path, options = {}) {
      const url = new URL(path, "http://synthetic.invalid"),
        q = url.searchParams;
      if (url.pathname !== "/api/chat/events")
        throw fail("样例没有此事件流", 501);
      const current = journal(
        q.get("project"),
        q.get("goal_id"),
        q.get("session_id"),
      );
      const handlers = new Map();
      let cursor = Math.max(
        Number(q.get("after")) || 0,
        Number(options.lastEventId) || 0,
      );
      const source = {
        CONNECTING: 0,
        OPEN: 1,
        CLOSED: 2,
        readyState: 0,
        url: path,
        withCredentials: true,
        onopen: null,
        onmessage: null,
        onerror: null,
        addEventListener(type, handler) {
          if (!handlers.has(type)) handlers.set(type, new Set());
          handlers.get(type).add(handler);
        },
        removeEventListener(type, handler) {
          handlers.get(type)?.delete(handler);
        },
        close() {
          source.readyState = 2;
          listeners.delete(listener);
          handlers.clear();
        },
      };
      function emit(type, event) {
        if (source.readyState === 2) return;
        const value = { type, target: source, ...event };
        if (typeof source["on" + type] === "function")
          source["on" + type](value);
        for (const handler of handlers.get(type) || []) {
          if (typeof handler === "function") handler(value);
          else handler?.handleEvent?.(value);
        }
      }
      const listener = {
        current,
        deliver(event) {
          if (source.readyState !== 1 || event.seq <= cursor) return;
          cursor = event.seq;
          emit("message", {
            data: JSON.stringify(event),
            lastEventId: String(event.seq),
          });
        },
      };
      later(() => {
        if (source.readyState === 2) return;
        if (scenario === "offline") {
          source.readyState = 0;
          emit("error", { message: "样例断连场景" });
          return;
        }
        source.readyState = 1;
        listeners.add(listener);
        emit("open", {});
        for (const event of current.events) listener.deliver(event);
      }, 0);
      return source;
    }
    async function fetchMock(path, init = {}) {
      if (closed) throw fail("样例客户端已关闭", 410);
      if (init.signal?.aborted) throw new DOMException("Aborted", "AbortError");
      if (scenario === "offline")
        throw new TypeError("样例断连：无法连接服务。不会切换到其他数据源。");
      const url = new URL(path, "http://synthetic.invalid"),
        method = (init.method || "GET").toUpperCase();
      if (!["GET", "HEAD", "POST"].includes(method))
        return new ResponseType(
          JSON.stringify({ error: "样例方法未实现", synthetic: true }),
          { status: 501 },
        );
      if (url.pathname === "/api/chat/events" && method === "GET") {
        const headers = new (environment.Headers || globalThis.Headers)(
          init.headers || {},
        );
        let source, detach;
        const stream = new StreamType({
          start(controller) {
            source = eventSource(path, {
              lastEventId: headers.get("Last-Event-ID"),
            });
            source.onmessage = (event) =>
              controller.enqueue(
                new Encoder().encode(
                  `id: ${event.lastEventId}\ndata: ${event.data}\n\n`,
                ),
              );
            source.onerror = (event) => {
              source.close();
              detach?.();
              controller.error(new Error(event.message || "样例事件流断开"));
            };
            const abort = () => {
              source.close();
              detach?.();
              try {
                controller.close();
              } catch (_) {
                /* Already cancelled by reader. */
              }
            };
            init.signal?.addEventListener("abort", abort, { once: true });
            detach = () => init.signal?.removeEventListener("abort", abort);
          },
          cancel() {
            source?.close();
            detach?.();
          },
        });
        return new ResponseType(stream, {
          headers: {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-store",
          },
        });
      }
      try {
        const file = binary(path);
        if (file && method !== "POST")
          return new ResponseType(method === "HEAD" ? null : file.content, {
            headers: { "Content-Type": file.mime, "X-Crew-Synthetic": "1" },
          });
        let body;
        try {
          body =
            init.body == null
              ? undefined
              : typeof init.body === "string"
                ? JSON.parse(init.body)
                : init.body;
        } catch (_) {
          throw fail("请求正文不是有效 JSON");
        }
        const identity = body?.request_id || body?.message_id;
        // Sample login/configuration never persists passwords, including in
        // request fingerprints. These endpoints do not use task idempotency.
        const secret = ["/api/auth/login", "/api/remote/configure"].includes(
          url.pathname,
        );
        const receiptKey =
          identity && !secret ? `${url.pathname}:${identity}` : null;
        const fingerprint = JSON.stringify(body);
        let result;
        if (receiptKey && state.receipts[receiptKey]) {
          const receipt = state.receipts[receiptKey];
          if (receipt.fingerprint !== fingerprint)
            throw fail("同一请求编号不能提交不同内容。", 409);
          result = clone(receipt.result);
        } else {
          result = route(path, body, method);
          if (receiptKey)
            state.receipts[receiptKey] = { fingerprint, result: clone(result) };
        }
        persist();
        return new ResponseType(
          method === "HEAD" ? null : JSON.stringify(clone(result)),
          {
            headers: {
              "Content-Type": "application/json; charset=utf-8",
              "X-Crew-Synthetic": "1",
            },
          },
        );
      } catch (error) {
        return new ResponseType(
          JSON.stringify(
            error.details || { error: error.message, synthetic: true },
          ),
          {
            status: error.status || 500,
            headers: {
              "Content-Type": "application/json; charset=utf-8",
              "X-Crew-Synthetic": "1",
            },
          },
        );
      }
    }
    function resourceURL(path) {
      if (scenario === "offline") throw fail("样例断连：资源暂不可用", 503);
      const file = binary(path);
      if (!file) throw fail("样例资源未实现", 501, { path });
      const bytes =
        typeof file.content === "string"
          ? new Encoder().encode(file.content)
          : file.content;
      let raw = "";
      for (const byte of bytes) raw += String.fromCharCode(byte);
      return `data:${file.mime};base64,${(environment.btoa || globalThis.btoa)(raw)}`;
    }
    return {
      mode: "mock",
      synthetic: true,
      scenario,
      namespace: key,
      fetch: fetchMock,
      eventSource,
      resourceURL,
      reset() {
        for (const listener of [...listeners]) listeners.delete(listener);
        state = seed(scenario === "empty");
        persist();
      },
      snapshot: () => clone(state),
      close() {
        closed = true;
        for (const id of timers) clear(id);
        timers.clear();
        listeners.clear();
      },
    };
  }
  return {
    create,
    seed,
    NAMESPACE,
    ids: { TASK, LEADER, OTHER, MEMBER, ASSISTANT },
  };
});
