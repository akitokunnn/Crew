"use strict";
// Architecture board: the workspace's domain tree. Parts nest under parts; each part card lists the leaders working
// in it (any number), at most one of them leading the layer. Leaders not placed yet wait in the 未归属 shelf and are
// dragged into a part; parts hang under a part when dropped on it or wired from its pin. The tree draws top-down
// (an architecture tree) or left-to-right (a blueprint); positions, folds and direction are saved with the tree,
// the camera stays in this browser. Editing needs a window wider than a phone; phones can look, pan, zoom and open
// a leader. 版本历史 lists every structural version of the board with its difference to now and rolls back to one;
// a leader's menu merges or retires it through the restructure preview (docs/ASSISTANT_CAMPAIGN_DESIGN.md §7).
const CrewArchitecture = (() => {
  const PART_W = 248, HEAD_H = 42, PIN_Y = 21, ROW_H = 48, GAP_TB = 64, GAP_LR = 96, GAP_CROSS = 28;
  const MIN_ZOOM = .2, MAX_ZOOM = 2, SLOP = 4, HISTORY = 100, NAME_MAX = 40;
  const phone = window.matchMedia("(max-width:760px)");
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[c]);
  const icon = d => `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
  const ICON = {
    plus:'<path d="M12 5v14M5 12h14"/>', minus:'<path d="M5 12h14"/>',
    undo:'<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>', redo:'<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
    tidy:'<path d="M4 4h16M4 10h10M4 16h16M4 22h10"/>',
    fit:'<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>', down:'<path d="m6 9 6 6 6-6"/>', right:'<path d="m9 6 6 6-6 6"/>',
    folder:'<path d="M3 6.5h6.5l2 2.5H21v10.5H3z"/>',
    tb:'<rect x="8" y="3" width="8" height="5" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/><rect x="14" y="16" width="7" height="5" rx="1.5"/><path d="M12 8v4M6.5 16v-2.5a1.5 1.5 0 0 1 1.5-1.5h8a1.5 1.5 0 0 1 1.5 1.5V16"/>',
    lr:'<rect x="3" y="9" width="5" height="6" rx="1.5"/><rect x="16" y="3" width="5" height="6" rx="1.5"/><rect x="16" y="15" width="5" height="6" rx="1.5"/><path d="M8 12h3M11 12c2 0 2-6 5-6M11 12c2 0 2 6 5 6"/>',
    history:'<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/>', close:'<path d="M6 6l12 12M18 6 6 18"/>', back:'<path d="m15 6-6 6 6 6"/>',
    people:'<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20v-.5a6.5 6.5 0 0 1 13 0v.5M15.5 4.7a3.5 3.5 0 0 1 0 6.6M18 13.6a6.5 6.5 0 0 1 3.5 5.9v.5"/>'
  };
  const STATUS = {working:"在干活", waiting:"等你", idle:"空闲", offline:"离线", closed:"已归档"};
  const DIRECTION = {TB:"上→下", LR:"左→右"};
  const SOURCE_TONE = {baseline:"base", edit:"edit", mindmap:"map", restructure:"plan", rollback:"back"};

  let host = null, options = null, board = null, project = null, errorText = "", loadToken = 0;
  let camera = null, selection = new Set(), undoStack = [], redoStack = [], queue = Promise.resolve(), busy = 0;
  let gesture = null, renaming = null, menu = null, focusRequest = null, spaceDown = false, cached = null, leaderSignature = "";
  let pendingFit = null, resizer = null, pendingDirection = null, shelfOpen = null;
  let history = null, historyToken = 0, planner = null;
  const pointers = new Map(), pending = new Map(), sizes = new Map();

  const editable = () => !phone.matches;
  const clampZoom = z => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));
  const $ = selector => host?.querySelector(selector);
  const direction = () => pendingDirection ?? board?.direction ?? "TB";
  const partEl = id => host?.querySelector(`.arch-part[data-part="${CSS.escape(id)}"]`);

  // ---- model ----------------------------------------------------------------------------------------------------
  function model() {
    const leadersList = options.state.leaders || [];
    if (cached && cached.board === board && cached.leadersList === leadersList) return cached;
    const order = new Map(leadersList.map((item, index) => [item.session_id, index]));
    const parts = new Map(board.parts.map(part => [part.id, part]));
    const leaders = new Map(leadersList.map(item => [item.session_id, item]));
    const places = new Map(board.leaders.filter(place => leaders.has(place.session_id)).map(place => [place.session_id, place]));
    const children = new Map(), members = new Map();
    for (const part of board.parts) { if (!children.has(part.parent_id)) children.set(part.parent_id, []); children.get(part.parent_id).push(part); }
    for (const place of places.values()) { if (!members.has(place.domain_id)) members.set(place.domain_id, []); members.get(place.domain_id).push(place); }
    for (const list of members.values()) list.sort((a, b) => (b.lead - a.lead) || (order.get(a.session_id) - order.get(b.session_id)));
    const loose = leadersList.filter(item => !places.has(item.session_id));
    cached = {board, leadersList, parts, leaders, places, children, members, loose};
    return cached;
  }
  function hidden(m, id) {
    for (let part = m.parts.get(m.parts.get(id)?.parent_id); part; part = m.parts.get(part.parent_id)) if (part.collapsed) return true;
    return false;
  }
  const visible = (m, id) => m.parts.has(id) && !hidden(m, id);
  function ancestors(m, id) { const out = []; for (let part = m.parts.get(m.parts.get(id)?.parent_id); part; part = m.parts.get(part.parent_id)) out.push(part.id); return out; }
  function descendants(m, id) {
    const out = [], walk = partId => { for (const child of m.children.get(partId) || []) { out.push(child.id); walk(child.id); } };
    walk(id); return out;
  }
  const membersOf = (m, id) => m.members.get(id) || [];
  function posOf(m, id) {
    if (gesture?.positions?.has(id)) return gesture.positions.get(id);
    if (pending.has(id)) return pending.get(id).pos;
    const part = m.parts.get(id); return {x:part.x, y:part.y};
  }
  function sizeOf(m, id) {
    if (sizes.has(id)) return sizes.get(id);
    const part = m.parts.get(id), rows = part?.collapsed ? 0 : membersOf(m, id).length;
    return {w:PART_W, h:HEAD_H + (part?.collapsed ? 34 : Math.max(1, rows) * ROW_H + 12)};
  }
  function measure() {
    sizes.clear();
    for (const node of host.querySelectorAll(".arch-part")) sizes.set(node.dataset.part, {w:node.offsetWidth, h:node.offsetHeight});
  }

  // ---- rendering ------------------------------------------------------------------------------------------------
  function scaffold() {
    return `<div id="arch-view" class="arch-view">
      <div class="head big arch-head"></div>
      <div class="arch-stage">
      <div class="arch-canvas" tabindex="0" aria-label="架构图画板" aria-describedby="arch-hint">
        <div class="arch-world"><svg class="arch-wires" aria-hidden="true"></svg><div class="arch-nodes"></div></div>
        <div class="arch-marquee" hidden></div><div class="arch-drop-label" hidden></div><div class="arch-ghost" hidden></div>
        <aside class="arch-shelf" aria-label="未归属的 leader"></aside>
        <div class="arch-empty" hidden></div>
        <p class="arch-hint" id="arch-hint"></p>
        <div class="arch-zoom" role="group" aria-label="缩放"><button type="button" data-arch-act="zoom-out" aria-label="缩小">${icon(ICON.minus)}</button><button type="button" class="arch-zoom-value" data-arch-act="zoom-reset" title="恢复 100%"></button><button type="button" data-arch-act="zoom-in" aria-label="放大">${icon(ICON.plus)}</button><button type="button" data-arch-act="fit" title="显示全部 (Home)" aria-label="显示全部">${icon(ICON.fit)}</button></div>
        <div class="arch-menu" role="menu" hidden></div>
      </div>
      <aside class="arch-history" aria-label="版本历史" hidden></aside>
      </div>
    </div>`;
  }
  function render() {
    if (host === null) return;
    if (board === null) {
      const head = $(".arch-head"); if (head) head.innerHTML = `<div><div class="title">架构图</div><div class="sub">${errorText ? "" : "正在读取…"}</div></div><div class="right">${backToChat()}</div>`;
      const empty = $(".arch-empty");
      if (empty) { empty.hidden = !errorText; empty.innerHTML = errorText ? `<p class="arch-error" role="alert">${esc(errorText)}</p><button type="button" class="btn" data-arch-act="reload">重新读取</button>` : ""; }
      return;
    }
    renderHead(); renderWorld(); applyCamera(); renderHistory();
  }
  const backToChat = () => options.canReturn ? '<button type="button" class="btn ghost" data-act="architecture-back">返回会话</button>' : "";
  function renderHead() {
    const head = $(".arch-head"); if (!head || board === null) return;
    const m = model(), edit = editable(), dir = direction();
    const picked = edit && selection.size > 1 ? ` · 已选 ${selection.size} 个 part` : "";
    const toggle = `<div class="arch-dir" role="radiogroup" aria-label="架构图方向">${["TB", "LR"].map(value => `<button type="button" role="radio" aria-checked="${dir === value}" data-arch-act="direction" data-direction="${value}" title="${value === "TB" ? "架构树：从上到下" : "蓝图：从左到右"}">${icon(ICON[value.toLowerCase()])}<span>${DIRECTION[value]}</span></button>`).join("")}</div>`;
    const tools = edit ? `${toggle}<span class="arch-tool-gap" aria-hidden="true"></span><button type="button" class="btn primary arch-new" data-arch-act="new">${icon(ICON.plus)}新 part</button><button type="button" class="icon-btn" data-arch-act="undo" title="撤销${undoStack.length ? `：${esc(undoStack.at(-1).label)}` : ""} (${window.CrewBrowser.keyboardShortcut("Z")})" aria-label="撤销" ${undoStack.length ? "" : "disabled"}>${icon(ICON.undo)}</button><button type="button" class="icon-btn" data-arch-act="redo" title="重做${redoStack.length ? `：${esc(redoStack.at(-1).label)}` : ""} (${window.CrewBrowser.keyboardShortcut("Z", {shift:true})})" aria-label="重做" ${redoStack.length ? "" : "disabled"}>${icon(ICON.redo)}</button><button type="button" class="btn ghost" data-arch-act="tidy" title="按当前方向自动排列（可撤销）">${icon(ICON.tidy)}<span class="arch-tidy-label">自动排列</span></button>` : "";
    const versions = `<button type="button" class="btn ghost arch-history-open" data-arch-act="history" aria-expanded="${history !== null}" aria-label="版本历史" title="版本历史：每次结构改动的版本、差异与回滚">${icon(ICON.history)}<span class="arch-history-label">版本历史</span></button>`;
    const markup = `<div class="arch-heading"><div class="title">${icon(ICON.tb)}架构图${busy ? '<span class="arch-saving">保存中…</span>' : ""}</div><div class="sub">${board.parts.length} 个 part · ${m.leaders.size} 位 leader${m.loose.length ? ` · ${m.loose.length} 位未归属` : ""}${picked}${edit ? "" : ' · <span title="在电脑上打开 Crew 可以编辑">只读</span>'}</div></div><div class="right">${backToChat()}${versions}${tools}</div>`;
    if (head.dataset.render !== markup) { head.innerHTML = markup; head.dataset.render = markup; }
    const hint = $(".arch-hint");
    const text = edit ? "把 leader 拖进 part 归入 · 拖 part 到 part 上或从圆点拉线改挂 · 右键 leader 设负责人 · 右键 / 空格拖动平移 · 滚轮缩放" : "拖动平移 · 双指缩放 · 点 leader 进入对话 · 在电脑上可以编辑";
    if (hint.textContent !== text) hint.textContent = text;
  }
  function memberRow(m, item, lead, partName) {
    const status = STATUS[item.status] ? item.status : "idle";
    return `<div class="arch-member${lead ? " lead" : ""}${gesture?.type === "leader" && gesture.moved && gesture.leader === item.session_id ? " dragging" : ""}" data-leader="${esc(item.session_id)}" style="--tint:${esc(options.tint(item.name))}" role="link" aria-label="${esc(item.name)}${lead ? `，「${esc(partName)}」负责人` : ""}，${STATUS[status]}，进入对话">`
      + `${options.avatar(item.name, 24)}<span class="arch-member-text"><span class="arch-member-line"><b class="arch-member-name" title="${esc(item.name)}">${esc(item.name)}</b>${lead ? '<span class="arch-lead" title="本层负责人：负责这一层各子 part 共用的部分">负责</span>' : ""}</span>`
      + `<span class="arch-member-brief" title="${esc(item.goal || "")}">${item.goal ? esc(item.goal) : "还没有简介"}</span></span>`
      + `<span class="st ${status}" title="${STATUS[status]}"><i class="dot ${status}"></i><span>${STATUS[status]}</span></span></div>`;
  }
  function partNode(m, part) {
    const p = posOf(m, part.id), people = membersOf(m, part.id), kids = (m.children.get(part.id) || []).length;
    const below = part.collapsed ? descendants(m, part.id) : [];
    const folded = part.collapsed ? [people.length + below.reduce((n, id) => n + membersOf(m, id).length, 0), below.length] : null;
    const body = part.collapsed
      ? `<p class="arch-part-note">已折叠${folded[0] ? ` · ${folded[0]} 位 leader` : ""}${folded[1] ? ` · ${folded[1]} 个子 part` : ""}</p>`
      : people.length ? people.map(place => memberRow(m, m.leaders.get(place.session_id), place.lead, part.name)).join("")
      : `<p class="arch-part-note">${editable() ? "把 leader 拖到这里" : "还没有 leader"}</p>`;
    const name = renaming === part.id ? `<input class="arch-rename" value="${esc(part.name)}" maxlength="${NAME_MAX}" aria-label="part 名称" spellcheck="false">`
      : `<span class="arch-part-name" title="${esc(part.name)}${editable() ? "（双击改名）" : ""}">${esc(part.name)}</span>`;
    const cls = `${selection.has(part.id) ? " selected" : ""}${part.collapsed ? " collapsed" : ""}${gesture?.ids?.includes(part.id) ? " dragging" : ""}`;
    return `<div class="arch-node arch-part${cls}" data-part="${esc(part.id)}" style="transform:translate(${p.x}px,${p.y}px)" role="group" aria-label="part ${esc(part.name)}">`
      + `<span class="arch-pin in${part.parent_id ? " linked" : ""}" data-pin="in" title="上级 part：拉到别的 part 改挂，拉到空白处移出"></span>`
      + `<div class="arch-part-head"><button type="button" class="arch-fold" data-arch-act="fold" data-id="${esc(part.id)}" aria-expanded="${!part.collapsed}" aria-label="${part.collapsed ? "展开" : "折叠"} ${esc(part.name)}" ${kids || people.length || part.collapsed ? "" : "disabled"}>${icon(part.collapsed ? ICON.right : ICON.down)}</button>${icon(ICON.folder)}${name}${!part.collapsed && people.length ? `<span class="arch-count" title="${people.length} 位 leader">${icon(ICON.people)}${people.length}</span>` : ""}</div>`
      + `<div class="arch-members">${body}</div>`
      + `<span class="arch-pin out${kids ? " linked" : ""}" data-pin="out" title="下级 part：拉线连到别的 part，或拉到空白处新建"></span></div>`;
  }
  function renderShelf(m) {
    const shelf = $(".arch-shelf"); if (!shelf) return;
    const open = shelfOpen ?? (!phone.matches);
    shelf.dataset.open = String(open);
    const rows = m.loose.map(item => memberRow(m, item, false, "")).join("");
    shelf.innerHTML = `<button type="button" class="arch-shelf-head" data-arch-act="shelf" aria-expanded="${open}">${icon(ICON.people)}<span>未归属</span><b>${m.loose.length}</b>${icon(open ? ICON.down : ICON.right)}</button>`
      + `<div class="arch-shelf-body" ${open ? "" : "hidden"}>${rows || `<p class="arch-shelf-note">所有 leader 都在架构里了${editable() ? "；把 leader 拖到这里可移出" : ""}</p>`}${rows && editable() ? '<p class="arch-shelf-note">拖进 part 归入</p>' : ""}</div>`;
  }
  function renderWorld() {
    const nodes = $(".arch-nodes"); if (!nodes) return;
    const m = model();
    $(".arch-canvas").dataset.dir = direction();
    nodes.innerHTML = board.parts.filter(part => !hidden(m, part.id)).map(part => partNode(m, part)).join("");
    measure();
    renderShelf(m);
    renderWires();
    const empty = $(".arch-empty");
    empty.hidden = board.parts.length > 0;
    if (!empty.hidden) empty.innerHTML = `<div class="arch-empty-text"><h2>把领域拆成一棵树</h2><p>part 可以层层嵌套，比如 感知 › 视觉 › 相机；一个 part 里可以放多位 leader，其中一位可以设为本层负责人，负责下面各子 part 共用的部分。${editable() ? "新建 part 后，把左侧未归属的 leader 拖进去。" : "在电脑上打开 Crew 可以编辑。"}</p></div>${editable() ? `<button type="button" class="btn primary" data-arch-act="new">${icon(ICON.plus)}新建第一个 part</button>` : ""}`;
    if (renaming !== null) {
      const input = $(".arch-rename");
      if (input && document.activeElement !== input) { reveal(renaming); input.focus({preventScroll:true}); input.select(); }
    }
  }
  // Wires: a blueprint curve left-to-right, a tree elbow top-down. Each returns its path and the box it covers.
  function wire(a, b, tb) {
    if (!tb) {
      const dx = Math.max(40, Math.abs(b.x - a.x) * .5);
      return {d:`M${a.x} ${a.y}C${a.x + dx} ${a.y} ${b.x - dx} ${b.y} ${b.x} ${b.y}`, x0:Math.min(a.x, b.x - dx), x1:Math.max(a.x + dx, b.x), y0:Math.min(a.y, b.y), y1:Math.max(a.y, b.y)};
    }
    const gap = b.y - a.y, wide = Math.abs(b.x - a.x);
    if (gap < 32) {
      const dy = Math.max(40, Math.abs(gap) * .5);
      return {d:`M${a.x} ${a.y}C${a.x} ${a.y + dy} ${b.x} ${b.y - dy} ${b.x} ${b.y}`, x0:Math.min(a.x, b.x), x1:Math.max(a.x, b.x), y0:Math.min(a.y, b.y - dy), y1:Math.max(a.y + dy, b.y)};
    }
    if (wide < 1) return {d:`M${a.x} ${a.y}V${b.y}`, x0:a.x, x1:a.x, y0:a.y, y1:b.y};
    const mid = a.y + Math.min(GAP_TB / 2, gap / 2), r = Math.min(10, wide / 2, (gap) / 4), s = Math.sign(b.x - a.x);
    return {d:`M${a.x} ${a.y}V${mid - r}Q${a.x} ${mid} ${a.x + s * r} ${mid}H${b.x - s * r}Q${b.x} ${mid} ${b.x} ${mid + r}V${b.y}`,
            x0:Math.min(a.x, b.x), x1:Math.max(a.x, b.x), y0:a.y, y1:b.y};
  }
  function outPin(m, id) { const p = posOf(m, id), s = sizeOf(m, id); return direction() === "TB" ? {x:p.x + s.w / 2, y:p.y + s.h} : {x:p.x + s.w, y:p.y + PIN_Y}; }
  function inPin(m, id) { const p = posOf(m, id), s = sizeOf(m, id); return direction() === "TB" ? {x:p.x + s.w / 2, y:p.y} : {x:p.x, y:p.y + PIN_Y}; }
  // The SVG box spans exactly the wires: Chrome skips painting an SVG whose own box is off screen, overflow or not.
  function renderWires() {
    const m = model(), svg = $(".arch-wires"); if (!svg) return;
    const tb = direction() === "TB", wires = [];
    for (const part of board.parts) if (part.parent_id !== null && !hidden(m, part.id)) wires.push(["arch-wire", wire(outPin(m, part.parent_id), inPin(m, part.id), tb)]);
    if (gesture?.type === "wire" && gesture.moved) {
      const from = gesture.pin === "out" ? outPin(m, gesture.from) : gesture.end, to = gesture.pin === "out" ? gesture.end : inPin(m, gesture.from);
      wires.push([`arch-wire draft${gesture.over ? " ok" : ""}`, wire(from, to, tb)]);
    }
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const [, w] of wires) { x0 = Math.min(x0, w.x0); y0 = Math.min(y0, w.y0); x1 = Math.max(x1, w.x1); y1 = Math.max(y1, w.y1); }
    if (!wires.length) { x0 = y0 = 0; x1 = y1 = 1; }
    x0 -= 4; y0 -= 4; x1 += 4; y1 += 4;
    Object.assign(svg.style, {left:`${x0}px`, top:`${y0}px`, width:`${x1 - x0}px`, height:`${y1 - y0}px`});
    svg.setAttribute("viewBox", `${x0} ${y0} ${x1 - x0} ${y1 - y0}`);
    svg.innerHTML = wires.map(([cls, w]) => `<path class="${cls}" d="${w.d}"/>`).join("");
  }
  function renderSelection() {
    for (const node of host.querySelectorAll(".arch-part")) node.classList.toggle("selected", selection.has(node.dataset.part));
    renderHead();
  }
  function applyCamera() {
    const world = $(".arch-world"), canvas = $(".arch-canvas"); if (!world || camera === null) return;
    world.style.transform = `translate(${camera.x}px,${camera.y}px) scale(${camera.z})`;
    canvas.style.setProperty("--grid", `${24 * camera.z}px`);
    canvas.style.setProperty("--grid-x", `${camera.x}px`); canvas.style.setProperty("--grid-y", `${camera.y}px`);
    canvas.dataset.fine = String(camera.z >= .7);
    const value = $(".arch-zoom-value"); if (value) value.textContent = `${Math.round(camera.z * 100)}%`;
  }
  // Phone and desktop widths frame the board differently, so each keeps its own camera.
  const cameraKey = () => `crew.architecture.camera.${phone.matches ? "phone." : ""}${project}`;
  function saveCamera() { try { localStorage.setItem(cameraKey(), JSON.stringify(camera)); } catch { /* the camera is only a convenience */ } }
  function loadCamera() {
    try {
      const value = JSON.parse(localStorage.getItem(cameraKey()) || "null");
      if (value && [value.x, value.y, value.z].every(Number.isFinite)) return {x:value.x, y:value.y, z:clampZoom(value.z)};
    } catch { /* fall through to fitting the board */ }
    return null;
  }
  function setShelf(open) {
    shelfOpen = open;
    try { localStorage.setItem("crew.architecture.shelf", String(open)); } catch { /* per-viewer convenience */ }
    renderShelf(model());
  }

  // ---- camera ---------------------------------------------------------------------------------------------------
  function canvasRect() { return $(".arch-canvas").getBoundingClientRect(); }
  function toWorld(clientX, clientY) { const r = canvasRect(); return {x:(clientX - r.left - camera.x) / camera.z, y:(clientY - r.top - camera.y) / camera.z}; }
  function zoomAt(z, clientX, clientY) {
    const r = canvasRect(), sx = clientX - r.left, sy = clientY - r.top, next = clampZoom(z);
    camera = {x:sx - (sx - camera.x) * next / camera.z, y:sy - (sy - camera.y) * next / camera.z, z:next};
    applyCamera(); saveCamera();
  }
  // Pan just enough to bring a part fully into view.
  function reveal(id) {
    const node = partEl(id); if (!node) return;
    const r = canvasRect(), b = node.getBoundingClientRect(), margin = 32;
    const dx = b.left < r.left + margin ? r.left + margin - b.left : b.right > r.right - margin ? r.right - margin - b.right : 0;
    const dy = b.top < r.top + margin ? r.top + margin - b.top : b.bottom > r.bottom - 64 ? r.bottom - 64 - b.bottom : 0;
    if (dx || dy) { camera = {...camera, x:camera.x + dx, y:camera.y + dy}; applyCamera(); saveCamera(); }
  }
  function zoomCenter(factor) { const r = canvasRect(); zoomAt(camera.z * factor, r.left + r.width / 2, r.top + r.height / 2); }
  function bounds(ids) {
    const m = model(); let box = null;
    for (const id of ids) {
      if (!m.parts.has(id)) continue;
      const p = posOf(m, id), size = sizeOf(m, id);
      box = box === null ? {x0:p.x, y0:p.y, x1:p.x + size.w, y1:p.y + size.h}
        : {x0:Math.min(box.x0, p.x), y0:Math.min(box.y0, p.y), x1:Math.max(box.x1, p.x + size.w), y1:Math.max(box.y1, p.y + size.h)};
    }
    return box;
  }
  const shownIds = () => [...host.querySelectorAll(".arch-part")].map(node => node.dataset.part);
  function fit(ids = shownIds(), maxZoom = 1) {
    const r = canvasRect(), box = bounds(ids), pad = r.width < 500 ? 24 : 56;
    // Not laid out yet (the page is still appearing): fit once the canvas has a size.
    if (r.width === 0 || r.height === 0) { pendingFit = [ids, maxZoom]; return; }
    pendingFit = null;
    if (box === null) { camera = {x:r.width / 2 - PART_W / 2, y:Math.max(80, r.height / 3), z:1}; applyCamera(); return; }
    // Keep clear of the shelf and the first-use banner, and of the hint and zoom bar at the bottom.
    const shelf = $(".arch-shelf"), banner = $(".arch-empty");
    const left = shelf && !phone.matches && shelf.dataset.open === "true" ? shelf.offsetLeft + shelf.offsetWidth + 24 : pad;
    const top = banner && !banner.hidden ? banner.offsetTop + banner.offsetHeight + 16 : pad, bottom = pad + 24;
    const z = clampZoom(Math.min(maxZoom, (r.width - left - pad) / Math.max(1, box.x1 - box.x0), (r.height - top - bottom) / Math.max(1, box.y1 - box.y0)));
    camera = {z, x:left + (r.width - left - pad - (box.x1 - box.x0) * z) / 2 - box.x0 * z, y:top + (r.height - top - bottom - (box.y1 - box.y0) * z) / 2 - box.y0 * z};
    applyCamera(); saveCamera();
  }
  function flash(node) { if (!node) return; node.classList.remove("flash"); void node.offsetWidth; node.classList.add("flash"); }
  // From the chat path or the right panel: show the leader's part (or the nearest shown one above it) and flash both.
  function applyFocus() {
    if (focusRequest === null || board === null || host === null) return;
    const m = model(), request = focusRequest; focusRequest = null;
    const domain = request.part && m.parts.has(request.part) ? request.part : m.places.get(request.leader)?.domain_id;
    if (!domain) {
      if (request.leader && m.leaders.has(request.leader)) { setShelf(true); flash(host.querySelector(`.arch-shelf [data-leader="${CSS.escape(request.leader)}"]`)); }
      return;
    }
    const target = [domain, ...ancestors(m, domain)].find(id => !hidden(m, id));
    selection = new Set([target]); renderSelection();
    fit([target], Math.max(camera?.z ?? 1, .9));
    flash(partEl(target));
    if (request.leader) flash(partEl(target)?.querySelector(`[data-leader="${CSS.escape(request.leader)}"]`));
  }

  // ---- saving and undo ------------------------------------------------------------------------------------------
  async function load(clearHistory = false) {
    const token = ++loadToken, name = project;
    try {
      const result = await options.api(`/api/domains?project=${encodeURIComponent(name)}`);
      if (token !== loadToken || name !== project) return;
      board = result; errorText = ""; cached = null; pendingDirection = null;
      if (clearHistory) { undoStack = []; redoStack = []; }
      if (host !== null) { const m = model(); selection = new Set([...selection].filter(id => visible(m, id))); }
    } catch (error) {
      if (token !== loadToken) return;
      errorText = error.message || "读取架构图失败";
      if (board !== null) options.toast(errorText);
    }
    if (host === null) return;
    const first = camera === null;
    render();
    if (first && board !== null) { camera = loadCamera(); if (camera === null) { camera = {x:0, y:0, z:1}; fit(); } applyCamera(); }
    applyFocus();
    if (history !== null) void loadHistory();
  }
  function run(task) {
    busy++; renderHead();
    queue = queue.then(task).catch(error => {
      pending.clear(); pendingDirection = null;
      if (error.status === 409) { undoStack = []; redoStack = []; options.toast("架构图在别处改过了，已重新载入；之前的撤销记录已清空。"); }
      else options.toast(error.message || "保存失败，请重试。");
      return load(error.status === 409);
    }).finally(() => { busy--; if (host !== null && board !== null) render(); });
    return queue;
  }
  async function send(ops) {
    const result = await options.api("/api/domains", {project, base_version:board.version, ops});
    board = result.board; cached = null;
    if (history !== null && result.version !== null) void loadHistory();
    return result;
  }
  // The undo batch: each op's opposite in reverse order, from the board before the batch and what the server did.
  function invert(before, ops, effects) {
    const parts = new Map(before.parts.map(part => [part.id, part])), places = new Map(before.leaders.map(place => [place.session_id, place]));
    const back = [];
    ops.forEach((op, index) => {
      const effect = effects[index];
      let steps = [];
      if (op.op === "create") steps = [{op:"delete", id:effect.id}];
      else if (op.op === "rename") steps = [{op:"rename", id:op.id, name:parts.get(op.id).name}];
      else if (op.op === "collapse") steps = [{op:"collapse", id:op.id, collapsed:parts.get(op.id).collapsed}];
      else if (op.op === "direction") steps = [{op:"direction", direction:before.direction}];
      else if (op.op === "parent") steps = [{op:"parent", id:op.id, parent_id:parts.get(op.id).parent_id}];
      else if (op.op === "move") steps = [{op:"move", items:op.items.map(item => ({id:item.id, x:parts.get(item.id).x, y:parts.get(item.id).y}))}];
      else if (op.op === "assign") {
        const old = places.get(op.session_id);
        steps = [{op:"assign", session_id:op.session_id, domain_id:old?.domain_id ?? null}, ...(old?.lead ? [{op:"lead", session_id:op.session_id, lead:true}] : [])];
      } else if (op.op === "lead") {
        const was = places.get(op.session_id)?.lead === true;
        steps = op.lead ? (effect.previous && effect.previous !== op.session_id ? [{op:"lead", session_id:effect.previous, lead:true}] : was ? [] : [{op:"lead", session_id:op.session_id, lead:false}])
          : was ? [{op:"lead", session_id:op.session_id, lead:true}] : [];
      } else if (op.op === "delete") {
        const part = parts.get(op.id);
        steps = [{op:"create", id:part.id, name:part.name, parent_id:effect.parent_id, x:part.x, y:part.y, collapsed:part.collapsed},
          ...effect.children.map(child => ({op:"parent", id:child, parent_id:part.id})),
          ...effect.leaders.map(session_id => ({op:"assign", session_id, domain_id:part.id})),
          ...(effect.lead ? [{op:"lead", session_id:effect.lead, lead:true}] : [])];
      }
      back.unshift(...steps);
    });
    return back;
  }
  // One user action. Moves (and a direction switch) show at once; the rest redraws when the server answers.
  function commit(label, ops, {positions = null, dir = null, undoable = true, after = null} = {}) {
    const token = {};
    if (positions) for (const [id, pos] of positions) pending.set(id, {pos, token});
    if (dir) pendingDirection = dir;
    if (positions || dir) renderWorld();
    return run(async () => {
      const before = board;
      const result = await send(ops);
      for (const [id, value] of pending) if (value.token === token) pending.delete(id);
      if (dir) pendingDirection = null;
      let step = null;
      if (undoable) { step = {label, forward:ops, backward:invert(before, ops, result.effects)}; undoStack.push(step); if (undoStack.length > HISTORY) undoStack.shift(); redoStack = []; }
      after?.(result, before, step);
    });
  }
  function replay(ops) { return ops.length ? send(ops) : Promise.resolve(); }
  function undo() {
    if (!editable() || !undoStack.length) return;
    const step = undoStack.pop();
    run(async () => { try { await replay(step.backward); redoStack.push(step); } catch (error) { undoStack.push(step); throw error; } });
  }
  function redo() {
    if (!editable() || !redoStack.length) return;
    const step = redoStack.pop();
    run(async () => { try { await replay(step.forward); undoStack.push(step); } catch (error) { redoStack.push(step); throw error; } });
  }
  // A toast's 撤销 undoes that step only while nothing newer sits on top of it.
  const undoStep = step => () => {
    if (undoStack.at(-1) !== step) { options.toast(`之后又有改动，请用 ${window.CrewBrowser.keyboardShortcut("Z")} 逐步撤销`); return Promise.resolve(); }
    undo(); return queue;
  };

  // ---- edits ----------------------------------------------------------------------------------------------------
  function newId() { const bytes = new Uint8Array(6); crypto.getRandomValues(bytes); return "p" + [...bytes].map(b => b.toString(16).padStart(2, "0")).join(""); }
  function freeName(base = "新 part") { const names = new Set(board.parts.map(part => part.name)); if (!names.has(base)) return base; for (let n = 2; ; n++) if (!names.has(`${base} ${n}`)) return `${base} ${n}`; }
  // Where a new child of a part goes: one level further, after the children already shown there.
  function slotFor(m, parentId, skip = new Set()) {
    const p = posOf(m, parentId), s = sizeOf(m, parentId), tb = direction() === "TB";
    const kids = (m.children.get(parentId) || []).filter(child => !skip.has(child.id) && visible(m, child.id));
    if (tb) {
      const y = p.y + s.h + GAP_TB;
      return {x:kids.length ? Math.max(...kids.map(child => posOf(m, child.id).x + sizeOf(m, child.id).w)) + GAP_CROSS : p.x, y};
    }
    const x = p.x + s.w + GAP_LR;
    return {x, y:kids.length ? Math.max(...kids.map(child => posOf(m, child.id).y + sizeOf(m, child.id).h)) + GAP_CROSS : p.y};
  }
  function createPart(parentId = null, at = null) {
    if (!editable()) return;
    const m = model(), id = newId(), name = freeName();
    let pos = at ?? (parentId !== null ? slotFor(m, parentId) : null);
    if (pos === null) { const r = canvasRect(); pos = toWorld(r.left + r.width / 2 - PART_W / 2 * camera.z, r.top + r.height / 2 - 40 * camera.z); }
    const ops = [{op:"create", id, name, parent_id:parentId, x:Math.round(pos.x), y:Math.round(pos.y)}];
    if (parentId !== null && m.parts.get(parentId).collapsed) ops.push({op:"collapse", id:parentId, collapsed:false});
    commit("新建 part", ops, {after:() => { selection = new Set([id]); renaming = id; }});
  }
  function startRename(id) { if (!editable()) return; renaming = id; renderWorld(); }
  function finishRename(save) {
    const input = $(".arch-rename"), id = renaming;
    if (id === null) return;
    renaming = null;
    const part = board.parts.find(item => item.id === id), name = input ? input.value.trim().replace(/\s+/g, " ") : "";
    renderWorld(); $(".arch-canvas")?.focus({preventScroll:true});
    if (!save || !part || !name || name === part.name) return;
    commit(`改名为「${name}」`, [{op:"rename", id, name}]);
  }
  function setCollapsed(id, collapsed) {
    const m = model(), part = m.parts.get(id); if (!part || part.collapsed === collapsed) return;
    if (collapsed) { const below = new Set(descendants(m, id)); selection = new Set([...selection].filter(item => !below.has(item))); }
    commit(collapsed ? "折叠" : "展开", [{op:"collapse", id, collapsed}], {undoable:false});
  }
  // Which of the dragged parts may hang on `partId`: no cycles, only the tops of a dragged branch, not where they are.
  function attachable(m, ids, partId) {
    if (ids.includes(partId)) return null;
    const above = new Set([partId, ...ancestors(m, partId)]);
    if (ids.some(id => above.has(id))) return null;
    const dragged = new Set(ids);
    const tops = ids.filter(id => !ancestors(m, id).some(up => dragged.has(up)));
    const moving = tops.filter(id => m.parts.get(id).parent_id !== partId);
    return moving.length ? moving : null;
  }
  function detachPart(id) {
    const m = model(), from = m.parts.get(id)?.parent_id;
    if (!from) return;
    commit(`从「${m.parts.get(from)?.name}」移出`, [{op:"parent", id, parent_id:null}],
      {after:(result, before, step) => options.toast(`「${m.parts.get(id).name}」已从「${m.parts.get(from)?.name}」移出，成为顶层 part`, undoStep(step))});
  }
  function connect(id, partId) {
    const m = model(), ids = attachable(m, [id], partId);
    if (ids === null) { if (m.parts.get(id).parent_id !== partId) options.toast("不能挂到它自己或它的下级下面"); return; }
    commit(`挂到「${m.parts.get(partId).name}」下`, [{op:"parent", id, parent_id:partId}]);
  }
  // A part grows by a row when a leader joins it; parts it would then cover move down with their branches.
  function makeRoom(m, partId) {
    const p = posOf(m, partId), s = sizeOf(m, partId), grow = membersOf(m, partId).length ? ROW_H : 12;
    const bottom = p.y + s.h, above = new Set([partId, ...ancestors(m, partId)]), moved = new Map();
    for (const id of shownIds()) {
      if (above.has(id)) continue;
      const q = posOf(m, id), size = sizeOf(m, id);
      // Parts right below keep their distance to it; ones far enough away stay put.
      if (q.x >= p.x + s.w || q.x + size.w <= p.x || q.y < bottom - 1 || q.y - bottom >= GAP_TB + grow) continue;
      const shift = grow;
      for (const branch of [id, ...descendants(m, id)]) {
        if (moved.has(branch)) continue;
        const at = posOf(m, branch); moved.set(branch, {x:at.x, y:at.y + shift});
      }
    }
    return moved;
  }
  function placeLeader(sessionId, partId) {
    const m = model(), item = m.leaders.get(sessionId), from = m.places.get(sessionId)?.domain_id ?? null;
    if (!item || from === partId) return;
    const lead = m.places.get(sessionId)?.lead, room = partId ? makeRoom(m, partId) : new Map();
    commit(partId ? `${item.name} 归入「${m.parts.get(partId).name}」` : `${item.name} 移出到未归属`,
      [{op:"assign", session_id:sessionId, domain_id:partId}, ...(room.size ? [moveOp(room)] : [])],
      {positions:room.size ? room : null, after:(result, before, step) => { if (lead) options.toast(`${item.name} 不再是「${m.parts.get(from)?.name}」的负责人`, undoStep(step)); }});
  }
  function setLead(sessionId, lead) {
    const m = model(), place = m.places.get(sessionId); if (!place) return;
    const part = m.parts.get(place.domain_id)?.name, item = m.leaders.get(sessionId);
    commit(lead ? `${item.name} 负责「${part}」` : `取消 ${item.name} 的负责人`, [{op:"lead", session_id:sessionId, lead}]);
  }
  function deleteParts(ids) {
    if (!editable() || !ids.length) return;
    const m = model(), names = ids.map(id => `「${m.parts.get(id)?.name}」`).join("、");
    selection = new Set([...selection].filter(id => !ids.includes(id)));
    commit(`删除 ${names}`, ids.map(id => ({op:"delete", id})),
      {after:(result, before, step) => options.toast(`已删除 ${names}，里面的 leader 和子 part 已上移一层`, undoStep(step))});
  }
  function selectBranch(id) { const m = model(); selection = new Set([id, ...descendants(m, id).filter(item => visible(m, item))]); renderSelection(); }
  // Lay the tree out in a direction: depth along the main axis (rows top-down, columns left-to-right), siblings
  // side by side in their current order, each part centred over its children. Folded branches travel along.
  function layout(dir) {
    const m = model(), tb = dir === "TB", out = new Map();
    const main = id => tb ? sizeOf(m, id).h : sizeOf(m, id).w, cross = id => tb ? sizeOf(m, id).w : sizeOf(m, id).h;
    const order = list => [...list].sort((a, b) => tb ? (a.x - b.x) || (a.y - b.y) : (a.y - b.y) || (a.x - b.x));
    const kids = id => m.parts.get(id).collapsed ? [] : order(m.children.get(id) || []);
    const roots = order(m.children.get(null) || []), extent = [], span = new Map();
    const depth = (id, d) => { extent[d] = Math.max(extent[d] || 0, main(id)); kids(id).forEach(child => depth(child.id, d + 1)); };
    roots.forEach(root => depth(root.id, 0));
    const offset = [0];
    for (let d = 1; d < extent.length; d++) offset[d] = offset[d - 1] + extent[d - 1] + (tb ? GAP_TB : GAP_LR);
    const width = id => { const total = kids(id).reduce((sum, child, index) => sum + width(child.id) + (index ? GAP_CROSS : 0), 0); const s = Math.max(cross(id), total); span.set(id, s); return s; };
    roots.forEach(root => width(root.id));
    const place = (id, d, start) => {
      const list = kids(id), total = list.reduce((sum, child, index) => sum + span.get(child.id) + (index ? GAP_CROSS : 0), 0);
      let cursor = start + (span.get(id) - total) / 2;
      for (const child of list) { place(child.id, d + 1, cursor); cursor += span.get(child.id) + GAP_CROSS; }
      const along = Math.round(start + (span.get(id) - cross(id)) / 2);
      out.set(id, tb ? {x:along, y:offset[d]} : {x:offset[d], y:along});
    };
    let cursor = 0;
    for (const root of roots) { place(root.id, 0, cursor); cursor += span.get(root.id) + GAP_CROSS * 2; }
    for (const part of board.parts) if (part.collapsed && out.has(part.id)) {
      const from = posOf(m, part.id), to = out.get(part.id);
      for (const id of descendants(m, part.id)) { const p = posOf(m, id); out.set(id, {x:p.x + to.x - from.x, y:p.y + to.y - from.y}); }
    }
    return out;
  }
  const moveOp = positions => ({op:"move", items:[...positions].map(([id, p]) => ({id, x:Math.round(p.x), y:Math.round(p.y)}))});
  function tidy() {
    if (!editable() || !board.parts.length) return;
    const next = layout(direction());
    commit("自动排列", [moveOp(next)], {positions:next});
    requestAnimationFrame(() => fit());
  }
  function switchDirection(dir) {
    if (!editable() || dir === direction()) return;
    const next = layout(dir);
    commit(`改为${DIRECTION[dir]}`, [{op:"direction", direction:dir}, ...(next.size ? [moveOp(next)] : [])], {positions:next, dir});
    requestAnimationFrame(() => fit());
  }

  // ---- pointer gestures -----------------------------------------------------------------------------------------
  function under(clientX, clientY) { return document.elementFromPoint(clientX, clientY); }
  function capture(event) { try { $(".arch-canvas").setPointerCapture(event.pointerId); } catch { /* pointer already gone */ } }
  function pointerDown(event) {
    if (event.target.closest(".arch-menu, .arch-zoom, .arch-rename, .arch-empty button, .arch-shelf-head")) return;
    closeMenu();
    if (renaming !== null) finishRename(true);
    $(".arch-canvas").focus({preventScroll:true});
    pointers.set(event.pointerId, {x:event.clientX, y:event.clientY});
    if (pointers.size === 2 && (gesture === null || gesture.type === "pan" || !gesture.moved)) { cancelGesture(); startPinch(); return; }
    if (pointers.size > 1 || event.target.closest("[data-arch-act]")) return;
    const node = event.target.closest(".arch-part"), pin = event.target.closest(".arch-pin"), row = event.target.closest(".arch-member"), edit = editable();
    const start = {id:event.pointerId, x:event.clientX, y:event.clientY, moved:false};
    if (event.button === 1 || event.button === 2 || (event.button === 0 && spaceDown)) {
      event.preventDefault();
      gesture = {...start, type:"pan", camera:{...camera}, button:event.button, part:node?.dataset.part ?? null, leader:row?.dataset.leader ?? null}; return;
    }
    if (event.button !== 0) return;
    if (row) { gesture = edit ? {...start, type:"leader", leader:row.dataset.leader} : {...start, type:"pan", camera:{...camera}, tap:row.dataset.leader}; return; }
    if (event.target.closest(".arch-shelf")) return;
    if (edit && pin && node) {
      if (event.altKey && pin.dataset.pin === "in") { detachPart(node.dataset.part); return; }
      gesture = {...start, type:"wire", from:node.dataset.part, pin:pin.dataset.pin, end:toWorld(event.clientX, event.clientY), over:null}; return;
    }
    if (node) {
      const id = node.dataset.part;
      if (edit && (event.shiftKey || event.ctrlKey || event.metaKey)) { if (selection.has(id)) selection.delete(id); else selection.add(id); renderSelection(); return; }
      if (edit) { if (!selection.has(id)) { selection = new Set([id]); renderSelection(); } gesture = {...start, type:"part", part:id}; }
      else gesture = {...start, type:"pan", camera:{...camera}};
      return;
    }
    gesture = edit && event.pointerType === "mouse"
      ? {...start, type:"marquee", additive:event.shiftKey || event.ctrlKey || event.metaKey, base:new Set(selection)}
      : {...start, type:"pan", camera:{...camera}, clear:true};
  }
  function pointerMove(event) {
    if (pointers.has(event.pointerId)) pointers.set(event.pointerId, {x:event.clientX, y:event.clientY});
    if (gesture?.type === "pinch") { movePinch(); return; }
    if (gesture === null || gesture.id !== event.pointerId) return;
    const dx = event.clientX - gesture.x, dy = event.clientY - gesture.y;
    if (!gesture.moved) {
      if (Math.hypot(dx, dy) < SLOP) return;
      gesture.moved = true; capture(event);
      if (gesture.type === "part") beginDrag();
      if (gesture.type === "leader") beginLeaderDrag();
    }
    if (gesture.type === "pan") { camera = {...camera, x:gesture.camera.x + dx, y:gesture.camera.y + dy}; applyCamera(); }
    else if (gesture.type === "part") moveDrag(dx, dy, event);
    else if (gesture.type === "leader") moveLeaderDrag(event);
    else if (gesture.type === "wire") moveWire(event);
    else if (gesture.type === "marquee") moveMarquee(event);
  }
  function pointerUp(event) {
    pointers.delete(event.pointerId);
    if (gesture?.type === "pinch") { if (pointers.size < 2) { gesture = null; saveCamera(); } return; }
    if (gesture === null || gesture.id !== event.pointerId) return;
    if (event.type === "pointercancel") { cancelGesture(); return; }
    const done = gesture; gesture = null;
    if (done.type === "pan") {
      if (done.moved) saveCamera();
      else if (done.button === 2) openMenu(done, event.clientX, event.clientY);
      else if (done.tap) options.openLeader(done.tap);
      else if (done.clear && selection.size) { selection.clear(); renderSelection(); }
    } else if (done.type === "part") {
      if (done.moved) finishDrag(done);
      // A plain click inside a multi-selection picks just that part (the selection stayed for a possible drag).
      else if (selection.size > 1) { selection = new Set([done.part]); renderSelection(); }
    } else if (done.type === "leader") { if (done.moved) finishLeaderDrag(done); else options.openLeader(done.leader); }
    else if (done.type === "wire") finishWire(done);
    else if (done.type === "marquee") { $(".arch-marquee").hidden = true; if (!done.moved && !done.additive && selection.size) { selection.clear(); renderSelection(); } }
  }
  function cancelGesture() {
    const done = gesture; gesture = null;
    if (done === null) return;
    $(".arch-drop-label").hidden = true; $(".arch-ghost").hidden = true; clearOver();
    if ((done.type === "part" || done.type === "leader") && done.moved) renderWorld();
    if (done.type === "wire") renderWires();
    if (done.type === "marquee") $(".arch-marquee").hidden = true;
  }
  function startPinch() {
    const [a, b] = [...pointers.values()];
    gesture = {type:"pinch", distance:Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)), middle:{x:(a.x + b.x) / 2, y:(a.y + b.y) / 2}, camera:{...camera}};
  }
  function movePinch() {
    const [a, b] = [...pointers.values()]; if (!b) return;
    const r = canvasRect(), start = gesture.camera, z = clampZoom(start.z * Math.hypot(a.x - b.x, a.y - b.y) / gesture.distance);
    const wx = (gesture.middle.x - r.left - start.x) / start.z, wy = (gesture.middle.y - r.top - start.y) / start.z;
    camera = {z, x:(a.x + b.x) / 2 - r.left - wx * z, y:(a.y + b.y) / 2 - r.top - wy * z}; applyCamera();
  }
  function label(text, event) {
    const box = $(".arch-drop-label"), r = canvasRect();
    if (text === null) { box.hidden = true; return; }
    box.textContent = text; box.style.transform = `translate(${event.clientX - r.left + 14}px,${event.clientY - r.top + 16}px)`; box.hidden = false;
  }
  function clearOver() { for (const node of host.querySelectorAll(".drop-target")) node.classList.remove("drop-target"); }
  // Dragging moves the selected parts; a folded part carries its hidden branch along.
  function beginDrag() {
    const m = model(), ids = [...selection].filter(id => visible(m, id));
    const carried = new Set(ids);
    for (const id of ids) if (m.parts.get(id).collapsed) for (const below of descendants(m, id)) carried.add(below);
    gesture.ids = ids; gesture.origin = new Map([...carried].map(id => [id, posOf(m, id)])); gesture.positions = new Map(gesture.origin);
    for (const id of ids) partEl(id)?.classList.add("dragging");
  }
  function moveDrag(dx, dy, event) {
    const z = camera.z;
    for (const [id, origin] of gesture.origin) {
      const pos = {x:origin.x + dx / z, y:origin.y + dy / z}; gesture.positions.set(id, pos);
      const node = partEl(id); if (node) node.style.transform = `translate(${pos.x}px,${pos.y}px)`;
    }
    renderWires();
    const m = model(), target = under(event.clientX, event.clientY)?.closest?.(".arch-part");
    const ids = target ? attachable(m, gesture.ids, target.dataset.part) : null;
    gesture.drop = ids === null ? null : {part:target.dataset.part, ids};
    clearOver();
    if (gesture.drop) { target.classList.add("drop-target"); label(`松开挂到「${m.parts.get(gesture.drop.part).name}」下`, event); } else label(null);
  }
  function finishDrag(done) {
    label(null); clearOver();
    for (const node of host.querySelectorAll(".arch-part.dragging")) node.classList.remove("dragging");
    const m = model(), positions = new Map(done.positions), tb = direction() === "TB";
    const ops = [];
    let text = "移动";
    if (done.drop) {
      const {part, ids} = done.drop, slot = slotFor(m, part, new Set(ids));
      let cursor = tb ? slot.x : slot.y;
      for (const id of [...ids].sort((a, b) => tb ? positions.get(a).x - positions.get(b).x : positions.get(a).y - positions.get(b).y)) {
        const from = positions.get(id), to = tb ? {x:cursor, y:slot.y} : {x:slot.x, y:cursor}, dx = to.x - from.x, dy = to.y - from.y;
        const branch = new Set([id, ...descendants(m, id)]);
        for (const [moved, pos] of positions) if (branch.has(moved)) positions.set(moved, {x:pos.x + dx, y:pos.y + dy});
        cursor += (tb ? sizeOf(m, id).w : sizeOf(m, id).h) + GAP_CROSS;
      }
      ops.push(...ids.map(id => ({op:"parent", id, parent_id:part})));
      text = `挂到「${m.parts.get(part).name}」下`;
    }
    ops.push(moveOp(positions));
    commit(text, ops, {positions, after:() => { if (done.drop) requestAnimationFrame(() => done.drop.ids.forEach(reveal)); }});
  }
  // Leaders are carried as a chip; dropping on a part puts them there, on the shelf takes them out.
  function beginLeaderDrag() {
    const m = model(), item = m.leaders.get(gesture.leader), ghost = $(".arch-ghost");
    ghost.innerHTML = `${options.avatar(item.name, 22)}<span>${esc(item.name)}</span>`; ghost.style.setProperty("--tint", options.tint(item.name)); ghost.hidden = false;
    gesture.from = m.places.get(gesture.leader)?.domain_id ?? null;
    for (const row of host.querySelectorAll(`[data-leader="${CSS.escape(gesture.leader)}"]`)) row.classList.add("dragging");
  }
  function moveLeaderDrag(event) {
    const r = canvasRect(), ghost = $(".arch-ghost");
    ghost.style.transform = `translate(${event.clientX - r.left + 10}px,${event.clientY - r.top + 10}px)`;
    const m = model(), hit = under(event.clientX, event.clientY), part = hit?.closest?.(".arch-part"), shelf = hit?.closest?.(".arch-shelf");
    clearOver();
    gesture.target = undefined;
    if (part && part.dataset.part !== gesture.from) { gesture.target = part.dataset.part; part.classList.add("drop-target"); label(`松开归入「${m.parts.get(part.dataset.part).name}」`, event); }
    else if (shelf && gesture.from !== null) { gesture.target = null; shelf.classList.add("drop-target"); label("松开移出到未归属", event); }
    else label(null);
  }
  function finishLeaderDrag(done) {
    label(null); clearOver(); $(".arch-ghost").hidden = true;
    for (const row of host.querySelectorAll(".arch-member.dragging")) row.classList.remove("dragging");
    if (done.target !== undefined) placeLeader(done.leader, done.target);
  }
  function wireTarget(done, clientX, clientY) {
    const m = model(), node = under(clientX, clientY)?.closest?.(".arch-part"); if (!node) return null;
    const id = node.dataset.part;
    if (done.pin === "out") return attachable(m, [id], done.from) ? id : null;
    return attachable(m, [done.from], id) ? id : null;
  }
  function moveWire(event) {
    gesture.end = toWorld(event.clientX, event.clientY);
    gesture.over = wireTarget(gesture, event.clientX, event.clientY);
    clearOver();
    if (gesture.over) partEl(gesture.over)?.classList.add("drop-target");
    renderWires();
  }
  function finishWire(done) {
    clearOver(); renderWires();
    if (!done.moved) return;
    if (done.over === null) {
      if (done.pin === "in") detachPart(done.from);
      else {
        const tb = direction() === "TB";
        createPart(done.from, tb ? {x:Math.round(done.end.x - PART_W / 2), y:Math.round(done.end.y)} : {x:Math.round(done.end.x), y:Math.round(done.end.y - PIN_Y)});
      }
      return;
    }
    if (done.pin === "out") connect(done.over, done.from); else connect(done.from, done.over);
  }
  function moveMarquee(event) {
    const r = canvasRect(), box = $(".arch-marquee");
    const x0 = Math.min(gesture.x, event.clientX), y0 = Math.min(gesture.y, event.clientY), x1 = Math.max(gesture.x, event.clientX), y1 = Math.max(gesture.y, event.clientY);
    Object.assign(box.style, {left:`${x0 - r.left}px`, top:`${y0 - r.top}px`, width:`${x1 - x0}px`, height:`${y1 - y0}px`}); box.hidden = false;
    const picked = new Set(gesture.additive ? gesture.base : []);
    for (const node of host.querySelectorAll(".arch-part")) {
      const b = node.getBoundingClientRect();
      if (b.right >= x0 && b.left <= x1 && b.bottom >= y0 && b.top <= y1) picked.add(node.dataset.part);
    }
    selection = picked; renderSelection();
  }
  function wheel(event) {
    if (event.target.closest(".arch-menu, .arch-shelf-body")) return;
    event.preventDefault();
    if (!event.ctrlKey && Math.abs(event.deltaX) > 0) { camera = {...camera, x:camera.x - event.deltaX, y:camera.y - event.deltaY}; applyCamera(); saveCamera(); return; }
    const delta = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY;
    zoomAt(camera.z * Math.exp(-delta * (event.ctrlKey ? .01 : .0015)), event.clientX, event.clientY);
  }

  // ---- menu, keyboard, buttons ----------------------------------------------------------------------------------
  function openMenu(target, clientX, clientY) {
    const m = model(), edit = editable(), items = [];
    if (target.leader) {
      const place = m.places.get(target.leader), part = place ? m.parts.get(place.domain_id) : null;
      items.push(["open", "进入对话"]);
      if (edit && part) {
        items.push(place.lead ? ["unlead", `不再负责「${part.name}」`] : ["lead", `设为「${part.name}」负责人`]);
        items.push(["leave", "移出到未归属"]);
      }
      if (edit && m.leaders.get(target.leader)?.status !== "closed") {
        if (activeLeaders().some(item => item.session_id !== target.leader)) items.push(["merge", "合并到另一位 leader…"]);
        items.push(["retire", "退役…", null, "danger"]);
      }
    } else if (target.part) {
      const id = target.part, part = m.parts.get(id);
      if (!selection.has(id)) { selection = new Set([id]); renderSelection(); }
      if (edit) items.push(["rename", "改名", "F2"], ["child", "新建子 part"]);
      if (part.collapsed || descendants(m, id).length || membersOf(m, id).length) items.push(["fold", part.collapsed ? "展开" : "折叠"]);
      if (edit) { items.push(["branch", "选中这一支"]); if (part.parent_id) items.push(["detach", `从「${m.parts.get(part.parent_id).name}」移出`]); items.push(["delete", "删除 part", "Del", "danger"]); }
    } else {
      if (edit) items.push(["here", "在这里新建 part"], ["tidy", "自动排列"]);
      items.push(["fit", "显示全部", "Home"]);
    }
    menu = {...target, at:toWorld(clientX, clientY)};
    const box = $(".arch-menu"), r = canvasRect();
    box.innerHTML = items.map(([cmd, text, hint, tone]) => `<button type="button" role="menuitem" class="${tone || ""}" data-arch-act="menu" data-cmd="${cmd}"><span>${esc(text)}</span>${hint ? `<kbd>${hint}</kbd>` : ""}</button>`).join("");
    box.hidden = false;
    const x = Math.min(clientX - r.left, r.width - box.offsetWidth - 8), y = Math.min(clientY - r.top, r.height - box.offsetHeight - 8);
    box.style.transform = `translate(${Math.max(8, x)}px,${Math.max(8, y)}px)`;
    box.querySelector("button")?.focus({preventScroll:true});
  }
  function closeMenu() { if (menu === null) return; menu = null; const box = $(".arch-menu"); if (box) { box.hidden = true; box.innerHTML = ""; } }
  function menuCommand(cmd) {
    const current = menu; closeMenu();
    const m = model(), id = current?.part, leader = current?.leader;
    if (cmd === "open") options.openLeader(leader);
    else if (cmd === "lead") setLead(leader, true);
    else if (cmd === "unlead") setLead(leader, false);
    else if (cmd === "leave") placeLeader(leader, null);
    else if (cmd === "merge") openPlan(leader, "merge");
    else if (cmd === "retire") openPlan(leader, "retire");
    else if (cmd === "rename") startRename(id);
    else if (cmd === "child") createPart(id);
    else if (cmd === "fold") setCollapsed(id, !m.parts.get(id).collapsed);
    else if (cmd === "branch") selectBranch(id);
    else if (cmd === "detach") detachPart(id);
    else if (cmd === "delete") deleteParts([id]);
    else if (cmd === "here") createPart(null, {x:Math.round(current.at.x), y:Math.round(current.at.y)});
    else if (cmd === "tidy") tidy();
    else if (cmd === "fit") fit();
  }
  function click(event) {
    const button = event.target.closest("[data-arch-act]"); if (!button || !host.contains(button) || button.disabled) return;
    const act = button.dataset.archAct;
    if (act === "new") createPart();
    else if (act === "undo") undo();
    else if (act === "redo") redo();
    else if (act === "tidy") tidy();
    else if (act === "direction") switchDirection(button.dataset.direction);
    else if (act === "shelf") setShelf(!(shelfOpen ?? !phone.matches));
    else if (act === "fit") fit();
    else if (act === "zoom-in") zoomCenter(1.2);
    else if (act === "zoom-out") zoomCenter(1 / 1.2);
    else if (act === "zoom-reset") zoomCenter(1 / camera.z);
    else if (act === "fold") { const part = model().parts.get(button.dataset.id); if (part) setCollapsed(part.id, !part.collapsed); }
    else if (act === "menu") menuCommand(button.dataset.cmd);
    else if (act === "reload") { errorText = ""; render(); load(); }
    else if (act === "history") { if (history === null) openHistory(); else closeHistory(); }
    else if (act === "history-close") closeHistory();
    else if (act === "history-pick") pickVersion(Number(button.dataset.number));
    else if (act === "history-more") void loadHistory(true);
    else if (act === "history-back") { history.pane = "list"; history.confirming = false; renderHistory(); $(`.arch-version[data-number="${history.selected}"]`)?.focus(); }
    else if (act === "history-rollback") { history.confirming = true; renderHistory(); $('[data-arch-act="history-confirm"]')?.focus(); }
    else if (act === "history-cancel") { history.confirming = false; renderHistory(); $('[data-arch-act="history-rollback"]')?.focus(); }
    else if (act === "history-confirm") void rollback();
  }
  function doubleClick(event) {
    if (!editable()) return;
    const node = event.target.closest(".arch-part");
    if (node && event.target.closest(".arch-part-head") && !event.target.closest("[data-arch-act], .arch-pin")) startRename(node.dataset.part);
  }
  // The menu opens on a right click without movement (pointerup); a right drag pans instead.
  function contextMenu(event) { event.preventDefault(); }
  function keyDown(event) {
    if (event.target.classList?.contains("arch-rename")) {
      if (event.key === "Enter") { event.preventDefault(); finishRename(true); }
      else if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); finishRename(false); }
      return;
    }
    if (event.target.closest("input, textarea, select, [contenteditable]")) return;
    if (event.target.closest(".arch-history")) { if (event.key === "Escape") { event.preventDefault(); closeHistory(); } return; }
    const mod = event.ctrlKey || event.metaKey, k = event.key.toLowerCase();
    if (event.key === " ") { if (!spaceDown) { spaceDown = true; $(".arch-canvas")?.classList.add("panning"); } if (event.target.closest(".arch-canvas")) event.preventDefault(); return; }
    if (event.target.closest(".arch-menu") && event.key === "Escape") { event.preventDefault(); closeMenu(); $(".arch-canvas").focus({preventScroll:true}); return; }
    if (!event.target.closest(".arch-canvas")) return;
    if (mod && k === "z") { event.preventDefault(); if (event.shiftKey) redo(); else undo(); }
    else if (mod && k === "y") { event.preventDefault(); redo(); }
    else if (mod && k === "a" && editable()) { event.preventDefault(); selection = new Set(shownIds()); renderSelection(); }
    else if ((event.key === "Delete" || event.key === "Backspace") && editable()) { if (selection.size) { event.preventDefault(); deleteParts([...selection]); } }
    else if (event.key === "F2" && editable()) { if (selection.size === 1) { event.preventDefault(); startRename([...selection][0]); } }
    else if (event.key === "Home" || (k === "f" && !mod)) { event.preventDefault(); fit(k === "f" && selection.size ? [...selection] : shownIds(), k === "f" && selection.size ? 1.2 : 1); }
    else if (event.key === "Escape") { if (gesture) cancelGesture(); else if (menu) closeMenu(); else if (selection.size) { selection.clear(); renderSelection(); } }
  }
  function keyUp(event) { if (event.key === " ") { spaceDown = false; $(".arch-canvas")?.classList.remove("panning"); } }
  function focusOut(event) { if (event.target.classList?.contains("arch-rename") && renaming !== null) finishRename(true); }
  function viewportChange() {
    if (host === null || board === null) return;
    if (!editable()) { selection.clear(); renaming = null; }
    render();
    const saved = loadCamera();
    if (saved) { camera = saved; applyCamera(); } else fit();
  }

  // ---- version history ------------------------------------------------------------------------------------------
  const when = iso => { const d = new Date(iso), pad = n => String(n).padStart(2, "0"); return `${d.getFullYear() === new Date().getFullYear() ? "" : `${d.getFullYear()}-`}${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`; };
  function openHistory() {
    history = {list:null, more:false, latest:true, selected:null, detail:null, confirming:false, busy:false, error:"", detailError:"", pane:"list"};
    renderHead(); renderHistory(); $(".arch-history-close")?.focus(); void loadHistory();
  }
  function closeHistory() {
    if (history === null || history.busy) return;
    history = null; historyToken++; renderHead(); renderHistory(); $(".arch-history-open")?.focus({preventScroll:true});
  }
  async function loadHistory(more = false) {
    const mine = ++historyToken, name = project, before = more ? history.list.at(-1).number : null;
    try {
      const result = await options.api(`/api/domains/versions?project=${encodeURIComponent(name)}${before ? `&before=${before}` : ""}`);
      if (mine !== historyToken || history === null || name !== project) return;
      history.list = more ? history.list.concat(result.versions) : result.versions;
      history.more = result.more; history.latest = result.latest_matches; history.error = "";
    } catch (error) { if (mine !== historyToken || history === null) return; history.error = error.message || "读取版本历史失败"; }
    renderHistory();
    if (!more && history?.selected != null) await loadVersion(history.selected);
  }
  function pickVersion(number) {
    history.selected = number; history.detail = null; history.detailError = ""; history.confirming = false; history.pane = "detail";
    renderHistory(); void loadVersion(number);
  }
  async function loadVersion(number) {
    const name = project;
    try {
      const result = await options.api(`/api/domains/version?project=${encodeURIComponent(name)}&number=${number}`);
      if (history === null || history.selected !== number || name !== project) return;
      history.detail = result; history.detailError = "";
    } catch (error) { if (history === null || history.selected !== number) return; history.detail = null; history.detailError = error.message || "读取版本失败"; }
    renderHistory();
  }
  async function rollback() {
    const detail = history.detail, number = detail.version.number;
    history.busy = true; renderHistory();
    try {
      const result = await options.api("/api/domains/rollback", {project, number, board_version:detail.board_version});
      history.busy = false; history.confirming = false; history.selected = result.version; history.pane = "detail";
      undoStack = []; redoStack = [];
      options.toast(`已回滚到版本 ${number}，存为版本 ${result.version}。会话没有被删除或恢复。`);
      await load();
    } catch (error) {
      history.busy = false; history.confirming = false;
      options.toast(error.status === 409 ? "架构图刚被改过，已重新读取差异，请再核对一次。" : error.message || "回滚失败，请重试。");
      await loadHistory();
    }
  }
  function versionDetailNotes(meta) {
    const detail = meta.detail; if (!detail) return "";
    const rows = [];
    if (detail.changes?.length) rows.push(`<li>变更：${detail.changes.map(esc).join("；")}</li>`);
    if (detail.archived?.length) rows.push(`<li>归档（原会话保留）：${detail.archived.map(esc).join("、")}</li>`);
    if (detail.goals?.length) rows.push(`<li>任务：${detail.goals.map(item => `${esc(item.title)} ${item.action === "cancel" ? "作废" : `改派给 ${esc(item.to)}`}`).join("；")}</li>`);
    if (detail.handoffs?.length) rows.push(`<li>交接稿：${detail.handoffs.map(item => `${esc(item.from)} → ${esc(item.to)}（${item.chars} 字）`).join("；")}</li>`);
    if (detail.to_start?.length) rows.push(`<li>待启动的新 leader：${detail.to_start.map(esc).join("、")}</li>`);
    if (detail.unplaced?.length) rows.push(`<li>当时失去归属：${detail.unplaced.map(esc).join("、")}</li>`);
    if (detail.gone?.length) rows.push(`<li>已删除未恢复：${detail.gone.map(esc).join("、")}</li>`);
    return rows.length ? `<ul class="arch-version-notes">${rows.join("")}</ul>` : "";
  }
  function versionDetail() {
    if (history.selected === null) return `<p class="arch-history-empty">选一个版本，查看它和现在的差异，需要时回滚过去。</p>`;
    if (history.detailError) return `<p class="arch-history-error" role="alert">${esc(history.detailError)}</p>`;
    const detail = history.detail;
    if (detail === null) return `<p class="arch-history-empty" role="status">正在读取版本 ${history.selected}…</p>`;
    const meta = detail.version, impact = detail.rollback, gone = new Set(impact.gone.map(item => item.session_id));
    const target = {...detail.state, leaders:detail.state.leaders.filter(item => !gone.has(item.session_id))};
    const lists = [];
    if (impact.unplaced.length) lists.push(`<div class="arch-history-warn"><b>回滚后这些 leader 不在任何 part</b>：${impact.unplaced.map(item => `${esc(item.name)}${item.from.length ? `（现在在「${esc(item.from.join(" › "))}」）` : ""}`).join("、")}</div>`);
    if (impact.gone.length) lists.push(`<p class="arch-history-note">已永久删除，位置无法恢复：${impact.gone.map(item => esc(item.name)).join("、")}</p>`);
    if (impact.archived.length) lists.push(`<p class="arch-history-note">恢复位置，但仍是归档状态：${impact.archived.map(item => esc(item.name)).join("、")}</p>`);
    let actions = "";
    if (!impact.nothing && editable()) actions = history.confirming
      ? `<div class="arch-history-confirm" role="group" aria-label="确认回滚"><p>把架构图回滚到版本 ${meta.number}？只恢复 part 与 leader 的位置，不删除、归档或恢复任何会话；回滚本身也存成一个新版本，可以再回滚回来。</p><div class="arch-history-acts"><button type="button" class="btn ghost" data-arch-act="history-cancel" ${history.busy ? "disabled" : ""}>取消</button><button type="button" class="btn primary danger" data-arch-act="history-confirm" ${history.busy ? "disabled" : ""}>${history.busy ? "正在回滚…" : "确认回滚"}</button></div></div>`
      : `<div class="arch-history-acts"><button type="button" class="btn" data-arch-act="history-rollback">${icon(ICON.undo)}回滚到这个版本…</button></div>`;
    else if (!impact.nothing) actions = '<p class="arch-history-note">在电脑上打开 Crew 可以回滚。</p>';
    return `<button type="button" class="btn ghost arch-history-back" data-arch-act="history-back">${icon(ICON.back)}版本列表</button>`
      + `<div class="arch-version-title"><b>版本 ${meta.number}</b><span class="arch-source ${SOURCE_TONE[meta.source]}">${esc(meta.source_label)}</span><span class="arch-version-time">${esc(when(meta.created_at))}</span></div>`
      + `<p class="arch-version-note">${esc(meta.note)}</p>${versionDetailNotes(meta)}`
      + (impact.nothing ? '<p class="arch-history-same">现在的架构和这个版本相同。</p>'
        : `<p class="arch-history-sum">回滚会：${esc(CrewRestructure.diffSummary(detail.diff))}</p>${CrewRestructure.compare(detail.current, target, {beforeTitle:"现在", afterTitle:"回滚后"})}${lists.join("")}`)
      + actions;
  }
  function renderHistory() {
    const box = $(".arch-history"); if (!box) return;
    box.hidden = history === null;
    if (history === null) { box.innerHTML = ""; return; }
    const listScroll = box.querySelector(".arch-history-list")?.scrollTop ?? 0, detailScroll = box.querySelector(".arch-history-detail")?.scrollTop ?? 0;
    const focused = document.activeElement && box.contains(document.activeElement) ? document.activeElement.dataset.archAct + ":" + (document.activeElement.dataset.number || "") : null;
    box.dataset.pane = history.pane;
    const rows = history.list === null ? (history.error ? "" : '<p class="arch-history-empty" role="status">正在读取…</p>')
      : history.list.length ? history.list.map(item => `<button type="button" class="arch-version${item.number === history.selected ? " selected" : ""}" data-arch-act="history-pick" data-number="${item.number}" aria-pressed="${item.number === history.selected}"><span class="arch-version-line"><b>#${item.number}</b><span class="arch-source ${SOURCE_TONE[item.source]}">${esc(item.source_label)}</span>${item.restored_from ? `<span class="arch-version-from">← #${item.restored_from}</span>` : ""}<span class="arch-version-time">${esc(when(item.created_at))}</span></span><span class="arch-version-text">${esc(item.note)}</span><span class="arch-version-count">${item.parts} 个 part · ${item.leaders} 位 leader</span></button>`).join("")
        + (history.more ? '<button type="button" class="btn ghost arch-history-more" data-arch-act="history-more">加载更早的版本</button>' : "")
      : '<p class="arch-history-empty">还没有版本。改动架构图的结构（新建、改名、改挂、删除 part，或调整 leader）后，每批改动都会存一个版本。</p>';
    box.innerHTML = `<div class="arch-history-head"><h2>${icon(ICON.history)}版本历史</h2><button type="button" class="icon-btn arch-history-close" data-arch-act="history-close" aria-label="关闭版本历史" ${history.busy ? "disabled" : ""}>${icon(ICON.close)}</button></div>`
      + (history.latest ? "" : '<p class="arch-history-warn top">现在的架构有没记成版本的变化（例如永久删除了 leader），回滚前请看清差异。</p>')
      + (history.error ? `<p class="arch-history-error" role="alert">${esc(history.error)}</p>` : "")
      + `<div class="arch-history-body"><div class="arch-history-list" role="list" aria-label="版本">${rows}</div><div class="arch-history-detail" aria-live="polite">${versionDetail()}</div></div>`;
    box.querySelector(".arch-history-list").scrollTop = listScroll; box.querySelector(".arch-history-detail").scrollTop = detailScroll;
    if (focused) { const [act, number] = focused.split(":"); box.querySelector(`[data-arch-act="${act}"]${number ? `[data-number="${number}"]` : ""}`)?.focus({preventScroll:true}); }
  }

  // ---- merge / retire a leader through the restructure preview --------------------------------------------------
  const activeLeaders = () => (options.state.leaders || []).filter(item => item.status !== "closed");
  function openPlan(leader, kind) {
    planner ??= CrewRestructure.create({
      api:(...args) => options.api(...args), toast:message => options.toast(message), leaders:() => options.state.leaders || [],
      onApplied:async () => { undoStack = []; redoStack = []; await load(); }
    });
    const name = model().leaders.get(leader)?.name || leader, other = activeLeaders().find(item => item.session_id !== leader);
    planner.open(kind === "merge"
      ? {project, changes:[{type:"leader.merge", leader, into:other.session_id}], note:`合并 leader「${name}」`}
      : {project, changes:[{type:"leader.retire", leader, successor:null}], note:`退役 leader「${name}」`});
  }

  // ---- lifecycle ------------------------------------------------------------------------------------------------
  const listeners = [["click", click], ["dblclick", doubleClick], ["keydown", keyDown], ["keyup", keyUp], ["focusout", focusOut]];
  const canvasListeners = [["pointerdown", pointerDown], ["pointermove", pointerMove], ["pointerup", pointerUp], ["pointercancel", pointerUp], ["contextmenu", contextMenu]];
  function mount(element, settings) {
    if (settings.focus) focusRequest = settings.focus;
    if (host === element && project === settings.project) { update(settings); applyFocus(); return; }
    unmount();
    options = settings;
    if (project !== settings.project) {
      project = settings.project; board = null; cached = null; errorText = ""; camera = null; selection = new Set();
      undoStack = []; redoStack = []; pending.clear(); renaming = null; pendingDirection = null; history = null;
    }
    if (shelfOpen === null) { try { const saved = localStorage.getItem("crew.architecture.shelf"); if (saved !== null) shelfOpen = saved === "true"; } catch { /* default by width */ } }
    host = element; host.innerHTML = scaffold(); delete host.dataset.render;
    for (const [type, handler] of listeners) host.addEventListener(type, handler);
    const canvas = $(".arch-canvas");
    for (const [type, handler] of canvasListeners) canvas.addEventListener(type, handler);
    canvas.addEventListener("wheel", wheel, {passive:false});
    // overflow:hidden still scrolls to a focused child; the camera is the only way this canvas moves.
    canvas.addEventListener("scroll", () => { canvas.scrollLeft = 0; canvas.scrollTop = 0; });
    resizer = new ResizeObserver(() => { if (pendingFit !== null && host !== null && board !== null) { fit(...pendingFit); applyFocus(); } });
    resizer.observe(canvas);
    phone.addEventListener("change", viewportChange);
    leaderSignature = signature(settings.state);
    if (board === null) { render(); void load(); return; }
    render(); if (camera === null) fit(); applyCamera(); applyFocus();
    if ((settings.state.domains?.version ?? 0) > board.version) void load(true);
  }
  const signature = state => JSON.stringify((state.leaders || []).map(item => [item.session_id, item.name, item.goal, item.status, item.color, item.avatar_version]));
  function update(settings) {
    options = settings;
    if (host === null || board === null) return;
    const remote = settings.state.domains?.version ?? 0;
    if (remote > board.version && busy === 0 && gesture === null) { void load(true); return; }
    const next = signature(settings.state);
    if (next !== leaderSignature && gesture === null && renaming === null) { leaderSignature = next; cached = null; renderWorld(); }
    renderHead();
  }
  function unmount() {
    if (host !== null) {
      for (const [type, handler] of listeners) host.removeEventListener(type, handler);
      phone.removeEventListener("change", viewportChange);
      resizer?.disconnect(); resizer = null;
      if (camera !== null && pendingFit === null) saveCamera();
    }
    gesture = null; pointers.clear(); menu = null; spaceDown = false; renaming = null; host = null; history = null; historyToken++;
  }
  return {mount, update, unmount};
})();
