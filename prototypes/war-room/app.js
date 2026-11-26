"use strict";
/* 跨 Leader 作战室原型。假数据：支付（牵头）、语音（Codex，外部窗口）、前端 联调“登录接口签名 v2”。
   index.html 渲染作战室；entry.html（body[data-page=entry]）渲染交流区入口小样与状态样例。 */
(() => {
  const PATHS = {
    back:'<path d="m14.5 5.5-6.5 6.5 6.5 6.5"/>', x:'<path d="m6.5 6.5 11 11M17.5 6.5l-11 11"/>',
    room:'<rect x="3.5" y="5" width="17" height="14" rx="2.5"/><path d="M3.5 9.5h17M8 14h3M8 16.5h6"/>',
    send:'<path d="m5 11 7-7 7 7M12 4v16"/>', chev:'<path d="m9 6 6 6-6 6"/>',
    thread:'<path d="M6 4v9a3 3 0 0 0 3 3h9"/><path d="m15 13 3 3-3 3"/>',
    reply:'<path d="M9.5 7 5 11.5 9.5 16"/><path d="M5 11.5h9a5 5 0 0 1 5 5V18"/>',
    clock:'<circle cx="12" cy="12" r="8"/><path d="M12 7.5V12l3 2"/>',
    "clock-alert":'<circle cx="12" cy="12" r="8"/><path d="M12 7.5v5"/><path d="M12 16h.01" stroke-width="2.4"/>',
    check:'<path d="m5.5 12.5 4 4 9-9"/>', replied:'<path d="m2.5 12.5 4 4 9-9"/><path d="m11.5 16 1 .5 9-9"/>',
    chat:'<path d="M5 5.5h14v10H11l-4 3.2v-3.2H5z"/><path d="M9 9.5h6M9 12h3.5"/>',
    msg:'<path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.5 3.5V17A2.5 2.5 0 0 1 4 14.5z"/>',
    hourglass:'<path d="M7 4h10M7 20h10M8 4c0 4.5 8 5.5 8 8s-8 3.5-8 8M16 4c0 4.5-8 5.5-8 8s8 3.5 8 8"/>',
    crown:'<path d="M4.5 18 3.5 8l5 3.8L12 5.5l3.5 6.3 5-3.8-1 10z"/><path d="M5 20.5h14"/>',
    invite:'<circle cx="9.5" cy="8.5" r="3.5"/><path d="M3 20v-.5a6.5 6.5 0 0 1 11.6-4M18 14v6M15 17h6"/>',
    gavel:'<path d="m13.5 4.5 6 6M11 7l6 6M12.2 5.8l-4.4 4.4 6 6 4.4-4.4M10.8 13.2 4 20"/>',
    finish:'<rect x="4" y="4" width="16" height="16" rx="3"/><path d="m8.5 12.3 2.4 2.4 4.6-5"/>',
    pause:'<path d="M9 6v12M15 6v12"/>', play:'<path d="M8 5.5v13l10.5-6.5z"/>',
    takeover:'<path d="M5 5.5h14v10H11l-4 3.2v-3.2H5z"/><path d="m10 10.5 2 2 4-4"/>',
    window:'<rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M3.5 9h17"/><path d="M6.5 7h.01M8.8 7h.01" stroke-width="2"/>',
    doing:'<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor"/>',
    blocked:'<path d="M8.7 3.5h6.6l5.2 5.2v6.6l-5.2 5.2H8.7l-5.2-5.2V8.7z"/><path d="M8.5 12h7"/>',
    todo:'<circle cx="12" cy="12" r="8" stroke-dasharray="3.2 2.6"/>',
    done:'<circle cx="12" cy="12" r="8"/><path d="m8.5 12.3 2.4 2.4 4.6-5"/>',
    contract:'<path d="M8 4.5H6.5a2 2 0 0 0-2 2v3a2 2 0 0 1-2 2 2 2 0 0 1 2 2v3a2 2 0 0 0 2 2H8M16 4.5h1.5a2 2 0 0 1 2 2v3a2 2 0 0 0 2 2 2 2 0 0 0-2 2v3a2 2 0 0 1-2 2H16"/>',
    target:'<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r=".6" fill="currentColor"/>',
    board:'<rect x="4" y="4" width="16" height="16" rx="2.5"/><path d="M4 9.5h16M9.5 9.5V20"/>',
    edit:'<path d="m15.5 4.5 4 4L9 19H5v-4z"/><path d="m13 7 4 4"/>',
    history:'<path d="M4 12a8 8 0 1 0 2.4-5.7L4 8.5"/><path d="M4 4.5v4h4M12 8v4l2.5 1.5"/>',
    plus:'<path d="M12 5v14M5 12h14"/>', more:'<circle cx="6" cy="12" r="1.1"/><circle cx="12" cy="12" r="1.1"/><circle cx="18" cy="12" r="1.1"/>',
    branch:'<circle cx="7" cy="5" r="2"/><circle cx="17" cy="7" r="2"/><circle cx="7" cy="19" r="2"/><path d="M7 7v10m0-4h4a6 6 0 0 0 6-4"/>',
    doc:'<path d="M7 3.5h7l4 4V20a.5.5 0 0 1-.5.5h-10A.5.5 0 0 1 7 20z"/><path d="M13.5 3.5V8H18M9.5 12h6M9.5 15h6M9.5 18h3.5"/>',
    lock:'<rect x="5" y="10.5" width="14" height="9.5" rx="2"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/>',
    archive:'<rect x="4" y="5" width="16" height="4" rx="1"/><path d="M5.5 9v9a1 1 0 0 0 1 1h11a1 1 0 0 0 1-1V9M10 13h4"/>',
    returned:'<path d="M9 14 4.5 9.5 9 5"/><path d="M4.5 9.5H14a5.5 5.5 0 0 1 0 11h-2"/>',
    alert:'<path d="M12 4 21 19.5H3z"/><path d="M12 10v4.5"/><path d="M12 17h.01" stroke-width="2.4"/>',
    home:'<path d="M4 10.5 12 4l8 6.5V20H4z"/><path d="M10 20v-5h4v5"/>', team:'<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20v-.5a6.5 6.5 0 0 1 13 0v.5M15.5 4.7a3.5 3.5 0 0 1 0 6.6M18 13.5a6.5 6.5 0 0 1 3.5 6"/>',
    git:'<circle cx="6" cy="6" r="2"/><circle cx="6" cy="18" r="2"/><circle cx="18" cy="9" r="2"/><path d="M6 8v8M18 11a5 5 0 0 1-5 5H8"/>',
    gear:'<circle cx="12" cy="12" r="3"/><path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8"/>'
  };
  const icon = (name, cls = "") => `<svg class="i ${cls}" viewBox="0 0 24 24" aria-hidden="true">${PATHS[name]}</svg>`;
  const esc = value => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  // 与 frontend/app.js 的 defaultTint 一致，头像颜色和正式版相同
  const COLORS = ["#3E7C74", "#8A5A7E", "#A07A3A", "#4F6B8F", "#5E7F55", "#A8604F", "#8C7650", "#5C5F9E", "#A35D6A", "#4F7A8C", "#77773F"];
  const tint = name => { let n = 0; for (const c of name) n = (n + c.charCodeAt(0)) % COLORS.length; return COLORS[n]; };
  const av = (name, size, round = true) => name === "我"
    ? `<span class="av round cx-av-user" style="--s:${size}px" aria-hidden="true">我</span>`
    : `<span class="av${round ? " round" : ""}" style="--s:${size}px;--tint:${tint(name)}" aria-hidden="true">${esc(name.slice(0, 1))}</span>`;

  /* ------------------------------ 假数据 ------------------------------ */
  const ROOM = {title:"登录接口签名 v2 联调", goal:"三端登录切到签名 v2，旧签名下周一下线", lead:"支付", origin:212, closedAt:"06:10",
    summary:"三端已切到 v2 签名；nonce 端上生成；iOS 老版本保留旧签名到 12-01 再下线。"};
  const MEMBERS = [
    {id:"pay", name:"支付", lead:true, rt:"Claude", p:"working", ctx:46, task:"网关验签 v2", work:"分支 pay/sign-v2 · 交付物 1", go:"交付物", goIc:"branch"},
    {id:"voice", name:"语音", rt:"Codex", p:"external", ctx:71, task:"SDK 签名适配", blocked:true, work:"在外部窗口里跑 · 还没有交付物", go:"私聊", goIc:"chat"},
    {id:"web", name:"前端", rt:"Claude", p:"online", ctx:22, task:"登录页联调", work:"报告：联调用例 9/12 通过", go:"报告", goIc:"doc"}
  ];
  const OTHERS = ["网关", "测试", "运维"];
  const PRESENCE = {
    online:{t:"在线", short:"在线", hint:"在线，点名会立刻送达"},
    working:{t:"在跑任务", short:"在跑", hint:"在跑任务，点名先排队，这一轮结束后送达"},
    external:{t:"外部窗口", short:"外部窗口", hint:"在外部窗口运行，点名会在它下次提示时送达"},
    offline:{t:"离线", short:"离线", hint:"离线，点名先排队"},
    paused:{t:"已暂停", short:"已暂停", hint:"你暂停了它，再发话之前点名只排队"},
    back:{t:"已回原岗", short:"回原岗", hint:"作战室已收尾，它回到原岗位继续"}
  };
  const ITEMS = [
    {id:"i1", s:"doing", title:"网关验签 v2", who:"支付", note:"双签名并行中"},
    {id:"i2", s:"doing", title:"登录页联调", who:"前端", note:"用例 9/12 通过"},
    {id:"i3", s:"blocked", title:"SDK 签名适配（iOS / Android）", who:"语音", note:"阻塞：等签名算法定稿 · 已 19 分钟"},
    {id:"i4", s:"todo", title:"联调用例回归", who:"支付"},
    {id:"i5", s:"todo", title:"旧签名开关（iOS 老版本）", who:"前端"},
    {id:"i6", s:"done", title:"接口契约 v2 初稿", who:"支付"},
    {id:"i7", s:"done", title:"拉齐三端测试账号", who:"前端"}
  ];
  const STATUS = {doing:"进行中", blocked:"阻塞", todo:"待办", done:"完成"};
  const ORDER = ["doing", "blocked", "todo", "done"];
  const DECISIONS = {
    d1:{q:"旧签名保留多久？", why:"旧签名一下线，没升级的客户端就登录不了。", at:"05:10",
      opts:[{k:"A", t:"v2 上线当天就下线", s:"最干净，但老版本用户会被挡在登录页"}, {k:"B", t:"保留到下周一（12-01）", s:"给 iOS 发版留时间，网关多扛一周双签名", rec:true}, {k:"C", t:"保留一个月", s:"最稳，但双签名要多维护四周"}],
      answer:{k:"B", at:"05:13"}, votes:[["支付", "B"], ["前端", "B"], ["语音", "B"]]},
    d2:{q:"nonce 由谁生成？", why:"语音想让网关统一下发，支付和前端倾向端上自己生成。三端得统一，契约里这一行还空着。", at:"05:34",
      opts:[{k:"A", t:"端上生成", s:"各端生成 16 位随机串，网关只查 10 分钟内不重复；不多一次请求", rec:true}, {k:"B", t:"网关下发", s:"登录前多一次请求，弱网下慢 200–400ms；SDK 不用换随机数库"}],
      recWhy:"少一次网络往返。SDK 换随机数库约半天，语音说今天能排进去。", answer:null, votes:[["支付", "A"], ["前端", "A"], ["语音", "B"]]}
  };
  const ORIGIN = {id:212, who:"支付", at:"05:01", text:"登录接口下周一切签名 v2，旧签名同时下线。语音、前端谁来接？", replies:5, people:["支付", "语音", "前端"]};
  const TIMELINE = [
    {k:"event", ic:"room", who:"支付", html:"开了作战室，邀请 <strong>语音</strong>、<strong>前端</strong>", at:"05:02"},
    {k:"msg", id:301, who:"支付", at:"05:03", body:"目标：三端登录都走 v2 签名、网关校验通过。契约我先写了一版在右边，各自把负责的那块填进状态板。@所有成员", all:true,
      mentions:[{name:"语音", d:"replied", reply:303}, {name:"前端", d:"replied", reply:302}]},
    {k:"fold", items:[
      {s:"todo", who:"支付", text:"新增 5 项任务", at:"05:04"},
      {s:"doing", who:"支付", item:"网关验签 v2", at:"05:05", ref:"i1"},
      {s:"doing", who:"前端", item:"登录页联调", at:"05:07", ref:"i2"}]},
    {k:"msg", id:302, who:"前端", at:"05:08", reply:301, body:"收到。登录页只差签名拼接，等契约里 nonce 规则定了就能联调。", notified:[{name:"支付", d:"replied", reply:304}]},
    {k:"decision", id:"d1"},
    {k:"msg", id:303, who:"语音", at:"05:14", reply:301, body:"SDK 现在还是 MD5 拼接，换 HMAC 得先知道 secret 怎么下发。@支付 这个你定一下？", mentions:[{name:"支付", d:"replied", reply:304}]},
    {k:"node", s:"blocked", who:"语音", item:"SDK 签名适配", why:"等签名算法定稿（secret 怎么下发）", at:"05:16", ref:"i3"},
    {k:"node", s:"contract", who:"支付", text:"更新了接口契约：改用 HMAC-SHA256，secret 登录前握手下发", at:"05:20"},
    {k:"msg", id:304, who:"支付", at:"05:21", reply:303, body:"契约更新了：HMAC-SHA256，secret 走 `/handshake` 下发。@语音 看下 SDK 能不能接。", mentions:[{name:"语音", d:"queued", age:14, stale:true, waiting:"external"}]},
    {k:"unread", n:3},
    {k:"msg", id:305, who:"前端", at:"05:29", body:"@语音 nonce 你们打算端上生成还是等网关给？我这边两种都能接，但三端得统一。", mentions:[{name:"语音", d:"queued", age:6, waiting:"external"}]},
    {k:"msg", id:306, who:"我", user:true, at:"05:31", body:"iOS 老版本先别动，下周才发版。@所有成员", all:true,
      mentions:[{name:"支付", d:"delivered", age:4}, {name:"语音", d:"queued", age:4, waiting:"external"}, {name:"前端", d:"replied", reply:307}]},
    {k:"msg", id:307, who:"前端", at:"05:32", reply:306, body:"好，iOS 老版本先保持旧签名，开关我加在登录页配置里。"},
    {k:"node", s:"todo", who:"前端", item:"旧签名开关（iOS 老版本）", verb:"新增", at:"05:33", ref:"i5"},
    {k:"decision", id:"d2"}
  ];
  const CLOSED_TAIL = [
    {k:"node", s:"done", who:"语音", item:"SDK 签名适配（iOS / Android）", at:"05:58", ref:"i3"},
    {k:"event", ic:"finish", who:"支付", html:"收尾了作战室：三端已切 v2，用例 12/12 通过", at:"06:10"}
  ];
  const CONTRACT = [
    ["line", "POST /api/v2/login"],
    ["kv", "X-Sign-Ver", "2"],
    ["kv", "X-Ts", "秒级时间戳，误差 ±300s"],
    ["kv", "X-Nonce", "16 位 [a-z0-9]，10 分钟内不复用", "nonce"],
    ["kv", "X-Sign", "HMAC-SHA256(secret, ts+nonce+body)"],
    ["kv", "secret", "登录前 POST /handshake 下发，30 分钟有效"],
    ["c", "# 40121 签名错 · 40122 过期 · 40123 nonce 重复"]
  ];
  const ACCEPT = [
    {t:"三端测试账号拉齐", ok:true},
    {t:"三端登录带 v2 签名，网关校验通过", n:"1/3"},
    {t:"联调用例全部通过", n:"9/12"},
    {t:"iOS 老版本可一键退回旧签名"}
  ];

  /* ------------------------------ 状态 ------------------------------ */
  const params = new URLSearchParams(location.search);
  const S = {closed:params.get("closed") === "1", fold:params.has("fold"), doneOpen:false, contractOpen:params.has("contract"), paused:new Set(), answers:{},
    reply:params.get("reply") ? Number(params.get("reply")) : null, targets:new Set(), allTargets:false, extra:[], panel:params.get("sheet") === "panel", member:params.get("sheet") === "member" ? "voice" : null};
  const msgById = id => TIMELINE.concat(S.extra).find(x => x.k === "msg" && x.id === id);
  const plain = text => String(text).replace(/`/g, "");
  const narrow = () => matchMedia("(max-width:760px)").matches;
  const memberBy = name => MEMBERS.find(m => m.name === name);
  const presenceOf = m => S.closed ? "back" : S.paused.has(m.id) ? "paused" : m.p;
  const decisionOf = id => {
    const d = DECISIONS[id], answer = S.answers[id] || d.answer || (S.closed && id === "d2" ? {k:"A", at:"05:41"} : null);
    return {...d, answer};
  };
  const itemsNow = () => S.closed ? ITEMS.map(x => ({...x, s:"done", note:x.id === "i3" ? "05:58 解除阻塞后完成" : undefined})) : ITEMS;

  /* ------------------------------ 小部件 ------------------------------ */
  function presenceMark(p) {
    if (p === "working") return '<span class="dot working"></span>';
    if (p === "offline") return '<span class="dot offline"></span>';
    if (p === "online") return '<i class="wr-pdot"></i>';
    return icon({external:"window", paused:"pause", back:"returned"}[p], "xs");
  }
  const badge = p => `<span class="wr-badge" data-p="${p}" aria-hidden="true">${p === "online" ? "<i></i>" : presenceMark(p)}</span>`;
  function ring(pct) {
    const c = 2 * Math.PI * 6, level = pct >= 85 ? "danger" : pct >= 65 ? "warn" : "calm";
    return `<span class="context-ring ${level} wr-ring" role="img" aria-label="上下文已用 ${pct}%${level === "warn" ? "，快满了" : ""}" title="上下文已用 ${pct}%"><svg viewBox="0 0 16 16" aria-hidden="true"><circle class="context-track" cx="8" cy="8" r="6"/><circle class="context-arc" cx="8" cy="8" r="6" stroke-dasharray="${(c * pct / 100).toFixed(2)} ${c.toFixed(2)}"/></svg><span class="context-pct">${pct}%</span></span>`;
  }
  const chip = (s, text = STATUS[s]) => `<span class="wr-chip" data-s="${s}">${icon(s, "xs")}${esc(text)}</span>`;
  const DL = {queued:{ic:"clock", label:"排队中"}, delivered:{ic:"check", label:"已送达"}, replied:{ic:"replied", label:"已回复"}};
  function dlBadge(msgId, item, kind) {
    if (S.closed && item.d === "queued") item = {...item, d:"delivered", stale:false, age:item.age};
    const ui = item.stale ? {ic:"clock-alert", label:`排队 ${item.age} 分钟`} : DL[item.d], what = kind === "notified" ? "回复通知" : "点名";
    const name = `${kind === "notified" ? "" : "@"}${esc(item.name)}`, key = `${msgId}:${kind}:${item.name}`;
    return `<span class="cx-dl" data-state="${item.d}"${item.stale ? " data-stale" : ""}><button type="button" class="cx-dl-main" data-badge="${esc(key)}" aria-label="${esc(`${what} ${item.name}：${ui.label}。点开看说明`)}"><span class="cx-dl-name">${name}</span><span class="cx-dl-sep" aria-hidden="true"></span>${icon(ui.ic, "xs")}<span class="cx-dl-text">${esc(ui.label)}</span></button>`
      + `<button type="button" class="cx-dl-go" data-takeover="${esc(memberBy(item.name)?.id || "")}" aria-label="${esc(`打开 ${item.name} 的私聊`)}" title="${esc(`打开 ${item.name} 的私聊`)}">${icon("chat", "xs")}</button></span>`;
  }
  function deliveryRow(m) {
    const group = (label, kind) => !m[kind] || m[kind].length === 0 ? "" : `<div class="cx-dl-group"><span class="cx-dl-label">${label}</span><span class="cx-dl-list">${m[kind].map(x => dlBadge(m.id, x, kind)).join("")}</span></div>`;
    const html = group(m.all ? "点名全体" : "点名", "mentions") + group("回复通知", "notified");
    return html && `<div class="cx-dl-row">${html}</div>`;
  }
  function bodyHTML(text) {
    return esc(text).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/@(所有成员|支付|语音|前端)/g, '<span class="cx-at">@$1</span>');
  }
  function messageHTML(m) {
    const reply = m.reply ? msgById(m.reply) : null, closed = S.closed;
    const quote = reply ? `<button type="button" class="cx-quote" data-jump="${reply.id}" aria-label="跳到被回复的 #${reply.id} ${esc(reply.who)} 的发言">${icon("reply", "xs")}<span class="cx-id">#${reply.id}</span><strong>${esc(reply.who)}</strong><span class="cx-quote-text">${esc(plain(reply.body))}</span></button>` : "";
    const meta = `<div class="cx-meta"><strong>${esc(m.who)}</strong><span class="cx-id">#${m.id}</span><time>${m.at}</time>${closed ? "" : `<button type="button" class="cx-reply-btn" data-reply="${m.id}" aria-label="回复 #${m.id}">${icon("reply", "xs")}<span>回复</span></button>`}</div>`;
    const body = `<div class="${m.user ? "cx-bubble" : "cx-content"}"><div class="markdown cx-md"><p>${bodyHTML(m.body)}</p></div></div>`;
    return `<article class="cx-msg${m.user ? " cx-user" : ""}" data-msg="${m.id}">${av(m.who, 28)}<div class="cx-body-col">${meta}${quote}${body}${deliveryRow(m)}</div></article>`;
  }
  function nodeHTML(n) {
    let txt;
    if (n.s === "contract") txt = `${av(n.who, 18)}<strong>${esc(n.who)}</strong><span>${esc(n.text)}</span>`;
    else if (n.text) txt = `${av(n.who, 18)}<strong>${esc(n.who)}</strong><span>${esc(n.text)}</span>`;
    else if (n.verb) txt = `${av(n.who, 18)}<strong>${esc(n.who)}</strong><span>${n.verb}</span><q>${esc(n.item)}</q>${chip(n.s)}`;
    else txt = `${av(n.who, 18)}<strong>${esc(n.who)}</strong><span>把</span><q>${esc(n.item)}</q><span>标为</span>${chip(n.s)}${n.why ? `<span class="wr-node-why">${esc(n.why)}</span>` : ""}`;
    const go = n.ref ? `<button type="button" class="wr-link" data-item="${n.ref}">在状态板看</button>` : n.s === "contract" ? '<button type="button" class="wr-link" data-contract>看改动</button>' : "";
    const label = n.s === "contract" ? "契约变化" : n.text && n.s === "todo" ? "状态板变化" : `状态变化：${STATUS[n.s]}`;
    return `<div class="wr-node" data-s="${n.s}" role="note" aria-label="${label}"><span class="wr-node-ic">${icon(n.s === "contract" ? "contract" : n.text && n.s === "todo" ? "board" : n.s, "sm")}</span><span class="wr-node-txt">${txt}</span><time>${n.at}</time>${go}</div>`;
  }
  const eventHTML = e => `<div class="wr-node" data-s="event"><span class="wr-node-ic">${icon(e.ic, "sm")}</span><span class="wr-node-txt">${av(e.who, 18)}<strong>${esc(e.who)}</strong><span>${e.html}</span></span><time>${e.at}</time></div>`;
  function foldHTML(f) {
    const first = f.items[0];
    return `<div class="wr-fold"><button type="button" class="wr-fold-btn" data-fold aria-expanded="${S.fold}">${icon("chev", "xs chev")}<span>${f.items.length} 条状态变化</span><span>· ${esc(first.who)} ${esc(first.text || "")} 等</span></button>`
      + `${S.fold ? `<div class="wr-fold-list">${f.items.map(nodeHTML).join("")}</div>` : ""}</div>`;
  }
  function decisionHTML(id, compact = false) {
    const d = decisionOf(id), answered = Boolean(d.answer), lead = ROOM.lead;
    const state = answered ? `<span class="wr-dc-state" data-s="answered">${icon("check", "xs")}已答复</span>` : `<span class="wr-dc-state" data-s="pending">${icon("hourglass", "xs")}待你答复</span>`;
    const head = `<div class="wr-dc-head"><span class="wr-dc-kind">${icon("gavel", "sm")}拍板卡</span>${state}<span class="wr-dc-from">${av(lead, 16)}牵头 ${esc(lead)} 汇总 · ${d.at}</span></div><h3 class="wr-dc-q" id="dc-${id}">${esc(d.q)}</h3>`;
    const votes = `<div class="wr-dc-votes"><span>成员意见</span>${d.votes.map(([who, k]) => `<span class="wr-vote">${av(who, 16)}${esc(who)} <b>${k}</b></span>`).join("")}</div>`;
    if (answered) {
      const pick = d.opts.find(o => o.k === d.answer.k);
      return `<section class="wr-decide" data-s="answered" data-decision="${id}" aria-labelledby="dc-${id}">${head}<div class="wr-dc-answer">${icon("check", "sm")}<div>你选了 <b>${pick.k} · ${esc(pick.t)}</b><small>${d.answer.at} 答复 · ${esc(pick.s)}</small></div></div>`
        + `<div class="wr-dc-foot"><span>其余 ${d.opts.length - 1} 个选项已收起</span><span>·</span><span>答复已回到牵头 ${esc(lead)}，由它在房里收口</span></div></section>`;
    }
    const opts = d.opts.map(o => `<button type="button" class="opt${o.rec ? " recommended" : ""}" data-answer="${id}:${o.k}"><span class="k">${o.k}</span><span class="t"><strong>${esc(o.t)}</strong><small>${esc(o.s)}</small></span>${o.rec ? '<span class="rec">推荐</span>' : ""}</button>`).join("");
    return `<section class="wr-decide" data-s="pending" data-decision="${id}" aria-labelledby="dc-${id}">${head}<p class="wr-dc-why">${esc(d.why)}</p>`
      + `<div class="wr-dc-body"><div class="opts" role="group" aria-label="选项">${opts}</div>${d.recWhy ? `<p class="wr-dc-why" style="margin:8px 0 0"><b style="color:var(--accent-text);font-weight:500">推荐 A：</b>${esc(d.recWhy)}</p>` : ""}`
      + `<form class="free" data-free="${id}"><input placeholder="或者直接说你的想法…" aria-label="自由答复"><button type="submit" class="icon-btn" aria-label="提交答复">${icon("send", "sm")}</button></form>${votes}</div></section>`;
  }

  /* ------------------------------ 作战室页面 ------------------------------ */
  function railHTML() {
    return `<span class="rail-brand" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="2.6" fill="currentColor"/></svg></span>`
      + `<span class="rail-divider"></span><button class="rail-button" aria-label="工作台">${icon("home")}</button><button class="rail-button" aria-label="Leader">${icon("team")}</button>`
      + `<button class="rail-button active" aria-label="Leader 交流区 · 作战室">${icon("room")}<i class="wr-rail-dot"></i></button><button class="rail-button" aria-label="Git 助手">${icon("git")}</button><span class="wr-rail-space"></span><button class="rail-button" aria-label="设置">${icon("gear")}</button>`;
  }
  function headHTML() {
    const status = S.closed ? `<span class="wr-status" data-s="closed">${icon("archive", "xs")}已收尾 · ${ROOM.closedAt}</span>` : `<span class="wr-status" data-s="open"><span class="dot working" aria-hidden="true"></span>进行中</span>`;
    const actions = S.closed
      ? `<span class="wr-readonly">${icon("lock", "xs")}只读</span><button type="button" class="cx-btn" data-act="notice" aria-label="看收尾公告">${icon("msg", "sm")}<span class="wr-act-label">看收尾公告</span></button>`
      : `<button type="button" class="cx-btn" data-act="invite" aria-haspopup="menu" aria-label="邀请成员">${icon("invite", "sm")}<span class="wr-act-label">邀请</span></button>`
        + `<button type="button" class="cx-btn" data-act="decide" aria-label="发拍板卡（请牵头汇总）" title="拍板卡由牵头 支付 发：点这里会在发言框里请它把待定的事汇成一张">${icon("gavel", "sm")}<span class="wr-act-label">发拍板卡</span></button>`
        + `<button type="button" class="cx-btn cx-ghost" data-act="close" aria-label="收尾作战室">${icon("finish", "sm")}<span class="wr-act-label">收尾</span></button>`;
    return `<header class="wr-head"><button type="button" class="cx-icon-btn wr-back" aria-label="返回 Leader 交流区">${icon("back")}</button>`
      + `<div class="wr-title"><h1><span class="wr-title-ic" aria-hidden="true">${icon("room")}</span><span class="wr-title-text" id="wr-title">${esc(ROOM.title)}</span>${status}</h1>`
      + `<p class="wr-sub">${status}<span class="wr-sub-lead">牵头 ${av(ROOM.lead, 16)}<b>${esc(ROOM.lead)}</b></span><span class="sep" aria-hidden="true"></span><span class="wr-sub-goal">目标：<b>${esc(ROOM.goal)}</b></span></p></div>`
      + `<div class="wr-head-actions">${actions}<button type="button" class="cx-icon-btn wr-more" data-act="more" aria-label="更多操作" aria-haspopup="menu">${icon("more")}</button></div></header>`;
  }
  function counts() {
    const c = {doing:0, blocked:0, todo:0, done:0};
    for (const x of itemsNow()) c[x.s] += 1;
    return c;
  }
  function panelbarHTML() {
    const c = counts(), pending = Object.keys(DECISIONS).filter(id => !decisionOf(id).answer).length;
    const bits = S.closed ? `<span>${c.done} 项完成 · 2 张拍板卡已答复</span>` : `${c.blocked ? chip("blocked", `${c.blocked} 阻塞`) : ""}${pending ? `<span class="wr-dc-state" data-s="pending" style="height:20px">${icon("hourglass", "xs")}${pending} 待你拍板</span>` : ""}<span class="wr-pb-rest" style="color:var(--wr-muted)">${c.doing} 进行中 · ${c.todo} 待办</span>`;
    return `<button type="button" class="wr-panelbar" data-panel aria-haspopup="dialog" aria-controls="wr-aside" aria-expanded="${S.panel}"><span class="wr-pb-ic">${icon("board", "sm")}</span><span class="wr-pb-txt"><strong>目标与状态板</strong>${bits}</span><span class="wr-pb-more">展开${icon("chev", "xs")}</span></button>`;
  }
  function crewHTML() {
    const list = MEMBERS.map(m => {
      const p = presenceOf(m), pr = PRESENCE[p], paused = S.paused.has(m.id);
      const task = `<span class="wr-mem-sep" aria-hidden="true">·</span><span class="wr-mem-task${m.blocked && !S.closed ? " is-blocked" : ""}">${m.blocked && !S.closed ? icon("blocked", "xs") : ""}${esc(m.task)}${m.blocked && !S.closed ? "（阻塞）" : ""}</span>`;
      const acts = S.closed
        ? `<button type="button" class="wr-mini" data-takeover="${m.id}" aria-label="打开 ${esc(m.name)} 的私聊">${icon("chat", "xs")}<span class="wr-mini-t">私聊</span></button>`
        : `<button type="button" class="wr-mini" data-pause="${m.id}" aria-pressed="${paused}" aria-label="${paused ? "继续" : "暂停"} ${esc(m.name)}">${icon(paused ? "play" : "pause", "xs")}<span class="wr-mini-t">${paused ? "继续" : "暂停"}</span></button>`
          + `<button type="button" class="wr-mini" data-takeover="${m.id}" aria-label="接管 ${esc(m.name)}：打开它的私聊">${icon("takeover", "xs")}<span class="wr-mini-t">接管</span></button>`;
      return `<li class="wr-mem" data-p="${p}"${m.lead ? " data-lead" : ""}><button type="button" class="wr-mem-open" data-member="${m.id}" aria-label="${esc(`${m.name}${m.lead ? "（牵头）" : ""}，${m.rt}，${pr.t}，正在做：${m.task}。点开看详情`)}"></button>`
        + `<span class="wr-mem-av">${av(m.name, 34, false)}${badge(p)}${m.lead ? `<span class="wr-lead-flag" aria-hidden="true">${icon("crown", "xs")}</span>` : ""}</span>`
        + `<span class="wr-mem-name"><strong>${esc(m.name)}</strong>${m.lead ? `<span class="wr-lead">${icon("crown", "xs")}牵头</span>` : ""}<span class="cx-rt" data-rt="${m.rt}">${m.rt}</span></span>`
        + `<span class="wr-mem-state"><span class="wr-pres" data-p="${p}">${presenceMark(p)}<span>${narrowLabel(pr)}</span></span>${task}</span>${ring(m.ctx)}<span class="wr-mem-acts">${acts}</span></li>`;
    }).join("");
    const add = S.closed ? "" : `<li class="wr-mem-add"><button type="button" data-act="invite" aria-haspopup="menu">${icon("plus", "sm")}<span>邀请</span></button></li>`;
    return `<section class="wr-crew" aria-label="参战成员"><ul class="wr-crew-list">${list}${add}</ul></section>`;
  }
  const narrowLabel = pr => matchMedia("(max-width:620px)").matches ? pr.short : pr.t;
  function timelineHTML() {
    const origin = `<section class="wr-origin" aria-label="起因"><div class="wr-origin-label">${icon("thread", "xs")}起因 <span>· 从交流区这条脉络升级，原脉络留在交流区</span></div>`
      + `<button type="button" class="cx-quote" data-act="origin" aria-label="在交流区打开 #${ORIGIN.id}">${av(ORIGIN.who, 18)}<span class="cx-id">#${ORIGIN.id}</span><strong>${esc(ORIGIN.who)}</strong><span class="cx-quote-text">${esc(ORIGIN.text)}</span></button>`
      + `<div class="wr-origin-foot"><span class="cx-thread-avs" aria-hidden="true">${ORIGIN.people.map(n => av(n, 16)).join("")}</span><span>脉络 · ${ORIGIN.replies} 条回复 · ${ORIGIN.at}</span><button type="button" class="wr-link" data-act="origin">去交流区看${icon("chev", "xs")}</button></div></section>`;
    const list = TIMELINE.concat(S.closed ? CLOSED_TAIL : S.extra);
    const items = list.map(x => {
      if (x.k === "msg") return messageHTML(x);
      if (x.k === "node") return nodeHTML(x);
      if (x.k === "event") return eventHTML(x);
      if (x.k === "fold") return foldHTML(x);
      if (x.k === "decision") return decisionHTML(x.id);
      if (x.k === "unread") return S.closed ? "" : `<div class="cx-divider cx-unread"><span>新发言 · ${x.n} 条未读</span></div>`;
      return "";
    }).join("");
    return origin + items;
  }
  function composerHTML() {
    if (S.closed) return `<div class="cx-composer-wrap wr-composer-wrap"><div class="wr-closedbar">${icon("lock", "sm")}<span>作战室已收尾，只读保留<span class="wr-closed-more">。要接着聊，回交流区或打开成员私聊</span></span><button type="button" class="cx-btn" data-act="origin">${icon("msg", "sm")}去交流区</button></div></div>`;
    const reply = S.reply ? msgById(S.reply) : null;
    const replybar = reply ? `<div class="cx-replybar">${icon("reply", "sm")}<button type="button" class="cx-replybar-quote" data-jump="${reply.id}"><span>回复 <span class="cx-id">#${reply.id}</span> <strong>${esc(reply.who)}</strong></span><span class="cx-replybar-text">${esc(plain(reply.body))}</span></button>${!reply.user && !S.allTargets && !S.targets.has(reply.who) ? `<span class="cx-replybar-note">会通知 ${esc(reply.who)}</span>` : ""}<button type="button" class="cx-icon-btn cx-replybar-x" data-unreply aria-label="取消回复">${icon("x", "sm")}</button></div>` : "";
    const targets = `<span class="cx-target-label">点名</span><button type="button" class="cx-target wr-target-all${S.allTargets ? " selected" : ""}" data-to="*" aria-pressed="${S.allTargets}" title="叫醒全体成员（不含房外的 leader）">@所有成员</button>`
      + MEMBERS.map(m => {
        const p = presenceOf(m), on = S.allTargets || S.targets.has(m.name), dotP = p === "paused" ? "offline" : p;
        return `<button type="button" class="cx-target${on ? " selected" : ""}" data-to="${esc(m.name)}" aria-pressed="${on}" title="${esc(m.name + "：" + PRESENCE[p].hint)}" aria-label="${esc(`点名 ${m.name}（${PRESENCE[p].t}）`)}"><i class="cx-tdot" data-p="${dotP}" aria-hidden="true"></i>@${esc(m.name)}${p === "online" ? "" : `<small>${PRESENCE[p].short}</small>`}</button>`;
      }).join("");
    return `<div class="cx-composer-wrap wr-composer-wrap${reply ? " is-replying" : ""}">${replybar}<div class="cx-targets" role="group" aria-label="点名成员">${targets}</div>`
      + `<form class="cx-composer" data-composer><textarea rows="1" aria-label="在作战室发言" placeholder="${reply ? `回复 ${esc(reply.who)}…` : "在作战室说…"}"></textarea><div class="cx-c-row"><span class="cx-hint">Enter 发送 · 只叫醒被点名的成员 · 150 字内，长内容写私聊</span><button type="submit" class="cx-send" aria-label="发送到作战室">${icon("send")}</button></div></form></div>`;
  }
  function asideHTML() {
    const items = itemsNow(), c = counts();
    const accept = ACCEPT.map(a => {
      const ok = a.ok || S.closed;
      return `<li class="${ok ? "ok" : ""}">${icon(ok ? "done" : "todo", "xs")}<span>${esc(a.t)}</span>${a.n && !S.closed ? `<small>${a.n}</small>` : ""}</li>`;
    }).join("");
    const groups = ORDER.map(s => {
      const list = items.filter(x => x.s === s);
      if (list.length === 0) return "";
      const collapsible = s === "done" && !S.closed, open = !collapsible || S.doneOpen;
      const h = collapsible
        ? `<button type="button" class="wr-group-h" data-s="${s}" data-done-toggle aria-expanded="${open}">${icon(s, "xs")}${STATUS[s]} <span class="n">${list.length}</span>${icon("chev", "xs chev")}</button>`
        : `<div class="wr-group-h" data-s="${s}">${icon(s, "xs")}${STATUS[s]} <span class="n">${list.length}</span></div>`;
      const rows = open ? `<ul class="wr-items">${list.map(x => `<li class="wr-item" data-s="${s}" data-item-row="${x.id}" tabindex="0" aria-label="${esc(`${x.title}，${x.who} 负责，${STATUS[s]}${x.note ? "，" + x.note : ""}`)}">${icon(s)}<span class="wr-item-title">${esc(x.title)}</span><span class="wr-item-who">${av(x.who, 16)}${esc(x.who)}</span>${x.note ? `<span class="wr-item-note">${esc(x.note)}</span>` : ""}</li>`).join("")}</ul>` : "";
      return `<div class="wr-group">${h}${rows}</div>`;
    }).join("");
    const contract = CONTRACT.map(([kind, a, b, flag]) => kind === "line" ? `<span class="k wr-code-wide">${esc(a)}</span>` : kind === "c" ? `<span class="c wr-code-wide">${esc(a)}</span>` : `<span class="k">${esc(a)}</span><span>${esc(b)}${flag && !S.closed ? " <mark>待拍板：谁生成</mark>" : ""}</span>`).join("");
    const dl = Object.keys(DECISIONS).map(id => {
      const d = decisionOf(id);
      return `<li data-s="${d.answer ? "answered" : "pending"}"><button type="button" data-goto-decision="${id}">${icon("gavel", "xs")}<span class="q">${esc(d.q)}</span>${d.answer ? `<span class="wr-dc-state" data-s="answered">${icon("check", "xs")}${d.answer.k}</span>` : `<span class="wr-dc-state" data-s="pending">${icon("hourglass", "xs")}待你答复</span>`}</button></li>`;
    }).join("");
    const work = MEMBERS.map(m => `<li>${av(m.name, 26, false)}<span class="wr-work-txt"><strong>${esc(m.name)} · ${esc(m.task)}</strong><small>${esc(S.closed ? "已完成 · 回到原岗" : m.work)}</small></span><button type="button" class="wr-work-go" data-takeover="${m.id}" aria-label="${esc(`打开 ${m.name} 的${m.go}`)}">${icon(m.goIc, "xs")}${m.go}</button></li>`).join("");
    return `<aside class="wr-aside${S.panel ? " is-open" : ""}" id="wr-aside" aria-label="目标与状态板"><div class="wr-aside-head"><span class="wr-grab" aria-hidden="true"></span><div><strong>目标与状态板</strong><button type="button" class="cx-icon-btn" data-panel-close aria-label="关闭">${icon("x")}</button></div></div>`
      + `<section class="wr-sec"><h2>${icon("target", "xs")}目标与验收</h2><p class="wr-goal">${esc(ROOM.goal)}。</p><ul class="wr-accept">${accept}</ul></section>`
      + `<section class="wr-sec"><h2>${icon("board", "xs")}任务状态板 <span class="cx-count">${items.length}</span>${S.closed ? "" : `<button type="button" class="wr-sec-act" data-act="add-item">${icon("plus", "xs")}加一项</button>`}</h2>${groups}</section>`
      + `<section class="wr-sec" id="wr-contract"><h2>${icon("contract", "xs")}接口契约 <span class="cx-count">第 3 版</span></h2><div class="wr-contract${S.contractOpen ? "" : " is-clamped"}"><div class="wr-contract-meta">${icon("history", "xs")}<span>05:20 由 ${av("支付", 14)}<b>支付</b> 更新</span>${S.closed ? "" : `<button type="button" class="wr-sec-act" data-act="edit-contract">${icon("edit", "xs")}编辑</button>`}</div><div class="wr-code" role="table" aria-label="接口契约内容">${contract}</div>${S.contractOpen ? "" : `<div class="wr-contract-more"><button type="button" class="wr-sec-act" data-contract>展开全部</button></div>`}</div></section>`
      + `<section class="wr-sec"><h2>${icon("gavel", "xs")}拍板卡 <span class="cx-count">${Object.keys(DECISIONS).length}</span></h2><ul class="wr-dlist">${dl}</ul></section>`
      + `<section class="wr-sec"><h2>${icon("doc", "xs")}成员当前任务与交付物</h2><ul class="wr-work">${work}</ul></section></aside>`;
  }
  function pinHTML() {
    return S.closed ? `<div class="wr-pin"><div class="wr-summary">${icon("finish")}<div><strong>收尾结论</strong><p>${esc(ROOM.summary)}</p><small>${av(ROOM.lead, 14)} 支付 收尾于 ${ROOM.closedAt} · 用时 1 小时 8 分 · 成员已收到留档通知，回到原岗</small></div></div></div>` : "";
  }
  function renderRoom() {
    const host = document.getElementById("wr-room"), scroll = host.querySelector(".wr-scroll"), aside = host.querySelector(".wr-aside");
    const keep = scroll ? scroll.scrollTop : null, keepAside = aside ? aside.scrollTop : 0;
    host.innerHTML = headHTML() + panelbarHTML() + crewHTML()
      + `<div class="wr-body"><section class="wr-feed" aria-label="作战室时间线">${pinHTML()}<div class="wr-scroll" tabindex="0" aria-label="作战室发言与状态变化"><div class="wr-inner">${timelineHTML()}</div></div>${composerHTML()}</section>${asideHTML()}</div>`;
    const sc = host.querySelector(".wr-scroll");
    sc.scrollTop = keep === null ? (params.get("scroll") === "top" ? 0 : sc.scrollHeight) : keep;
    host.querySelector(".wr-aside").scrollTop = keep === null && params.get("aside") === "bottom" ? 1e6 : keepAside;
    syncLayers();
    for (const b of document.querySelectorAll("[data-proto]")) b.setAttribute("aria-pressed", String((b.dataset.proto === "closed") === S.closed && b.dataset.proto !== "theme"));
  }

  /* ------------------------------ 浮层 ------------------------------ */
  const $ = id => document.getElementById(id);
  let toastTimer = 0;
  function toast(text) { const t = $("wr-toast"); t.textContent = text; t.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 2800); }
  function syncLayers() {
    const aside = document.querySelector(".wr-aside");
    if (aside) aside.classList.toggle("is-open", S.panel);
    const sheet = $("wr-member-sheet");
    sheet.hidden = !S.member;
    if (S.member) sheet.innerHTML = memberSheetHTML(MEMBERS.find(m => m.id === S.member));
    const drawer = S.panel && matchMedia("(max-width:1099px)").matches;
    $("wr-backdrop").hidden = !(drawer || S.member || !$("wr-dialog").hidden);
  }
  function memberSheetHTML(m) {
    const p = presenceOf(m), pr = PRESENCE[p], paused = S.paused.has(m.id);
    return `<span class="wr-grab" aria-hidden="true"></span><div class="wr-ms-head"><span class="wr-mem-av" style="position:relative">${av(m.name, 44, false)}${badge(p)}</span><div><strong id="wr-ms-title">${esc(m.name)}${m.lead ? `<span class="wr-lead">${icon("crown", "xs")}牵头</span>` : ""}<span class="cx-rt" data-rt="${m.rt}">${m.rt}</span></strong><small><span class="wr-pres" data-p="${p}" style="display:inline-flex">${presenceMark(p)}${pr.t}</span></small></div><button type="button" class="cx-icon-btn" data-sheet-close aria-label="关闭">${icon("x")}</button></div>`
      + `<dl class="wr-ms-facts"><dt>正在做</dt><dd>${esc(m.task)}${m.blocked && !S.closed ? chip("blocked") : ""}</dd><dt>上下文</dt><dd>${ring(m.ctx)}</dd><dt>点名会</dt><dd>${esc(pr.hint)}</dd><dt>交付物</dt><dd>${esc(m.work)}</dd></dl>`
      + `<div class="wr-ms-acts">${S.closed ? "" : `<button type="button" class="cx-btn" data-pause="${m.id}" aria-pressed="${paused}">${icon(paused ? "play" : "pause", "sm")}${paused ? "继续" : "暂停"}</button>`}<button type="button" class="cx-btn cx-primary" data-takeover="${m.id}">${icon("takeover", "sm")}${S.closed ? "打开私聊" : "接管（打开私聊）"}</button></div>`;
  }
  function openMenu(anchor, html) {
    const menu = $("wr-menu"); menu.innerHTML = html; menu.hidden = false;
    const r = anchor.getBoundingClientRect(), w = menu.offsetWidth;
    menu.style.left = `${Math.max(8, Math.min(r.right - w, innerWidth - w - 8))}px`; menu.style.top = `${r.bottom + 6}px`;
    const first = menu.querySelector("button"); if (first) first.focus({preventScroll:true});
  }
  const closeMenu = () => { $("wr-menu").hidden = true; };
  function openClose() {
    const c = counts(), pending = Object.keys(DECISIONS).filter(id => !decisionOf(id).answer).length, d = $("wr-dialog");
    d.innerHTML = `<h2 id="wr-dialog-title">${icon("finish")}收尾作战室</h2><p>收尾后作战室只读保留。交流区会出现收尾公告；每位成员收到一条不叫醒的留档通知，带着这次的上下文回原岗。</p>`
      + `<label for="wr-summary-input">结论（一句话，会写进收尾公告）</label><textarea id="wr-summary-input">${esc(ROOM.summary)}</textarea>`
      + `<ul class="wr-dialog-check"><li class="${c.blocked + c.todo + c.doing ? "warn" : ""}">${icon(c.blocked + c.todo + c.doing ? "alert" : "done")}状态板：${c.done}/${itemsNow().length} 完成${c.blocked ? `，${c.blocked} 项阻塞` : ""}${c.todo ? `，${c.todo} 项待办` : ""}</li><li class="${pending ? "warn" : ""}">${icon(pending ? "alert" : "done")}${pending ? `${pending} 张拍板卡还没答复` : "拍板卡都已答复"}</li></ul>`
      + `<div class="wr-dialog-acts"><button type="button" class="cx-btn" data-dialog-cancel>先不收尾</button><button type="button" class="cx-btn cx-primary" data-dialog-ok>${icon("finish", "sm")}确认收尾</button></div>`;
    d.hidden = false; syncLayers(); d.querySelector("textarea").focus();
  }
  function explainBadge(key) {
    const [id, kind, name] = key.split(":"), m = msgById(Number(id)), item = m[kind].find(x => x.name === name);
    const what = kind === "notified" ? "回复通知" : "点名";
    if (item.d === "queued") return `${what} ${name}：${item.waiting === "external" ? `${name} 在外部窗口运行，下次在那边提示时才会带上` : `${name} 空下来才会收到`}。它还没看到，已排队 ${item.age} 分钟${item.stale ? "，等得有点久，可以打开私聊看看" : ""}。`;
    if (item.d === "delivered") return `${what} ${name}：已送进它的输入（${item.age} 分钟前），还没在房里回复。`;
    return `${what} ${name}：已经看到并回复了 #${item.reply}。`;
  }
  function flashItem(id) {
    if (matchMedia("(max-width:1099px)").matches) { S.panel = true; syncLayers(); }
    const row = document.querySelector(`[data-item-row="${id}"]`);
    if (!row) { if (id === "i6" || id === "i7") { S.doneOpen = true; renderRoom(); } return; }
    row.scrollIntoView({block:"center", behavior:matchMedia("(prefers-reduced-motion:reduce)").matches ? "auto" : "smooth"});
    row.classList.remove("is-flash"); void row.offsetWidth; row.classList.add("is-flash");
  }
  function send(form) {
    const text = form.querySelector("textarea").value.trim();
    if (!text) { toast("先写点什么"); return; }
    const names = S.allTargets ? MEMBERS.map(m => m.name) : [...S.targets];
    const mentions = names.map(n => { const p = presenceOf(memberBy(n)); return {name:n, d:p === "online" ? "delivered" : "queued", age:0, waiting:p === "external" ? "external" : null}; });
    const reply = S.reply ? msgById(S.reply) : null, notified = reply && !reply.user && !names.includes(reply.who) ? [{name:reply.who, d:"delivered", age:0}] : [];
    S.extra.push({k:"msg", id:308 + S.extra.length, who:"我", user:true, at:"05:36", body:text, all:S.allTargets, reply:S.reply, mentions, notified});
    S.reply = null; S.targets.clear(); S.allTargets = false;
    const sc = document.querySelector(".wr-scroll"); renderRoom(); sc && (document.querySelector(".wr-scroll").scrollTop = 1e6);
    toast(names.length ? `已发到作战室，${names.join("、")} 空闲时收到，送达情况看徽章` : "已发到作战室（没点名，不叫醒任何人）");
  }

  function mountRoom() {
    document.querySelector(".wr-rail").innerHTML = railHTML();
    renderRoom();
    document.addEventListener("click", event => {
      const t = event.target, hit = sel => t.closest(sel);
      let n;
      if (!hit("#wr-menu") && !hit("[data-act=invite]") && !hit("[data-act=more]")) closeMenu();
      if ((n = hit("[data-proto]"))) {
        const v = n.dataset.proto;
        if (v === "theme") { const dark = document.documentElement.dataset.theme === "dark" || (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme:dark)").matches); document.documentElement.dataset.theme = dark ? "light" : "dark"; }
        else { S.closed = v === "closed"; S.member = null; renderRoom(); }
        return;
      }
      if ((n = hit("[data-badge]"))) return toast(explainBadge(n.dataset.badge));
      if ((n = hit("[data-takeover]"))) { const m = MEMBERS.find(x => x.id === n.dataset.takeover); S.member = null; syncLayers(); return toast(m ? `打开 ${m.name} 的私聊（原型）${S.closed ? "" : "：你在私聊里说的话会直接送进它的输入"}` : "打开私聊"); }
      if ((n = hit("[data-pause]"))) {
        const id = n.dataset.pause, m = MEMBERS.find(x => x.id === id);
        if (S.paused.has(id)) { S.paused.delete(id); toast(`${m.name} 继续了，排队的点名会送进去`); } else { S.paused.add(id); toast(`已暂停 ${m.name}（停止当前这一轮）；在你再发话之前，点名只排队`); }
        return renderRoom();
      }
      if ((n = hit("[data-member]"))) { S.member = n.dataset.member; return syncLayers(); }
      if (hit("[data-sheet-close]")) { S.member = null; return syncLayers(); }
      if (hit("[data-panel]")) { S.panel = true; syncLayers(); document.querySelector("[data-panel-close]").focus(); return; }
      if (hit("[data-panel-close]")) { S.panel = false; return syncLayers(); }
      if (hit("#wr-backdrop")) { S.panel = false; S.member = null; $("wr-dialog").hidden = true; return syncLayers(); }
      if ((n = hit("[data-reply]"))) { S.reply = Number(n.dataset.reply); renderRoom(); document.querySelector("[data-composer] textarea").focus(); return; }
      if (hit("[data-unreply]")) { S.reply = null; return renderRoom(); }
      if ((n = hit("[data-jump]"))) {
        const node = document.querySelector(`[data-msg="${n.dataset.jump}"]`);
        if (node) { node.scrollIntoView({block:"center", behavior:"smooth"}); node.classList.remove("cx-flash"); void node.offsetWidth; node.classList.add("cx-flash"); }
        return;
      }
      if ((n = hit("[data-to]"))) {
        const v = n.dataset.to;
        if (v === "*") { S.allTargets = !S.allTargets; S.targets.clear(); }
        else if (S.allTargets) { S.allTargets = false; S.targets = new Set(MEMBERS.map(m => m.name).filter(x => x !== v)); }
        else if (!S.targets.delete(v)) S.targets.add(v);
        const draft = document.querySelector("[data-composer] textarea")?.value || "";
        renderRoom(); document.querySelector("[data-composer] textarea").value = draft; return;
      }
      if (hit("[data-fold]")) { S.fold = !S.fold; return renderRoom(); }
      if (hit("[data-done-toggle]")) { S.doneOpen = !S.doneOpen; return renderRoom(); }
      if ((n = hit("[data-item]"))) return flashItem(n.dataset.item);
      if (hit("[data-contract]")) { S.contractOpen = true; renderRoom(); if (matchMedia("(max-width:1099px)").matches) { S.panel = true; syncLayers(); } document.getElementById("wr-contract").scrollIntoView({block:"start"}); return; }
      if ((n = hit("[data-goto-decision]"))) {
        S.panel = false; syncLayers();
        const card = document.querySelector(`.wr-decide[data-decision="${n.dataset.gotoDecision}"]`);
        card.scrollIntoView({block:"center", behavior:"smooth"}); card.classList.remove("cx-flash"); void card.offsetWidth; card.classList.add("cx-flash"); return;
      }
      if ((n = hit("[data-answer]"))) { const [id, k] = n.dataset.answer.split(":"); S.answers[id] = {k, at:"05:36"}; renderRoom(); return toast(`已答复 ${k}。答复回到牵头 支付，由它在房里收口`); }
      if ((n = hit("[data-act]"))) {
        const a = n.dataset.act;
        if (a === "invite") return openMenu(n, `<small>只能邀请本工作区在用的 Leader</small>${OTHERS.map(x => `<button type="button" role="menuitem" data-invite="${x}">${av(x, 22, false)}<span>${x}</span></button>`).join("")}`);
        if (a === "more") return openMenu(n, S.closed
          ? `<button type="button" role="menuitem" data-act="notice">${icon("msg", "sm")}看收尾公告</button><button type="button" role="menuitem" data-act="origin">${icon("thread", "sm")}看起因脉络</button>`
          : `<button type="button" role="menuitem" data-act="invite-list">${icon("invite", "sm")}邀请成员</button><button type="button" role="menuitem" data-act="decide">${icon("gavel", "sm")}发拍板卡</button><button type="button" role="menuitem" data-act="close">${icon("finish", "sm")}收尾作战室</button><small>拍板卡由牵头 支付 发；这里会请它汇总</small>`);
        closeMenu();
        if (a === "invite-list") return toast("可邀请：网关、测试、运维（原型）");
        if (a === "decide") { const ta = document.querySelector("[data-composer] textarea"); S.targets = new Set(["支付"]); S.allTargets = false; renderRoom(); const box = document.querySelector("[data-composer] textarea"); box.value = "@支付 把还没定的事汇成一张拍板卡给我"; box.focus(); return; }
        if (a === "close") return openClose();
        if (a === "origin") return toast("打开交流区并定位到 #212（原型）");
        if (a === "notice") return toast("打开交流区的收尾公告（原型）");
        if (a === "add-item") return toast("新增一项：标题、负责人、状态（原型）");
        if (a === "edit-contract") return toast("编辑契约：整份替换，保存时带上 05:20 这一版做防覆盖（原型）");
        return;
      }
      if ((n = hit("[data-invite]"))) { closeMenu(); return toast(`已邀请 ${n.dataset.invite}，它下一轮会收到作战室摘要（原型）`); }
      if (hit("[data-dialog-cancel]")) { $("wr-dialog").hidden = true; return syncLayers(); }
      if (hit("[data-dialog-ok]")) { ROOM.summary = $("wr-summary-input").value.trim() || ROOM.summary; $("wr-dialog").hidden = true; S.closed = true; renderRoom(); return toast("已收尾：交流区出现收尾公告，成员收到留档通知"); }
    });
    document.addEventListener("submit", event => {
      event.preventDefault();
      const f = event.target;
      if (f.matches("[data-composer]")) send(f);
      if (f.dataset.free) { const v = f.querySelector("input").value.trim(); if (!v) return; S.answers[f.dataset.free] = {k:"A", at:"05:36"}; renderRoom(); toast("已把你的话作为答复交给牵头 支付"); }
    });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape") { closeMenu(); S.panel = false; S.member = null; $("wr-dialog").hidden = true; syncLayers(); }
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.target.matches("[data-composer] textarea")) { event.preventDefault(); send(event.target.form); }
      if ((event.key === "Enter" || event.key === " ") && event.target.matches("[data-item-row]")) { event.preventDefault(); toast("改这一项：状态、负责人、阻塞原因（原型）"); }
    });
    document.addEventListener("input", event => { if (event.target.matches("[data-composer] textarea")) { event.target.style.height = "auto"; event.target.style.height = `${Math.min(event.target.scrollHeight, 160)}px`; } });
    let wasNarrow = matchMedia("(max-width:620px)").matches;
    addEventListener("resize", () => { const now = matchMedia("(max-width:620px)").matches; if (now !== wasNarrow) { wasNarrow = now; renderRoom(); } syncLayers(); });
  }

  /* ------------------------------ 入口小样 ------------------------------ */
  function mountEntry() {
    document.querySelector(".wr-rail").innerHTML = railHTML();
    const room = (s) => `<div class="wr-announce" data-s="${s}"><div class="wr-ann-top"><span class="wr-title-ic" aria-hidden="true">${icon(s === "open" ? "room" : "archive")}</span><div class="wr-ann-t"><small>${s === "open" ? `${av("支付", 14)}支付 开了作战室 · 05:02 · 从 #212 升级` : `${av("支付", 14)}支付 收尾了作战室 · 06:10`}</small><strong>${esc(ROOM.title)}</strong></div>${s === "open" ? '<span class="wr-status" data-s="open"><span class="dot working" aria-hidden="true"></span>进行中</span>' : `<span class="wr-status" data-s="closed">${icon("archive", "xs")}已收尾</span>`}</div>`
      + `<p class="wr-ann-goal">${s === "open" ? `目标：${esc(ROOM.goal)}` : `结论：${esc(ROOM.summary)}`}</p>`
      + `<div class="wr-ann-foot"><span class="cx-thread-avs" aria-hidden="true">${MEMBERS.map(m => av(m.name, 18)).join("")}</span><span>${s === "open" ? `3 位成员 · 牵头 支付` : "用时 1 小时 8 分 · 7 项完成 · 2 张拍板卡"}</span>${s === "open" ? chip("blocked", "1 阻塞") : ""}<button type="button" class="cx-btn${s === "open" ? " cx-primary" : ""}">${s === "open" ? "进入作战室" : "查看（只读）"}${icon("chev", "xs")}</button></div></div>`;
    const msg = (who, id, at, body, extra = "") => `<article class="cx-msg">${av(who, 28)}<div class="cx-body-col"><div class="cx-meta"><strong>${who}</strong><span class="cx-id">#${id}</span><time>${at}</time></div><div class="cx-content"><div class="markdown cx-md"><p>${bodyHTML(body)}</p></div></div>${extra}</div></article>`;
    const sys = (html) => `<div class="wr-ann-row"><span class="av round" style="--s:28px;--tint:var(--accent)" aria-hidden="true">${icon("room", "sm")}</span>${html}</div>`;
    const thread = `<div class="cx-thread wr-thread-up"><button type="button" class="cx-thread-toggle" aria-expanded="false">${icon("thread", "sm")}<span>脉络 · 5 条回复</span><span class="cx-thread-avs" aria-hidden="true">${ORIGIN.people.map(n => av(n, 18)).join("")}</span><span class="cx-thread-who">支付、语音、前端</span>${icon("chev", "xs cx-thread-chev")}</button>`
      + `<button type="button" class="wr-upgrade">${icon("room", "sm")}升级为作战室</button><span class="wr-upgrade-hint">${icon("alert", "xs")}5 条回复还没收口，3 位 leader 在聊</span></div>`;
    const feed = `<div class="cx-divider"><span>今天</span></div>`
      + msg("支付", 212, "05:01", ORIGIN.text, `<div class="cx-dl-row"><div class="cx-dl-group"><span class="cx-dl-label">点名</span><span class="cx-dl-list">${dlBadge(0, {name:"语音", d:"replied"}, "mentions")}${dlBadge(0, {name:"前端", d:"replied"}, "mentions")}</span></div></div>`)
      + thread + sys(room("open"))
      + msg("网关", 213, "05:05", "灰度环境今晚 22:00 重启，有在联调的提前说一声。")
      + `<div class="cx-divider"><span>1 小时后</span></div>` + sys(room("closed"));
    const specimen = `
      <section class="wr-spec"><h2>成员状态（参战栏）</h2><div class="wr-spec-row">${["online", "working", "external", "offline", "paused", "back"].map(p => `<div class="wr-spec-mem"><span class="wr-mem-av" style="position:relative">${av("语音", 34, false)}${badge(p)}</span><span class="wr-pres" data-p="${p}">${presenceMark(p)}<span>${PRESENCE[p].t}</span></span></div>`).join("")}</div>
        <p class="wr-spec-note">形状区分：实心点 / 带脉冲的点 / 窗口图标 / 空心圈 / 暂停竖线 / 返回箭头；头像离线和回原岗时去色。</p></section>
      <section class="wr-spec"><h2>状态板四态</h2><ul class="wr-items">${ITEMS.filter((x, i) => [0, 2, 3, 5].includes(i)).map(x => `<li class="wr-item" data-s="${x.s}">${icon(x.s)}<span class="wr-item-title">${esc(x.title)}</span><span class="wr-item-who">${av(x.who, 16)}${esc(x.who)}</span>${x.note ? `<span class="wr-item-note">${esc(x.note)}</span>` : ""}</li>`).join("")}</ul>
        <div class="wr-spec-row" style="margin-top:8px">${ORDER.map(s => chip(s)).join("")}</div></section>
      <section class="wr-spec"><h2>交接节点</h2>${nodeHTML({s:"blocked", who:"语音", item:"SDK 签名适配", why:"等签名算法定稿", at:"05:16", ref:"i3"})}${nodeHTML({s:"doing", who:"前端", item:"登录页联调", at:"05:07", ref:"i2"})}${nodeHTML({s:"done", who:"语音", item:"SDK 签名适配", at:"05:58", ref:"i3"})}${nodeHTML({s:"contract", who:"支付", text:"更新了接口契约：加 X-Nonce", at:"05:20"})}<div class="wr-fold"><button type="button" class="wr-fold-btn" aria-expanded="false">${icon("chev", "xs chev")}<span>3 条状态变化</span><span>· 支付 新增 5 项任务 等</span></button></div></section>
      <section class="wr-spec"><h2>拍板卡摘要两态 · 投递徽章</h2><ul class="wr-dlist">${["d2", "d1"].map(id => { const d = {...DECISIONS[id], answer:id === "d1" ? DECISIONS.d1.answer : null}; return `<li data-s="${d.answer ? "answered" : "pending"}"><button type="button">${icon("gavel", "xs")}<span class="q">${esc(d.q)}</span>${d.answer ? `<span class="wr-dc-state" data-s="answered">${icon("check", "xs")}B</span>` : `<span class="wr-dc-state" data-s="pending">${icon("hourglass", "xs")}待你答复</span>`}</button></li>`; }).join("")}</ul>
        <div class="cx-dl-list" style="margin-top:10px">${[{name:"语音", d:"queued", age:3}, {name:"语音", d:"queued", age:14, stale:true}, {name:"支付", d:"delivered", age:4}, {name:"前端", d:"replied", reply:307}].map(x => dlBadge(0, x, "mentions")).join("")}</div></section>`;
    const side = `<section class="wr-spec"><h2>侧栏入口</h2><div class="wr-side-sample"><div class="leader-panel-channel">${icon("msg")}<span style="margin-left:0">Leader 交流区</span><span class="b-unread" style="margin-left:auto">3</span></div>`
      + `<div class="wr-side-label">作战室 <span>1 进行中</span></div><button type="button" class="wr-side-room"><span class="wr-title-ic" aria-hidden="true">${icon("room")}</span><span class="wr-side-txt"><strong>${esc(ROOM.title)}</strong><small><span class="dot working" aria-hidden="true"></span>3 人 · ${icon("blocked", "xs")}1 阻塞 · ${icon("hourglass", "xs")}1 待拍板</small></span><span class="b-unread">3</span></button>`
      + `<button type="button" class="wr-side-room is-closed"><span class="wr-title-ic" aria-hidden="true">${icon("archive")}</span><span class="wr-side-txt"><strong>支付回调联调</strong><small>已收尾 · 昨天</small></span></button></div></section>`
      + `<section class="wr-spec"><h2>升级为作战室（点开后）</h2><div class="wr-up-form"><label>名字<input value="${esc(ROOM.title)}"></label><label>目标（一句话）<input value="${esc(ROOM.goal)}"></label>`
      + `<div class="wr-up-label">成员 <small>脉络里发言、被点名、被回复的 leader 已默认加入</small></div><div class="wr-up-members">${MEMBERS.map(m => `<span class="wr-up-chip">${av(m.name, 18)}${m.name}${m.lead ? '<span class="wr-lead">牵头</span>' : ""}<button type="button" aria-label="移出 ${m.name}">${icon("x", "xs")}</button></span>`).join("")}<button type="button" class="wr-up-add">${icon("plus", "xs")}添加</button></div>`
      + `<div class="wr-up-acts"><span>原脉络留在交流区，作战室顶部引用它</span><button type="button" class="cx-btn">取消</button><button type="button" class="cx-btn cx-primary">${icon("room", "sm")}开房</button></div></div></section>`;
    document.getElementById("wr-entry").innerHTML = `<header class="wr-head"><div class="wr-title"><h1><span class="wr-title-ic" aria-hidden="true">${icon("msg")}</span><span class="wr-title-text">入口小样 · 状态样例</span></h1><p class="wr-sub">交流区里的开房 / 收尾公告、脉络旁的“升级为作战室”、侧栏入口，以及各状态外观</p></div><div class="wr-head-actions"><a class="cx-btn" href="index.html${location.search}" style="text-decoration:none" aria-label="回作战室">${icon("back", "sm")}<span class="wr-act-label">回作战室</span></a></div></header>`
      + `<div class="wr-entry-body"><section class="wr-entry-feed" aria-label="交流区小样"><div class="wr-entry-cap">交流区（Leader 交流区主流）</div><div class="cx-inner">${feed}</div></section><div class="wr-entry-side">${side}${specimen}</div></div>`;
  }

  if (document.body.dataset.page === "entry") mountEntry(); else mountRoom();
})();
