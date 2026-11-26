"use strict";
(() => {
  /* ================= 假数据（按后端契约） ================= */
  const NOW = new Date("2026-11-27T15:00:00+08:00").getTime();
  const STALE_MIN = 10; // 排队超过 10 分钟 → 醒目提示
  const at = hm => `2026-11-27T${hm}:00+08:00`;

  const LEADERS = {
    "架构": {tint:"var(--accent)", runtime:"Claude", presence:"idle", presenceText:"空闲"},
    "前端": {tint:"var(--codex-accent)", runtime:"Codex", presence:"working", presenceText:"在跑任务"},
    "网关": {tint:"var(--task-running)", runtime:"Claude", presence:"idle", presenceText:"空闲"},
    "语音": {tint:"var(--warn)", runtime:"Codex", presence:"offline", presenceText:"离线"},
  };

  const DIGEST = {
    created_at: at("14:10"), total_message_count: 24, runtime: "claude", model: "haiku", cost_usd: 0.0123,
    body: [
      "登录页改版本周联调，<b>前端</b>负责页面，<b>网关</b>负责鉴权路由，<b>语音</b>负责唤醒词入口。",
      "旧契约 v1 已冻结，只修 bug；新字段一律走 v2。",
      "待定：语音入口放输入框左侧还是右侧，等语音确认。",
    ],
  };
  const ORIGINALS = [
    {id:12, speaker_name:"架构", created_at:at("11:02"), body:"先按 v1 跑通登录，v2 的字段我下午整理。", mentions:[], notified:[]},
    {id:13, speaker_name:"用户", created_at:at("11:05"), body:"好，v2 整理完在这里说一声。", reply:{id:12, speaker_name:"架构", excerpt:"先按 v1 跑通登录，v2 的字段我下午整理。"}, mentions:[], notified:[{name:"架构", delivery:"replied"}]},
  ];

  const MESSAGES = [
    {id:38, speaker_name:"用户", created_at:at("14:20"),
      body:"@架构 @前端 登录页改版这周要联调，接口契约以哪版为准？token 刷新逻辑也一起确认下。",
      mentions:[{name:"架构", delivery:"replied", replied:true, reply_id:39}, {name:"前端", delivery:"replied", replied:true, reply_id:42}], notified:[]},
    {id:39, speaker_name:"架构", created_at:at("14:23"), reply:{id:38, speaker_name:"用户", excerpt:"登录页改版这周要联调，接口契约以哪版为准？"},
      body:"以<b>契约 v2</b>为准。字段增删和迁移步骤比较长，放在私聊里了。", detail:{chars:1240}, mentions:[], notified:[]},
    {id:40, speaker_name:"架构", created_at:at("14:26"),
      body:"@网关 v2 里 <code>/auth/refresh</code> 改成了 POST，网关路由要同步，改完在这里说一声。",
      mentions:[{name:"网关", delivery:"delivered", replied:false}], notified:[]},
    {id:41, speaker_name:"用户", created_at:at("14:31"), reply:{id:39, speaker_name:"架构", excerpt:"以契约 v2 为准。字段增删和迁移步骤比较长…"},
      body:"v2 里 token 过期时间也改了吗？", mentions:[], notified:[{name:"架构", delivery:"replied", reply_id:43}]},
    {id:42, speaker_name:"前端", created_at:at("14:46"), reply:{id:38, speaker_name:"用户", excerpt:"登录页改版这周要联调，接口契约以哪版为准？"},
      body:"前端按 v2 改，预计今晚提测。@语音 唤醒词按钮放在输入框左侧可以吗？需要你确认。",
      mentions:[{name:"语音", delivery:"queued", replied:false}], notified:[]},
    {id:43, speaker_name:"架构", created_at:at("14:48"), auto:true, reply:{id:41, speaker_name:"用户", excerpt:"v2 里 token 过期时间也改了吗？"},
      body:"回复在私聊里", mentions:[], notified:[]},
    {id:44, speaker_name:"用户", created_at:at("14:57"), reply:{id:42, speaker_name:"前端", excerpt:"前端按 v2 改，预计今晚提测。@语音 唤醒词按钮…"},
      body:"@前端 提测前先在 375 宽度自查一遍。", mentions:[{name:"前端", delivery:"queued", replied:false}], notified:[]},
    {id:45, speaker_name:"架构", created_at:at("14:59"),
      body:"@所有 leader 契约 v2.1 今晚 22:00 冻结，之后的改动走变更单。",
      mentions:[{name:"架构", delivery:"none", replied:false}, {name:"前端", delivery:"queued", replied:false}, {name:"网关", delivery:"delivered", replied:false}, {name:"语音", delivery:"queued", replied:false}], notified:[]},
  ];

  const LEGEND = [
    {state:"queued", name:"前端", text:"已排队：对方忙/离线/被停止，还没收到"},
    {state:"queued", stale:true, name:"语音", age:14, text:"排队超过 10 分钟：值得去看看对方在干什么"},
    {state:"delivered", name:"网关", text:"已送达对方输入，还没回复"},
    {state:"replied", name:"架构", text:"对方已在交流区回复"},
    {state:"none", name:"架构", text:"不投递（如点名自己），不显示状态"},
    {state:"archived", name:"语音", text:"私聊协作：仅归档，不唤醒"},
    {state:"failed", name:"网关", text:"私聊协作：投递失败，点开看原因"},
  ];

  /* ================= 工具 ================= */
  const $ = id => document.getElementById(id);
  const esc = v => String(v).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const hm = iso => String(iso).slice(11, 16); // 假数据都是同一天的 +08:00 时间，直接取时分，避免受截图机器时区影响
  const minsSince = iso => Math.max(0, Math.round((NOW - new Date(iso).getTime()) / 60000));
  const icon = (id, cls = "") => `<svg class="i ${cls}" aria-hidden="true"><use href="#i-${id}"/></svg>`;
  const plain = html => html.replace(/<[^>]+>/g, "");
  const byId = new Map([...ORIGINALS, ...MESSAGES].map(m => [m.id, m]));
  const avatar = (name, size) => {
    const user = name === "用户";
    const tint = user ? "var(--text-2)" : (LEADERS[name] ? LEADERS[name].tint : "var(--accent)");
    return `<span class="av round${user ? " cx-av-user" : ""}" style="--s:${size}px;--tint:${tint}" aria-hidden="true">${esc(name.slice(0, 1))}</span>`;
  };

  /* ================= 投递状态 ================= */
  // 一个“投递项”= 某条发言里对某个 leader 的点名（kind=mention）或回复通知（kind=notified）。
  function stateOf(item, message) {
    if (item.delivery === "queued") {
      const age = item.age != null ? item.age : minsSince(message ? message.created_at : NOW);
      return {key:"queued", stale: item.stale != null ? item.stale : age >= STALE_MIN, age};
    }
    return {key:item.delivery, stale:false, age: message ? minsSince(message.created_at) : 0};
  }
  const STATE_UI = {
    queued:   {icon:"clock",   label:"排队中",  title:"已排队 · 对方还没收到"},
    stale:    {icon:"clock-alert", label:null,  title:"排队较久 · 对方还没收到"},
    delivered:{icon:"check",   label:"已送达",  title:"已送达 · 还没回复"},
    replied:  {icon:"replied", label:"已回复",  title:"已回复"},
    archived: {icon:"archive", label:"仅归档",  title:"仅归档 · 不唤醒"},
    failed:   {icon:"alert",   label:"失败",    title:"投递失败"},
  };
  function badgeLabel(s) {
    if (s.key === "queued" && s.stale) return `排队 ${s.age} 分钟`;
    return STATE_UI[s.key].label;
  }
  function ariaFor(name, s, kind) {
    const who = kind === "notified" ? `回复通知 ${name}` : `点名 ${name}`;
    const what = {queued: s.stale ? `已排队 ${s.age} 分钟，对方还没收到` : "已排队，对方还没收到", delivered:"已送达，还没回复", replied:"已回复", archived:"仅归档，不唤醒", failed:"投递失败"}[s.key];
    return `${who}：${what}。点开看说明`;
  }
  const items = []; // 注册所有可点的徽章，供浮层查找
  function badgeHTML(item, message, kind) {
    const s = stateOf(item, message);
    if (s.key === "none") {
      return `<span class="cx-dl" data-state="none" title="点名自己，不投递"><span class="cx-dl-main cx-dl-static"><span class="cx-dl-name">@${esc(item.name)}</span></span></span>`;
    }
    const idx = items.push({item, message, kind, s}) - 1;
    const ui = s.key === "queued" && s.stale ? STATE_UI.stale : STATE_UI[s.key];
    return `<span class="cx-dl" data-state="${s.key}"${s.stale ? " data-stale" : ""}>`
      + `<button type="button" class="cx-dl-main" data-badge="${idx}" aria-haspopup="dialog" aria-label="${esc(ariaFor(item.name, s, kind))}">`
      + `<span class="cx-dl-name">${kind === "notified" ? "" : "@"}${esc(item.name)}</span><span class="cx-dl-sep" aria-hidden="true"></span>`
      + `${icon(ui.icon, "xs")}<span class="cx-dl-text">${esc(badgeLabel(s))}</span></button>`
      + `<button type="button" class="cx-dl-go" data-go="badge:${idx}" aria-label="打开 ${esc(item.name)} 的私聊并定位到这条${kind === "notified" ? "回复通知" : "点名"}" title="打开 ${esc(item.name)} 的私聊">${icon("chat", "xs")}</button>`
      + `</span>`;
  }
  function deliveryRow(message) {
    const parts = [];
    if (message.mentions.length) parts.push(`<div class="cx-dl-group"><span class="cx-dl-label">点名</span><span class="cx-dl-list">${message.mentions.map(m => badgeHTML(m, message, "mention")).join("")}</span></div>`);
    if (message.notified.length) parts.push(`<div class="cx-dl-group"><span class="cx-dl-label" title="没写 @，但因为回复了它的发言而通知它">回复通知</span><span class="cx-dl-list">${message.notified.map(m => badgeHTML(m, message, "notified")).join("")}</span></div>`);
    return parts.length ? `<div class="cx-dl-row">${parts.join("")}</div>` : "";
  }

  /* ================= 脉络（引用树） ================= */
  const children = new Map();
  for (const m of MESSAGES) if (m.reply) { if (!children.has(m.reply.id)) children.set(m.reply.id, []); children.get(m.reply.id).push(m.id); }
  const threadOpen = new Set([38]);
  function descendants(id, depth = 0, out = []) {
    for (const child of children.get(id) || []) { out.push({id:child, depth}); descendants(child, depth + 1, out); }
    return out;
  }
  function threadHTML(message) {
    const all = descendants(message.id);
    if (all.length < 2) return ""; // 只有一条回复时引用块已经够清楚
    const open = threadOpen.has(message.id);
    const names = [...new Set(all.map(x => byId.get(x.id).speaker_name))];
    const head = `<button type="button" class="cx-thread-toggle" data-thread="${message.id}" aria-expanded="${open}">`
      + `${icon("thread", "sm")}<span>脉络 · ${all.length} 条回复</span><span class="cx-thread-avs" aria-hidden="true">${names.slice(0, 4).map(n => avatar(n, 18)).join("")}</span>`
      + `<span class="cx-thread-who">${esc(names.join("、"))}</span>${icon("chev", "xs cx-thread-chev")}</button>`;
    if (!open) return `<div class="cx-thread${message.speaker_name === "用户" ? " is-user" : ""}">${head}</div>`;
    const rows = all.map(x => {
      const m = byId.get(x.id);
      const text = m.auto ? "回复在私聊里" : plain(m.body);
      return `<li style="--d:${x.depth}"><button type="button" class="cx-thread-row" data-jump="${m.id}" aria-label="跳到 #${m.id} ${esc(m.speaker_name)} 的发言">`
        + `${avatar(m.speaker_name, 18)}<span class="cx-thread-name">${esc(m.speaker_name)}</span><span class="cx-id">#${m.id}</span>`
        + `<span class="cx-thread-text${m.auto ? " is-auto" : ""}">${esc(text)}</span><time>${hm(m.created_at)}</time></button></li>`;
    }).join("");
    return `<div class="cx-thread is-open${message.speaker_name === "用户" ? " is-user" : ""}">${head}<ol class="cx-thread-tree">${rows}</ol></div>`;
  }

  /* ================= 发言 ================= */
  function quoteHTML(message) {
    if (!message.reply) return "";
    return `<button type="button" class="cx-quote" data-jump="${message.reply.id}" aria-label="跳到被回复的 #${message.reply.id} ${esc(message.reply.speaker_name)} 的发言">`
      + `${icon("reply", "xs")}<span class="cx-id">#${message.reply.id}</span><strong>${esc(message.reply.speaker_name)}</strong><span class="cx-quote-text">${esc(message.reply.excerpt)}</span></button>`;
  }
  function metaHTML(message) {
    return `<div class="cx-meta"><strong>${esc(message.speaker_name)}</strong><span class="cx-id">#${message.id}</span><time datetime="${message.created_at}">${hm(message.created_at)}</time>`
      + `<button type="button" class="cx-reply-btn" data-reply="${message.id}" aria-label="回复 #${message.id}">${icon("reply", "xs")}<span>回复</span></button></div>`;
  }
  function messageHTML(message, opts = {}) {
    const user = message.speaker_name === "用户";
    if (message.auto) {
      return `<article class="cx-msg cx-auto" data-id="${message.id}">${avatar(message.speaker_name, 22)}`
        + `<div class="cx-auto-body"><span><strong>${esc(message.speaker_name)}</strong> 在私聊里回复了 `
        + `<button type="button" class="cx-inline-link" data-jump="${message.reply.id}">${esc(message.reply.speaker_name)} #${message.reply.id}</button></span>`
        + `<span class="cx-auto-meta"><span class="cx-id">#${message.id}</span><time>${hm(message.created_at)}</time></span>`
        + `<button type="button" class="cx-chip-btn" data-go="auto:${message.id}">${icon("chat", "xs")}<span>看私聊回复</span></button></div></article>`;
    }
    const detail = message.detail ? `<button type="button" class="cx-detail" data-go="detail:${message.id}" aria-label="打开 ${esc(message.speaker_name)} 的私聊看完整内容">`
      + `<span class="cx-detail-ic">${icon("doc", "sm")}</span><span class="cx-detail-txt"><strong>完整内容在 ${esc(message.speaker_name)} 的私聊里</strong><small>约 ${message.detail.chars.toLocaleString("zh-CN")} 字 · 定位到这条发言</small></span>${icon("chev", "xs")}</button>` : "";
    const body = `<div class="${user ? "cx-bubble" : "cx-content"}"><div class="cx-md">${message.body.replace(/@所有 leader|@[^\s@，。？！、]+/g, s => `<span class="cx-at">${s}</span>`)}</div></div>`;
    return `<article class="cx-msg${user ? " cx-user" : ""}${opts.original ? " cx-original" : ""}" data-id="${message.id}">${avatar(message.speaker_name, 28)}`
      + `<div class="cx-body-col">${metaHTML(message)}${quoteHTML(message)}${body}${detail}${deliveryRow(message)}</div></article>`
      + (opts.original ? "" : threadHTML(message));
  }

  /* ================= 页面各块 ================= */
  let originalsOpen = false;
  function renderFeed() {
    items.length = 0;
    const originals = `<div class="cx-originals"><button type="button" class="cx-originals-toggle" id="originals-toggle" aria-expanded="${originalsOpen}">${icon("chev", "xs cx-originals-chev")}已整理的 ${DIGEST.total_message_count} 条原始发言</button>`
      + (originalsOpen ? `<div class="cx-original-list">${ORIGINALS.map(m => messageHTML(m, {original:true})).join("")}<button type="button" class="cx-btn cx-ghost">加载更早的</button></div>` : "") + `</div>`;
    $("feed").innerHTML = originals + `<div class="cx-divider cx-unread" id="unread"><span>新发言 · 要点之后 ${MESSAGES.length} 条</span></div>` + MESSAGES.map(m => messageHTML(m)).join("");
    renderAside();
  }
  function renderPin() {
    const open = !window.matchMedia("(max-width:760px)").matches;
    $("pin").innerHTML = `<details class="cx-digest"${open ? " open" : ""}><summary>${icon("sparkle", "sm")}<span class="cx-digest-title">要点</span><span class="cx-digest-meta">截至 ${hm(DIGEST.created_at)} · 累计整理 ${DIGEST.total_message_count} 条</span>${icon("chev", "xs cx-digest-chev")}</summary>`
      + `<div class="cx-digest-body"><ul>${DIGEST.body.map(b => `<li>${b}</li>`).join("")}</ul><p class="cx-digest-foot">Claude Code · ${DIGEST.model} · $${DIGEST.cost_usd.toFixed(4)}</p></div></details>`;
  }
  function pendingList() {
    const list = [];
    for (const m of MESSAGES) for (const [kind, arr] of [["mention", m.mentions], ["notified", m.notified]]) for (const it of arr) {
      const s = stateOf(it, m);
      if (s.key === "queued" || s.key === "delivered") list.push({it, m, s, kind});
    }
    const rank = x => x.s.key === "queued" ? (x.s.stale ? 0 : 1) : 2;
    return list.sort((a, b) => rank(a) - rank(b) || b.s.age - a.s.age);
  }
  function renderAside() {
    $("members").innerHTML = Object.entries(LEADERS).map(([name, l]) => `<li title="${esc(name)} · ${l.runtime} · ${esc(l.presenceText)}">${avatar(name, 24)}<span class="cx-member-txt"><span class="cx-member-name">${esc(name)}<span class="cx-rt" data-rt="${l.runtime}">${l.runtime}</span></span><span class="cx-presence" data-p="${l.presence}"><i aria-hidden="true"></i>${l.presenceText}</span></span></li>`).join("");
    const pend = pendingList();
    // 按 leader 合并：每人一行，展示最该关注的那条（排序已把“排队最久”放前面）。
    const groups = new Map();
    for (const x of pend) { if (!groups.has(x.it.name)) groups.set(x.it.name, []); groups.get(x.it.name).push(x); }
    $("pending-count").textContent = groups.size;
    $("pending").innerHTML = [...groups.values()].map(list => {
      const x = list[0];
      const idx = items.findIndex(b => b.item === x.it && b.message === x.m);
      const ui = x.s.key === "queued" && x.s.stale ? STATE_UI.stale : STATE_UI[x.s.key];
      const age = x.s.key === "queued" ? `排队 ${x.s.age} 分钟，还没收到` : `已送达 ${x.s.age} 分钟，未回复`;
      const more = list.length > 1 ? ` · 共 ${list.length} 条` : "";
      return `<li class="cx-pend" data-state="${x.s.key}"${x.s.stale ? " data-stale" : ""}>`
        + `<button type="button" class="cx-pend-main" data-jump="${x.m.id}" aria-label="跳到 #${x.m.id}：${esc(x.it.name)} ${esc(age)}${esc(more)}">`
        + `<span class="cx-pend-ic">${icon(ui.icon, "sm")}</span><span class="cx-pend-txt"><strong>${esc(x.it.name)}<span class="cx-id">#${x.m.id}${esc(more)}</span></strong><small>${esc(age)}</small></span></button>`
        + `<button type="button" class="cx-dl-go cx-pend-go" data-go="badge:${idx}" aria-label="打开 ${esc(x.it.name)} 的私聊">${icon("chat", "xs")}</button></li>`;
    }).join("");
    $("legend").innerHTML = LEGEND.map(l => {
      const fake = {name:l.name, delivery:l.state, age:l.age, stale:l.stale};
      return `<li>${badgeHTML(fake, null, "mention")}<span>${esc(l.text)}</span></li>`;
    }).join("");
    const stale = pend.filter(x => x.s.stale);
    const first = pend[0];
    $("pendingbar").innerHTML = pend.length === 0 ? "" : `<span class="cx-pb-ic${stale.length ? " is-stale" : ""}">${icon(stale.length ? "clock-alert" : "hourglass", "sm")}</span>`
      + `<span class="cx-pb-txt"><strong>${groups.size} 位 leader 待回应</strong><span>${first ? `${esc(first.it.name)} ${first.s.key === "queued" ? `排队 ${first.s.age} 分钟` : "已送达未回复"}` : ""}</span></span><span class="cx-pb-more">投递追踪${icon("chev", "xs")}</span>`;
    $("pendingbar").dataset.stale = stale.length ? "1" : "0";
  }

  /* ================= 发言框 ================= */
  let replyTo = 42;
  const selected = new Set(["前端"]);
  let allLeaders = false;
  function renderComposer() {
    const bar = $("replybar");
    if (replyTo == null) { bar.hidden = true; bar.innerHTML = ""; }
    else {
      const m = byId.get(replyTo);
      const mentioned = selected.has(m.speaker_name) || allLeaders;
      bar.hidden = false;
      bar.innerHTML = `${icon("reply", "sm")}<button type="button" class="cx-replybar-quote" data-jump="${m.id}"><span>回复 <span class="cx-id">#${m.id}</span> <strong>${esc(m.speaker_name)}</strong></span><span class="cx-replybar-text">${esc(plain(m.body))}</span></button>`
        + (m.speaker_name !== "用户" && !mentioned ? `<span class="cx-replybar-note">会通知 ${esc(m.speaker_name)}</span>` : "")
        + `<button type="button" class="cx-icon-btn cx-replybar-x" id="reply-cancel" aria-label="取消回复">${icon("x", "sm")}</button>`;
    }
    $("targets").innerHTML = `<span class="cx-target-label">点名</span>`
      + `<button type="button" class="cx-target${allLeaders ? " selected" : ""}" data-to="*" aria-pressed="${allLeaders}">@所有 leader</button>`
      + Object.entries(LEADERS).map(([name, l]) => {
        const on = allLeaders || selected.has(name);
        const warn = l.presence !== "idle" ? `${name}${l.presenceText}，点名会先排队` : `${name}空闲，点名会立刻送达`;
        return `<button type="button" class="cx-target${on ? " selected" : ""}" data-to="${esc(name)}" aria-pressed="${on}" title="${esc(warn)}" aria-label="点名 ${esc(name)}（${esc(l.presenceText)}）"><i class="cx-tdot" data-p="${l.presence}" aria-hidden="true"></i>@${esc(name)}${l.presence !== "idle" ? `<small>${esc(l.presenceText)}</small>` : ""}</button>`;
      }).join("");
    const input = $("input");
    input.placeholder = replyTo == null ? "和所有 leader 说…" : `回复 ${byId.get(replyTo).speaker_name}…`;
    $("send").disabled = input.value.trim() === "";
  }

  /* ================= 说明浮层（悬停 + 点击/触摸） ================= */
  const pop = $("pop"), backdrop = $("backdrop");
  let popFor = null, pinned = false, hoverTimer = 0, hideTimer = 0;
  const isSheet = () => window.matchMedia("(max-width:760px)").matches;
  function explain(b) {
    const {item, message, kind, s} = b;
    const l = LEADERS[item.name] || {presenceText:"状态未知"};
    const where = message ? `#${message.id} ${message.speaker_name}${kind === "notified" ? "的回复" : "的点名"} · ${hm(message.created_at)}` : "示例";
    const why = kind === "notified" ? `这条发言回复了 ${item.name} 的发言，虽然没写 @，Crew 也会通知它。` : "";
    let text, facts = [];
    if (s.key === "queued") {
      text = `${item.name}现在${l.presenceText}，这条${kind === "notified" ? "通知" : "点名"}在排队，它空下来时才会送进它的输入。<b>它还没看到</b>。`;
      facts.push(["已排队", `${s.age} 分钟${s.stale ? "（超过 10 分钟）" : ""}`]);
      if (s.stale) text += " 等得有点久了，可以打开私聊看看它在忙什么，必要时在私聊里直接说。";
    } else if (s.key === "delivered") {
      text = `已经送进 ${item.name} 的输入，它<b>还没在交流区回复</b>。送达不代表已经读完或处理完。`;
      facts.push(["已等待", `${s.age} 分钟`]);
    } else if (s.key === "replied") {
      const rid = item.reply_id;
      text = `${item.name} 已经回复${rid ? `：#${rid}` : ""}${rid && byId.get(rid) && byId.get(rid).auto ? "（回复内容在私聊里）" : ""}。`;
    } else if (s.key === "archived") {
      text = `消息已存进 ${item.name} 的私聊记录，但<b>不会叫醒它</b>；它下次被唤醒时能看到。`;
    } else if (s.key === "failed") {
      text = "这条消息<b>没有送到</b>，需要重发或换人。";
      facts.push(["原因", "会话已被停止（leader 已退役）"]);
    }
    facts.unshift(["来源", where]);
    const ui = s.key === "queued" && s.stale ? STATE_UI.stale : STATE_UI[s.key];
    const idx = items.indexOf(b);
    const extra = s.key === "replied" && item.reply_id ? `<button type="button" class="cx-btn" data-jump="${item.reply_id}">跳到回复 #${item.reply_id}</button>` : "";
    return `<div class="cx-pop-head" data-state="${s.key}"${s.stale ? " data-stale" : ""}><span class="cx-pop-ic">${icon(ui.icon, "sm")}</span><div><strong>${esc(ui.title)}</strong><small>${kind === "notified" ? "回复通知" : "点名"} ${esc(item.name)}</small></div>`
      + `<button type="button" class="cx-icon-btn cx-pop-x" aria-label="关闭说明">${icon("x", "sm")}</button></div>`
      + `<p class="cx-pop-text">${why ? `${esc(why)} ` : ""}${text}</p>`
      + `<dl class="cx-pop-facts">${facts.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>`
      + `<div class="cx-pop-acts">${extra}<button type="button" class="cx-btn cx-primary" data-go="badge:${idx}">${icon("chat", "xs")}打开 ${esc(item.name)} 的私聊</button></div>`;
  }
  function showPop(anchor, b, pin) {
    clearTimeout(hideTimer);
    popFor = anchor; pinned = pin;
    pop.innerHTML = explain(b);
    pop.hidden = false;
    const sheet = isSheet();
    pop.classList.toggle("is-sheet", sheet);
    backdrop.hidden = !sheet;
    pop.setAttribute("aria-label", anchor.getAttribute("aria-label") || "投递状态说明");
    anchor.setAttribute("aria-expanded", "true");
    if (!sheet) {
      const r = anchor.getBoundingClientRect(), w = pop.offsetWidth, h = pop.offsetHeight;
      let left = Math.min(Math.max(8, r.left), window.innerWidth - w - 8);
      let top = r.bottom + 6;
      if (top + h > window.innerHeight - 8) top = r.top - h - 6;
      pop.style.left = `${left}px`; pop.style.top = `${Math.max(8, top)}px`;
    } else { pop.style.left = ""; pop.style.top = ""; }
    if (pin) pop.querySelector(".cx-pop-acts .cx-primary").focus({preventScroll:true});
  }
  function hidePop() {
    if (popFor) popFor.removeAttribute("aria-expanded");
    pop.hidden = true; backdrop.hidden = true; popFor = null; pinned = false;
  }
  document.addEventListener("mouseover", e => {
    if (isSheet() || window.matchMedia("(hover:none)").matches) return;
    const a = e.target.closest("[data-badge]");
    if (a && a !== popFor) { clearTimeout(hoverTimer); hoverTimer = setTimeout(() => { if (!pinned) showPop(a, items[a.dataset.badge], false); }, 220); }
    if (a || e.target.closest("#pop")) clearTimeout(hideTimer);
  });
  document.addEventListener("mouseout", e => {
    const a = e.target.closest("[data-badge]");
    if (a) clearTimeout(hoverTimer);
    if (!pinned && (a || e.target.closest("#pop")) && !(e.relatedTarget && (e.relatedTarget.closest("#pop") || e.relatedTarget === popFor))) hideTimer = setTimeout(hidePop, 160);
  });
  backdrop.addEventListener("click", hidePop);
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !pop.hidden) { const a = popFor; hidePop(); if (a) a.focus(); } });

  /* ================= 跳转与私聊直达 ================= */
  function flash(id) {
    const node = document.querySelector(`.cx-msg[data-id="${id}"]`);
    if (!node) { toast(`#${id} 已整理进要点卡，展开“已整理的原始发言”可以看到`); return; }
    node.scrollIntoView({block:"center", behavior:"smooth"});
    node.classList.remove("cx-flash"); void node.offsetWidth; node.classList.add("cx-flash");
  }
  function landing(spec) {
    const [type, key] = spec.split(":");
    if (type === "detail") { const m = byId.get(+key); return [m.speaker_name, `定位到 #${m.id} 发言时刻（${hm(m.created_at)}）的完整内容`]; }
    if (type === "auto") { const m = byId.get(+key); return [m.speaker_name, `定位到它对「${m.reply.speaker_name} #${m.reply.id}」的回复`]; }
    const b = items[+key]; const n = b.item.name, mid = b.message ? `#${b.message.id}` : "示例";
    return [n, {
      queued: `跳到底部的“待送达：交流区 ${mid}”标记（对方还没收到）`,
      delivered: `定位到送达的“交流区 ${mid}”输入处`,
      replied: `定位到“交流区 ${mid}”及其后的回复`,
      archived: "定位到这条归档消息",
      failed: "定位到失败的那条消息，可在原处重发",
    }[b.s.key]];
  }
  function go(spec) {
    const [who, where] = landing(spec);
    hidePop(); closeAside();
    toast(`（原型）打开「${who}」私聊 · ${where}`);
  }
  let toastTimer = 0;
  function toast(text) {
    const t = $("toast"); t.textContent = text; t.classList.add("show");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("show"), 3200);
  }

  /* ================= 投递追踪抽屉（≤1023） ================= */
  function openAside() { $("aside").classList.add("is-open"); backdrop.hidden = false; $("pendingbar").setAttribute("aria-expanded", "true"); }
  function closeAside() { if (!$("aside").classList.contains("is-open")) return; $("aside").classList.remove("is-open"); backdrop.hidden = true; $("pendingbar").setAttribute("aria-expanded", "false"); }
  backdrop.addEventListener("click", closeAside);

  /* ================= 事件分发 ================= */
  document.addEventListener("click", e => {
    const t = e.target;
    const badge = t.closest("[data-badge]");
    if (badge) { if (popFor === badge && pinned) hidePop(); else showPop(badge, items[badge.dataset.badge], true); return; }
    const goEl = t.closest("[data-go]"); if (goEl) { go(goEl.dataset.go); return; }
    const jumpEl = t.closest("[data-jump]"); if (jumpEl) { hidePop(); closeAside(); flash(+jumpEl.dataset.jump); return; }
    if (t.closest(".cx-pop-x")) { hidePop(); return; }
    if (t.closest(".cx-aside-close")) { closeAside(); return; }
    if (t.closest("#pendingbar")) { openAside(); return; }
    const th = t.closest("[data-thread]");
    if (th) { const id = +th.dataset.thread; threadOpen.has(id) ? threadOpen.delete(id) : threadOpen.add(id); renderFeed(); return; }
    if (t.closest("#originals-toggle")) { originalsOpen = !originalsOpen; renderFeed(); return; }
    const rp = t.closest("[data-reply]"); if (rp) { replyTo = +rp.dataset.reply; renderComposer(); $("input").focus(); return; }
    if (t.closest("#reply-cancel")) { replyTo = null; renderComposer(); return; }
    const to = t.closest("[data-to]");
    if (to) {
      const n = to.dataset.to;
      if (n === "*") { allLeaders = !allLeaders; selected.clear(); }
      else if (allLeaders) { allLeaders = false; selected.clear(); for (const k of Object.keys(LEADERS)) if (k !== n) selected.add(k); }
      else selected.has(n) ? selected.delete(n) : selected.add(n);
      renderComposer(); return;
    }
    if (t.closest("#digest-btn")) { toast("（原型）开始整理：把要点之后的发言收进要点卡"); return; }
    if (!pop.hidden && pinned && !t.closest("#pop")) hidePop();
  });
  $("input").addEventListener("input", () => {
    const i = $("input"); i.style.height = "auto"; i.style.height = `${Math.min(i.scrollHeight, 180)}px`;
    $("send").disabled = i.value.trim() === "";
  });
  $("composer").addEventListener("submit", e => { e.preventDefault(); toast("（原型）已发到交流区；被点名的 leader 会显示“排队中/已送达”"); });

  /* ================= 启动 ================= */
  $("rail-leaders").innerHTML = Object.entries(LEADERS).map(([n, l]) => `<button type="button" class="cx-rail-leader" aria-label="${esc(n)}（${esc(l.presenceText)}）">${avatar(n, 28)}<i class="cx-tdot" data-p="${l.presence}" aria-hidden="true"></i></button>`).join("");
  renderPin(); renderFeed(); renderComposer();
  const params = new URLSearchParams(location.search);
  const scroll = $("scroll");
  const anchor = params.get("at");
  requestAnimationFrame(() => {
    // 默认落在第一条未读（“新发言”分隔线）；?at=bottom 到底部，?at=<id> 到指定发言。
    if (anchor === "bottom") scroll.scrollTop = scroll.scrollHeight;
    else { const n = anchor ? document.querySelector(`.cx-msg[data-id="${anchor}"]`) : $("unread"); if (n) scroll.scrollTop = n.offsetTop - 6; }
    document.documentElement.dataset.ready = "1";
  });
  window.CX = {flash, openAside, showBadge: i => { const a = document.querySelectorAll("[data-badge]")[i]; if (a) showPop(a, items[a.dataset.badge], true); }};
})();
