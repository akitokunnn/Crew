"use strict";
(() => {
  const escape = value => String(value).replace(/[&<>"']/g, character => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[character]);
  const PATHS = {
    msg:'<path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.5 3.5V17A2.5 2.5 0 0 1 4 14.5z"/>',
    chat:'<path d="M5 5.5h14v10H11l-4 3.2v-3.2H5z"/><path d="M9 9.5h6M9 12h3.5"/>',
    clock:'<circle cx="12" cy="12" r="8"/><path d="M12 7.5V12l3 2"/>',
    "clock-alert":'<circle cx="12" cy="12" r="8"/><path d="M12 7.5v5"/><path d="M12 16h.01" stroke-width="2.4"/>',
    check:'<path d="m5.5 12.5 4 4 9-9"/>', replied:'<path d="m2.5 12.5 4 4 9-9"/><path d="m11.5 16 1 .5 9-9"/>',
    reply:'<path d="M9.5 7 5 11.5 9.5 16"/><path d="M5 11.5h9a5 5 0 0 1 5 5V18"/>',
    archive:'<rect x="4" y="5" width="16" height="4" rx="1"/><path d="M5.5 9v9a1 1 0 0 0 1 1h11a1 1 0 0 0 1-1V9M10 13h4"/>',
    alert:'<path d="M12 4 21 19.5H3z"/><path d="M12 10v4.5"/><path d="M12 17h.01" stroke-width="2.4"/>',
    doc:'<path d="M7 3.5h7l4 4V20a.5.5 0 0 1-.5.5h-10A.5.5 0 0 1 7 20z"/><path d="M13.5 3.5V8H18M9.5 12h6M9.5 15h6M9.5 18h3.5"/>',
    back:'<path d="m14.5 5.5-6.5 6.5 6.5 6.5"/>', x:'<path d="m6.5 6.5 11 11M17.5 6.5l-11 11"/>',
    room:'<rect x="3.5" y="5" width="17" height="14" rx="2.5"/><path d="M3.5 9.5h17M8 14h3M8 16.5h6"/>', send:'<path d="m5 11 7-7 7 7M12 4v16"/>',
    thread:'<path d="M6 4v9a3 3 0 0 0 3 3h9"/><path d="m15 13 3 3-3 3"/>', sparkle:'<path d="M12 4.5 13.6 10l5.4 2-5.4 2L12 19.5 10.4 14 5 12l5.4-2z"/>',
    blocked:'<path d="M8.7 3.5h6.6l5.2 5.2v6.6l-5.2 5.2H8.7l-5.2-5.2V8.7z"/><path d="M8.5 12h7"/>',
    chev:'<path d="m9 6 6 6-6 6"/>', hourglass:'<path d="M7 4h10M7 20h10M8 4c0 4.5 8 5.5 8 8s-8 3.5-8 8M16 4c0 4.5-8 5.5-8 8s8 3.5 8 8"/>'
  };
  const icon = (name, cls = "") => `<svg class="i ${cls}" viewBox="0 0 24 24" aria-hidden="true">${PATHS[name]}</svg>`;
  const STALE_MINUTES = 10;
  const HM = {hour:"2-digit", minute:"2-digit", hour12:false};
  const time = at => new Date(at).toLocaleString("zh-CN", {month:"numeric", day:"numeric", ...HM});
  const clock = at => new Date(at).toDateString() === new Date().toDateString() ? new Date(at).toLocaleTimeString("zh-CN", HM) : time(at);
  const minutes = at => Math.max(0, Math.floor((Date.now() - new Date(at)) / 60000));
  const plain = text => String(text).replace(/\[([^\]]*)\]\([^)]*\)/g, "$1").replace(/\*\*|`+|^\s*(?:#+|>)\s*/gm, "").replace(/\s+/g, " ").trim().slice(0, 200);
  const narrow = () => window.matchMedia("(max-width:760px)").matches;
  const put = (node, html) => { if (node.crewHTML !== html) { node.crewHTML = html; node.innerHTML = html; } };
  const STATES = {
    queued:{icon:"clock", label:"排队中", title:"已排队 · 对方还没收到", says:"已排队，对方还没收到"},
    delivered:{icon:"check", label:"已送达", title:"已送达 · 还没回复", says:"已送达，还没回复"},
    replied:{icon:"replied", label:"已回复", title:"已回复", says:"已回复"},
    archived:{icon:"archive", label:"仅归档", title:"仅归档 · 不唤醒", says:"仅归档，不唤醒"},
    failed:{icon:"alert", label:"失败", title:"投递失败", says:"投递失败"}};
  const LEGEND = [[{key:"queued", stale:false}, "已排队：对方忙、离线或被停止，还没收到"], [{key:"queued", stale:true, age:12}, "排队超过 10 分钟：值得去看看对方在干什么"],
    [{key:"delivered"}, "已送达对方输入，还没回复"], [{key:"replied"}, "对方已在交流区回复"]];
  let active = null;
  const current = context => active === context;
  const atBottom = context => context.scroll.scrollHeight - context.scroll.scrollTop - context.scroll.clientHeight < 48;
  const all = context => context.originalMessages.concat(context.messages);
  function stateOf(item) {
    const age = item.delivery === "queued" ? minutes(item.queued_at) : item.delivered_at ? minutes(item.delivered_at) : null;
    return {key:item.delivery, age, stale:item.delivery === "queued" && age >= STALE_MINUTES};
  }
  const look = s => s.stale ? {icon:"clock-alert", label:`排队 ${s.age} 分钟`, title:"排队较久 · 对方还没收到", says:`已排队 ${s.age} 分钟，对方还没收到`} : STATES[s.key];
  function presence(leader) {
    if (!leader || leader.where === "offline") return {key:"offline", text:"离线", short:"离线"};
    if (leader.where === "vscode") return {key:"external", text:"外部窗口运行", short:"外部窗口"};
    return leader.busy ? {key:"working", text:"在跑任务", short:"在跑任务"} : {key:"online", text:"在线", short:""};
  }
  const avatar = (context, message, size) => message.speaker_session_id === "user"
    ? `<span class="av round cx-av-user" style="--s:${size}px" aria-hidden="true">${escape(message.speaker_name.slice(0, 1))}</span>` : context.avatar(message.speaker_name, size, true);
  function badgeHTML(message, item, kind) {
    const s = stateOf(item), what = kind === "notified" ? "回复通知" : "点名", name = `${kind === "notified" ? "" : "@"}${escape(item.name)}`;
    if (s.key === "none") return `<span class="cx-dl" data-state="none" title="不投递（如点名自己）"><span class="cx-dl-main cx-dl-static"><span class="cx-dl-name">${name}</span></span></span>`;
    const ui = look(s), key = escape(`${message.id}:${kind}:${item.session_id}`);
    return `<span class="cx-dl" data-state="${escape(s.key)}"${s.stale ? " data-stale" : ""}><button type="button" class="cx-dl-main" data-badge="${key}" aria-haspopup="dialog" aria-controls="cx-pop" aria-label="${escape(`${what} ${item.name}：${ui.says}。点开看说明`)}">`
      + `<span class="cx-dl-name">${name}</span><span class="cx-dl-sep" aria-hidden="true"></span>${icon(ui.icon, "xs")}<span class="cx-dl-text">${escape(ui.label)}</span></button>`
      + `<button type="button" class="cx-dl-go" data-go="${key}" aria-label="${escape(`打开 ${item.name} 的私聊并定位到这条${what}`)}" title="${escape(`打开 ${item.name} 的私聊`)}">${icon("chat", "xs")}</button></span>`;
  }
  function deliveryHTML(message) {
    const group = (label, kind, title) => message[kind].length === 0 ? "" : `<div class="cx-dl-group"><span class="cx-dl-label"${title}>${label}</span><span class="cx-dl-list">${message[kind].map(item => badgeHTML(message, item, kind)).join("")}</span></div>`;
    const html = group("点名", "mentions", "") + group("回复通知", "notified", ' title="没写 @，但回复了它的发言，Crew 也会通知它"');
    return html && `<div class="cx-dl-row">${html}</div>`;
  }
  function highlight(html, message) {
    const names = ["所有 leader", ...message.mentions.map(item => escape(item.name))].sort((a, b) => b.length - a.length).map(name => name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    return html.replace(new RegExp(`(<[^>]*>)|@(?:${names.join("|")})`, "g"), (match, tag) => tag || `<span class="cx-at">${match}</span>`);
  }
  function descendants(context, id, depth = 0, out = []) {
    for (const item of context.children.get(id) || []) { out.push({message:item, depth}); descendants(context, item.id, depth + 1, out); }
    return out;
  }
  function threadHTML(context, message) {
    const rows = descendants(context, message.id);
    if (rows.length < 2) return "";
    const open = context.threads.has(message.id), people = [...new Map(rows.map(row => [row.message.speaker_session_id, row.message])).values()];
    const head = `<button type="button" class="cx-thread-toggle" data-channel-thread="${message.id}" aria-expanded="${open}">${icon("thread", "sm")}<span>脉络 · ${rows.length} 条回复</span>`
      + `<span class="cx-thread-avs" aria-hidden="true">${people.slice(0, 4).map(item => avatar(context, item, 18)).join("")}</span><span class="cx-thread-who">${escape(people.map(item => item.speaker_name).join("、"))}</span>${icon("chev", "xs cx-thread-chev")}</button>`;
    const tree = open ? `<ol class="cx-thread-tree">${rows.map(({message:item, depth}) => `<li${depth ? ' class="is-child"' : ""} style="--d:${Math.min(depth, 4)}"><button type="button" class="cx-thread-row" data-channel-jump="${item.id}" aria-label="跳到 #${item.id} ${escape(item.speaker_name)} 的发言">`
      + `${avatar(context, item, 18)}<span class="cx-thread-name">${escape(item.speaker_name)}</span><span class="cx-id">#${item.id}</span><span class="cx-thread-text${item.auto ? " is-auto" : ""}">${escape(item.auto ? "回复在私聊里" : plain(item.body))}</span><time>${escape(clock(item.created_at))}</time></button></li>`).join("")}</ol>` : "";
    return `<div class="cx-thread wr-thread-up${open ? " is-open" : ""}${message.speaker_session_id === "user" ? " is-user" : ""}">${head}${upgradeHTML(context, message, rows)}${tree}</div>`;
  }
  /* ---------- 作战室入口：脉络升级、开房 / 收尾公告、页头列表 ---------- */
  // 脉络参与者：发言人、被点名、被回复通知的 leader（只留本工作区在用的）。
  function participants(context, message, rows) {
    const ids = new Set();
    for (const post of [message, ...rows.map(row => row.message)]) { ids.add(post.speaker_session_id); for (const item of post.mentions.concat(post.notified)) ids.add(item.session_id); }
    return context.state.leaders.filter(item => ids.has(item.session_id));
  }
  function upgradeHTML(context, message, rows) {
    const room = context.rooms.find(item => item.origin_message_id === message.id);
    if (room) return `<button type="button" class="wr-upgrade is-done" data-channel-room="${room.id}">${icon("room", "sm")}<span>已升级：${escape(room.title)}</span></button>`;
    const people = participants(context, message, rows);
    if (message.room || people.length < 2) return "";
    const hint = rows.length >= 4 || people.length >= 3 ? `<span class="wr-upgrade-hint">${icon("alert", "xs")}${rows.length} 条回复还没收口，${people.length} 位 leader 在聊</span>` : "";
    return `<button type="button" class="wr-upgrade" data-channel-upgrade="${message.id}">${icon("room", "sm")}<span>升级为作战室</span></button>${hint}`;
  }
  const lasted = (from, to) => { const m = Math.max(1, Math.round((new Date(to) - new Date(from)) / 60000)); return m < 60 ? `${m} 分钟` : `${Math.floor(m / 60)} 小时${m % 60 ? ` ${m % 60} 分` : ""}`; };
  function announceHTML(context, message) {
    const ref = message.room, info = context.rooms.find(item => item.id === ref.id), opened = ref.event === "opened", live = (info ? info.status : ref.status) === "open";
    const by = `${avatar(context, message, 14)}${escape(message.speaker_name)} ${opened ? "开了作战室" : "收尾了作战室"} · <time datetime="${escape(message.created_at)}">${escape(clock(message.created_at))}</time>${opened && info && info.origin_message_id ? ` · 从 #${info.origin_message_id} 升级` : ""}`;
    const status = live ? '<span class="wr-status" data-s="open"><span class="dot working" aria-hidden="true"></span>进行中</span>' : `<span class="wr-status" data-s="closed">${icon("archive", "xs")}已收尾</span>`;
    const text = info ? (opened ? `目标：${escape(info.goal)}` : `结论：${escape(info.summary)}`) : escape(message.body);
    const facts = info === undefined ? "" : `<span class="cx-thread-avs" aria-hidden="true">${info.members.slice(0, 5).map(item => context.avatar(item.crew_name, 18, true)).join("")}</span>`
      + `<span>${opened ? `${info.members.length} 位成员 · 牵头 ${escape(info.lead_name)}` : `${info.closed_at ? `用时 ${lasted(info.created_at, info.closed_at)} · ` : ""}${info.items.done} 项完成`}</span>`
      + `${live && info.items.blocked ? `<span class="wr-chip" data-s="blocked">${icon("blocked", "xs")}${info.items.blocked} 阻塞</span>` : ""}${live && info.open_decisions ? `<span class="wr-dc-state" data-s="pending">${icon("hourglass", "xs")}${info.open_decisions} 待拍板</span>` : ""}`;
    return `<div class="wr-ann-row" data-channel-id="${message.id}"><span class="av round" style="--s:28px;--tint:var(--accent)" aria-hidden="true">${icon("room", "sm")}</span><div class="wr-announce" data-s="${live ? "open" : "closed"}">`
      + `<div class="wr-ann-top"><span class="wr-title-ic" aria-hidden="true">${icon(live ? "room" : "archive")}</span><div class="wr-ann-t"><small>${by}</small><strong>${escape(ref.title)}</strong></div>${status}</div>`
      + `<p class="wr-ann-goal">${text}</p><div class="wr-ann-foot">${facts}<button type="button" class="cx-btn${live ? " cx-primary" : ""}" data-channel-room="${ref.id}">${live ? "进入作战室" : "查看（只读）"}${icon("chev", "xs")}</button></div></div></div>`;
  }
  function roomsHTML(context) {
    const open = context.rooms.filter(item => item.status === "open").length;
    const list = context.rooms.map(item => {
      const live = item.status === "open";
      const sub = live ? `<span class="dot working" aria-hidden="true"></span>${item.members.length} 人${item.items.blocked ? ` · ${icon("blocked", "xs")}${item.items.blocked} 阻塞` : ""}${item.open_decisions ? ` · ${icon("hourglass", "xs")}${item.open_decisions} 待拍板` : ""}`
        : `已收尾 · ${escape(time(item.closed_at))}`;
      return `<button type="button" class="wr-side-room${live ? "" : " is-closed"}" data-channel-room="${item.id}" aria-label="${escape(`${item.title}，${live ? "进行中" : "已收尾"}${item.unread ? `，${item.unread} 条未读` : ""}${item.open_decisions ? `，${item.open_decisions} 张拍板卡待你答复` : ""}`)}">`
        + `<span class="wr-title-ic" aria-hidden="true">${icon(live ? "room" : "archive")}</span><span class="wr-side-txt"><strong>${escape(item.title)}</strong><small>${sub}</small></span>${item.unread ? `<span class="b-unread">${item.unread}</span>` : ""}</button>`;
    }).join("");
    return `<div class="wr-rooms-head">作战室 <span>${open} 进行中</span></div>${list || '<p class="wr-rooms-empty">还没有作战室。同一件事要 3 位以上 leader 一起推时开作战室：在一条脉络旁点“升级为作战室”，或让 leader 自己开。</p>'}`;
  }
  function renderRoomButton(context) {
    const open = context.rooms.filter(item => item.status === "open").length;
    put(context.roomButton, `${icon("room", "sm")}<span class="cx-room-label">作战室</span>${open ? `<span class="cx-count">${open}</span>` : ""}`);
    context.roomButton.setAttribute("aria-label", `作战室，${open} 个进行中`);
  }
  async function loadRooms(context) {
    try {
      const result = await context.api("/api/op/war_room.list");
      if (!current(context)) return;
      context.rooms = result.rooms; renderRoomButton(context);
      if (!context.loading) renderMessages(context);
      if (context.roomsOpen) put(context.host.querySelector(".wr-rooms"), roomsHTML(context));
    } catch (error) { if (current(context)) context.toast(error.message); }
  }
  function toggleRooms(context, open) {
    const pop = context.host.querySelector(".wr-rooms");
    context.roomsOpen = open; pop.hidden = !open; context.roomButton.setAttribute("aria-expanded", String(open));
    if (!open) return;
    put(pop, roomsHTML(context));
    const rect = context.roomButton.getBoundingClientRect();
    pop.style.left = `${Math.max(8, Math.min(rect.right - pop.offsetWidth, window.innerWidth - pop.offsetWidth - 8))}px`; pop.style.top = `${rect.bottom + 6}px`;
    pop.querySelector("button")?.focus({preventScroll:true});
    loadRooms(context);
  }
  // 升级表单：成员默认是脉络参与者，牵头默认是起头的 leader（起头是用户时取第一位）。
  function openUpgrade(context, id) {
    const message = all(context).find(item => item.id === Number(id)), people = participants(context, message, descendants(context, message.id));
    context.upgrade = {message, members:people.map(item => item.session_id), lead:(people.find(item => item.session_id === message.speaker_session_id) || people[0]).session_id};
    const layer = context.host.querySelector(".wr-layerbox");
    layer.innerHTML = `<form class="wr-dialog wr-up-form" role="dialog" aria-modal="true" aria-labelledby="wr-up-title"><h2 id="wr-up-title">${icon("room")}升级为作战室</h2>`
      + `<p>从 #${message.id} 这条脉络升级。原脉络留在交流区，作战室顶部引用它；交流区会出现一条开房公告。</p>`
      + `<label class="wr-field"><span>名字</span><input name="title" required maxlength="30" value="${escape(plain(message.body).slice(0, 24))}"></label>`
      + '<label class="wr-field"><span>目标（一句话）</span><input name="goal" required maxlength="200" placeholder="几位 leader 要一起推到什么结果"></label>'
      + '<div class="wr-up-label">成员 <small>脉络里发言、被点名、被回复的 leader 已默认加入；点名字设为牵头，牵头负责收口、发拍板卡和收尾</small></div><div class="wr-up-members"></div>'
      + `<p class="wr-dialog-error" role="alert"></p><div class="wr-dialog-acts"><button type="button" class="cx-btn" data-up-cancel>取消</button><button type="submit" class="cx-btn cx-primary">${icon("room", "sm")}开房</button></div></form>`;
    renderUpgrade(context); layer.hidden = false;
    layer.querySelector("input[name=goal]").focus();
    layer.querySelector("form").onsubmit = event => { event.preventDefault(); submitUpgrade(context, event.target); };
  }
  function renderUpgrade(context) {
    const up = context.upgrade, chosen = context.state.leaders.filter(item => up.members.includes(item.session_id)), others = context.state.leaders.filter(item => !up.members.includes(item.session_id));
    context.host.querySelector(".wr-up-members").innerHTML = chosen.map(item => {
      const lead = item.session_id === up.lead;
      return `<span class="wr-up-chip${lead ? " is-lead" : ""}"><button type="button" class="wr-up-pick" data-up-lead="${escape(item.session_id)}" aria-pressed="${lead}" title="${lead ? "牵头" : "设为牵头"}">${context.avatar(item.name, 18, true)}${escape(item.name)}${lead ? '<span class="wr-lead">牵头</span>' : ""}</button>`
        + `${lead ? "" : `<button type="button" class="wr-up-x" data-up-remove="${escape(item.session_id)}" aria-label="移出 ${escape(item.name)}">${icon("x", "xs")}</button>`}</span>`;
    }).join("") + (others.length ? `<select class="wr-up-add" data-up-add aria-label="添加成员"><option value="">＋ 添加</option>${others.map(item => `<option value="${escape(item.session_id)}">${escape(item.name)}</option>`).join("")}</select>` : "");
  }
  async function submitUpgrade(context, form) {
    const up = context.upgrade, error = form.querySelector(".wr-dialog-error"), button = form.querySelector("[type=submit]");
    const name = id => context.state.leaders.find(item => item.session_id === id).name;
    if (up.members.length < 2) { error.textContent = "作战室至少要两位 leader"; return; }
    button.disabled = true; error.textContent = "";
    try {
      const room = await context.api("/api/op/war_room.open", {project:context.state.project.name, title:form.elements.title.value.trim(), goal:form.elements.goal.value.trim(),
        members:up.members.map(name), from_message:up.message.id, lead:name(up.lead)});
      if (current(context)) context.openRoom(room.id);
    } catch (err) { error.textContent = err.message; button.disabled = false; }
  }
  function closeUpgrade(context) { const layer = context.host.querySelector(".wr-layerbox"); layer.hidden = true; layer.innerHTML = ""; context.upgrade = null; }
  function messageHTML(context, message, original = false) {
    if (message.room) return announceHTML(context, message);
    const user = message.speaker_session_id === "user", name = escape(message.speaker_name), at = `<time datetime="${escape(message.created_at)}" title="${escape(time(message.created_at))}">${escape(clock(message.created_at))}</time>`;
    if (message.auto) {
      return `<article class="cx-msg cx-auto" data-channel-id="${message.id}">${avatar(context, message, 22)}<div class="cx-auto-body"><span><strong>${name}</strong> 在私聊里回复了 `
        + `${message.reply ? `<button type="button" class="cx-inline-link" data-channel-jump="${message.reply.id}">${escape(message.reply.speaker_name)} #${message.reply.id}</button>` : "这条发言"}</span>`
        + `<span class="cx-auto-meta"><span class="cx-id">#${message.id}</span>${at}</span><button type="button" class="cx-chip-btn" data-channel-detail="${message.id}">${icon("chat", "xs")}<span>看私聊回复</span></button></div></article>`;
    }
    const meta = `<div class="cx-meta"><strong>${name}</strong><span class="cx-id">#${message.id}</span>${at}<button type="button" class="cx-reply-btn" data-channel-reply="${message.id}" aria-label="回复 #${message.id}">${icon("reply", "xs")}<span>回复</span></button></div>`;
    const quote = message.reply ? `<button type="button" class="cx-quote" data-channel-jump="${message.reply.id}" aria-label="跳到被回复的 #${message.reply.id} ${escape(message.reply.speaker_name)} 的发言">`
      + `${icon("reply", "xs")}<span class="cx-id">#${message.reply.id}</span><strong>${escape(message.reply.speaker_name)}</strong><span class="cx-quote-text">${escape(plain(message.reply.excerpt))}</span></button>` : "";
    const detail = !user && message.detail ? `<button type="button" class="cx-detail" data-channel-detail="${message.id}" aria-label="打开 ${name} 的私聊看完整内容"><span class="cx-detail-ic">${icon("doc", "sm")}</span>`
      + `<span class="cx-detail-txt"><strong>完整内容在 ${name} 的私聊里</strong><small>定位到这条发言</small></span>${icon("chev", "xs")}</button>` : "";
    const body = `<div class="${user ? "cx-bubble" : "cx-content"}"><div class="markdown cx-md">${highlight(CrewChat.renderMarkdown(message.body, message.speaker_session_id), message)}</div></div>`;
    return `<article class="cx-msg${user ? " cx-user" : ""}${original ? " cx-original" : ""}" data-channel-id="${message.id}">${avatar(context, message, 28)}<div class="cx-body-col">${meta}${quote}${body}${detail}${deliveryHTML(message)}</div></article>${original ? "" : threadHTML(context, message)}`;
  }
  function pendingItems(context) {
    const list = [];
    for (const message of all(context)) for (const kind of ["mentions", "notified"]) for (const item of message[kind]) {
      const s = stateOf(item);
      if (s.key === "queued" || s.key === "delivered") list.push({message, kind, item, s, key:`${message.id}:${kind}:${item.session_id}`});
    }
    const rank = x => x.s.stale ? 0 : x.s.key === "queued" ? 1 : 2;
    return list.sort((a, b) => rank(a) - rank(b) || (b.s.age ?? -1) - (a.s.age ?? -1));
  }
  function staticBadge(s) {
    const ui = look(s);
    return `<span class="cx-dl" data-state="${s.key}"${s.stale ? " data-stale" : ""} aria-hidden="true"><span class="cx-dl-main cx-dl-static"><span class="cx-dl-name">@leader</span><span class="cx-dl-sep"></span>${icon(ui.icon, "xs")}<span class="cx-dl-text">${ui.label}</span></span><span class="cx-dl-go">${icon("chat", "xs")}</span></span>`;
  }
  function renderAside(context) {
    const pending = pendingItems(context), groups = new Map();
    for (const x of pending) { if (!groups.has(x.item.session_id)) groups.set(x.item.session_id, []); groups.get(x.item.session_id).push(x); }
    put(context.members, context.state.leaders.map(leader => {
      const p = presence(leader), runtime = leader.runtime === "codex" ? "Codex" : "Claude";
      return `<li title="${escape(`${leader.name} · ${runtime} · ${p.text}`)}">${context.avatar(leader.name, 24, true)}<span class="cx-member-txt"><span class="cx-member-name"><span>${escape(leader.name)}</span><span class="cx-rt" data-rt="${runtime}">${runtime}</span></span><span class="cx-presence" data-p="${p.key}"><i aria-hidden="true"></i>${p.text}</span></span></li>`;
    }).join("") || '<li class="cx-none">还没有 leader</li>');
    put(context.pendingCount, String(groups.size));
    put(context.pending, [...groups.values()].map(list => {
      const x = list[0], ui = look(x.s), more = list.length > 1 ? ` · 共 ${list.length} 条` : "";
      const age = x.s.key === "queued" ? `排队 ${x.s.age} 分钟，还没收到` : x.s.age === null ? "已送达，未回复" : `已送达 ${x.s.age} 分钟，未回复`;
      return `<li class="cx-pend" data-state="${x.s.key}"${x.s.stale ? " data-stale" : ""}><button type="button" class="cx-pend-main" data-channel-jump="${x.message.id}" aria-label="${escape(`跳到 #${x.message.id}：${x.item.name} ${age}${more}`)}">`
        + `<span class="cx-pend-ic">${icon(ui.icon, "sm")}</span><span class="cx-pend-txt"><strong><span>${escape(x.item.name)}</span><span class="cx-id">#${x.message.id}${more}</span></strong><small>${age}</small></span></button>`
        + `<button type="button" class="cx-pend-go" data-go="${escape(x.key)}" aria-label="${escape(`打开 ${x.item.name} 的私聊`)}">${icon("chat", "xs")}</button></li>`;
    }).join("") || '<li class="cx-none">没有等待中的点名</li>');
    const stale = pending.some(x => x.s.stale), first = pending[0];
    context.pendingbar.hidden = groups.size === 0;
    context.pendingbar.toggleAttribute("data-stale", stale);
    put(context.pendingbar, groups.size === 0 ? "" : `<span class="cx-pb-ic">${icon(stale ? "clock-alert" : "hourglass", "sm")}</span><span class="cx-pb-txt"><strong>${groups.size} 位 leader 待回应</strong>`
      + `<span>${escape(first.item.name)} ${first.s.key === "queued" ? `排队 ${first.s.age} 分钟` : "已送达未回复"}</span></span><span class="cx-pb-more">投递追踪${icon("chev", "xs")}</span>`);
  }
  function badgeOf(context, key) {
    const [id, kind, ...session] = key.split(":"), message = all(context).find(item => item.id === Number(id));
    return {message, kind, item:message[kind].find(item => item.session_id === session.join(":"))};
  }
  function explain(context, {message, kind, item}) {
    const s = stateOf(item), ui = look(s), what = kind === "notified" ? "回复通知" : "点名", name = escape(item.name);
    const p = presence(context.state.leaders.find(leader => leader.session_id === item.session_id));
    const facts = [["来源", `#${message.id} ${message.speaker_name} 的${kind === "notified" ? "回复" : "点名"} · ${clock(message.created_at)}`], ["对方现在", p.text]];
    let text = kind === "notified" ? `这条发言回复了 ${name} 的发言，虽然没写 @，Crew 也会通知它。` : "";
    if (s.key === "queued") {
      const wait = {external:`${item.name} 在外部窗口运行，下次在那边提示时才会带上这条${what}`, busy:`${item.name} 正在忙，这一轮结束后才会送进它的输入`,
        stopped:`你已停止 ${item.name}，你再给它发话之前不会送达`}[item.waiting] || `${item.name} 空下来时才会送进它的输入`;
      text += `${escape(wait)}。<b>它还没看到</b>。${s.stale ? "等得有点久了，可以打开私聊看看它在忙什么，必要时在私聊里直接说。" : ""}`;
      facts.push(["已排队", `${s.age} 分钟${s.stale ? "（超过 10 分钟）" : ""}`]);
    } else if (s.key === "delivered") {
      text += `${item.via === "hook" ? `已由外部窗口的提示词带给 ${name}` : `已经送进 ${name} 的输入`}，<b>它已经收到，但还没在交流区回复</b>。送达不代表已经读完或处理完。`;
      if (item.delivered_at) facts.push(["送达于", `${clock(item.delivered_at)} · ${s.age} 分钟前`]);
    } else if (s.key === "replied") {
      text += `${name} <b>已经看到并回复</b>${item.reply_id ? `：#${item.reply_id}` : ""}。`;
    } else if (s.key === "archived") text += `消息已存进 ${name} 的私聊记录，但<b>不会叫醒它</b>；它下次被唤醒时能看到。`;
    else text += "这条消息<b>没有送到</b>，需要重发或换人。";
    if (item.via) facts.push(["送达方式", item.via === "hook" ? "外部窗口提示词" : "Crew 输入"]);
    const extra = s.key === "replied" && item.reply_id ? `<button type="button" class="cx-btn" data-channel-jump="${item.reply_id}">跳到回复 #${item.reply_id}</button>` : "";
    return `<div class="cx-pop-head" data-state="${escape(s.key)}"${s.stale ? " data-stale" : ""}><span class="cx-pop-ic">${icon(ui.icon, "sm")}</span><div><strong id="cx-pop-title">${escape(ui.title)}</strong><small>${what} ${name}</small></div>`
      + `<button type="button" class="cx-icon-btn cx-pop-x" aria-label="关闭说明">${icon("x", "sm")}</button></div><p class="cx-pop-text">${text}</p>`
      + `<dl class="cx-pop-facts">${facts.map(([key, value]) => `<dt>${key}</dt><dd>${escape(value)}</dd>`).join("")}</dl>`
      + `<div class="cx-pop-acts">${extra}<button type="button" class="cx-btn cx-primary" data-go="${escape(`${message.id}:${kind}:${item.session_id}`)}">${icon("chat", "xs")}打开 ${name} 的私聊</button></div>`
      + '<p class="cx-pop-note">私聊协作里还有“仅归档”（存进记录、不叫醒）和“失败”（没送到，要重发）两种状态，交流区不会出现。</p>';
  }
  function syncBackdrop(context) { context.backdrop.hidden = !context.asideOpen && !(context.popFor && context.pop.classList.contains("is-sheet")); }
  function showPop(context, anchor, pinned) {
    clearTimeout(context.hideTimer);
    if (context.popFor && context.popFor !== anchor) context.popFor.removeAttribute("aria-expanded");
    const pop = context.pop, sheet = narrow();
    context.popFor = anchor; context.popKey = anchor.dataset.badge; context.pinned = pinned;
    pop.innerHTML = explain(context, badgeOf(context, anchor.dataset.badge));
    pop.hidden = false; pop.classList.toggle("is-sheet", sheet); anchor.setAttribute("aria-expanded", "true"); syncBackdrop(context);
    if (sheet) { pop.style.left = pop.style.top = ""; }
    else {
      const rect = anchor.getBoundingClientRect(), width = pop.offsetWidth, height = pop.offsetHeight, below = rect.bottom + 6;
      pop.style.left = `${Math.min(Math.max(8, rect.left), window.innerWidth - width - 8)}px`;
      pop.style.top = `${Math.max(8, below + height > window.innerHeight - 8 ? rect.top - height - 6 : below)}px`;
    }
    if (pinned) pop.querySelector(".cx-primary").focus({preventScroll:true});
  }
  function hidePop(context, restore = false) {
    clearTimeout(context.hoverTimer);
    if (context.popFor === null) return;
    const anchor = context.popFor;
    anchor.removeAttribute("aria-expanded");
    context.pop.hidden = true; context.popFor = context.popKey = null; context.pinned = false; syncBackdrop(context);
    if (restore) anchor.focus();
  }
  function setAside(context, open) {
    if (context.asideOpen === open) return;
    context.asideOpen = open; context.aside.classList.toggle("is-open", open); context.pendingbar.setAttribute("aria-expanded", String(open)); syncBackdrop(context);
    if (open) context.aside.querySelector(".cx-aside-close").focus();
  }
  function go(context, key) {
    const {item} = badgeOf(context, key);
    hidePop(context); setAside(context, false);
    context.openChat(item.session_id, {at:item.delivery === "queued" ? null : item.delivered_at, prefix:null});
  }
  function composerState(context) {
    context.send.disabled = context.sending || context.input.value.trim() === "" || (context.allLeaders && context.state.leaders.length === 0);
    for (const button of context.targets.querySelectorAll("button")) button.disabled = context.sending;
  }
  function resizeInput(context) {
    context.input.style.height = "auto";
    context.input.style.height = `${Math.min(context.input.scrollHeight, 220)}px`;
  }
  function renderReply(context) {
    const target = context.replyTo;
    context.wrap.classList.toggle("is-replying", target !== null);
    context.replybar.hidden = target === null;
    context.input.placeholder = target === null ? "和所有 leader 说…" : `回复 ${target.speaker_name}…`;
    const notify = target !== null && target.speaker_session_id !== "user" && !context.allLeaders && !context.selected.has(target.speaker_session_id);
    put(context.replybar, target === null ? "" : `${icon("reply", "sm")}<button type="button" class="cx-replybar-quote" data-channel-jump="${target.id}"><span>回复 <span class="cx-id">#${target.id}</span> <strong>${escape(target.speaker_name)}</strong></span>`
      + `<span class="cx-replybar-text">${escape(target.auto ? "回复在私聊里" : plain(target.body))}</span></button>${notify ? `<span class="cx-replybar-note">会通知 ${escape(target.speaker_name)}</span>` : ""}`
      + `<button type="button" class="cx-icon-btn cx-replybar-x" aria-label="取消回复">${icon("x", "sm")}</button>`);
  }
  function renderTargets(context) {
    const ids = new Set(context.state.leaders.map(item => item.session_id));
    for (const id of context.selected) if (!ids.has(id)) context.selected.delete(id);
    const everyone = ids.size || context.allLeaders ? `<button type="button" class="cx-target${context.allLeaders ? " selected" : ""}" data-channel-to="*" aria-pressed="${Boolean(context.allLeaders)}" title="发送时点名本工作区所有在用的 leader" ${context.sending ? "disabled" : ""}>@所有 leader</button>` : "";
    put(context.targets, `<span class="cx-target-label">点名</span>${everyone}${context.state.leaders.map(item => {
      const p = presence(item), on = context.allLeaders || context.selected.has(item.session_id);
      const hint = {online:"在线，点名会立刻送达", working:"在跑任务，点名会先排队，这一轮结束后送达", external:"在外部窗口运行，点名会在它下次提示时送达", offline:"离线，点名会先排队"}[p.key];
      return `<button type="button" class="cx-target${on ? " selected" : ""}" data-channel-to="${escape(item.session_id)}" aria-pressed="${on}" title="${escape(item.name + hint)}" aria-label="${escape(`点名 ${item.name}（${p.text}）`)}" ${context.sending ? "disabled" : ""}><i class="cx-tdot" data-p="${p.key}" aria-hidden="true"></i>@${escape(item.name)}${p.short ? `<small>${p.short}</small>` : ""}</button>`;
    }).join("")}${ids.size === 0 ? '<span class="cx-target-label">还没有 leader</span>' : ""}`);
    renderReply(context);
  }
  function jump(context, id) {
    const node = context.inner.querySelector(`[data-channel-id="${Number(id)}"]`);
    if (!node) {
      context.toast(context.digest !== null && Number(id) <= context.digest.upto_message_id && !context.originalsOpen ? `#${id} 已整理进要点卡，展开“已整理的原始发言”可以看到` : "这条发言不在当前列表里，可能已整理进要点卡或在更早的记录中");
      return;
    }
    hidePop(context); setAside(context, false);
    node.scrollIntoView({block:"center", behavior:window.matchMedia("(prefers-reduced-motion:reduce)").matches ? "auto" : "smooth"});
    node.classList.remove("cx-flash"); void node.offsetWidth; node.classList.add("cx-flash");
  }
  function openDetail(context, id) {
    const message = context.messages.concat(context.originalMessages).find(item => item.id === Number(id));
    if (!message) return;
    // A reply to the user lands on the user's own words in that private chat; otherwise on the moment it was said.
    const focus = {at:message.created_at, prefix:message.reply && message.reply.speaker_session_id === "user" ? `channel:${message.reply.id}:` : null};
    context.openChat(message.speaker_session_id, focus);
  }
  function renderMessages(context, position = "keep") {
    const height = context.scroll.scrollHeight, top = context.scroll.scrollTop;
    context.children = new Map();
    for (const item of all(context)) if (item.reply) { if (!context.children.has(item.reply.id)) context.children.set(item.reply.id, []); context.children.get(item.reply.id).push(item); }
    const originals = context.digest === null ? "" : `<div class="cx-originals"><button class="cx-originals-toggle" type="button" aria-expanded="${context.originalsOpen}">${icon("chev", "xs cx-originals-chev")}已整理的 ${context.digest.total_message_count} 条原始发言</button>`
      + `${context.originalsOpen ? `<div class="cx-original-list">${context.originalMessages.map(item => messageHTML(context, item, true)).join("")}${context.hasMoreOriginals ? `<button type="button" class="cx-btn cx-ghost cx-originals-older" ${context.loadingOriginals ? "disabled" : ""}>${context.loadingOriginals ? "正在加载…" : "加载更早的"}</button>` : ""}</div>` : ""}</div>`;
    const lead = context.digest !== null && context.messages.length > 0 && context.messages[0].id !== context.unreadFrom ? `<div class="cx-divider"><span>要点之后 ${context.messages.length} 条</span></div>` : "";
    const feed = context.messages.map(item => (item.id === context.unreadFrom ? `<div class="cx-divider cx-unread"><span>新发言 · ${context.unreadCount} 条未读</span></div>` : "") + messageHTML(context, item)).join("");
    context.inner.innerHTML = originals + (context.messages.length === 0 ? `<div class="cx-empty">${context.digest === null ? "还没有发言。在这里和所有 leader 交流。" : "要点之后暂无新发言"}</div>` : lead + feed);
    context.older.hidden = !context.hasMore;
    context.older.disabled = context.loadingOlder;
    context.older.textContent = context.loadingOlder ? "正在加载…" : "加载更早的";
    const unread = position === "unread" && context.inner.querySelector(".cx-unread");
    if (unread) context.scroll.scrollTop = unread.offsetTop - 6;
    else if (position === "bottom" || position === "unread") context.scroll.scrollTop = context.scroll.scrollHeight;
    else if (position === "prepend") context.scroll.scrollTop = top + context.scroll.scrollHeight - height;
    else context.scroll.scrollTop = top;
    if (context.popKey !== null) {
      const anchor = context.inner.querySelector(`[data-badge="${context.popKey}"]`);
      if (anchor) { context.popFor = anchor; anchor.setAttribute("aria-expanded", "true"); } else hidePop(context);
    }
    context.minute = Math.floor(Date.now() / 60000);
    renderAside(context); renderReply(context);
    markRead(context);
  }
  function renderDigest(context) {
    context.pin.hidden = context.digest === null;
    if (context.digest === null) { context.pin.innerHTML = ""; return; }
    const runtime = {claude:"Claude Code",codex:"Codex"}[context.digest.runtime] || "执行工具未知";
    const cost = context.digest.cost_usd == null ? "费用未知" : `$${Number(context.digest.cost_usd).toFixed(4)}`;
    context.pin.innerHTML = `<details class="cx-digest"${context.digestOpen ? " open" : ""}><summary>${icon("sparkle", "sm")}<span class="cx-digest-title">要点</span><span class="cx-digest-meta">截至 ${escape(time(context.digest.created_at))} · 累计整理 ${context.digest.total_message_count} 条</span>${icon("chev", "xs cx-digest-chev")}</summary>`
      + `<div class="cx-digest-body"><div class="markdown">${CrewChat.renderMarkdown(context.digest.body, "user")}</div><p class="cx-digest-foot">${escape(runtime)} · ${escape(context.digest.model || "模型未知")} · ${escape(cost)}</p></div></details>`;
  }
  function digestState(context) {
    const busy = context.requestingDigest || context.state.channel.digesting;
    context.digestButton.disabled = busy;
    put(context.digestButton, `${icon("sparkle", "sm")}<span>${busy ? "整理中…" : "整理"}</span>`);
    const reason = context.state.channel.digest_error;
    if (!busy) {
      if (reason !== null && reason !== context.digestError) context.toast(reason);
      context.digestError = reason;
    }
  }
  async function organize(context) {
    if (context.digestButton.disabled) return;
    context.requestingDigest = true; digestState(context);
    try {
      await context.api("/api/op/channel.digest.start", {project:context.state.project.name});
      context.state.channel.digesting = true; context.digestError = null;
    } catch (error) {
      if (current(context)) context.toast(error.message);
    } finally {
      context.requestingDigest = false;
      if (current(context)) digestState(context);
    }
  }
  async function originals(context) {
    if (context.loadingOriginals || !context.hasMoreOriginals) return;
    const digestId = context.digest.id;
    context.loadingOriginals = true; renderMessages(context);
    try {
      const before = context.originalMessages.length === 0 ? context.digest.upto_message_id + 1 : context.originalMessages[0].id;
      const result = await context.api(`/api/op/channel.list?limit=20&before=${before}`);
      if (!current(context) || context.digest.id !== digestId) return;
      context.originalMessages = [...result.messages, ...context.originalMessages];
      context.hasMoreOriginals = result.has_more;
    } catch (error) { if (current(context)) context.toast(error.message); }
    finally { context.loadingOriginals = false; if (current(context)) renderMessages(context); }
  }
  function merge(context, messages, advance = true) {
    const byId = new Map(context.messages.map(item => [item.id, item]));
    for (const message of messages) byId.set(message.id, message);
    context.messages = Array.from(byId.values()).sort((a, b) => a.id - b.id);
    if (advance && messages.length > 0) context.latestId = Math.max(context.latestId, ...messages.map(item => item.id));
  }
  async function markRead(context) {
    if (!current(context) || context.loading || context.reading || context.latestId <= context.readId || document.visibilityState !== "visible" || !atBottom(context)) return;
    context.reading = true;
    let succeeded = false;
    try {
      const result = await context.api("/api/op/channel.mark_read", {project:context.state.project.name, id:context.latestId});
      context.readId = result.read_id;
      succeeded = true;
      if (current(context)) context.onRead(result);
    } catch (error) { if (current(context)) context.toast(error.message); }
    finally { context.reading = false; if (succeeded && current(context)) markRead(context); }
  }
  const afterDigest = (context, messages) => messages.filter(item => context.digest === null || item.id > context.digest.upto_message_id);
  async function load(context) {
    context.loading = true;
    try {
      const result = await context.api("/api/op/channel.list?limit=20");
      if (!current(context)) return;
      context.digest = result.digest; context.messages = []; context.originalMessages = [];
      context.originalsOpen = false; context.hasMoreOriginals = context.digest !== null;
      merge(context, afterDigest(context, result.messages));
      if (context.digest !== null) context.latestId = Math.max(context.latestId, context.digest.upto_message_id);
      context.pageBefore = result.messages.length === 0 ? null : result.messages[0].id;
      context.hasMore = result.has_more && (context.digest === null || context.pageBefore > context.digest.upto_message_id);
      const others = context.messages.filter(item => item.speaker_session_id !== "user"), unread = context.state.channel.unread || 0;
      context.unreadCount = unread; context.unreadFrom = unread > 0 && others.length > 0 ? others[Math.max(0, others.length - unread)].id : null;
      context.loading = false; renderDigest(context);
      renderMessages(context, "unread");
      if (context.focus != null) { const id = context.focus; context.focus = null; jump(context, id); }
      if (context.stale || context.state.channel.latest_id > context.latestId) refresh(context);
      loadRooms(context);
    } catch (error) {
      if (current(context)) { context.loading = false; context.inner.textContent = "Leader 交流区加载失败"; context.toast(error.message); }
    }
  }
  async function older(context) {
    if (context.loadingOlder || !context.hasMore) return;
    context.loadingOlder = true; context.older.disabled = true; context.older.textContent = "正在加载…";
    try {
      const result = await context.api(`/api/op/channel.list?limit=20&before=${context.pageBefore}`);
      if (!current(context)) return;
      merge(context, afterDigest(context, result.messages));
      context.pageBefore = result.messages[0].id;
      context.hasMore = result.has_more && (context.digest === null || context.pageBefore > context.digest.upto_message_id);
      renderMessages(context, "prepend");
    } catch (error) { if (current(context)) context.toast(error.message); }
    finally { context.loadingOlder = false; if (current(context)) { context.older.disabled = false; context.older.textContent = "加载更早的"; } }
  }
  async function refresh(context) {
    if (context.loading || context.refreshing) return;
    context.refreshing = true; context.stale = false;
    const known = context.latestId, messages = [];
    let before = null, succeeded = false;
    try {
      while (true) {
        const result = await context.api(`/api/op/channel.list?limit=20${before === null ? "" : `&before=${before}`}`);
        if (!current(context)) return;
        if ((result.digest === null ? null : result.digest.id) !== (context.digest === null ? null : context.digest.id)) {
          await load(context); return;
        }
        // Delivery badges change without new posts, so the newest page is merged back in full.
        messages.push(...afterDigest(context, result.messages));
        if (!result.has_more || result.messages[0].id <= known) break;
        before = result.messages[0].id;
      }
      const follow = atBottom(context);
      merge(context, messages); renderMessages(context, follow ? "bottom" : "keep"); succeeded = true;
    } catch (error) { if (current(context)) context.toast(error.message); }
    finally {
      context.refreshing = false;
      if (succeeded && current(context) && (context.stale || context.state.channel.latest_id > context.latestId)) refresh(context);
    }
  }
  // Posts from the change stream (app-data.js `channel_message`): each replaces the shown post with its id. A post not
  // shown yet arrives current when its page is read. A page read while changes arrived may hold older badges, so it is
  // read again. `null` follows a snapshot, which says nothing about posts: the newest page is read again.
  function changed(changes) {
    if (!active) return;
    const context = active;
    if (changes === null) { context.stale = true; refresh(context); return; }
    if (changes.length === 0) return;
    if (context.loading || context.refreshing) context.stale = true;
    let shown = false;
    for (const change of changes) {
      const id = Number(change.entity);
      for (const list of ["messages", "originalMessages"]) {
        const index = context[list].findIndex(item => item.id === id);
        if (index < 0) continue;
        if (change.data === null) context[list].splice(index, 1); else context[list][index] = change.data;
        shown = true;
      }
    }
    if (shown && !context.loading) renderMessages(context);
  }
  async function send(context) {
    if (context.send.disabled) return;
    const body = context.input.value.trim(), version = context.version, reply = context.replyTo;
    const allLeaders = context.allLeaders;
    const to = allLeaders ? ["*"] : context.state.leaders.filter(item => context.selected.has(item.session_id)).map(item => item.name);
    context.sending = true; composerState(context);
    try {
      const result = await context.api("/api/op/channel.post", {project:context.state.project.name, body, to, ...(reply === null ? {} : {reply_to:reply.id})});
      if (!current(context)) return;
      if (context.version === version) { context.input.value = ""; resizeInput(context); }
      context.selected.clear(); context.allLeaders = false; context.replyTo = null; renderTargets(context);
      // Fetch through intervening posts before advancing the cursor to our new message.
      merge(context, [result.message], false); renderMessages(context, "bottom"); refresh(context);
      const told = result.message.mentions.concat(result.message.notified).filter(item => item.delivery !== "none");
      context.toast(told.length === 0 ? "已发到 Leader 交流区" : `已发到 Leader 交流区，${allLeaders ? `本工作区 ${told.length} 位 leader` : told.map(item => item.name).join("、")}空闲时收到，送达情况看徽章`);
    } catch (error) { if (current(context)) context.toast(error.message); }
    finally { context.sending = false; if (current(context)) composerState(context); }
  }
  const LAYOUT = `<header class="cx-head"><button type="button" class="cx-icon-btn cx-back" data-act="view" data-view="all" aria-label="返回工作台">${icon("back")}</button>`
    + `<div class="cx-title"><h1>${icon("msg")}Leader 交流区</h1><p class="cx-sub"><span class="cx-sub-long">所有 leader 和用户的</span>公共频道 · 点名才叫醒</p></div>`
    + `<div class="cx-head-actions"><button type="button" class="cx-btn cx-digest-btn"></button><button type="button" class="cx-btn cx-room" aria-haspopup="dialog" aria-expanded="false" title="几位 leader 为同一个目标持续协作的房间">${icon("room", "sm")}<span class="cx-room-label">作战室</span></button></div></header>`
    + '<button type="button" class="cx-pendingbar" aria-haspopup="dialog" aria-controls="cx-aside" aria-expanded="false" hidden></button>'
    + '<div class="cx-body"><section class="cx-feed" aria-label="Leader 交流区"><div class="cx-pin" hidden></div><div class="cx-scroll" tabindex="0" aria-label="Leader 交流区发言"><div class="cx-history"><button type="button" class="cx-btn cx-ghost cx-older" hidden>加载更早的</button></div><div class="cx-inner"><div class="cx-empty">正在加载…</div></div></div>'
    + `<div class="cx-composer-wrap"><div class="cx-replybar" hidden></div><div class="cx-targets" role="group" aria-label="点名 leader"></div><form class="cx-composer"><textarea rows="1" aria-label="发到 Leader 交流区" placeholder="和所有 leader 说…"></textarea><div class="cx-c-row"><span class="cx-hint">Enter 发送 · Shift+Enter 换行或续列表</span><button type="submit" class="cx-send" aria-label="发送到 Leader 交流区" disabled>${icon("send")}</button></div></form></div></section>`
    + `<aside class="cx-aside" id="cx-aside" aria-label="投递追踪"><div class="cx-aside-head"><strong>投递追踪</strong><button type="button" class="cx-icon-btn cx-aside-close" aria-label="关闭投递追踪">${icon("x")}</button></div>`
    + '<section class="cx-aside-sec"><h2>成员</h2><ul class="cx-members"></ul></section><section class="cx-aside-sec"><h2>等待回应 <span class="cx-count"></span></h2><ul class="cx-pending"></ul></section>'
    + `<section class="cx-aside-sec"><h2>状态说明</h2><ul class="cx-legend">${LEGEND.map(([s, text]) => `<li>${staticBadge(s)}<span>${text}</span></li>`).join("")}</ul></section></aside></div>`
    + '<div class="cx-backdrop" hidden></div><div class="cx-pop" id="cx-pop" role="dialog" aria-modal="false" aria-labelledby="cx-pop-title" hidden></div>'
    + '<div class="wr-menu wr-rooms" role="dialog" aria-label="作战室列表" hidden></div><div class="wr-layerbox" hidden></div>';
  function mount(options) {
    unmount();
    const context = {...options, digest:null, digestError:null, requestingDigest:false, originalMessages:[], originalsOpen:false, hasMoreOriginals:false, loadingOriginals:false, messages:[], children:new Map(), threads:new Set(), selected:new Set(), allLeaders:false, replyTo:null,
      rooms:[], roomsOpen:false, upgrade:null, latestId:0, readId:0, unreadFrom:null, unreadCount:0, loading:true, loadingOlder:false, refreshing:false, reading:false, sending:false, composing:false, version:0, digestOpen:!narrow(), asideOpen:false, popFor:null, popKey:null, pinned:false, hoverTimer:0, hideTimer:0, minute:0, stale:false};
    active = context;
    const host = options.host, find = selector => host.querySelector(selector);
    host.innerHTML = LAYOUT;
    Object.assign(context, {digestButton:find(".cx-digest-btn"), pendingbar:find(".cx-pendingbar"), pin:find(".cx-pin"), scroll:find(".cx-scroll"), inner:find(".cx-inner"), older:find(".cx-older"), wrap:find(".cx-composer-wrap"), replybar:find(".cx-replybar"),
      targets:find(".cx-targets"), input:find("textarea"), send:find(".cx-send"), aside:find(".cx-aside"), members:find(".cx-members"), pending:find(".cx-pending"), pendingCount:find(".cx-count"), backdrop:find(".cx-backdrop"), pop:find(".cx-pop"), roomButton:find(".cx-room")});
    CrewBrowser.enableChatLists(context.input);
    context.pin.addEventListener("toggle", event => { context.digestOpen = event.target.open; }, true);
    const actions = [
      ["[data-badge]", node => { if (context.popFor === node && context.pinned) hidePop(context); else showPop(context, node, true); }],
      ["[data-go]", node => go(context, node.dataset.go)],
      ["[data-channel-jump]", node => jump(context, node.dataset.channelJump)],
      ["[data-channel-detail]", node => openDetail(context, node.dataset.channelDetail)],
      ["[data-channel-reply]", node => { context.replyTo = all(context).find(item => item.id === Number(node.dataset.channelReply)); renderReply(context); context.input.focus(); }],
      ["[data-channel-thread]", node => { const id = Number(node.dataset.channelThread); if (!context.threads.delete(id)) context.threads.add(id); renderMessages(context); context.inner.querySelector(`[data-channel-thread="${id}"]`).focus({preventScroll:true}); }],
      ["[data-channel-to]", node => {
        const id = node.dataset.channelTo;
        if (id === "*") { context.allLeaders = !context.allLeaders; context.selected.clear(); }
        else if (context.allLeaders) {
          context.allLeaders = false; context.selected.clear();
          for (const item of context.state.leaders) if (item.session_id !== id) context.selected.add(item.session_id);
        } else if (context.selected.has(id)) context.selected.delete(id); else context.selected.add(id);
        renderTargets(context); composerState(context);
      }],
      [".cx-originals-toggle", () => { context.originalsOpen = !context.originalsOpen; renderMessages(context); context.inner.querySelector(".cx-originals-toggle").focus({preventScroll:true}); if (context.originalsOpen && context.originalMessages.length === 0) originals(context); }],
      [".cx-originals-older", () => originals(context)],
      [".cx-older", () => older(context)],
      [".cx-digest-btn", () => organize(context)],
      [".cx-replybar-x", () => { context.replyTo = null; renderReply(context); context.input.focus(); }],
      [".cx-pop-x", () => hidePop(context, true)],
      [".cx-aside-close", () => { setAside(context, false); context.pendingbar.focus(); }],
      [".cx-pendingbar", () => setAside(context, true)],
      [".cx-backdrop", () => { hidePop(context); setAside(context, false); }],
      ["[data-channel-room]", node => { toggleRooms(context, false); context.openRoom(Number(node.dataset.channelRoom)); }],
      ["[data-channel-upgrade]", node => openUpgrade(context, node.dataset.channelUpgrade)],
      ["[data-up-lead]", node => { context.upgrade.lead = node.dataset.upLead; renderUpgrade(context); }],
      ["[data-up-remove]", node => { context.upgrade.members = context.upgrade.members.filter(id => id !== node.dataset.upRemove); renderUpgrade(context); }],
      ["[data-up-cancel]", () => closeUpgrade(context)],
      [".wr-layerbox", (node, event) => { if (event.target === node) closeUpgrade(context); }],
      [".cx-room", () => toggleRooms(context, !context.roomsOpen)]];
    host.onclick = event => {
      for (const [selector, run] of actions) {
        const node = event.target.closest(selector);
        if (node) { if (!node.disabled) run(node, event); return; }
      }
    };
    host.onchange = event => { if (event.target.matches("[data-up-add]") && event.target.value) { context.upgrade.members.push(event.target.value); renderUpgrade(context); } };
    host.onmouseover = event => {
      if (narrow() || !window.matchMedia("(hover:hover)").matches) return;
      const anchor = event.target.closest("[data-badge]");
      if (anchor || event.target.closest(".cx-pop")) clearTimeout(context.hideTimer);
      if (anchor && anchor !== context.popFor) { clearTimeout(context.hoverTimer); context.hoverTimer = setTimeout(() => { if (current(context) && !context.pinned && anchor.isConnected) showPop(context, anchor, false); }, 220); }
    };
    host.onmouseout = event => {
      const anchor = event.target.closest("[data-badge]"), into = event.relatedTarget;
      if (anchor) clearTimeout(context.hoverTimer);
      if (!context.pinned && context.popFor && (anchor || event.target.closest(".cx-pop")) && !(into && (into.closest(".cx-pop") || into.closest("[data-badge]") === context.popFor))) context.hideTimer = setTimeout(() => hidePop(context), 160);
    };
    context.scroll.onscroll = () => { if (context.popFor && !context.pop.classList.contains("is-sheet")) hidePop(context); markRead(context); };
    context.visibility = () => markRead(context);
    context.outside = event => {
      if (context.pinned && !event.target.closest(".cx-pop, [data-badge]")) hidePop(context);
      if (context.roomsOpen && !event.target.closest(".wr-rooms, .cx-room")) toggleRooms(context, false);
    };
    context.keys = event => {
      if (event.key !== "Escape") return;
      if (context.upgrade) closeUpgrade(context);
      else if (context.roomsOpen) { toggleRooms(context, false); context.roomButton.focus(); }
      else if (context.popFor) hidePop(context, true);
      else if (context.asideOpen) { setAside(context, false); context.pendingbar.focus(); }
    };
    // A popover or drawer opened on one layout must not linger once the layout switches between desktop and sheet.
    context.media = window.matchMedia("(max-width:760px)");
    context.relayout = () => { hidePop(context); setAside(context, false); };
    context.media.addEventListener("change", context.relayout);
    document.addEventListener("visibilitychange", context.visibility);
    document.addEventListener("click", context.outside);
    document.addEventListener("keydown", context.keys);
    context.input.oninput = () => { context.version += 1; resizeInput(context); composerState(context); };
    context.input.oncompositionstart = () => { context.composing = true; };
    context.input.oncompositionend = () => { context.composing = false; };
    context.input.onkeydown = event => {
      if (!context.composing && CrewBrowser.chatSendKey(event)) { event.preventDefault(); send(context); }
    };
    find("form").onsubmit = event => { event.preventDefault(); if (!context.composing) send(context); };
    renderTargets(context); renderAside(context); digestState(context); load(context);
    return {update, unmount};
  }
  function update(state) {
    if (!active) return;
    const context = active;
    context.state = state; renderTargets(context); composerState(context); digestState(context); renderAside(context);
    if (state.channel.latest_id > context.latestId || state.channel.digest_id !== (context.digest === null ? null : context.digest.id)) refresh(context);
    else if (Math.floor(Date.now() / 60000) !== context.minute && !context.loading && !context.popFor && pendingItems(context).some(x => x.s.key === "queued")) renderMessages(context);
  }
  // Show one post of the mounted channel (the sidebar's queued mention); while loading, the load lands there.
  function focus(id) {
    if (!active) return;
    if (active.loading) active.focus = id; else jump(active, id);
  }
  function unmount() {
    if (!active) return;
    clearTimeout(active.hoverTimer); clearTimeout(active.hideTimer);
    document.removeEventListener("visibilitychange", active.visibility);
    document.removeEventListener("click", active.outside);
    document.removeEventListener("keydown", active.keys);
    active.media.removeEventListener("change", active.relayout);
    active = null;
  }
  // The war room reuses the channel's delivery badges, explanation popover and presence.
  window.CrewChannelKit = {deliveryHTML, explain, presence, clock, time, plain};
  // A war room's card changed (or a new snapshot came): the header's room list re-reads.
  function roomsChanged() { if (active) loadRooms(active); }
  window.CrewChannel = {mount, update, changed, roomsChanged, focus, unmount};
})();
