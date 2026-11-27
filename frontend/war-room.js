"use strict";
/* 跨 Leader 作战室（docs/WAR_ROOM_DESIGN.md §8）。发言、投递徽章、说明浮层、发言框沿用交流区的 cx- 样式与 CrewChannelKit；
   作战室自己的类名 wr-。数据来自 GET /api/op/war_room.get，成员实时状态取 state.leaders。 */
(() => {
  const K = window.CrewChannelKit;
  const esc = value => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const PATHS = {
    back:'<path d="m14.5 5.5-6.5 6.5 6.5 6.5"/>', x:'<path d="m6.5 6.5 11 11M17.5 6.5l-11 11"/>',
    room:'<rect x="3.5" y="5" width="17" height="14" rx="2.5"/><path d="M3.5 9.5h17M8 14h3M8 16.5h6"/>',
    send:'<path d="m5 11 7-7 7 7M12 4v16"/>', chev:'<path d="m9 6 6 6-6 6"/>',
    thread:'<path d="M6 4v9a3 3 0 0 0 3 3h9"/><path d="m15 13 3 3-3 3"/>',
    reply:'<path d="M9.5 7 5 11.5 9.5 16"/><path d="M5 11.5h9a5 5 0 0 1 5 5V18"/>',
    check:'<path d="m5.5 12.5 4 4 9-9"/>', chat:'<path d="M5 5.5h14v10H11l-4 3.2v-3.2H5z"/><path d="M9 9.5h6M9 12h3.5"/>',
    msg:'<path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.5 3.5V17A2.5 2.5 0 0 1 4 14.5z"/>',
    doc:'<path d="M7 3.5h7l4 4V20a.5.5 0 0 1-.5.5h-10A.5.5 0 0 1 7 20z"/><path d="M13.5 3.5V8H18M9.5 12h6M9.5 15h6M9.5 18h3.5"/>',
    hourglass:'<path d="M7 4h10M7 20h10M8 4c0 4.5 8 5.5 8 8s-8 3.5-8 8M16 4c0 4.5-8 5.5-8 8s8 3.5 8 8"/>',
    crown:'<path d="M4.5 18 3.5 8l5 3.8L12 5.5l3.5 6.3 5-3.8-1 10z"/><path d="M5 20.5h14"/>',
    invite:'<circle cx="9.5" cy="8.5" r="3.5"/><path d="M3 20v-.5a6.5 6.5 0 0 1 11.6-4M18 14v6M15 17h6"/>',
    gavel:'<path d="m13.5 4.5 6 6M11 7l6 6M12.2 5.8l-4.4 4.4 6 6 4.4-4.4M10.8 13.2 4 20"/>',
    finish:'<rect x="4" y="4" width="16" height="16" rx="3"/><path d="m8.5 12.3 2.4 2.4 4.6-5"/>',
    pause:'<path d="M9 6v12M15 6v12"/>', takeover:'<path d="M5 5.5h14v10H11l-4 3.2v-3.2H5z"/><path d="m10 10.5 2 2 4-4"/>',
    window:'<rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M3.5 9h17"/><path d="M6.5 7h.01M8.8 7h.01" stroke-width="2"/>',
    doing:'<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor"/>',
    blocked:'<path d="M8.7 3.5h6.6l5.2 5.2v6.6l-5.2 5.2H8.7l-5.2-5.2V8.7z"/><path d="M8.5 12h7"/>',
    todo:'<circle cx="12" cy="12" r="8" stroke-dasharray="3.2 2.6"/>', done:'<circle cx="12" cy="12" r="8"/><path d="m8.5 12.3 2.4 2.4 4.6-5"/>',
    contract:'<path d="M8 4.5H6.5a2 2 0 0 0-2 2v3a2 2 0 0 1-2 2 2 2 0 0 1 2 2v3a2 2 0 0 0 2 2H8M16 4.5h1.5a2 2 0 0 1 2 2v3a2 2 0 0 0 2 2 2 2 0 0 0-2 2v3a2 2 0 0 1-2 2H16"/>',
    target:'<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r=".6" fill="currentColor"/>',
    board:'<rect x="4" y="4" width="16" height="16" rx="2.5"/><path d="M4 9.5h16M9.5 9.5V20"/>',
    edit:'<path d="m15.5 4.5 4 4L9 19H5v-4z"/><path d="m13 7 4 4"/>',
    history:'<path d="M4 12a8 8 0 1 0 2.4-5.7L4 8.5"/><path d="M4 4.5v4h4M12 8v4l2.5 1.5"/>',
    plus:'<path d="M12 5v14M5 12h14"/>', more:'<circle cx="6" cy="12" r="1.1"/><circle cx="12" cy="12" r="1.1"/><circle cx="18" cy="12" r="1.1"/>',
    lock:'<rect x="5" y="10.5" width="14" height="9.5" rx="2"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/>',
    archive:'<rect x="4" y="5" width="16" height="4" rx="1"/><path d="M5.5 9v9a1 1 0 0 0 1 1h11a1 1 0 0 0 1-1V9M10 13h4"/>',
    returned:'<path d="M9 14 4.5 9.5 9 5"/><path d="M4.5 9.5H14a5.5 5.5 0 0 1 0 11h-2"/>',
    alert:'<path d="M12 4 21 19.5H3z"/><path d="M12 10v4.5"/><path d="M12 17h.01" stroke-width="2.4"/>',
    gauge:'<path d="M4.5 16.5a7.5 7.5 0 1 1 15 0"/><path d="m12 16.5 3.5-4.5"/><path d="M4.5 19.5h15"/>'
  };
  const icon = (name, cls = "") => `<svg class="i ${cls}" viewBox="0 0 24 24" aria-hidden="true">${PATHS[name]}</svg>`;
  const STATUS = {doing:"进行中", blocked:"阻塞", todo:"待办", done:"完成"}, ORDER = ["doing", "blocked", "todo", "done"];
  const PRESENCE = {
    online:{t:"在线", s:"在线", hint:"在线，点名会立刻送达"}, working:{t:"在跑任务", s:"在跑", hint:"在跑任务，点名先排队，这一轮结束后送达"},
    external:{t:"外部窗口", s:"外部窗口", hint:"在外部窗口运行，点名会在它下次提示时送达"}, offline:{t:"离线", s:"离线", hint:"离线，点名先排队"},
    back:{t:"已回原岗", s:"回原岗", hint:"作战室已收尾，它回到原岗位继续"}};
  const LETTERS = "ABCDEF";
  const minutes = at => Math.max(0, Math.floor((Date.now() - Date.parse(at)) / 60000));
  const lasted = (from, to) => { const m = Math.max(1, Math.round((Date.parse(to) - Date.parse(from)) / 60000)); return m < 60 ? `${m} 分钟` : `${Math.floor(m / 60)} 小时${m % 60 ? ` ${m % 60} 分` : ""}`; };
  const media = query => window.matchMedia(query).matches;
  const put = (node, html) => { if (node.crewHTML !== html) { node.crewHTML = html; node.innerHTML = html; } };
  const closed = ctx => ctx.room.status === "closed";
  const project = ctx => ctx.state.project.name;
  let active = null;
  const current = ctx => active === ctx;
  const atBottom = ctx => ctx.scroll.scrollHeight - ctx.scroll.scrollTop - ctx.scroll.clientHeight < 48;
  const who = (ctx, id, name, size) => id === "user" ? `<span class="av round cx-av-user" style="--s:${size}px" aria-hidden="true">${esc(name.slice(0, 1))}</span>` : ctx.avatar(name, size, true);
  const tokens = n => n >= 1e8 ? `${+(n / 1e8).toFixed(2)} 亿` : n >= 1e4 ? `${+(n / 1e4).toFixed(1)} 万` : String(n);
  // 预算：null（没设）或 {s:"ok"|"near"|"paused", pct}；≥80% 为接近上限。
  function budgetState(room) {
    const b = room.budget;
    if (b === null) return null;
    const pct = Math.round(b.spent / b.tokens * 100);
    return {...b, pct, s:b.paused_at ? "paused" : pct >= 80 ? "near" : "ok"};
  }
  const chip = (s, text = STATUS[s]) => `<span class="wr-chip" data-s="${s}">${icon(s, "xs")}${esc(text)}</span>`;

  /* ------------------------------ 派生 ------------------------------ */
  // 该项进入当前状态的时间：最后一条新增它或改了它状态的 item 事件。
  function since(room, id) {
    let at = null;
    for (const e of room.events) if (e.kind === "item" && e.detail.id === id && e.detail.was !== e.detail.status) at = e.at;
    return at;
  }
  const contractVersion = room => room.events.filter(e => e.kind === "contract").length;
  const question = (room, d) => { const prefix = `作战室《${room.title}》：`; return d.question.startsWith(prefix) ? d.question.slice(prefix.length) : d.question; };
  function counts(room) { return room.board.reduce((c, x) => ({...c, [x.status]:c[x.status] + 1}), {doing:0, blocked:0, todo:0, done:0}); }
  function people(ctx) {
    const room = ctx.room, done = closed(ctx), goals = ctx.state.goals || [];
    return room.members.map(m => {
      const live = ctx.state.leaders.find(item => item.session_id === m.session_id);
      const mine = room.board.filter(x => x.owner_session_id === m.session_id), blocked = done ? undefined : mine.find(x => x.status === "blocked");
      const goal = live && live.active_goal_id != null ? goals.find(g => String(g.id) === String(live.active_goal_id)) : undefined;
      const doing = mine.find(x => x.status === "doing");
      const stop = !live || live.where === "offline" ? "没在运行，不用暂停" : live.where === "vscode" ? "在外部窗口运行，Crew 停不了它" : live.busy || live.status === "working" ? "" : "这一轮没在工作，不用暂停";
      return {id:m.session_id, name:m.crew_name, lead:m.session_id === room.lead_session_id, live, p:done ? "back" : K.presence(live).key,
        runtime:live ? (live.runtime === "codex" ? "Codex" : "Claude") : "", task:blocked ? blocked.title : goal ? goal.title : doing ? doing.title : "", blocked:Boolean(blocked), stop};
    });
  }
  // 细碎节点：非阻塞的状态板变化、成员变化。连续 3 条以上折叠。
  const minor = e => e.kind === "members" || (e.kind === "item" && e.detail.status !== "blocked");
  function timeline(room) {
    const oldest = room.has_more && room.messages.length ? Date.parse(room.messages[0].created_at) : -Infinity;
    const rows = room.events.filter(e => Date.parse(e.at) >= oldest).map(e => ({at:Date.parse(e.at), order:0, event:e}))
      .concat(room.messages.map(m => ({at:Date.parse(m.created_at), order:1, message:m}))).sort((a, b) => a.at - b.at || a.order - b.order);
    const out = [];
    for (let i = 0; i < rows.length;) {
      let j = i;
      while (j < rows.length && rows[j].event && minor(rows[j].event)) j += 1;
      if (j - i >= 3) { out.push({fold:rows.slice(i, j).map(row => row.event)}); i = j; } else { out.push(rows[i]); i += 1; }
    }
    return out;
  }

  /* ------------------------------ 时间线 ------------------------------ */
  function highlight(html, names) {
    const list = [...new Set(names)].sort((a, b) => b.length - a.length).map(name => esc(name).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    return html.replace(new RegExp(`(<[^>]*>)|@(?:${list.join("|")})`, "g"), (match, tag) => tag || `<span class="cx-at">${match}</span>`);
  }
  function messageHTML(ctx, m) {
    const user = m.speaker_session_id === "user", name = esc(m.speaker_name), at = `<time datetime="${esc(m.created_at)}" title="${esc(K.time(m.created_at))}">${esc(K.clock(m.created_at))}</time>`;
    if (m.auto) {
      return `<article class="cx-msg cx-auto" data-wr-msg="${m.id}">${who(ctx, m.speaker_session_id, m.speaker_name, 22)}<div class="cx-auto-body"><span><strong>${name}</strong> 在私聊里回复了 `
        + `${m.reply ? `<button type="button" class="cx-inline-link" data-wr-jump="${m.reply.id}">${esc(m.reply.speaker_name)} #${m.reply.id}</button>` : "这条发言"}</span>`
        + `<span class="cx-auto-meta"><span class="cx-id">#${m.id}</span>${at}</span><button type="button" class="cx-chip-btn" data-wr-detail="${m.id}">${icon("chat", "xs")}<span>看私聊回复</span></button></div></article>`;
    }
    const meta = `<div class="cx-meta"><strong>${name}</strong><span class="cx-id">#${m.id}</span>${at}${closed(ctx) ? "" : `<button type="button" class="cx-reply-btn" data-wr-reply="${m.id}" aria-label="回复 #${m.id}">${icon("reply", "xs")}<span>回复</span></button>`}</div>`;
    const quote = m.reply ? `<button type="button" class="cx-quote" data-wr-jump="${m.reply.id}" aria-label="跳到被回复的 #${m.reply.id} ${esc(m.reply.speaker_name)} 的发言">`
      + `${icon("reply", "xs")}<span class="cx-id">#${m.reply.id}</span><strong>${esc(m.reply.speaker_name)}</strong><span class="cx-quote-text">${esc(K.plain(m.reply.excerpt))}</span></button>` : "";
    const detail = !user && m.detail ? `<button type="button" class="cx-detail" data-wr-detail="${m.id}" aria-label="打开 ${name} 的私聊看完整内容"><span class="cx-detail-ic">${icon("doc", "sm")}</span>`
      + `<span class="cx-detail-txt"><strong>完整内容在 ${name} 的私聊里</strong><small>定位到这条发言</small></span>${icon("chev", "xs")}</button>` : "";
    const names = ["所有成员", ...ctx.room.members.map(item => item.crew_name), ...m.mentions.map(item => item.name)];
    const body = `<div class="${user ? "cx-bubble" : "cx-content"}"><div class="markdown cx-md">${highlight(CrewChat.renderMarkdown(m.body, m.speaker_session_id), names)}</div></div>`;
    return `<article class="cx-msg${user ? " cx-user" : ""}" data-wr-msg="${m.id}">${who(ctx, m.speaker_session_id, m.speaker_name, 28)}<div class="cx-body-col">${meta}${quote}${body}${detail}${K.deliveryHTML(m)}</div></article>`;
  }
  function eventParts(ctx, e) {
    const d = e.detail, bold = list => list.map(name => `<strong>${esc(name)}</strong>`).join("、");
    if (e.kind === "item") {
      const text = d.was === null ? `<span>新增</span><q>${esc(d.title)}</q>${chip(d.status)}` : d.was !== d.status ? `<span>把</span><q>${esc(d.title)}</q><span>标为</span>${chip(d.status)}` : `<span>更新了</span><q>${esc(d.title)}</q>`;
      return {s:d.status, ic:d.status, label:`状态变化：${STATUS[d.status]}`, text:text + (d.status === "blocked" && d.note ? `<span class="wr-node-why">${esc(d.note)}</span>` : ""),
        plain:`${d.was === null ? "新增" : d.was !== d.status ? "标为" + STATUS[d.status] : "更新了"}「${d.title}」`, go:`<button type="button" class="wr-link" data-wr-item="${d.id}">在状态板看</button>`};
    }
    if (e.kind === "contract") return {s:"contract", ic:"contract", label:"契约变化", text:"<span>更新了接口契约</span>", plain:"更新了接口契约", go:'<button type="button" class="wr-link" data-wr-contract>看契约</button>'};
    if (e.kind === "members") {
      const text = [d.added.length ? `邀请 ${bold(d.added)} 加入` : "", d.removed.length ? `移出 ${bold(d.removed)}` : ""].filter(Boolean).join("，");
      return {s:"event", ic:"invite", label:"成员变化", text:`<span>${text}</span>`, plain:text.replace(/<[^>]+>/g, ""), go:""};
    }
    if (e.kind === "budget") return d.paused
      ? {s:"paused", ic:"pause", label:"预算暂停", bare:true, text:`<span>预算到了，已暂停（已用 ${tokens(d.spent)} / 上限 ${tokens(d.budget)} tokens）</span>`, plain:"预算到了，已暂停", go:""}
      : {s:"resumed", ic:"gauge", label:"追加预算", bare:true, text:`<span>用户追加预算到 ${tokens(d.budget)} tokens，继续</span>`, plain:"用户追加预算，继续", go:""};
    if (e.kind === "opened") return {s:"event", ic:"room", label:"开房", text:`<span>开了作战室，成员 ${bold(d.members.filter(name => name !== e.actor_name))}</span>`, plain:"开了作战室", go:""};
    return {s:"event", ic:"finish", label:"收尾", text:`<span>收尾了作战室：${esc(d.summary)}</span>`, plain:"收尾了作战室", go:""};
  }
  function nodeHTML(ctx, e) {
    const p = eventParts(ctx, e);
    return `<div class="wr-node" data-s="${p.s}" role="note" aria-label="${p.label}"><span class="wr-node-ic">${icon(p.ic, "sm")}</span><span class="wr-node-txt">${p.bare ? "" : `${who(ctx, e.actor, e.actor_name, 18)}<strong>${esc(e.actor_name)}</strong>`}${p.text}</span>`
      + `<time datetime="${esc(e.at)}" title="${esc(K.time(e.at))}">${esc(K.clock(e.at))}</time>${p.go}</div>`;
  }
  function foldHTML(ctx, events) {
    const open = ctx.folds.has(events[0].id), first = events[0];
    return `<div class="wr-fold"><button type="button" class="wr-fold-btn" data-wr-fold="${first.id}" aria-expanded="${open}">${icon("chev", "xs chev")}<span>${events.length} 条状态变化</span><span>· ${esc(first.actor_name)} ${esc(eventParts(ctx, first).plain)} 等</span></button>`
      + `${open ? `<div class="wr-fold-list">${events.map(e => nodeHTML(ctx, e)).join("")}</div>` : ""}</div>`;
  }
  function decisionHTML(ctx, d) {
    const room = ctx.room, lead = room.lead_name, q = question(room, d), pending = d.status === "open";
    const tag = pending ? `<span class="wr-dc-state" data-s="pending">${icon("hourglass", "xs")}待你答复</span>`
      : d.status === "answered" ? `<span class="wr-dc-state" data-s="answered">${icon("check", "xs")}已答复</span>` : '<span class="wr-dc-state" data-s="withdrawn">已撤回</span>';
    const head = `<div class="wr-dc-head"><span class="wr-dc-kind">${icon("gavel", "sm")}拍板卡</span>${tag}<span class="wr-dc-from">${ctx.avatar(lead, 16, true)}牵头 ${esc(lead)} 汇总 · ${esc(K.clock(d.created_at))}</span></div><h3 class="wr-dc-q" id="wr-dc-${d.id}">${esc(q)}</h3>`;
    const s = pending ? "pending" : "answered";
    if (d.status === "answered") {
      const k = d.options.indexOf(d.answer), rest = d.options.length - (k >= 0 ? 1 : 0);
      return `<section class="wr-decide" data-s="${s}" data-wr-decision="${d.id}" aria-labelledby="wr-dc-${d.id}">${head}<div class="wr-dc-answer">${icon("check", "sm")}<div>${k >= 0 ? `你选了 <b>${LETTERS[k]} · ${esc(d.answer)}</b>` : `你的答复：<b>${esc(d.answer)}</b>`}<small>${esc(K.clock(d.answered_at))} 答复</small></div></div>`
        + `<div class="wr-dc-foot">${rest > 0 ? `<span>其余 ${rest} 个选项已收起</span><span>·</span>` : ""}<span>答复已回到牵头 ${esc(lead)}，由它在房里收口</span></div></section>`;
    }
    if (!pending) return `<section class="wr-decide" data-s="answered" data-wr-decision="${d.id}" aria-labelledby="wr-dc-${d.id}">${head}<div class="wr-dc-foot"><span>这张拍板卡已撤回</span></div></section>`;
    const off = closed(ctx) ? " disabled" : "";
    const opts = d.options.map((text, i) => `<button type="button" class="opt${text === d.recommendation ? " recommended" : ""}" data-wr-answer="${d.id}" data-wr-option="${i}"${off}><span class="k">${LETTERS[i]}</span><span class="t"><strong>${esc(text)}</strong></span>${text === d.recommendation ? '<span class="rec">推荐</span>' : ""}</button>`).join("");
    const rec = d.recommendation && !d.options.includes(d.recommendation) ? `<p class="wr-dc-why wr-dc-rec"><b>推荐：</b>${esc(d.recommendation)}</p>` : "";
    const free = closed(ctx) ? '<p class="wr-dc-why wr-dc-rec">作战室已收尾，这里只读；还要答复可在“待处理”里答。</p>'
      : `<form class="free" data-wr-free="${d.id}"><input placeholder="或者直接说你的想法…" aria-label="自由答复" maxlength="2000"><button type="submit" class="icon-btn" aria-label="提交答复">${icon("send", "sm")}</button></form>`;
    return `<section class="wr-decide" data-s="pending" data-wr-decision="${d.id}" aria-labelledby="wr-dc-${d.id}">${head}<div class="wr-dc-body">${d.options.length ? `<div class="opts" role="group" aria-label="选项">${opts}</div>` : ""}${rec}${free}</div></section>`;
  }
  function originHTML(ctx) {
    const o = ctx.room.origin;
    return o === null ? "" : `<section class="wr-origin" aria-label="起因"><div class="wr-origin-label">${icon("thread", "xs")}起因 <span>· 从交流区这条脉络升级，原脉络留在交流区</span></div>`
      + `<button type="button" class="cx-quote" data-wr-channel="${o.id}" aria-label="在交流区打开 #${o.id}">${ctx.avatar(o.speaker_name, 18, true)}<span class="cx-id">#${o.id}</span><strong>${esc(o.speaker_name)}</strong><span class="cx-quote-text">${esc(o.excerpt)}</span></button>`
      + `<div class="wr-origin-foot"><span>脉络 · ${o.replies} 条回复</span><button type="button" class="wr-link" data-wr-channel="${o.id}">去交流区看${icon("chev", "xs")}</button></div></section>`;
  }
  function timelineHTML(ctx) {
    const room = ctx.room, decisions = new Map(room.decisions.map(d => [d.id, d]));
    const unread = closed(ctx) ? [] : room.messages.filter(m => m.id > ctx.readFrom && m.speaker_session_id !== "user");
    const rows = timeline(room).map(row => {
      if (row.fold) return foldHTML(ctx, row.fold);
      if (row.message) return (row.message === unread[0] ? `<div class="cx-divider cx-unread"><span>新发言 · ${unread.length} 条未读</span></div>` : "") + messageHTML(ctx, row.message);
      return row.event.kind === "decision" ? decisionHTML(ctx, decisions.get(row.event.detail.id)) : nodeHTML(ctx, row.event);
    }).join("");
    const older = room.has_more ? `<div class="cx-divider"><span>只显示最近 ${room.messages.length} 条发言</span></div>` : "";
    return originHTML(ctx) + older + (rows || '<div class="cx-empty">还没有发言</div>');
  }
  function pausedHTML(ctx) {
    const b = budgetState(ctx.room);
    return b === null || b.s !== "paused" ? "" : `<div class="wr-paused" role="status">${icon("pause")}<div><strong>预算到了，作战室已暂停，成员已停下</strong>`
      + `<small>已用 ${tokens(b.spent)} / 上限 ${tokens(b.tokens)} tokens · ${esc(K.clock(b.paused_at))} 暂停${closed(ctx) ? " · 作战室已收尾" : "；追加后成员接着推进"}</small></div>`
      + `${closed(ctx) ? "" : `<button type="button" class="cx-btn cx-primary" data-wr="budget">${icon("gauge", "sm")}追加预算并继续</button>`}</div>`;
  }
  function pinHTML(ctx) {
    const room = ctx.room, by = room.events.find(e => e.kind === "closed");
    return pausedHTML(ctx) + (closed(ctx) ? summaryHTML(ctx, room, by) : "");
  }
  function summaryHTML(ctx, room, by) {
    return `<div class="wr-summary">${icon("finish")}<div><strong>收尾结论</strong><p>${esc(room.summary)}</p><small>${by ? `${who(ctx, by.actor, by.actor_name, 14)}${esc(by.actor_name)} ` : ""}收尾于 ${esc(K.clock(room.closed_at))} · 用时 ${lasted(room.created_at, room.closed_at)} · 成员已收到留档通知，回到原岗</small></div></div>`;
  }

  /* ------------------------------ 页头、参战栏 ------------------------------ */
  const statusHTML = ctx => closed(ctx) ? `<span class="wr-status" data-s="closed">${icon("archive", "xs")}已收尾 · ${esc(K.clock(ctx.room.closed_at))}</span>` : '<span class="wr-status" data-s="open"><span class="dot working" aria-hidden="true"></span>进行中</span>';
  function headHTML(ctx) {
    const room = ctx.room, lead = room.lead_name;
    const actions = closed(ctx)
      ? `<span class="wr-readonly">${icon("lock", "xs")}只读</span><button type="button" class="cx-btn" data-wr-channel="${room.closed_message_id}" aria-label="看收尾公告">${icon("msg", "sm")}<span class="wr-act-label">看收尾公告</span></button>`
      : `<button type="button" class="cx-btn" data-wr="invite" aria-haspopup="menu" aria-label="邀请成员">${icon("invite", "sm")}<span class="wr-act-label">邀请</span></button>`
        + `<button type="button" class="cx-btn" data-wr="decide" aria-label="发拍板卡（请牵头汇总）" title="${esc(`拍板卡由牵头 ${lead} 发：点这里会在发言框里请它把待定的事汇成一张`)}">${icon("gavel", "sm")}<span class="wr-act-label">发拍板卡</span></button>`
        + `<button type="button" class="cx-btn cx-ghost" data-wr="close" aria-label="收尾作战室">${icon("finish", "sm")}<span class="wr-act-label">收尾</span></button>`;
    return `<button type="button" class="cx-icon-btn wr-back" data-act="view" data-view="channel" aria-label="返回 Leader 交流区">${icon("back")}</button>`
      + `<div class="wr-title"><h1><span class="wr-title-ic" aria-hidden="true">${icon("room")}</span><span class="wr-title-text" id="wr-title">${esc(room.title)}</span>${statusHTML(ctx)}</h1>`
      + `<p class="wr-sub">${statusHTML(ctx)}<span class="wr-sub-lead">牵头 ${ctx.avatar(lead, 16, true)}<b>${esc(lead)}</b></span><span class="sep" aria-hidden="true"></span><span class="wr-sub-goal" title="${esc(room.goal)}">目标：<b>${esc(room.goal)}</b></span></p></div>`
      + `<div class="wr-head-actions">${actions}<button type="button" class="cx-icon-btn wr-more" data-wr="more" aria-label="更多操作" aria-haspopup="menu">${icon("more")}</button></div>`;
  }
  function presenceMark(p) {
    if (p === "working") return '<span class="dot working"></span>';
    if (p === "offline") return '<span class="dot offline"></span>';
    if (p === "online") return '<i class="wr-pdot"></i>';
    return icon(p === "external" ? "window" : "returned", "xs");
  }
  const badge = p => `<span class="wr-badge" data-p="${p}" aria-hidden="true">${p === "online" ? "<i></i>" : presenceMark(p)}</span>`;
  const presHTML = p => `<span class="wr-pres" data-p="${p}">${presenceMark(p)}<span class="wr-pres-long">${PRESENCE[p].t}</span><span class="wr-pres-short">${PRESENCE[p].s}</span></span>`;
  const ringOf = (ctx, m) => m.live && !closed(ctx) ? ctx.ring(m.live) : "";
  function pauseHTML(m, cls, label = true) {
    return `<button type="button" class="${cls}" data-wr-pause="${esc(m.id)}"${m.stop ? ` aria-disabled="true" title="${esc(m.stop)}"` : ""} aria-label="${esc(`暂停 ${m.name}${m.stop ? `（${m.stop}）` : "：停止它这一轮"}`)}">${icon("pause", label ? "sm" : "xs")}<span class="wr-mini-t">暂停</span></button>`;
  }
  function crewHTML(ctx) {
    const done = closed(ctx);
    const list = people(ctx).map(m => {
      const pr = PRESENCE[m.p], task = m.task ? `<span class="wr-mem-sep" aria-hidden="true">·</span><span class="wr-mem-task${m.blocked ? " is-blocked" : ""}" title="${esc(m.task)}">${m.blocked ? icon("blocked", "xs") : ""}${esc(m.task)}${m.blocked ? "（阻塞）" : ""}</span>` : "";
      const acts = done ? `<button type="button" class="wr-mini" data-wr-chat="${esc(m.id)}" aria-label="打开 ${esc(m.name)} 的私聊">${icon("chat", "xs")}<span class="wr-mini-t">私聊</span></button>`
        : pauseHTML(m, "wr-mini", false) + `<button type="button" class="wr-mini" data-wr-chat="${esc(m.id)}" aria-label="接管 ${esc(m.name)}：打开它的私聊">${icon("takeover", "xs")}<span class="wr-mini-t">接管</span></button>`;
      return `<li class="wr-mem" data-p="${m.p}"${m.lead ? " data-lead" : ""}><button type="button" class="wr-mem-open" data-wr-member="${esc(m.id)}" aria-label="${esc(`${m.name}${m.lead ? "（牵头）" : ""}，${m.runtime ? m.runtime + "，" : ""}${pr.t}${m.task ? `，正在做：${m.task}` : ""}。点开看详情`)}"></button>`
        + `<span class="wr-mem-av">${ctx.avatar(m.name, 34)}${badge(m.p)}${m.lead ? `<span class="wr-lead-flag" aria-hidden="true">${icon("crown", "xs")}</span>` : ""}</span>`
        + `<span class="wr-mem-name"><strong>${esc(m.name)}</strong>${m.lead ? `<span class="wr-lead">${icon("crown", "xs")}牵头</span>` : ""}${m.runtime ? `<span class="cx-rt" data-rt="${m.runtime}">${m.runtime}</span>` : ""}</span>`
        + `<span class="wr-mem-state">${presHTML(m.p)}${task}</span><span class="wr-mem-ring">${ringOf(ctx, m)}</span><span class="wr-mem-acts">${acts}</span></li>`;
    }).join("");
    const add = done ? "" : `<li class="wr-mem-add"><button type="button" data-wr="invite" aria-haspopup="menu">${icon("plus", "sm")}<span>邀请</span></button></li>`;
    return `<ul class="wr-crew-list">${list}${add}</ul>`;
  }
  function panelbarHTML(ctx) {
    const c = counts(ctx.room), pending = ctx.room.open_decisions;
    const bits = closed(ctx) ? `<span class="wr-pb-rest">${c.done} 项完成 · ${ctx.room.decisions.length} 张拍板卡</span>`
      : `${c.blocked ? chip("blocked", `${c.blocked} 阻塞`) : ""}${pending ? `<span class="wr-dc-state" data-s="pending">${icon("hourglass", "xs")}${pending} 待你拍板</span>` : ""}<span class="wr-pb-rest">${c.doing} 进行中 · ${c.todo} 待办</span>`;
    const b = budgetState(ctx.room), money = b && b.s !== "ok" ? `<span class="wr-budget-tag" data-s="${b.s}">${icon(b.s === "paused" ? "pause" : "gauge", "xs")}${b.s === "paused" ? "预算已暂停" : `预算 ${b.pct}%`}</span>` : "";
    return `<span class="wr-pb-ic">${icon("board", "sm")}</span><span class="wr-pb-txt"><strong>目标与状态板</strong>${money}${bits}</span><span class="wr-pb-more">展开${icon("chev", "xs")}</span>`;
  }

  /* ------------------------------ 右面板 ------------------------------ */
  function itemNote(room, x) {
    if (x.status !== "blocked") return x.note ? esc(x.note) : "";
    const at = since(room, x.id);
    return `阻塞${x.note ? `：${esc(x.note)}` : ""}${at ? ` · 已 ${minutes(at)} 分钟` : ""}`;
  }
  function budgetHTML(ctx) {
    const b = budgetState(ctx.room);
    if (b === null) return "";
    const tag = b.s === "paused" ? `${icon("pause", "xs")}已暂停` : b.s === "near" ? `${icon("alert", "xs")}接近上限 · ${b.pct}%` : `已用 ${b.pct}%`;
    const act = b.s === "paused" || closed(ctx) ? "" : `<button type="button" class="wr-sec-act" data-wr="budget">${icon("plus", "xs")}追加</button>`;
    return `<section class="wr-sec"><h2>${icon("gauge", "xs")}预算${act}</h2><div class="wr-budget" data-s="${b.s}">`
      + `<div class="wr-budget-row"><span>已用 <b>${tokens(b.spent)}</b> / ${tokens(b.tokens)} tokens</span><span class="wr-budget-tag" data-s="${b.s}">${tag}</span></div>`
      + `<div class="wr-budget-track" role="progressbar" aria-label="预算用量" aria-valuemin="0" aria-valuemax="${b.tokens}" aria-valuenow="${Math.min(b.spent, b.tokens)}" aria-valuetext="${esc(`已用 ${tokens(b.spent)} / ${tokens(b.tokens)} tokens`)}"><i style="width:${Math.min(100, b.pct)}%"></i></div>`
      + `<small>${closed(ctx) ? "作战室已收尾，只读保留" : b.s === "paused" ? "成员已停下，追加预算后继续" : "成员合计用量；到上限会暂停成员，等你追加"}</small></div></section>`;
  }
  function asideHTML(ctx) {
    const room = ctx.room, done = closed(ctx), version = contractVersion(room), contract = room.contract;
    const groups = ORDER.map(s => {
      const list = room.board.filter(x => x.status === s);
      if (list.length === 0) return "";
      const fold = s === "done" && !done, open = !fold || ctx.doneOpen;
      const h = fold ? `<button type="button" class="wr-group-h" data-s="${s}" data-wr-done aria-expanded="${open}">${icon(s, "xs")}${STATUS[s]} <span class="n">${list.length}</span>${icon("chev", "xs chev")}</button>`
        : `<div class="wr-group-h" data-s="${s}">${icon(s, "xs")}${STATUS[s]} <span class="n">${list.length}</span></div>`;
      const rows = open ? `<ul class="wr-items">${list.map(x => {
        const note = itemNote(room, x), owner = x.owner_name ? `<span class="wr-item-who">${ctx.avatar(x.owner_name, 16, true)}${esc(x.owner_name)}</span>` : '<span class="wr-item-who">未分配</span>';
        return `<li class="wr-item" data-s="${s}" data-wr-row="${x.id}"${done ? "" : ' tabindex="0" role="button"'} aria-label="${esc(`${x.title}，${x.owner_name ? x.owner_name + " 负责" : "未分配"}，${STATUS[s]}${x.note ? "，" + x.note : ""}${done ? "" : "。点开修改"}`)}">${icon(s)}<span class="wr-item-title">${esc(x.title)}</span>${owner}${note ? `<span class="wr-item-note">${note}</span>` : ""}</li>`;
      }).join("")}</ul>` : "";
      return `<div class="wr-group">${h}${rows}</div>`;
    }).join("") || `<p class="wr-empty">${done ? "状态板是空的" : "还没有事项。成员会把各自负责的那块写进来，你也可以加一项"}</p>`;
    const meta = contract.updated_at ? `${icon("history", "xs")}<span>${esc(K.clock(contract.updated_at))} 由 ${ctx.avatar(contract.updated_by, 14, true)}<b>${esc(contract.updated_by)}</b> 更新</span>` : `${icon("history", "xs")}<span>还没有契约</span>`;
    const code = contract.text ? `<pre class="wr-code" aria-label="接口契约内容">${esc(contract.text)}</pre>${ctx.contractOpen ? "" : '<div class="wr-contract-more"><button type="button" class="wr-sec-act" data-wr-contract>展开全部</button></div>'}`
      : '<p class="wr-empty wr-code-empty">需要共同遵守的接口 / 数据约定写在这里，整份替换，改前会核对版本</p>';
    const dl = room.decisions.map(d => `<li data-s="${d.status === "open" ? "pending" : "answered"}"><button type="button" data-wr-goto="${d.id}">${icon("gavel", "xs")}<span class="q">${esc(question(room, d))}</span>`
      + `${d.status === "open" ? `<span class="wr-dc-state" data-s="pending">${icon("hourglass", "xs")}待你答复</span>` : d.status === "answered" ? `<span class="wr-dc-state" data-s="answered">${icon("check", "xs")}${d.options.includes(d.answer) ? LETTERS[d.options.indexOf(d.answer)] : "已答"}</span>` : '<span class="wr-dc-state" data-s="withdrawn">已撤回</span>'}</button></li>`).join("");
    const work = people(ctx).map(m => `<li>${ctx.avatar(m.name, 26)}<span class="wr-work-txt"><strong>${esc(m.name)}${m.task ? ` · ${esc(m.task)}` : ""}</strong><small>${done ? "作战室已收尾 · 回到原岗" : esc(PRESENCE[m.p].hint)}</small></span>`
      + `<button type="button" class="wr-work-go" data-wr-chat="${esc(m.id)}" aria-label="${esc(`打开 ${m.name} 的私聊`)}">${icon("chat", "xs")}私聊</button></li>`).join("");
    return `<div class="wr-aside-head"><span class="wr-grab" aria-hidden="true"></span><div><strong>目标与状态板</strong><button type="button" class="cx-icon-btn" data-wr-aside-close aria-label="关闭">${icon("x")}</button></div></div>`
      + `<section class="wr-sec"><h2>${icon("target", "xs")}目标</h2><p class="wr-goal">${esc(room.goal)}</p></section>`
      + budgetHTML(ctx)
      + `<section class="wr-sec"><h2>${icon("board", "xs")}任务状态板 <span class="cx-count">${room.board.length}</span>${done ? "" : `<button type="button" class="wr-sec-act" data-wr="add-item">${icon("plus", "xs")}加一项</button>`}</h2>${groups}</section>`
      + `<section class="wr-sec" id="wr-contract"><h2>${icon("contract", "xs")}接口契约${version ? ` <span class="cx-count">第 ${version} 版</span>` : ""}</h2><div class="wr-contract${ctx.contractOpen || !contract.text ? "" : " is-clamped"}"><div class="wr-contract-meta">${meta}${done ? "" : `<button type="button" class="wr-sec-act" data-wr="edit-contract">${icon("edit", "xs")}${contract.text ? "编辑" : "写契约"}</button>`}</div>${code}</div></section>`
      + `<section class="wr-sec"><h2>${icon("gavel", "xs")}拍板卡 <span class="cx-count">${room.decisions.length}</span></h2>${dl ? `<ul class="wr-dlist">${dl}</ul>` : `<p class="wr-empty">还没有拍板卡。几个组的分歧要你定时，牵头 ${esc(room.lead_name)} 会汇成一张发给你</p>`}</section>`
      + `<section class="wr-sec"><h2>${icon("doc", "xs")}成员当前任务</h2><ul class="wr-work">${work}</ul></section>`;
  }

  /* ------------------------------ 发言框 ------------------------------ */
  function targetsHTML(ctx) {
    const off = ctx.sending ? " disabled" : "";
    return `<span class="cx-target-label">点名</span><button type="button" class="cx-target wr-target-all${ctx.all ? " selected" : ""}" data-wr-to="*" aria-pressed="${ctx.all}" title="叫醒全体成员（不含房外的 leader）"${off}>@所有成员</button>`
      + people(ctx).map(m => {
        const on = ctx.all || ctx.selected.has(m.id);
        return `<button type="button" class="cx-target${on ? " selected" : ""}" data-wr-to="${esc(m.id)}" aria-pressed="${on}" title="${esc(`${m.name}：${PRESENCE[m.p].hint}`)}" aria-label="${esc(`点名 ${m.name}（${PRESENCE[m.p].t}）`)}"${off}><i class="cx-tdot" data-p="${m.p}" aria-hidden="true"></i>@${esc(m.name)}${m.p === "online" ? "" : `<small>${PRESENCE[m.p].s}</small>`}</button>`;
      }).join("");
  }
  function replyHTML(ctx) {
    const r = ctx.replyTo;
    if (r === null) return "";
    const note = r.speaker_session_id !== "user" && !ctx.all && !ctx.selected.has(r.speaker_session_id);
    return `${icon("reply", "sm")}<button type="button" class="cx-replybar-quote" data-wr-jump="${r.id}"><span>回复 <span class="cx-id">#${r.id}</span> <strong>${esc(r.speaker_name)}</strong></span><span class="cx-replybar-text">${esc(r.auto ? "回复在私聊里" : K.plain(r.body))}</span></button>`
      + `${note ? `<span class="cx-replybar-note">会通知 ${esc(r.speaker_name)}</span>` : ""}<button type="button" class="cx-icon-btn cx-replybar-x" data-wr-unreply aria-label="取消回复">${icon("x", "sm")}</button>`;
  }
  function renderComposer(ctx) {
    const done = closed(ctx);
    ctx.form.hidden = ctx.targets.hidden = done; ctx.closedbar.hidden = !done;
    ctx.wrap.classList.toggle("is-replying", ctx.replyTo !== null);
    ctx.replybar.hidden = done || ctx.replyTo === null;
    put(ctx.replybar, replyHTML(ctx)); put(ctx.targets, targetsHTML(ctx));
    ctx.input.placeholder = ctx.replyTo === null ? "在作战室说…" : `回复 ${ctx.replyTo.speaker_name}…`;
    ctx.send.disabled = ctx.sending || ctx.input.value.trim() === "";
  }

  /* ------------------------------ 浮层 ------------------------------ */
  function syncLayers(ctx) {
    ctx.aside.classList.toggle("is-open", ctx.asideOpen);
    ctx.panelbar.setAttribute("aria-expanded", String(ctx.asideOpen));
    ctx.sheet.hidden = ctx.member === null;
    if (ctx.member !== null) put(ctx.sheet, sheetHTML(ctx, people(ctx).find(m => m.id === ctx.member)));
    ctx.backdrop.hidden = !((ctx.asideOpen && media("(max-width:1099px)")) || ctx.member !== null || (ctx.popFor !== null && ctx.pop.classList.contains("is-sheet")));
  }
  function sheetHTML(ctx, m) {
    const pr = PRESENCE[m.p], done = closed(ctx);
    return `<span class="wr-grab" aria-hidden="true"></span><div class="wr-ms-head"><span class="wr-mem-av">${ctx.avatar(m.name, 44)}${badge(m.p)}</span><div><strong id="wr-ms-title">${esc(m.name)}${m.lead ? `<span class="wr-lead">${icon("crown", "xs")}牵头</span>` : ""}${m.runtime ? `<span class="cx-rt" data-rt="${m.runtime}">${m.runtime}</span>` : ""}</strong><small>${presHTML(m.p)}</small></div><button type="button" class="cx-icon-btn" data-wr-sheet-close aria-label="关闭">${icon("x")}</button></div>`
      + `<dl class="wr-ms-facts"><dt>正在做</dt><dd>${m.task ? esc(m.task) : "—"}${m.blocked ? chip("blocked") : ""}</dd>${ringOf(ctx, m) ? `<dt>上下文</dt><dd>${ringOf(ctx, m)}</dd>` : ""}<dt>点名会</dt><dd>${esc(pr.hint)}</dd></dl>`
      + `<div class="wr-ms-acts">${done ? "" : pauseHTML(m, "cx-btn")}<button type="button" class="cx-btn cx-primary" data-wr-chat="${esc(m.id)}">${icon("takeover", "sm")}${done ? "打开私聊" : "接管（打开私聊）"}</button></div>`;
  }
  function openMenu(ctx, anchor, html) {
    const menu = ctx.menu;
    menu.innerHTML = html; menu.hidden = false; ctx.menuFor = anchor;
    const r = anchor.getBoundingClientRect(), w = menu.offsetWidth;
    menu.style.left = `${Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8))}px`; menu.style.top = `${r.bottom + 6}px`;
    menu.querySelector("button")?.focus({preventScroll:true});
  }
  const closeMenu = ctx => { ctx.menu.hidden = true; ctx.menuFor = null; };
  function openDialog(ctx, html, focus) {
    ctx.dialogReturn = document.activeElement;
    ctx.layer.innerHTML = html; ctx.layer.hidden = false;
    ctx.layer.querySelector(focus).focus();
  }
  function closeDialog(ctx) { ctx.layer.hidden = true; ctx.layer.innerHTML = ""; ctx.dialogReturn?.focus?.({preventScroll:true}); }
  function showPop(ctx, anchor) {
    const [id, kind, ...rest] = anchor.dataset.badge.split(":"), message = ctx.room.messages.find(m => m.id === Number(id));
    const item = message[kind].find(x => x.session_id === rest.join(":")), pop = ctx.pop, sheet = media("(max-width:760px)");
    ctx.popFor?.removeAttribute("aria-expanded");
    ctx.popFor = anchor; anchor.setAttribute("aria-expanded", "true");
    pop.innerHTML = K.explain({state:ctx.state}, {message, kind, item}).replaceAll("data-channel-jump=", "data-wr-jump=");
    pop.hidden = false; pop.classList.toggle("is-sheet", sheet); syncLayers(ctx);
    if (sheet) pop.style.left = pop.style.top = "";
    else {
      const rect = anchor.getBoundingClientRect(), below = rect.bottom + 6;
      pop.style.left = `${Math.min(Math.max(8, rect.left), window.innerWidth - pop.offsetWidth - 8)}px`;
      pop.style.top = `${Math.max(8, below + pop.offsetHeight > window.innerHeight - 8 ? rect.top - pop.offsetHeight - 6 : below)}px`;
    }
    pop.querySelector(".cx-primary").focus({preventScroll:true});
  }
  function hidePop(ctx) {
    if (ctx.popFor === null) return;
    ctx.popFor.removeAttribute("aria-expanded"); ctx.popFor = null; ctx.pop.hidden = true; syncLayers(ctx);
  }
  function flash(node) { node.scrollIntoView({block:"center", behavior:media("(prefers-reduced-motion:reduce)") ? "auto" : "smooth"}); node.classList.remove("cx-flash"); void node.offsetWidth; node.classList.add("cx-flash"); }
  function showAside(ctx) { if (media("(max-width:1099px)")) { ctx.asideOpen = true; syncLayers(ctx); } }
  function focusItem(ctx, id) {
    const item = ctx.room.board.find(x => x.id === Number(id));
    if (!item) { ctx.toast("这一项已不在状态板上"); return; }
    if (item.status === "done" && !ctx.doneOpen) { ctx.doneOpen = true; renderAside(ctx); }
    showAside(ctx);
    const row = ctx.aside.querySelector(`[data-wr-row="${item.id}"]`);
    row.scrollIntoView({block:"center", behavior:media("(prefers-reduced-motion:reduce)") ? "auto" : "smooth"});
    row.classList.remove("is-flash"); void row.offsetWidth; row.classList.add("is-flash");
  }

  /* ------------------------------ 渲染 ------------------------------ */
  function renderAside(ctx) { put(ctx.aside, asideHTML(ctx)); }
  function renderLive(ctx) {
    put(ctx.head, headHTML(ctx)); put(ctx.panelbar, panelbarHTML(ctx)); put(ctx.crew, crewHTML(ctx));
    renderAside(ctx); renderComposer(ctx); syncLayers(ctx);
  }
  function renderFeed(ctx, position = "keep") {
    const scroll = ctx.scroll, follow = atBottom(ctx), top = scroll.scrollTop;
    const pin = pinHTML(ctx);
    ctx.pin.hidden = pin === "";
    put(ctx.pin, pin);
    put(ctx.inner, timelineHTML(ctx));
    const unread = position === "unread" && ctx.inner.querySelector(".cx-unread");
    if (unread) scroll.scrollTop = unread.offsetTop - 6;
    else if (position !== "keep" || follow) scroll.scrollTop = scroll.scrollHeight;
    else scroll.scrollTop = top;
    if (ctx.popFor !== null && !ctx.popFor.isConnected) hidePop(ctx);
    ctx.minute = Math.floor(Date.now() / 60000);
    markRead(ctx);
  }
  function render(ctx, position) { renderLive(ctx); renderFeed(ctx, position); }
  async function load(ctx, position = "keep") {
    if (ctx.loading) { ctx.again = true; return; }
    ctx.loading = true;
    try {
      const room = await ctx.api(`/api/op/war_room.get?id=${ctx.id}&limit=200`);
      if (!current(ctx)) return;
      if (ctx.room === null) { ctx.readFrom = room.read_id; position = "unread"; }
      ctx.room = room; ctx.readId = Math.max(ctx.readId, room.read_id);
      render(ctx, position);
    } catch (error) {
      if (!current(ctx)) return;
      if (ctx.room === null) ctx.inner.innerHTML = `<div class="cx-empty">作战室加载失败：${esc(error.message)}</div>`;
      ctx.toast(error.message);
    } finally {
      ctx.loading = false;
      if (ctx.again && current(ctx)) { ctx.again = false; load(ctx); }
    }
  }
  async function markRead(ctx) {
    const room = ctx.room;
    if (!current(ctx) || ctx.reading || room.latest_id <= ctx.readId || document.visibilityState !== "visible" || !atBottom(ctx)) return;
    ctx.reading = true;
    try {
      const result = await ctx.api("/api/op/war_room.mark_read", {project:project(ctx), room:room.id, id:room.latest_id});
      ctx.readId = result.read_id; room.unread = result.unread;
    } catch (error) { if (current(ctx)) ctx.toast(error.message); }
    finally { ctx.reading = false; }
  }
  // 一次只跑一个写操作；失败提示原因，成功后重读房间并让外层刷新 state。
  async function act(ctx, run) {
    if (ctx.busy) return false;
    ctx.busy = true;
    try { await run(); if (current(ctx)) { ctx.onChanged(); await load(ctx); } return true; }
    catch (error) { if (current(ctx)) ctx.toast(error.message); return false; }
    finally { ctx.busy = false; }
  }
  function answer(ctx, id, text) {
    if (text.trim() === "") return Promise.resolve(false);
    return act(ctx, async () => { await ctx.api("/api/op/decision.answer", {id, text}); ctx.toast(`已答复。答复回到牵头 ${ctx.room.lead_name}，由它在房里收口`); });
  }
  function pause(ctx, id) {
    const m = people(ctx).find(item => item.id === id);
    if (m.stop) { ctx.toast(`${m.name}：${m.stop}`); return Promise.resolve(false); }
    return act(ctx, async () => { await ctx.api("/api/op/session.stop", {session_id:id}); ctx.toast(`已请求暂停 ${m.name}（停止当前这一轮）；你再给它发话之前，点名只排队`); });
  }
  async function send(ctx) {
    if (ctx.send.disabled) return;
    const body = ctx.input.value.trim(), version = ctx.version, reply = ctx.replyTo, all = ctx.all;
    const to = all ? ["*"] : people(ctx).filter(m => ctx.selected.has(m.id)).map(m => m.name);
    ctx.sending = true; renderComposer(ctx);
    try {
      const result = await ctx.api("/api/op/war_room.post", {project:project(ctx), room:ctx.id, body, to, ...(reply === null ? {} : {reply_to:reply.id})});
      if (!current(ctx)) return;
      if (ctx.version === version) { ctx.input.value = ""; resize(ctx); }
      ctx.selected.clear(); ctx.all = false; ctx.replyTo = null;
      const told = result.message.mentions.concat(result.message.notified).filter(item => item.delivery !== "none");
      ctx.toast(told.length === 0 ? "已发到作战室（没点名，不叫醒任何人）" : `已发到作战室，${all ? `全体 ${told.length} 位成员` : told.map(item => item.name).join("、")}空闲时收到，送达情况看徽章`);
      await load(ctx, "bottom");
    } catch (error) { if (current(ctx)) ctx.toast(error.message); }
    finally { ctx.sending = false; if (current(ctx)) renderComposer(ctx); }
  }
  function resize(ctx) { ctx.input.style.height = "auto"; ctx.input.style.height = `${Math.min(ctx.input.scrollHeight, 160)}px`; }
  function askLead(ctx) {
    const lead = ctx.room.lead_session_id;
    ctx.all = false; ctx.selected = new Set([lead]);
    ctx.input.value = `@${ctx.room.lead_name} 把还没定的事汇成一张拍板卡给我`; ctx.version += 1;
    renderComposer(ctx); resize(ctx); ctx.input.focus();
  }

  /* ------------------------------ 对话框 ------------------------------ */
  const field = (label, html) => `<label class="wr-field"><span>${label}</span>${html}</label>`;
  function closeDialogHTML(ctx) {
    const c = counts(ctx.room), open = c.doing + c.todo + c.blocked, pending = ctx.room.open_decisions;
    return `<form class="wr-dialog" data-wr-form="close" role="dialog" aria-modal="true" aria-labelledby="wr-dialog-title"><h2 id="wr-dialog-title">${icon("finish")}收尾作战室</h2>`
      + `<p>收尾后作战室只读保留。交流区会出现收尾公告；每位成员收到一条不叫醒的留档通知，带着这次的上下文回原岗。</p>`
      + field("结论（一句话，会写进收尾公告）", '<textarea name="summary" required maxlength="200"></textarea>')
      + `<ul class="wr-dialog-check"><li class="${open ? "warn" : ""}">${icon(open ? "alert" : "done")}状态板：${c.done}/${ctx.room.board.length} 完成${c.blocked ? `，${c.blocked} 项阻塞` : ""}${c.todo ? `，${c.todo} 项待办` : ""}${c.doing ? `，${c.doing} 项进行中` : ""}</li>`
      + `<li class="${pending ? "warn" : ""}">${icon(pending ? "alert" : "done")}${pending ? `${pending} 张拍板卡还没答复` : "拍板卡都已答复"}</li></ul>`
      + '<p class="wr-dialog-error" role="alert"></p><div class="wr-dialog-acts"><button type="button" class="cx-btn" data-wr-dialog-cancel>先不收尾</button>'
      + `<button type="submit" class="cx-btn cx-primary">${icon("finish", "sm")}确认收尾</button></div></form>`;
  }
  function itemDialogHTML(ctx, item) {
    const owners = people(ctx).map(m => `<option value="${esc(m.name)}"${item && item.owner_session_id === m.id ? " selected" : ""}>${esc(m.name)}</option>`).join("");
    const states = ORDER.map(s => `<option value="${s}"${(item ? item.status : "todo") === s ? " selected" : ""}>${STATUS[s]}</option>`).join("");
    return `<form class="wr-dialog" data-wr-form="item" data-id="${item ? item.id : ""}" role="dialog" aria-modal="true" aria-labelledby="wr-dialog-title"><h2 id="wr-dialog-title">${icon("board")}${item ? "改这一项" : "加一项"}</h2>`
      + field("事项", `<input name="title" required maxlength="60" value="${item ? esc(item.title) : ""}">`)
      + `<div class="wr-field-row">${field("负责人", `<select name="owner">${item ? "" : '<option value="">未分配</option>'}${owners}</select>`)}${field("状态", `<select name="status">${states}</select>`)}</div>`
      + field("说明 / 阻塞原因", `<input name="note" maxlength="120" value="${item && item.note ? esc(item.note) : ""}" placeholder="例如：等签名算法定稿">`)
      + '<p class="wr-dialog-error" role="alert"></p><div class="wr-dialog-acts"><button type="button" class="cx-btn" data-wr-dialog-cancel>取消</button>'
      + `<button type="submit" class="cx-btn cx-primary">${icon("check", "sm")}保存</button></div></form>`;
  }
  function contractDialogHTML(ctx) {
    const c = ctx.room.contract;
    return `<form class="wr-dialog wr-dialog-wide" data-wr-form="contract" data-base="${esc(c.updated_at || "")}" role="dialog" aria-modal="true" aria-labelledby="wr-dialog-title"><h2 id="wr-dialog-title">${icon("contract")}编辑接口契约</h2>`
      + `<p>整份替换。保存时会核对你打开时的版本${c.updated_at ? `（${esc(K.clock(c.updated_at))} 由 ${esc(c.updated_by)} 更新）` : ""}，期间有人改过会提示你，不会悄悄覆盖。</p>`
      + `<textarea name="text" class="wr-mono" maxlength="8000" aria-label="契约内容">${esc(c.text || "")}</textarea>`
      + '<p class="wr-dialog-error" role="alert"></p><div class="wr-dialog-acts"><button type="button" class="cx-btn" data-wr-dialog-cancel>取消</button>'
      + `<button type="submit" class="cx-btn cx-primary">${icon("check", "sm")}保存契约</button></div></form>`;
  }
  function budgetDialogHTML(ctx) {
    const b = ctx.room.budget, floor = Math.max(100000, b.spent + 1), value = Math.max(b.tokens * 2, floor);
    return `<form class="wr-dialog" data-wr-form="budget" role="dialog" aria-modal="true" aria-labelledby="wr-dialog-title"><h2 id="wr-dialog-title">${icon("gauge")}追加预算并继续</h2>`
      + `<p>已用 <b>${tokens(b.spent)}</b> / 原上限 ${tokens(b.tokens)} tokens。新上限要大于已用、不少于 1 万；${b.paused_at ? "保存后作战室继续，停下的成员接着推进。" : "到了新上限才会暂停。"}</p>`
      + field("新上限（tokens）", `<input name="budget" type="number" inputmode="numeric" required min="${floor}" step="1" value="${value}"><output class="wr-budget-out" aria-live="polite">= ${tokens(value)} tokens</output>`)
      + '<p class="wr-dialog-error" role="alert"></p><div class="wr-dialog-acts"><button type="button" class="cx-btn" data-wr-dialog-cancel>取消</button>'
      + `<button type="submit" class="cx-btn cx-primary">${icon("check", "sm")}${b.paused_at ? "追加并继续" : "保存上限"}</button></div></form>`;
  }
  async function submitDialog(ctx, form) {
    const kind = form.dataset.wrForm, error = form.querySelector(".wr-dialog-error"), v = name => form.elements[name].value.trim();
    const base = {project:project(ctx), room:ctx.id};
    if (form.dataset.saving) return;
    form.dataset.saving = "1"; error.textContent = "";
    try {
      if (kind === "close") await ctx.api("/api/op/war_room.close", {...base, summary:v("summary")});
      if (kind === "item") await ctx.api("/api/op/war_room.item", {...base, ...(form.dataset.id ? {id:Number(form.dataset.id)} : {}), title:v("title"), status:v("status"), note:v("note"), ...(v("owner") ? {owner:v("owner")} : {})});
      if (kind === "contract") await ctx.api("/api/op/war_room.contract", {...base, text:form.elements.text.value, expected_updated_at:form.dataset.base || null});
      if (kind === "budget") {
        const n = Number(v("budget")), spent = ctx.room.budget.spent;
        if (!Number.isInteger(n) || n <= spent || n < 10000) throw Error(`新上限要是整数，大于已用 ${tokens(spent)}，且不少于 1 万 tokens`);
        await ctx.api("/api/op/war_room.budget", {...base, budget_tokens:n});
        form.dataset.done = `预算上限已改为 ${tokens(n)} tokens${ctx.room.budget.paused_at ? "，作战室继续，成员接着推进" : ""}`;
      }
    } catch (err) {
      if (err.status === 409) { await load(ctx); form.dataset.base = ctx.room.contract.updated_at || ""; error.textContent = `${err.message}。已读到最新版（右侧可看），你的改动还在框里；再点保存会用它整份替换。`; }
      else error.textContent = err.message;
      delete form.dataset.saving;
      return;
    }
    closeDialog(ctx);
    ctx.toast({close:"已收尾：交流区出现收尾公告，成员收到留档通知", item:"状态板已更新", contract:"契约已更新", budget:form.dataset.done}[kind]);
    ctx.onChanged(); load(ctx);
  }
  function inviteMenu(ctx) {
    const inside = new Set(ctx.room.members.map(m => m.session_id)), others = ctx.state.leaders.filter(item => !inside.has(item.session_id));
    return '<small>只能邀请本工作区在用的 Leader</small>' + (others.map(item => `<button type="button" role="menuitem" data-wr-invite="${esc(item.name)}">${ctx.avatar(item.name, 22)}<span>${esc(item.name)}</span></button>`).join("") || "<small>本工作区其它 leader 都已在房里</small>");
  }
  function moreMenu(ctx) {
    return closed(ctx) ? `<button type="button" role="menuitem" data-wr-channel="${ctx.room.closed_message_id}">${icon("msg", "sm")}看收尾公告</button>`
      : `<button type="button" role="menuitem" data-wr="invite">${icon("invite", "sm")}邀请成员</button><button type="button" role="menuitem" data-wr="decide">${icon("gavel", "sm")}发拍板卡</button><button type="button" role="menuitem" data-wr="close">${icon("finish", "sm")}收尾作战室</button><small>${esc(`拍板卡由牵头 ${ctx.room.lead_name} 发；这里会请它汇总`)}</small>`;
  }

  /* ------------------------------ 挂载 ------------------------------ */
  const LAYOUT = '<header class="wr-head"></header><button type="button" class="wr-panelbar" data-wr-panel aria-haspopup="dialog" aria-controls="wr-aside" aria-expanded="false"></button>'
    + '<section class="wr-crew" aria-label="参战成员"></section><div class="wr-body"><section class="wr-feed" aria-label="作战室时间线"><div class="wr-pin" hidden></div>'
    + '<div class="wr-scroll" tabindex="0" aria-label="作战室发言与状态变化"><div class="wr-inner"><div class="cx-empty">正在加载作战室…</div></div></div>'
    + '<div class="cx-composer-wrap wr-composer-wrap"><div class="cx-replybar" hidden></div><div class="cx-targets" role="group" aria-label="点名成员"></div>'
    + `<form class="cx-composer"><textarea rows="1" aria-label="在作战室发言" placeholder="在作战室说…"></textarea><div class="cx-c-row"><span class="cx-hint">Enter 发送 · 只叫醒被点名的成员 · 150 字内，长内容写私聊</span><button type="submit" class="cx-send" aria-label="发送到作战室" disabled>${icon("send")}</button></div></form>`
    + `<div class="wr-closedbar" hidden>${icon("lock", "sm")}<span>作战室已收尾，只读保留<span class="wr-closed-more">。要接着聊，回交流区或打开成员私聊</span></span><button type="button" class="cx-btn" data-act="view" data-view="channel">${icon("msg", "sm")}去交流区</button></div></div></section>`
    + '<aside class="wr-aside" id="wr-aside" aria-label="目标与状态板"></aside></div>'
    + '<div class="wr-backdrop" hidden></div><div class="cx-pop" role="dialog" aria-modal="false" aria-labelledby="cx-pop-title" hidden></div>'
    + '<div class="wr-sheet" role="dialog" aria-modal="true" aria-labelledby="wr-ms-title" hidden></div><div class="wr-menu" role="menu" hidden></div><div class="wr-layerbox" hidden></div>';
  function mount(options) {
    unmount();
    const ctx = {...options, room:null, readFrom:0, readId:0, folds:new Set(), selected:new Set(), all:false, replyTo:null, doneOpen:false, contractOpen:false, asideOpen:false, member:null,
      popFor:null, menuFor:null, dialogReturn:null, loading:false, again:false, reading:false, sending:false, busy:false, composing:false, version:0, minute:0};
    active = ctx;
    const host = options.host, find = selector => host.querySelector(selector);
    host.innerHTML = LAYOUT;
    Object.assign(ctx, {head:find(".wr-head"), panelbar:find(".wr-panelbar"), crew:find(".wr-crew"), pin:find(".wr-pin"), scroll:find(".wr-scroll"), inner:find(".wr-inner"), wrap:find(".wr-composer-wrap"),
      replybar:find(".cx-replybar"), targets:find(".cx-targets"), form:find(".cx-composer"), input:find("textarea"), send:find(".cx-send"), closedbar:find(".wr-closedbar"), aside:find(".wr-aside"),
      backdrop:find(".wr-backdrop"), pop:find(".cx-pop"), sheet:find(".wr-sheet"), menu:find(".wr-menu"), layer:find(".wr-layerbox")});
    CrewBrowser.enableChatLists(ctx.input);
    const find2 = id => ctx.room.messages.find(m => m.id === Number(id));
    const chat = (id, at = null, prefix = null) => { hidePop(ctx); ctx.member = null; ctx.asideOpen = false; syncLayers(ctx); ctx.openChat(id, {at, prefix}); };
    const actions = [
      ["[data-wr-dialog-cancel]", () => closeDialog(ctx)],
      [".wr-layerbox", (node, event) => { if (event.target === node) closeDialog(ctx); }],
      ["[data-badge]", node => { if (ctx.popFor === node) hidePop(ctx); else showPop(ctx, node); }],
      ["[data-go]", node => { const [id, kind, ...rest] = node.dataset.go.split(":"), item = find2(id)[kind].find(x => x.session_id === rest.join(":")); chat(item.session_id, item.delivery === "queued" ? null : item.delivered_at); }],
      [".cx-pop-x", () => hidePop(ctx)],
      ["[data-wr-chat]", node => chat(node.dataset.wrChat)],
      ["[data-wr-detail]", node => { const m = find2(node.dataset.wrDetail); chat(m.speaker_session_id, m.created_at, m.reply && m.reply.speaker_session_id === "user" ? `channel:${m.reply.id}:` : null); }],
      ["[data-wr-channel]", node => ctx.openChannel(Number(node.dataset.wrChannel))],
      ["[data-wr-pause]", node => pause(ctx, node.dataset.wrPause)],
      ["[data-wr-member]", node => { ctx.member = node.dataset.wrMember; syncLayers(ctx); ctx.sheet.querySelector("[data-wr-sheet-close]").focus(); }],
      ["[data-wr-sheet-close]", () => { ctx.member = null; syncLayers(ctx); }],
      ["[data-wr-panel]", () => { ctx.asideOpen = true; syncLayers(ctx); ctx.aside.querySelector("[data-wr-aside-close]").focus(); }],
      ["[data-wr-aside-close]", () => { ctx.asideOpen = false; syncLayers(ctx); ctx.panelbar.focus(); }],
      [".wr-backdrop", () => { hidePop(ctx); ctx.asideOpen = false; ctx.member = null; syncLayers(ctx); }],
      ["[data-wr-reply]", node => { ctx.replyTo = find2(node.dataset.wrReply); renderComposer(ctx); ctx.input.focus(); }],
      ["[data-wr-unreply]", () => { ctx.replyTo = null; renderComposer(ctx); ctx.input.focus(); }],
      ["[data-wr-jump]", node => { const target = ctx.inner.querySelector(`[data-wr-msg="${Number(node.dataset.wrJump)}"]`); hidePop(ctx); if (target) flash(target); else ctx.toast("这条发言不在当前列表里"); }],
      ["[data-wr-to]", node => {
        const id = node.dataset.wrTo;
        if (id === "*") { ctx.all = !ctx.all; ctx.selected.clear(); }
        else if (ctx.all) { ctx.all = false; ctx.selected = new Set(ctx.room.members.map(m => m.session_id).filter(x => x !== id)); }
        else if (!ctx.selected.delete(id)) ctx.selected.add(id);
        renderComposer(ctx);
      }],
      ["[data-wr-fold]", node => { const id = Number(node.dataset.wrFold); if (!ctx.folds.delete(id)) ctx.folds.add(id); renderFeed(ctx); ctx.inner.querySelector(`[data-wr-fold="${id}"]`).focus({preventScroll:true}); }],
      ["[data-wr-done]", () => { ctx.doneOpen = !ctx.doneOpen; renderAside(ctx); }],
      ["[data-wr-item]", node => focusItem(ctx, node.dataset.wrItem)],
      ["[data-wr-row]", node => { if (!closed(ctx)) openDialog(ctx, itemDialogHTML(ctx, ctx.room.board.find(x => x.id === Number(node.dataset.wrRow))), "input"); }],
      ["[data-wr-contract]", () => { ctx.contractOpen = true; renderAside(ctx); showAside(ctx); ctx.aside.querySelector("#wr-contract").scrollIntoView({block:"start"}); }],
      ["[data-wr-goto]", node => { ctx.asideOpen = false; syncLayers(ctx); flash(ctx.inner.querySelector(`[data-wr-decision="${node.dataset.wrGoto}"]`)); }],
      ["[data-wr-answer]", node => { const d = ctx.room.decisions.find(x => x.id === Number(node.dataset.wrAnswer)); answer(ctx, d.id, d.options[Number(node.dataset.wrOption)]); }],
      ["[data-wr-invite]", node => { closeMenu(ctx); act(ctx, async () => { await ctx.api("/api/op/war_room.members", {project:project(ctx), room:ctx.id, add:[node.dataset.wrInvite]}); ctx.toast(`已邀请 ${node.dataset.wrInvite}，它会在房里收到邀请`); }); }],
      ["[data-wr]", node => {
        const a = node.dataset.wr;
        if (a === "invite" || a === "more") { if (ctx.menuFor === node) closeMenu(ctx); else openMenu(ctx, a === "more" || node.getAttribute("role") === "menuitem" ? ctx.head.querySelector(".wr-more") : node, a === "more" ? moreMenu(ctx) : inviteMenu(ctx)); return; }
        closeMenu(ctx);
        if (a === "decide") askLead(ctx);
        if (a === "close") openDialog(ctx, closeDialogHTML(ctx), "textarea");
        if (a === "add-item") openDialog(ctx, itemDialogHTML(ctx, null), "input");
        if (a === "edit-contract") openDialog(ctx, contractDialogHTML(ctx), "textarea");
        if (a === "budget") openDialog(ctx, budgetDialogHTML(ctx), "input");
      }]];
    host.onclick = event => {
      if (ctx.menuFor !== null && !event.target.closest(".wr-menu, [data-wr=invite], [data-wr=more]")) closeMenu(ctx);
      if (ctx.popFor !== null && !event.target.closest(".cx-pop, [data-badge]")) hidePop(ctx);
      for (const [selector, run] of actions) {
        const node = event.target.closest(selector);
        if (node) { if (!node.disabled) run(node, event); return; }
      }
    };
    host.onsubmit = event => {
      event.preventDefault();
      const form = event.target;
      if (form === ctx.form) { if (!ctx.composing) send(ctx); }
      else if (form.dataset.wrFree) answer(ctx, Number(form.dataset.wrFree), form.querySelector("input").value);
      else submitDialog(ctx, form);
    };
    ctx.keys = event => {
      if (event.key === "Escape") { if (!ctx.layer.hidden) closeDialog(ctx); else { closeMenu(ctx); hidePop(ctx); ctx.asideOpen = false; ctx.member = null; syncLayers(ctx); } }
      else if ((event.key === "Enter" || event.key === " ") && event.target.matches?.("[data-wr-row][tabindex]")) { event.preventDefault(); event.target.click(); }
    };
    ctx.visibility = () => { if (ctx.room) markRead(ctx); };
    ctx.media = window.matchMedia("(max-width:1099px)");
    ctx.relayout = () => { hidePop(ctx); closeMenu(ctx); ctx.asideOpen = false; ctx.member = null; syncLayers(ctx); };
    ctx.media.addEventListener("change", ctx.relayout);
    document.addEventListener("keydown", ctx.keys);
    document.addEventListener("visibilitychange", ctx.visibility);
    ctx.scroll.onscroll = () => { if (ctx.popFor !== null && !ctx.pop.classList.contains("is-sheet")) hidePop(ctx); if (ctx.room) markRead(ctx); };
    host.oninput = event => { if (event.target.name === "budget") event.target.nextElementSibling.textContent = `= ${tokens(Number(event.target.value) || 0)} tokens`; };
    ctx.input.oninput = () => { ctx.version += 1; resize(ctx); ctx.send.disabled = ctx.sending || ctx.input.value.trim() === ""; };
    ctx.input.oncompositionstart = () => { ctx.composing = true; };
    ctx.input.oncompositionend = () => { ctx.composing = false; };
    ctx.input.onkeydown = event => { if (!ctx.composing && CrewBrowser.chatSendKey(event)) { event.preventDefault(); send(ctx); } };
    load(ctx);
    return {update, unmount};
  }
  function update(state) {
    if (!active) return;
    const ctx = active;
    ctx.state = state;
    if (ctx.room === null) return;
    renderLive(ctx);
    if (Math.floor(Date.now() / 60000) !== ctx.minute && ctx.popFor === null) renderFeed(ctx);
  }
  // Live changes (docs/EVENT_STREAM_DESIGN.md): null = a new snapshot; a `war_room` change of this room re-reads it
  // (board, contract, cards, new posts); a `channel_message` change replaces a shown post's delivery badges.
  function changed(changes) {
    if (!active) return;
    const ctx = active;
    if (changes === null || changes.some(change => change.type.startsWith("war_room.") && Number(change.entity) === ctx.id)) { load(ctx); return; }
    if (ctx.room === null) return;
    let shown = false;
    for (const change of changes) {
      if (!change.type.startsWith("channel_message.") || change.data === null) continue;
      const index = ctx.room.messages.findIndex(item => item.id === Number(change.entity));
      if (index >= 0) { ctx.room.messages[index] = change.data; shown = true; }
    }
    if (shown && !ctx.loading) renderFeed(ctx);
  }
  function unmount() {
    if (!active) return;
    document.removeEventListener("keydown", active.keys);
    document.removeEventListener("visibilitychange", active.visibility);
    active.media.removeEventListener("change", active.relayout);
    active = null;
  }
  window.CrewWarRoom = {mount, update, changed, unmount};
})();
