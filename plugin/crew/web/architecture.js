"use strict";
// Architecture board: the workspace's domain tree drawn as a node graph. A part nests under another part and may
// own one leader for its layer's shared work; sub-parts and leaders hang on a part when dropped on it or wired from
// its pin. Positions and folds are saved with the tree; the camera stays in this browser. Editing needs a window
// wider than a phone; on phones the board can be panned, zoomed and leaders opened.
const CrewArchitecture = (() => {
  const PART_W = 212, LEADER_W = 252, PIN_Y = 21, PART_H = 76, LEADER_H = 104, COLUMN = 320, ROW_GAP = 24;
  const MIN_ZOOM = .2, MAX_ZOOM = 2, SLOP = 4, HISTORY = 100, NAME_MAX = 40;
  const phone = window.matchMedia("(max-width:760px)");
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[c]);
  const icon = d => `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
  const ICON = {
    plus:'<path d="M12 5v14M5 12h14"/>', minus:'<path d="M5 12h14"/>',
    undo:'<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>', redo:'<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
    tidy:'<rect x="3" y="9" width="6" height="5" rx="1.5"/><rect x="15" y="3" width="6" height="5" rx="1.5"/><rect x="15" y="15" width="6" height="5" rx="1.5"/><path d="M9 11.5h3v-6h3M12 11.5v6h3"/>',
    fit:'<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>', down:'<path d="m6 9 6 6 6-6"/>', right:'<path d="m9 6 6 6-6 6"/>',
    folder:'<path d="M3 6.5h6.5l2 2.5H21v10.5H3z"/>', board:'<rect x="9" y="3" width="6" height="5" rx="1.5"/><rect x="3" y="16" width="6" height="5" rx="1.5"/><rect x="15" y="16" width="6" height="5" rx="1.5"/><path d="M12 8v4M6 16v-2a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v2"/>'
  };
  const STATUS = {working:"在干活", waiting:"等你", idle:"空闲", offline:"离线", closed:"已归档"};

  let host = null, options = null, board = null, project = null, errorText = "", loadToken = 0;
  let camera = null, selection = new Set(), undoStack = [], redoStack = [], queue = Promise.resolve(), busy = 0;
  let gesture = null, renaming = null, menu = null, focusRequest = null, spaceDown = false, cached = null, leaderSignature = "";
  let pendingFit = null, resizer = null;
  const pointers = new Map(), pending = new Map();

  const key = (kind, id) => `${kind}:${id}`;
  const split = k => { const at = k.indexOf(":"); return [k.slice(0, at), k.slice(at + 1)]; };
  const editable = () => !phone.matches;
  const clampZoom = z => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));
  const $ = selector => host?.querySelector(selector);

  // ---- model ----------------------------------------------------------------------------------------------------
  function model() {
    const leadersList = options.state.leaders || [];
    if (cached && cached.board === board && cached.leadersList === leadersList) return cached;
    const parts = new Map(board.parts.map(part => [part.id, part]));
    const places = new Map(board.leaders.map(place => [place.session_id, place]));
    const leaders = new Map(leadersList.map(item => [item.session_id, item]));
    const children = new Map(), holder = new Map();
    for (const part of board.parts) { if (!children.has(part.parent_id)) children.set(part.parent_id, []); children.get(part.parent_id).push(part); }
    for (const place of board.leaders) if (place.domain_id !== null) holder.set(place.domain_id, place);
    cached = {board, leadersList, parts, places, leaders, children, holder, auto:new Map()};
    autoPlace(cached);
    return cached;
  }
  // Leaders without a saved place: beside their part, or in a column left of everything else.
  function autoPlace(m) {
    let minX = Infinity, minY = Infinity;
    for (const part of board.parts) { minX = Math.min(minX, part.x); minY = Math.min(minY, part.y); }
    for (const place of board.leaders) if (place.x !== null && m.leaders.has(place.session_id)) { minX = Math.min(minX, place.x); minY = Math.min(minY, place.y); }
    const content = Number.isFinite(minX);
    let loose = 0;
    for (const item of m.leaders.values()) {
      const place = m.places.get(item.session_id);
      if (place && place.x !== null && place.y !== null) continue;
      const part = place?.domain_id ? m.parts.get(place.domain_id) : null;
      if (part) { m.auto.set(item.session_id, {x:part.x + COLUMN, y:part.y}); continue; }
      const column = Math.floor(loose / 6), row = loose % 6; loose++;
      m.auto.set(item.session_id, {x:content ? minX - COLUMN - column * COLUMN : column * COLUMN, y:(content ? minY : 0) + row * (LEADER_H + ROW_GAP)});
    }
  }
  function partHidden(m, id) {
    for (let part = m.parts.get(m.parts.get(id)?.parent_id); part; part = m.parts.get(part.parent_id)) if (part.collapsed) return true;
    return false;
  }
  function leaderHidden(m, sessionId) {
    const domain = m.places.get(sessionId)?.domain_id, part = domain ? m.parts.get(domain) : null;
    return part ? part.collapsed || partHidden(m, part.id) : false;
  }
  const visible = (m, k) => { const [kind, id] = split(k); return kind === "part" ? m.parts.has(id) && !partHidden(m, id) : m.leaders.has(id) && !leaderHidden(m, id); };
  function ancestors(m, id) { const out = []; for (let part = m.parts.get(m.parts.get(id)?.parent_id); part; part = m.parts.get(part.parent_id)) out.push(part.id); return out; }
  // Everything below a part: its sub-parts at any depth and the leaders they own.
  function descendants(m, id) {
    const out = [], walk = partId => {
      const owner = m.holder.get(partId); if (owner && m.leaders.has(owner.session_id)) out.push(key("leader", owner.session_id));
      for (const child of m.children.get(partId) || []) { out.push(key("part", child.id)); walk(child.id); }
    };
    walk(id); return out;
  }
  const parentOf = (m, k) => { const [kind, id] = split(k); return kind === "part" ? m.parts.get(id)?.parent_id ?? null : m.places.get(id)?.domain_id ?? null; };
  function posOf(m, k) {
    if (gesture?.positions?.has(k)) return gesture.positions.get(k);
    if (pending.has(k)) return pending.get(k).pos;
    const [kind, id] = split(k);
    if (kind === "part") { const part = m.parts.get(id); return {x:part.x, y:part.y}; }
    const place = m.places.get(id);
    return place && place.x !== null && place.y !== null ? {x:place.x, y:place.y} : m.auto.get(id) || {x:0, y:0};
  }
  function sizeOf(k) {
    const node = host?.querySelector(`.arch-node[data-key="${CSS.escape(k)}"]`);
    return node ? {w:node.offsetWidth, h:node.offsetHeight} : k.startsWith("part:") ? {w:PART_W, h:PART_H} : {w:LEADER_W, h:LEADER_H};
  }
  const nameOf = (m, k) => { const [kind, id] = split(k); return kind === "part" ? m.parts.get(id)?.name : m.leaders.get(id)?.name ?? m.places.get(id)?.name; };

  // ---- rendering ------------------------------------------------------------------------------------------------
  function scaffold() {
    return `<div id="arch-view" class="arch-view">
      <div class="head big arch-head"></div>
      <div class="arch-canvas" tabindex="0" aria-label="架构图画板" aria-describedby="arch-hint">
        <div class="arch-world"><svg class="arch-wires" aria-hidden="true"></svg><div class="arch-nodes"></div></div>
        <div class="arch-marquee" hidden></div><div class="arch-drop-label" hidden></div>
        <div class="arch-empty" hidden></div>
        <p class="arch-hint" id="arch-hint"></p>
        <div class="arch-zoom" role="group" aria-label="缩放"><button type="button" data-arch-act="zoom-out" aria-label="缩小">${icon(ICON.minus)}</button><button type="button" class="arch-zoom-value" data-arch-act="zoom-reset" title="恢复 100%"></button><button type="button" data-arch-act="zoom-in" aria-label="放大">${icon(ICON.plus)}</button><button type="button" data-arch-act="fit" title="显示全部 (Home)" aria-label="显示全部">${icon(ICON.fit)}</button></div>
        <div class="arch-menu" role="menu" hidden></div>
      </div>
    </div>`;
  }
  function render() {
    if (host === null) return;
    if (board === null) {
      const head = $(".arch-head"); if (head) head.innerHTML = `<div><div class="title">架构图</div><div class="sub">${errorText ? "" : "正在读取…"}</div></div>`;
      const empty = $(".arch-empty");
      if (empty) { empty.hidden = !errorText; empty.innerHTML = errorText ? `<p class="arch-error" role="alert">${esc(errorText)}</p><button type="button" class="btn" data-arch-act="reload">重新读取</button>` : ""; }
      return;
    }
    renderHead(); renderWorld(); applyCamera();
  }
  function renderHead() {
    const head = $(".arch-head"); if (!head || board === null) return;
    const m = model(), loose = [...m.leaders.keys()].filter(id => !m.places.get(id)?.domain_id).length;
    const edit = editable(), picked = edit && selection.size > 1 ? ` · 已选 ${selection.size} 项` : "";
    const tools = edit ? `<button type="button" class="btn primary arch-new" data-arch-act="new">${icon(ICON.plus)}新 part</button><span class="arch-tool-gap" aria-hidden="true"></span><button type="button" class="icon-btn" data-arch-act="undo" title="撤销${undoStack.length ? `：${esc(undoStack.at(-1).label)}` : ""} (Ctrl+Z)" aria-label="撤销" ${undoStack.length ? "" : "disabled"}>${icon(ICON.undo)}</button><button type="button" class="icon-btn" data-arch-act="redo" title="重做${redoStack.length ? `：${esc(redoStack.at(-1).label)}` : ""} (Ctrl+Shift+Z)" aria-label="重做" ${redoStack.length ? "" : "disabled"}>${icon(ICON.redo)}</button><button type="button" class="btn ghost" data-arch-act="tidy" title="按领域树自动排列（可撤销）">${icon(ICON.tidy)}<span class="arch-tidy-label">自动排列</span></button>` : "";
    const markup = `<div class="arch-heading"><div class="title">${icon(ICON.board)}架构图${busy ? '<span class="arch-saving">保存中…</span>' : ""}</div><div class="sub">${board.parts.length} 个 part · ${m.leaders.size} 位 leader${loose ? ` · ${loose} 位未归属` : ""}${picked}${edit ? "" : ' · <span title="在电脑上打开 Crew 可以编辑">只读</span>'}</div></div><div class="right">${tools}</div>`;
    if (head.dataset.render !== markup) { head.innerHTML = markup; head.dataset.render = markup; }
    const hint = $(".arch-hint");
    const text = edit ? "拖到 part 上或从圆点拉线来挂接 · 左键框选 · 右键 / 空格拖动平移 · 滚轮缩放" : "拖动平移 · 双指缩放 · 点 leader 进入对话 · 在电脑上可以编辑";
    if (hint.textContent !== text) hint.textContent = text;
  }
  function partNode(m, part) {
    const k = key("part", part.id), p = posOf(m, k), owner = m.holder.get(part.id);
    const leader = owner ? m.leaders.get(owner.session_id) : null, kids = (m.children.get(part.id) || []).length + (owner && leader ? 1 : 0);
    const hidden = part.collapsed ? descendants(m, part.id).length : 0;
    const body = leader ? `<span class="arch-owner" style="--tint:${esc(options.tint(leader.name))}">${options.avatar(leader.name, 18)}<span>负责 <b>${esc(leader.name)}</b></span></span>`
      : owner ? `<span class="arch-owner archived" title="这位 leader 已归档；挂上别的 leader 会替换它">已归档的 ${esc(owner.name)}</span>`
      : `<span class="arch-folder-note">${editable() ? "文件夹 · 拖 leader 到这里负责本层" : "文件夹"}</span>`;
    const name = renaming === part.id ? `<input class="arch-rename" value="${esc(part.name)}" maxlength="${NAME_MAX}" aria-label="part 名称" spellcheck="false">`
      : `<span class="arch-part-name" title="${esc(part.name)}${editable() ? "（双击改名）" : ""}">${esc(part.name)}</span>`;
    return `<div class="arch-node arch-part${selection.has(k) ? " selected" : ""}${part.collapsed ? " collapsed" : ""}${gesture?.keys?.includes(k) ? " dragging" : ""}" data-key="${esc(k)}" style="transform:translate(${p.x}px,${p.y}px)" role="group" aria-label="part ${esc(part.name)}">`
      + `<span class="arch-pin in${part.parent_id ? " linked" : ""}" data-pin="in" title="上级 part：拖到别的 part 改挂"></span>`
      + `<div class="arch-part-head"><button type="button" class="arch-fold" data-arch-act="fold" data-id="${esc(part.id)}" aria-expanded="${!part.collapsed}" aria-label="${part.collapsed ? "展开" : "折叠"} ${esc(part.name)}" ${kids || part.collapsed ? "" : "disabled"}>${icon(part.collapsed ? ICON.right : ICON.down)}</button>${icon(ICON.folder)}${name}${hidden ? `<span class="arch-count" title="折叠了 ${hidden} 项">${hidden}</span>` : ""}</div>`
      + `<div class="arch-part-body">${body}</div>`
      + `<span class="arch-pin out${kids ? " linked" : ""}" data-pin="out" title="下属：拉线连到子 part 或负责的 leader"></span></div>`;
  }
  function leaderNode(m, item) {
    const k = key("leader", item.session_id), p = posOf(m, k), status = STATUS[item.status] ? item.status : "idle";
    const placed = Boolean(m.places.get(item.session_id)?.domain_id);
    return `<div class="arch-node arch-leader${selection.has(k) ? " selected" : ""}${gesture?.keys?.includes(k) ? " dragging" : ""}" data-key="${esc(k)}" style="--tint:${esc(options.tint(item.name))};transform:translate(${p.x}px,${p.y}px)" role="link" aria-label="${esc(item.name)}，${STATUS[status]}，进入对话">`
      + `<span class="arch-pin in${placed ? " linked" : ""}" data-pin="in" title="所属 part：拉线到 part 改挂"></span>`
      + `<div class="arch-leader-head">${options.avatar(item.name, 28)}<span class="arch-leader-name" title="${esc(item.name)}">${esc(item.name)}</span><span class="st ${status}"><i class="dot ${status}"></i>${STATUS[status]}</span></div>`
      + `<p class="arch-leader-goal" title="${esc(item.goal || "")}">${item.goal ? esc(item.goal) : '<span class="arch-muted">还没有简介</span>'}</p>`
      + `<span class="arch-leader-open">进入对话 ›</span></div>`;
  }
  function renderWorld() {
    const m = model(), nodes = $(".arch-nodes");
    if (!nodes) return;
    const markup = board.parts.filter(part => !partHidden(m, part.id)).map(part => partNode(m, part)).join("")
      + [...m.leaders.values()].filter(item => !leaderHidden(m, item.session_id)).map(item => leaderNode(m, item)).join("");
    nodes.innerHTML = markup;
    renderWires();
    const empty = $(".arch-empty");
    empty.hidden = board.parts.length > 0;
    if (!empty.hidden) empty.innerHTML = `<div class="arch-empty-text"><h2>把领域拆成一棵树</h2><p>part 可以层层嵌套，比如 感知 › 视觉 › 相机；每个 part 可以挂一位 leader 负责这一层共用的部分，也可以只是文件夹。${editable() ? "新建 part 后，把 leader 拖到它上面，或从 part 右侧的圆点拉线连过去。" : "在电脑上打开 Crew 可以编辑。"}</p></div>${editable() ? `<button type="button" class="btn primary" data-arch-act="new">${icon(ICON.plus)}新建第一个 part</button>` : ""}`;
    if (renaming !== null) {
      const input = $(".arch-rename");
      if (input && document.activeElement !== input) { reveal(key("part", renaming)); input.focus({preventScroll:true}); input.select(); }
    }
  }
  const bend = (a, b) => Math.max(40, Math.abs(b.x - a.x) * .5);
  const wirePath = (a, b) => { const dx = bend(a, b); return `M${a.x} ${a.y}C${a.x + dx} ${a.y} ${b.x - dx} ${b.y} ${b.x} ${b.y}`; };
  const outPin = (m, partId) => { const p = posOf(m, key("part", partId)); return {x:p.x + PART_W, y:p.y + PIN_Y}; };
  const inPin = (m, k) => { const p = posOf(m, k); return {x:p.x, y:p.y + PIN_Y}; };
  // The SVG box spans exactly the wires: Chrome skips painting an SVG whose own box is off screen, overflow or not.
  function renderWires() {
    const m = model(), svg = $(".arch-wires"); if (!svg) return;
    const wires = [];
    for (const part of board.parts) if (part.parent_id !== null && !partHidden(m, part.id)) wires.push(["arch-wire", "", outPin(m, part.parent_id), inPin(m, key("part", part.id))]);
    for (const item of m.leaders.values()) {
      const domain = m.places.get(item.session_id)?.domain_id;
      if (domain && !leaderHidden(m, item.session_id)) wires.push(["arch-wire leader", `--tint:${esc(options.tint(item.name))}`, outPin(m, domain), inPin(m, key("leader", item.session_id))]);
    }
    if (gesture?.type === "wire" && gesture.moved) {
      const from = gesture.pin === "out" ? outPin(m, split(gesture.from)[1]) : gesture.end, to = gesture.pin === "out" ? gesture.end : inPin(m, gesture.from);
      wires.push([`arch-wire draft${gesture.over ? " ok" : ""}`, "", from, to]);
    }
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const [, , a, b] of wires) {
      const dx = bend(a, b);
      x0 = Math.min(x0, a.x, b.x - dx); x1 = Math.max(x1, a.x + dx, b.x); y0 = Math.min(y0, a.y, b.y); y1 = Math.max(y1, a.y, b.y);
    }
    if (!wires.length) { x0 = y0 = 0; x1 = y1 = 1; }
    x0 -= 4; y0 -= 4; x1 += 4; y1 += 4;
    Object.assign(svg.style, {left:`${x0}px`, top:`${y0}px`, width:`${x1 - x0}px`, height:`${y1 - y0}px`});
    svg.setAttribute("viewBox", `${x0} ${y0} ${x1 - x0} ${y1 - y0}`);
    svg.innerHTML = wires.map(([cls, style, a, b]) => `<path class="${cls}"${style ? ` style="${style}"` : ""} d="${wirePath(a, b)}"/>`).join("");
  }
  function renderSelection() {
    for (const node of host.querySelectorAll(".arch-node")) node.classList.toggle("selected", selection.has(node.dataset.key));
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

  // ---- camera ---------------------------------------------------------------------------------------------------
  function canvasRect() { return $(".arch-canvas").getBoundingClientRect(); }
  function toWorld(clientX, clientY) { const r = canvasRect(); return {x:(clientX - r.left - camera.x) / camera.z, y:(clientY - r.top - camera.y) / camera.z}; }
  function zoomAt(z, clientX, clientY) {
    const r = canvasRect(), sx = clientX - r.left, sy = clientY - r.top, next = clampZoom(z);
    camera = {x:sx - (sx - camera.x) * next / camera.z, y:sy - (sy - camera.y) * next / camera.z, z:next};
    applyCamera(); saveCamera();
  }
  // Pan just enough to bring a node fully into view.
  function reveal(k) {
    const node = host.querySelector(`.arch-node[data-key="${CSS.escape(k)}"]`); if (!node) return;
    const r = canvasRect(), b = node.getBoundingClientRect(), margin = 32;
    const dx = b.left < r.left + margin ? r.left + margin - b.left : b.right > r.right - margin ? r.right - margin - b.right : 0;
    const dy = b.top < r.top + margin ? r.top + margin - b.top : b.bottom > r.bottom - 64 ? r.bottom - 64 - b.bottom : 0;
    if (dx || dy) { camera = {...camera, x:camera.x + dx, y:camera.y + dy}; applyCamera(); saveCamera(); }
  }
  function zoomCenter(factor) { const r = canvasRect(); zoomAt(camera.z * factor, r.left + r.width / 2, r.top + r.height / 2); }
  function bounds(keys) {
    const m = model(); let box = null;
    for (const k of keys) {
      const p = posOf(m, k), size = sizeOf(k);
      box = box === null ? {x0:p.x, y0:p.y, x1:p.x + size.w, y1:p.y + size.h}
        : {x0:Math.min(box.x0, p.x), y0:Math.min(box.y0, p.y), x1:Math.max(box.x1, p.x + size.w), y1:Math.max(box.y1, p.y + size.h)};
    }
    return box;
  }
  const shownKeys = () => [...host.querySelectorAll(".arch-node")].map(node => node.dataset.key);
  function fit(keys = shownKeys(), maxZoom = 1) {
    const r = canvasRect(), box = bounds(keys), pad = r.width < 500 ? 24 : 56;
    // Not laid out yet (the page is still appearing): fit once the canvas has a size.
    if (r.width === 0 || r.height === 0) { pendingFit = [keys, maxZoom]; return; }
    pendingFit = null;
    if (box === null) { camera = {x:r.width / 2 - 100, y:Math.max(80, r.height / 3), z:1}; applyCamera(); return; }
    // Keep clear of the first-use banner at the top and the hint and zoom bar at the bottom.
    const banner = $(".arch-empty"), top = banner && !banner.hidden ? banner.offsetTop + banner.offsetHeight + 16 : pad, bottom = pad + 24;
    const z = clampZoom(Math.min(maxZoom, (r.width - pad * 2) / Math.max(1, box.x1 - box.x0), (r.height - top - bottom) / Math.max(1, box.y1 - box.y0)));
    camera = {z, x:(r.width - (box.x1 - box.x0) * z) / 2 - box.x0 * z, y:top + (r.height - top - bottom - (box.y1 - box.y0) * z) / 2 - box.y0 * z};
    applyCamera(); saveCamera();
  }
  // From the chat path: show a part (or the nearest shown one above it) and flash it.
  function applyFocus() {
    if (focusRequest === null || board === null || host === null) return;
    const m = model(), request = focusRequest; focusRequest = null;
    let target = null;
    if (request.part && m.parts.has(request.part)) target = [request.part, ...ancestors(m, request.part)].find(id => !partHidden(m, id));
    const keys = target ? [key("part", target)] : [];
    if (request.leader && visible(m, key("leader", request.leader))) keys.push(key("leader", request.leader));
    if (!keys.length) return;
    selection = new Set(keys); renderSelection();
    fit(keys, Math.max(camera?.z ?? 1, .9));
    for (const k of keys) {
      const node = host.querySelector(`.arch-node[data-key="${CSS.escape(k)}"]`);
      if (node) { node.classList.remove("flash"); void node.offsetWidth; node.classList.add("flash"); }
    }
  }

  // ---- saving and undo ------------------------------------------------------------------------------------------
  async function load(clearHistory = false) {
    const token = ++loadToken, name = project;
    try {
      const result = await options.api(`/api/domains?project=${encodeURIComponent(name)}`);
      if (token !== loadToken || name !== project) return;
      board = result; errorText = ""; cached = null;
      if (clearHistory) { undoStack = []; redoStack = []; }
      selection = new Set([...selection].filter(k => host === null || visible(model(), k)));
    } catch (error) {
      if (token !== loadToken) return;
      errorText = error.message || "读取架构图失败";
      if (board !== null) options.toast(errorText);
    }
    if (host === null) return;
    const first = camera === null;
    render();
    if (first && board !== null) { camera = loadCamera(); if (camera === null) { camera = {x:0, y:0, z:1}; fit(); } applyCamera(); }
    applyFocus(); settleLeaders();
  }
  // A leader without a saved place is drawn where it would fit; save that once, so adding parts later doesn't move it.
  function settleLeaders() {
    if (!editable() || board === null || host === null || busy > 0 || gesture !== null) return;
    const m = model(), items = [...m.auto].map(([id, p]) => ({kind:"leader", id, x:Math.round(p.x), y:Math.round(p.y)}));
    if (items.length) commit("放置 leader", [{op:"move", items}], {undoable:false});
  }
  function run(task) {
    busy++; renderHead();
    queue = queue.then(task).catch(error => {
      pending.clear();
      if (error.status === 409) { undoStack = []; redoStack = []; options.toast("架构图在别处改过了，已重新载入；之前的撤销记录已清空。"); }
      else options.toast(error.message || "保存失败，请重试。");
      return load(error.status === 409);
    }).finally(() => { busy--; if (host !== null && board !== null) { render(); } });
    return queue;
  }
  async function send(ops) {
    const result = await options.api("/api/domains", {project, base_version:board.version, ops});
    board = result.board; cached = null;
    return result;
  }
  // The undo batch: each op's opposite in reverse order, from the board before the batch and what the server did.
  function invert(before, ops, effects) {
    const parts = new Map(before.parts.map(part => [part.id, part])), places = new Map(before.leaders.map(place => [place.session_id, place]));
    const m = model(), back = [];
    ops.forEach((op, index) => {
      const effect = effects[index];
      let steps = [];
      if (op.op === "create") steps = [{op:"delete", id:effect.id}];
      else if (op.op === "rename") steps = [{op:"rename", id:op.id, name:parts.get(op.id).name}];
      else if (op.op === "collapse") steps = [{op:"collapse", id:op.id, collapsed:parts.get(op.id).collapsed}];
      else if (op.op === "parent") steps = [{op:"parent", id:op.id, parent_id:parts.get(op.id).parent_id}];
      else if (op.op === "move") steps = [{op:"move", items:op.items.map(item => {
        const old = item.kind === "part" ? parts.get(item.id) : places.get(item.id);
        const at = old && old.x !== null && old.y !== null ? old : before.auto?.get(item.id) || m.auto.get(item.id) || item;
        return {kind:item.kind, id:item.id, x:at.x, y:at.y};
      })}];
      else if (op.op === "assign") steps = [{op:"assign", session_id:op.session_id, domain_id:places.get(op.session_id)?.domain_id ?? null},
        ...(effect ? [{op:"assign", session_id:effect.session_id, domain_id:effect.domain_id}] : [])];
      else if (op.op === "delete") {
        const part = parts.get(op.id);
        steps = [{op:"create", id:part.id, name:part.name, parent_id:effect.parent_id, x:part.x, y:part.y, collapsed:part.collapsed},
          ...effect.children.map(child => ({op:"parent", id:child, parent_id:part.id})),
          ...(effect.leader ? [{op:"assign", session_id:effect.leader, domain_id:part.id}] : [])];
      }
      back.unshift(...steps);
    });
    return back;
  }
  // One user action. Moves show at once; the rest redraws when the server answers.
  function commit(label, ops, {positions = null, undoable = true, after = null} = {}) {
    const token = {};
    if (positions) for (const [k, pos] of positions) pending.set(k, {pos, token});
    if (positions) { renderWorld(); }
    return run(async () => {
      const before = {...board, auto:new Map(model().auto)};
      const result = await send(ops);
      for (const [k, value] of pending) if (value.token === token) pending.delete(k);
      let step = null;
      if (undoable) { step = {label, forward:ops, backward:invert(before, ops, result.effects)}; undoStack.push(step); if (undoStack.length > HISTORY) undoStack.shift(); redoStack = []; }
      after?.(result, before, step);
    });
  }
  function undo() {
    if (!editable() || !undoStack.length) return;
    const step = undoStack.pop();
    run(async () => { try { await send(step.backward); redoStack.push(step); } catch (error) { undoStack.push(step); throw error; } });
  }
  function redo() {
    if (!editable() || !redoStack.length) return;
    const step = redoStack.pop();
    run(async () => { try { await send(step.forward); undoStack.push(step); } catch (error) { redoStack.push(step); throw error; } });
  }
  // A toast's 撤销 undoes that step only while nothing newer sits on top of it.
  const undoStep = step => () => {
    if (undoStack.at(-1) !== step) { options.toast("之后又有改动，请用 Ctrl+Z 逐步撤销"); return Promise.resolve(); }
    undo(); return queue;
  };

  // ---- edits ----------------------------------------------------------------------------------------------------
  function newId() { const bytes = new Uint8Array(6); crypto.getRandomValues(bytes); return "p" + [...bytes].map(b => b.toString(16).padStart(2, "0")).join(""); }
  function freeName(m, base = "新 part") { const names = new Set(board.parts.map(part => part.name)); if (!names.has(base)) return base; for (let n = 2; ; n++) if (!names.has(`${base} ${n}`)) return `${base} ${n}`; }
  // The next free row to the right of a part, below what already hangs there.
  function slotBelow(m, partId, skip = new Set()) {
    const parent = posOf(m, key("part", partId));
    let y = parent.y, any = false;
    const owner = m.holder.get(partId);
    const kids = [...(owner && m.leaders.has(owner.session_id) ? [key("leader", owner.session_id)] : []), ...(m.children.get(partId) || []).map(part => key("part", part.id))];
    for (const k of kids) if (!skip.has(k) && visible(m, k)) { const p = posOf(m, k); y = Math.max(y, p.y + sizeOf(k).h + ROW_GAP); any = true; }
    return {x:parent.x + COLUMN, y:any ? y : parent.y};
  }
  function createPart(parentId = null, at = null) {
    if (!editable()) return;
    const m = model(), id = newId(), name = freeName(m);
    let pos = at ?? (parentId !== null ? slotBelow(m, parentId) : null);
    if (pos === null) { const r = canvasRect(); pos = toWorld(r.left + r.width / 2 - PART_W / 2 * camera.z, r.top + r.height / 2 - PART_H / 2 * camera.z); }
    const ops = [{op:"create", id, name, parent_id:parentId, x:Math.round(pos.x), y:Math.round(pos.y)}];
    if (parentId !== null && m.parts.get(parentId).collapsed) ops.push({op:"collapse", id:parentId, collapsed:false});
    commit("新建 part", ops, {after:() => { selection = new Set([key("part", id)]); renaming = id; }});
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
    if (collapsed) selection = new Set([...selection].filter(k => !descendants(m, id).includes(k)));
    commit(collapsed ? "折叠" : "展开", [{op:"collapse", id, collapsed}], {undoable:false});
  }
  function attachOps(m, keys, partId) {
    const ops = [];
    for (const k of keys) { const [kind, id] = split(k); ops.push(kind === "part" ? {op:"parent", id, parent_id:partId} : {op:"assign", session_id:id, domain_id:partId}); }
    return ops;
  }
  // Which of the dragged nodes may hang on `partId`: no cycles, at most one leader, and not where they already are.
  function attachable(m, keys, partId) {
    if (keys.includes(key("part", partId))) return null;
    const above = new Set([partId, ...ancestors(m, partId)]);
    if (keys.some(k => k.startsWith("part:") && above.has(split(k)[1]))) return null;
    const dragged = new Set(keys.filter(k => k.startsWith("part:")).map(k => split(k)[1]));
    const tops = keys.filter(k => { for (let p = parentOf(m, k); p !== null; p = m.parts.get(p)?.parent_id ?? null) if (dragged.has(p)) return false; return true; });
    const moving = tops.filter(k => parentOf(m, k) !== partId);
    const leaders = moving.filter(k => k.startsWith("leader:"));
    const result = leaders.length > 1 ? moving.filter(k => k.startsWith("part:")) : moving;
    return result.length ? result : null;
  }
  function displacedToast(result, before, step, partId, leaderId) {
    const effect = result.effects.find(item => item?.op === "displaced");
    if (!effect) return;
    const m = model(), old = m.leaders.get(effect.session_id)?.name ?? before.leaders.find(place => place.session_id === effect.session_id)?.name ?? "原来的 leader";
    options.toast(`「${m.parts.get(partId)?.name ?? ""}」改由 ${m.leaders.get(leaderId)?.name ?? ""} 负责，${old} 已移出`, undoStep(step));
  }
  function detach(k) {
    const m = model(), [kind, id] = split(k), from = parentOf(m, k);
    if (from === null) return;
    commit(`从「${m.parts.get(from)?.name}」移出`, [kind === "part" ? {op:"parent", id, parent_id:null} : {op:"assign", session_id:id, domain_id:null}],
      {after:(result, before, step) => options.toast(`${nameOf(m, k)} 已从「${m.parts.get(from)?.name}」移出`, undoStep(step))});
  }
  function connect(k, partId) {
    const m = model(), keys = attachable(m, [k], partId);
    if (keys === null) { if (parentOf(m, k) !== partId) options.toast("不能挂到它自己或它的下级下面"); return; }
    const leaderId = k.startsWith("leader:") ? split(k)[1] : null;
    commit(`挂到「${m.parts.get(partId).name}」下`, attachOps(m, keys, partId), {after:(result, before, step) => leaderId && displacedToast(result, before, step, partId, leaderId)});
  }
  function deleteParts(ids) {
    if (!editable() || !ids.length) return;
    const m = model(), names = ids.map(id => `「${m.parts.get(id)?.name}」`).join("、");
    selection = new Set([...selection].filter(k => !ids.includes(split(k)[1])));
    commit(`删除 ${names}`, ids.map(id => ({op:"delete", id})),
      {after:(result, before, step) => options.toast(`已删除 ${names}，下面的 part 和 leader 已上移一层`, undoStep(step))});
  }
  function selectBranch(id) { const m = model(); selection = new Set([key("part", id), ...descendants(m, id).filter(k => visible(m, k))]); renderSelection(); }
  // Lay the tree out left to right: a part, then its leader and sub-parts one column further, centred on them.
  function tidy() {
    if (!editable()) return;
    const m = model(), next = new Map(), height = k => sizeOf(k).h;
    let cursor = 0;
    const place = (part, depth) => {
      const k = key("part", part.id), x = depth * COLUMN, owner = m.holder.get(part.id), kids = [];
      if (!part.collapsed) {
        if (owner && m.leaders.has(owner.session_id)) kids.push(key("leader", owner.session_id));
        for (const child of [...(m.children.get(part.id) || [])].sort((a, b) => a.y - b.y)) kids.push(key("part", child.id));
      }
      if (!kids.length) { next.set(k, {x, y:cursor}); cursor += height(k) + ROW_GAP; return; }
      const top = cursor;
      for (const kid of kids) {
        if (kid.startsWith("leader:")) { next.set(kid, {x:x + COLUMN, y:cursor}); cursor += height(kid) + ROW_GAP; }
        else place(m.parts.get(split(kid)[1]), depth + 1);
      }
      next.set(k, {x, y:Math.max(top, Math.round((top + cursor - ROW_GAP) / 2 - height(k) / 2))});
    };
    for (const root of [...(m.children.get(null) || [])].sort((a, b) => a.y - b.y)) { place(root, 0); cursor += ROW_GAP * 2; }
    const loose = [...m.leaders.keys()].filter(id => !m.places.get(id)?.domain_id).map(id => key("leader", id)).sort((a, b) => posOf(m, a).y - posOf(m, b).y);
    let y = 0;
    for (const k of loose) { next.set(k, {x:board.parts.length ? -COLUMN - 40 : 0, y}); y += height(k) + ROW_GAP; }
    // Folded branches travel with their part.
    for (const part of board.parts) if (part.collapsed && next.has(key("part", part.id))) {
      const from = posOf(m, key("part", part.id)), to = next.get(key("part", part.id));
      for (const k of descendants(m, part.id)) { const p = posOf(m, k); next.set(k, {x:p.x + to.x - from.x, y:p.y + to.y - from.y}); }
    }
    const items = [...next].map(([k, p]) => ({kind:split(k)[0], id:split(k)[1], x:Math.round(p.x), y:Math.round(p.y)}));
    if (!items.length) return;
    commit("自动排列", [{op:"move", items}], {positions:next});
    requestAnimationFrame(() => fit([...next.keys()].filter(k => visible(m, k))));
  }

  // ---- pointer gestures -----------------------------------------------------------------------------------------
  function nodeAt(clientX, clientY) { return document.elementFromPoint(clientX, clientY)?.closest?.(".arch-node") || null; }
  function capture(event) { try { $(".arch-canvas").setPointerCapture(event.pointerId); } catch { /* pointer already gone */ } }
  function pointerDown(event) {
    if (event.target.closest(".arch-menu, .arch-zoom, .arch-rename, .arch-empty button")) return;
    closeMenu();
    if (renaming !== null) finishRename(true);
    $(".arch-canvas").focus({preventScroll:true});
    pointers.set(event.pointerId, {x:event.clientX, y:event.clientY});
    if (pointers.size === 2 && (gesture === null || gesture.type === "pan" || !gesture.moved)) { cancelGesture(); startPinch(); return; }
    if (pointers.size > 1 || event.target.closest("[data-arch-act]")) return;
    const node = event.target.closest(".arch-node"), pin = event.target.closest(".arch-pin"), edit = editable();
    const start = {id:event.pointerId, x:event.clientX, y:event.clientY, moved:false};
    if (event.button === 1 || event.button === 2 || (event.button === 0 && spaceDown)) {
      event.preventDefault(); gesture = {...start, type:"pan", camera:{...camera}, button:event.button, node:node?.dataset.key ?? null}; return;
    }
    if (event.button !== 0) return;
    if (edit && pin && node) {
      if (event.altKey && pin.dataset.pin === "in") { detach(node.dataset.key); return; }
      gesture = {...start, type:"wire", from:node.dataset.key, pin:pin.dataset.pin, end:toWorld(event.clientX, event.clientY), over:null}; return;
    }
    if (node) {
      const k = node.dataset.key;
      if (edit && (event.shiftKey || event.ctrlKey || event.metaKey)) { if (selection.has(k)) selection.delete(k); else selection.add(k); renderSelection(); return; }
      if (edit) { if (!selection.has(k)) { selection = new Set([k]); renderSelection(); } gesture = {...start, type:"node", key:k}; }
      else gesture = {...start, type:"pan", camera:{...camera}, tap:k};
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
    if (!gesture.moved) { if (Math.hypot(dx, dy) < SLOP) return; gesture.moved = true; capture(event); if (gesture.type === "node") beginDrag(); }
    if (gesture.type === "pan") { camera = {...camera, x:gesture.camera.x + dx, y:gesture.camera.y + dy}; applyCamera(); }
    else if (gesture.type === "node") moveDrag(dx, dy, event);
    else if (gesture.type === "wire") moveWire(event);
    else if (gesture.type === "marquee") moveMarquee(event);
  }
  function pointerUp(event) {
    pointers.delete(event.pointerId);
    if (gesture?.type === "pinch") { if (pointers.size < 2) { gesture = null; saveCamera(); } return; }
    if (gesture === null || gesture.id !== event.pointerId) return;
    const done = gesture;
    if (event.type === "pointercancel") { cancelGesture(); return; }
    gesture = null;
    if (done.type === "pan") {
      if (done.moved) saveCamera();
      else if (done.button === 2) openMenu(done.node, event.clientX, event.clientY);
      else if (done.tap) tap(done.tap);
      else if (done.clear && selection.size) { selection.clear(); renderSelection(); }
    } else if (done.type === "node") {
      if (done.moved) finishDrag(done);
      else if (done.key.startsWith("leader:")) tap(done.key);
      // A plain click inside a multi-selection picks just that node (the selection stayed for a possible drag).
      else if (selection.size > 1) { selection = new Set([done.key]); renderSelection(); }
    } else if (done.type === "wire") finishWire(done);
    else if (done.type === "marquee") { $(".arch-marquee").hidden = true; if (!done.moved && !done.additive && selection.size) { selection.clear(); renderSelection(); } }
  }
  function cancelGesture() {
    const done = gesture; gesture = null;
    if (done === null) return;
    if (done.type === "node" && done.moved) { host.querySelector(".arch-drop-label").hidden = true; renderWorld(); }
    if (done.type === "wire") { clearOver(); renderWires(); }
    if (done.type === "marquee") $(".arch-marquee").hidden = true;
  }
  function tap(k) {
    const [kind, id] = split(k);
    if (kind === "leader") options.openLeader(id);
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
  // Dragging moves the selection; a folded part carries its hidden branch along.
  function beginDrag() {
    const m = model(), keys = [...selection].filter(k => visible(m, k));
    const carried = new Set(keys);
    for (const k of keys) if (k.startsWith("part:") && m.parts.get(split(k)[1]).collapsed) for (const hidden of descendants(m, split(k)[1])) carried.add(hidden);
    gesture.keys = keys; gesture.origin = new Map([...carried].map(k => [k, posOf(m, k)])); gesture.positions = new Map(gesture.origin);
    for (const k of keys) host.querySelector(`.arch-node[data-key="${CSS.escape(k)}"]`)?.classList.add("dragging");
  }
  function moveDrag(dx, dy, event) {
    const z = camera.z;
    for (const [k, origin] of gesture.origin) {
      const pos = {x:origin.x + dx / z, y:origin.y + dy / z}; gesture.positions.set(k, pos);
      const node = host.querySelector(`.arch-node[data-key="${CSS.escape(k)}"]`); if (node) node.style.transform = `translate(${pos.x}px,${pos.y}px)`;
    }
    renderWires();
    const m = model(), under = nodeAt(event.clientX, event.clientY), target = under?.classList.contains("arch-part") ? split(under.dataset.key)[1] : null;
    const keys = target === null ? null : attachable(m, gesture.keys, target);
    gesture.drop = keys === null ? null : {part:target, keys};
    clearOver();
    const label = $(".arch-drop-label");
    if (gesture.drop) {
      under.classList.add("drop-target");
      const multipleLeaders = gesture.keys.filter(k => k.startsWith("leader:")).length > 1;
      label.textContent = `松开挂到「${m.parts.get(target).name}」下${multipleLeaders ? "（一个 part 只挂一位 leader，leader 不挂）" : ""}`;
      const r = canvasRect(); label.style.transform = `translate(${event.clientX - r.left + 14}px,${event.clientY - r.top + 16}px)`; label.hidden = false;
    } else label.hidden = true;
  }
  function clearOver() { for (const node of host.querySelectorAll(".drop-target")) node.classList.remove("drop-target"); }
  function finishDrag(done) {
    $(".arch-drop-label").hidden = true; clearOver();
    for (const node of host.querySelectorAll(".dragging")) node.classList.remove("dragging");
    const m = model(), positions = new Map(done.positions);
    let ops = [], label = "移动";
    if (done.drop) {
      const {part, keys} = done.drop, slot = slotBelow(m, part, new Set(keys));
      let y = slot.y;
      for (const k of [...keys].sort((a, b) => positions.get(a).y - positions.get(b).y)) {
        const from = positions.get(k), dx = slot.x - from.x, dy = y - from.y;
        const branch = new Set(k.startsWith("part:") ? descendants(m, split(k)[1]) : []);
        for (const [moved, pos] of positions) if (moved === k || branch.has(moved)) positions.set(moved, {x:pos.x + dx, y:pos.y + dy});
        y += sizeOf(k).h + ROW_GAP;
      }
      ops = attachOps(m, keys, part);
      label = `挂到「${m.parts.get(part).name}」下`;
    }
    ops.push({op:"move", items:[...positions].map(([k, p]) => ({kind:split(k)[0], id:split(k)[1], x:Math.round(p.x), y:Math.round(p.y)}))});
    const leaderId = done.drop?.keys.find(k => k.startsWith("leader:"));
    commit(label, ops, {positions, after:(result, before, step) => {
      if (leaderId) displacedToast(result, before, step, done.drop.part, split(leaderId)[1]);
      if (done.drop) requestAnimationFrame(() => done.drop.keys.forEach(reveal));
    }});
  }
  function wireTarget(done, clientX, clientY) {
    const m = model(), node = nodeAt(clientX, clientY); if (!node) return null;
    const k = node.dataset.key;
    if (done.pin === "out") { const partId = split(done.from)[1]; return attachable(m, [k], partId) ? k : null; }
    return node.classList.contains("arch-part") && attachable(m, [done.from], split(k)[1]) ? k : null;
  }
  function moveWire(event) {
    gesture.end = toWorld(event.clientX, event.clientY);
    gesture.over = wireTarget(gesture, event.clientX, event.clientY);
    clearOver();
    if (gesture.over) host.querySelector(`.arch-node[data-key="${CSS.escape(gesture.over)}"]`)?.classList.add("drop-target");
    renderWires();
  }
  function finishWire(done) {
    clearOver(); renderWires();
    if (!done.moved) return;
    if (done.over === null) {
      if (done.pin === "in") detach(done.from);
      else createPart(split(done.from)[1], {x:Math.round(done.end.x), y:Math.round(done.end.y - PIN_Y)});
      return;
    }
    if (done.pin === "out") connect(done.over, split(done.from)[1]); else connect(done.from, split(done.over)[1]);
  }
  function moveMarquee(event) {
    const r = canvasRect(), box = $(".arch-marquee");
    const x0 = Math.min(gesture.x, event.clientX), y0 = Math.min(gesture.y, event.clientY), x1 = Math.max(gesture.x, event.clientX), y1 = Math.max(gesture.y, event.clientY);
    Object.assign(box.style, {left:`${x0 - r.left}px`, top:`${y0 - r.top}px`, width:`${x1 - x0}px`, height:`${y1 - y0}px`}); box.hidden = false;
    const picked = new Set(gesture.additive ? gesture.base : []);
    for (const node of host.querySelectorAll(".arch-node")) {
      const b = node.getBoundingClientRect();
      if (b.right >= x0 && b.left <= x1 && b.bottom >= y0 && b.top <= y1) picked.add(node.dataset.key);
    }
    selection = picked; renderSelection();
  }
  function wheel(event) {
    if (event.target.closest(".arch-menu")) return;
    event.preventDefault();
    if (!event.ctrlKey && Math.abs(event.deltaX) > 0) { camera = {...camera, x:camera.x - event.deltaX, y:camera.y - event.deltaY}; applyCamera(); saveCamera(); return; }
    const delta = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY;
    zoomAt(camera.z * Math.exp(-delta * (event.ctrlKey ? .01 : .0015)), event.clientX, event.clientY);
  }

  // ---- menu, keyboard, buttons ----------------------------------------------------------------------------------
  function openMenu(k, clientX, clientY) {
    const m = model(), edit = editable(), items = [];
    if (k && k.startsWith("part:")) {
      const id = split(k)[1], part = m.parts.get(id);
      if (!selection.has(k)) { selection = new Set([k]); renderSelection(); }
      if (edit) items.push(["rename", "改名", "F2"], ["child", "新建子 part"]);
      if (part.collapsed || descendants(m, id).length) items.push(["fold", part.collapsed ? "展开" : "折叠"]);
      if (edit) { items.push(["branch", "选中这一支"]); if (part.parent_id) items.push(["detach", `从「${m.parts.get(part.parent_id).name}」移出`]); items.push(["delete", "删除 part", "Del", "danger"]); }
    } else if (k) {
      const id = split(k)[1], domain = m.places.get(id)?.domain_id;
      items.push(["open", "进入对话"]);
      if (edit && domain) items.push(["detach", `从「${m.parts.get(domain)?.name}」移出`]);
    } else {
      if (edit) items.push(["here", "在这里新建 part"], ["tidy", "自动排列"]);
      items.push(["fit", "显示全部", "Home"]);
    }
    menu = {key:k, at:toWorld(clientX, clientY)};
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
    const m = model(), k = current?.key, id = k ? split(k)[1] : null;
    if (cmd === "rename") startRename(id);
    else if (cmd === "child") createPart(id);
    else if (cmd === "fold") setCollapsed(id, !m.parts.get(id).collapsed);
    else if (cmd === "branch") selectBranch(id);
    else if (cmd === "detach") detach(k);
    else if (cmd === "delete") deleteParts([id]);
    else if (cmd === "open") options.openLeader(id);
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
    else if (act === "fit") fit();
    else if (act === "zoom-in") zoomCenter(1.2);
    else if (act === "zoom-out") zoomCenter(1 / 1.2);
    else if (act === "zoom-reset") zoomCenter(1 / camera.z);
    else if (act === "fold") { const part = model().parts.get(button.dataset.id); if (part) setCollapsed(part.id, !part.collapsed); }
    else if (act === "menu") menuCommand(button.dataset.cmd);
    else if (act === "reload") { errorText = ""; render(); load(); }
  }
  function doubleClick(event) {
    if (!editable()) return;
    const node = event.target.closest(".arch-part");
    if (node && !event.target.closest("[data-arch-act], .arch-pin")) startRename(split(node.dataset.key)[1]);
  }
  function keyDown(event) {
    if (event.target.classList?.contains("arch-rename")) {
      if (event.key === "Enter") { event.preventDefault(); finishRename(true); }
      else if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); finishRename(false); }
      return;
    }
    if (event.target.closest("input, textarea, select, [contenteditable]")) return;
    const mod = event.ctrlKey || event.metaKey, k = event.key.toLowerCase();
    if (event.key === " ") { if (!spaceDown) { spaceDown = true; $(".arch-canvas")?.classList.add("panning"); } if (event.target.closest(".arch-canvas")) event.preventDefault(); return; }
    if (event.target.closest(".arch-menu") && event.key === "Escape") { event.preventDefault(); closeMenu(); $(".arch-canvas").focus({preventScroll:true}); return; }
    if (!event.target.closest(".arch-canvas")) return;
    if (mod && k === "z") { event.preventDefault(); if (event.shiftKey) redo(); else undo(); }
    else if (mod && k === "y") { event.preventDefault(); redo(); }
    else if (mod && k === "a" && editable()) { event.preventDefault(); selection = new Set(shownKeys()); renderSelection(); }
    else if ((event.key === "Delete" || event.key === "Backspace") && editable()) { const ids = [...selection].filter(item => item.startsWith("part:")).map(item => split(item)[1]); if (ids.length) { event.preventDefault(); deleteParts(ids); } }
    else if (event.key === "F2" && editable()) { const only = [...selection]; if (only.length === 1 && only[0].startsWith("part:")) { event.preventDefault(); startRename(split(only[0])[1]); } }
    else if (event.key === "Home" || (k === "f" && !mod)) { event.preventDefault(); fit(k === "f" && selection.size ? [...selection] : shownKeys(), k === "f" && selection.size ? 1.2 : 1); }
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

  // ---- lifecycle ------------------------------------------------------------------------------------------------
  const listeners = [["click", click], ["dblclick", doubleClick], ["keydown", keyDown], ["keyup", keyUp], ["focusout", focusOut]];
  const canvasListeners = [["pointerdown", pointerDown], ["pointermove", pointerMove], ["pointerup", pointerUp], ["pointercancel", pointerUp], ["contextmenu", event => event.preventDefault()]];
  function mount(element, settings) {
    if (settings.focus) focusRequest = settings.focus;
    if (host === element && project === settings.project) { update(settings); applyFocus(); return; }
    unmount();
    options = settings;
    if (project !== settings.project) {
      project = settings.project; board = null; cached = null; errorText = ""; camera = null; selection = new Set();
      undoStack = []; redoStack = []; pending.clear(); renaming = null;
    }
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
    if (next !== leaderSignature && gesture === null && renaming === null) { leaderSignature = next; cached = null; renderWorld(); settleLeaders(); }
    renderHead();
  }
  function unmount() {
    if (host !== null) {
      for (const [type, handler] of listeners) host.removeEventListener(type, handler);
      phone.removeEventListener("change", viewportChange);
      resizer?.disconnect(); resizer = null;
      if (camera !== null && pendingFit === null) saveCamera();
    }
    gesture = null; pointers.clear(); menu = null; spaceDown = false; renaming = null; host = null;
  }
  return {mount, update, unmount, icon:ICON.board};
})();
