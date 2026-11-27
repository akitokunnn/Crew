"use strict";
// Idea mind map: the main (导图) layout of a workspace's ideas page. Ideas wait in the tray until dragged, placed or
// tapped onto the canvas, topics group them, and picked branches turn into this workspace's architecture board
// after a before / after preview. The tray keeps a small box to jot an idea and hang it under the selected node.
// The page (notes.js) owns notes, dialogs and the ⋯ menu; this view owns the map and asks the page for the rest.
window.CrewMindmap = (() => {
  const MM = window.CrewMindmapModel;
  const ZOOM_KEY = "crew.mindmap.zoom", MIN_ZOOM = .5, MAX_ZOOM = 1.6, SLOP = 5;
  const icon = d => `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
  const ICON = {
    plus: '<path d="M12 5v14M5 12h14"/>', minus: '<path d="M5 12h14"/>', board: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/><path d="M6.5 10v4a2 2 0 0 0 2 2H14"/>',
    child: '<path d="M5 5v8a3 3 0 0 0 3 3h11"/><path d="m15 12 4 4-4 4"/>', sibling: '<path d="M5 7h14M5 17h14"/><path d="M12 13v8M8 17h8"/>',
    fold: '<path d="m7 9 5 5 5-5"/>', unfold: '<path d="m9 7 5 5-5 5"/>', rename: '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
    up: '<path d="M12 19V5M6 11l6-6 6 6"/>', down: '<path d="M12 5v14M6 13l6 6 6-6"/>', left: '<path d="M19 12H5M11 6l-6 6 6 6"/>', right: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    remove: '<path d="M5 12h14"/><circle cx="12" cy="12" r="9"/>', chat: '<path d="M4 5h16v11H9l-5 4z"/>', task: '<path d="M5 12l4 4 10-10"/>',
    chevron: '<path d="m6 9 6 6 6-6"/>'
  };

  function create(ctx) {
    const client = MM.client(ctx.api, ctx.project);
    const {esc} = ctx;
    let host = null, map = null, t = null, error = "", selected = null, draft = null, renaming = null, drag = null;
    let picking = null, trayOpen = false, attachCapture = true, layoutInfo = null, focusAfter = false;
    // armed: an idea picked from the tray by a tap, waiting for the node (or empty canvas) it goes to.
    // pendingAttach: an idea already recorded whose hanging failed; a retry only hangs it.
    let armed = null, pendingAttach = null, capturing = false, captureNote = "";
    let zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Number(localStorage.getItem(ZOOM_KEY) ?? 1))), suppressClick = false;
    const $ = selector => host.querySelector(selector);
    const note = id => ctx.byId(t.nodes.get(id).note_id);
    // Ideas are never deleted, so every idea node finds its note once the page has read them.
    const nodeTitle = id => { const node = t.nodes.get(id); return node.note_id === null ? node.label : ctx.M.title(ctx.byId(node.note_id)); };
    // What the user sees when a request fails: the server's reason, or the error's type for anything else.
    const reason = failure => failure.status === undefined ? `${failure.name}: ${failure.message}` : failure.message;
    const onMap = () => new Set(map.nodes.filter(node => node.note_id !== null).map(node => node.note_id));

    // ---- data -------------------------------------------------------------------------------------------------------
    async function load() {
      try { map = await client.load(); error = ""; }
      catch (failure) { error = reason(failure); }
      sync();
      render();
    }
    function sync() {
      if (!map) return;
      t = MM.tree(map, draft);
      if (selected !== null && !t.nodes.has(selected)) selected = null;
      if (picking) for (const id of [...picking]) if (!t.nodes.has(id)) picking.delete(id);
    }
    // Every edit goes to the server; a stale version re-reads before reporting.
    async function edit(ops, label) {
      try {
        const result = await client.edit(ops, map.version);
        map = result.map; sync(); render();
        return result;
      } catch (failure) {
        if (failure.status === 409) await load();
        ctx.toast(`${label}失败：${reason(failure)}`);
        return null;
      }
    }

    // ---- rendering --------------------------------------------------------------------------------------------------
    function scaffold() {
      host.innerHTML = `<div class="mm-shell">
        <aside class="mm-tray" aria-label="未上图的想法">
          <form class="mm-capture" autocomplete="off"><textarea class="mm-capture-input" rows="1" maxlength="4000" placeholder="记一条想法…" aria-label="记下一条想法，可挂到选中的节点下"></textarea><button type="submit" class="btn sm primary">记下</button></form>
          <p class="mm-capture-hint" role="status" aria-live="polite"></p>
          <button type="button" class="mm-tray-head" data-mm="tray" aria-expanded="false"></button><div class="mm-tray-list" role="list"></div></aside>
        <section class="mm-stage" aria-label="想法导图">
          <div class="mm-bar"></div>
          <div class="mm-scroll" tabindex="0" role="tree" aria-label="想法导图，方向键移动，Tab 新建子主题，Enter 新建同级，空格折叠" aria-describedby="mm-hint">
            <div class="mm-world"><div class="mm-plane"><svg class="mm-wires" aria-hidden="true"></svg><div class="mm-nodes"></div></div></div>
          </div>
          <p class="mm-empty" hidden></p>
          <div class="mm-actions" role="toolbar" aria-label="节点操作" hidden></div>
          <div class="mm-zoom" role="group" aria-label="缩放"><button type="button" data-mm="zoom-out" aria-label="缩小">${icon(ICON.minus)}</button><button type="button" class="mm-zoom-value" data-mm="zoom-reset" title="恢复 100%"></button><button type="button" data-mm="zoom-in" aria-label="放大">${icon(ICON.plus)}</button></div>
          <div class="mm-drop-label" hidden></div>
          <div class="mm-arm" role="status" hidden></div>
        </section>
      </div>`;
      const scroll = $(".mm-scroll");
      scroll.addEventListener("keydown", onKey);
      scroll.addEventListener("wheel", onWheel, {passive: false});
      scroll.addEventListener("scroll", () => ctx.closeMenu());
      host.addEventListener("pointerdown", onPointerDown);
      // Hanging uses pointer events; a native drag of selected text would cancel them midway.
      host.addEventListener("dragstart", event => event.preventDefault());
      host.addEventListener("click", onClick);
      host.addEventListener("dblclick", onDoubleClick);
      host.addEventListener("change", onChange);
      host.addEventListener("keydown", onTrayKey);
      const box = $(".mm-capture-input");
      box.value = ctx.draft.get();
      box.addEventListener("input", () => { ctx.draft.set(box.value); resizeBox(); captureNote = ""; renderCapture(); });
      box.addEventListener("keydown", event => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); $(".mm-capture").requestSubmit(); } });
      $(".mm-capture").addEventListener("submit", event => { event.preventDefault(); void capture(); });
      resizeBox();
    }
    // Grows with its text up to five lines; a hidden map (no layout yet) keeps the one-line height from CSS.
    function resizeBox() {
      const box = $(".mm-capture-input"); box.style.height = "";
      if (!box.value || !box.offsetParent) return;
      box.style.height = `${Math.min(box.scrollHeight, 120)}px`; box.style.overflowY = box.scrollHeight > 120 ? "auto" : "hidden";
    }
    function render() {
      if (host === null) return;
      renderBar(); renderTray(); renderCapture(); renderArm();
      const empty = $(".mm-empty"), scroll = $(".mm-scroll");
      if (!map || !ctx.loaded()) {
        empty.hidden = false; scroll.hidden = true;
        empty.innerHTML = error ? `<span role="alert">导图读取失败：${esc(error)}</span><button type="button" class="btn sm" data-mm="reload">重新读取</button>` : "正在读取导图…";
        renderActions(); return;
      }
      scroll.hidden = false;
      empty.hidden = t.roots.length > 0;
      empty.innerHTML = t.roots.length ? "" : `<b>导图还是空的</b><span>在左上角记一条想法，或把左边的想法拖进来、点一下再点画布放上来；也可以点“＋ 主题”起一个头。</span>`;
      renderCanvas(); renderActions();
    }
    function renderBar() {
      const bar = $(".mm-bar");
      if (picking) {
        const {tops, covered} = map ? MM.branches(t, picking) : {tops: [], covered: new Set()};
        bar.className = "mm-bar picking";
        bar.innerHTML = `<span class="mm-pick-text"><b>转成「${esc(ctx.workspaceName())}」的架构图</b><span>点节点勾选分支（含全部下级）· 已选 ${tops.length} 个分支 / ${covered.size} 个节点</span></span>`
          + `<button type="button" class="btn sm ghost" data-mm="pick-cancel">取消</button><button type="button" class="btn sm primary" data-mm="pick-preview" ${tops.length ? "" : "disabled"}>预览改动</button>`;
        return;
      }
      bar.className = "mm-bar";
      bar.innerHTML = `<button type="button" class="btn sm" data-mm="new-root">${icon(ICON.plus)}主题</button><span class="mm-hint" id="mm-hint">拖想法进来或点选后放下 · Tab 子主题 · Enter 同级 · 空格折叠 · Alt+方向键调整层级</span><span class="mm-gap"></span><button type="button" class="btn sm" data-mm="convert" ${map && t.roots.length ? "" : "disabled"}>${icon(ICON.board)}转成架构图</button>`;
    }
    function trayNotes() {
      const placed = map ? onMap() : new Set(), view = ctx.view();
      return ctx.M.sorted(ctx.M.filter(ctx.notes(), view), view.sort).filter(item => !placed.has(item.id));
    }
    function renderTray() {
      const list = trayNotes(), head = $(".mm-tray-head");
      head.innerHTML = `<span>未上图</span><b>${list.length}</b>${icon(ICON.chevron)}`;
      head.setAttribute("aria-expanded", String(trayOpen));
      host.querySelector(".mm-shell").classList.toggle("tray-open", trayOpen);
      const now = Date.now();
      $(".mm-tray-list").innerHTML = list.length ? list.map(item => `<div class="mm-tray-item${item.stale ? " stale" : ""}${armed === item.id ? " armed" : ""}" role="listitem" data-note="${esc(item.id)}" tabindex="0" aria-pressed="${armed === item.id}" aria-label="想法：${esc(ctx.M.title(item))}，回车放进导图，点一下再点画布放到那里">`
        + `<span class="mm-tray-text"><span class="mm-tray-title">${esc(ctx.M.title(item))}</span><small>${ctx.M.relative(item.updated_at, now)}${item.tags.length ? ` · ${item.tags.map(name => `#${esc(name)}`).join(" ")}` : ""}</small></span>`
        + `<button type="button" class="btn sm mm-place" data-mm="place" ${map ? "" : "disabled"} aria-label="把「${esc(ctx.M.title(item))}」放进导图${selected ? "，挂到选中节点下" : ""}">上图</button></div>`).join("")
        : `<p class="mm-tray-empty">${ctx.notes().length ? "当前筛选下的想法都在导图上了。" : "还没有想法。在上面的小框里记下第一条。"}</p>`;
    }
    // The tray's box says where the next idea goes, and offers the retry when only the hanging failed.
    function renderCapture() {
      const hint = $(".mm-capture-hint"), target = attachTarget();
      $(".mm-capture [type=submit]").disabled = capturing;
      if (pendingAttach) {
        hint.innerHTML = `<span class="mm-capture-warn">已记下，但没挂到「${esc(pendingAttach.title)}」下</span><button type="button" class="text-link" data-mm="retry-attach">重试挂上</button><button type="button" class="text-link" data-mm="drop-attach">先不挂</button>`;
        return;
      }
      if (captureNote) { hint.textContent = captureNote; return; }
      hint.innerHTML = target ? `记下后挂到「${esc(target.title)}」下<button type="button" class="text-link" data-mm="capture-detach" aria-label="只记下，不挂到导图">不挂</button>` : "记下后先放在下面，拖进导图或点一下再放。";
    }
    function attachTarget() { return map && attachCapture && selected !== null && t.nodes.has(selected) ? {id: selected, title: nodeTitle(selected)} : null; }
    async function capture() {
      const box = $(".mm-capture-input");
      if (capturing) return;
      const target = attachTarget();
      capturing = true; captureNote = "正在记下…"; pendingAttach = null; renderCapture();
      let note;
      try { note = await ctx.capture(box.value); }
      catch (failure) { capturing = false; captureNote = failure.empty ? failure.message : `没记下：${reason(failure)}（内容还在框里）`; renderCapture(); box.focus(); return; }
      box.value = ""; ctx.draft.set(""); resizeBox();
      await ctx.reload();
      if (target && t.nodes.has(target.id)) {
        const result = await place(note.id, target.id);
        if (!result) pendingAttach = {noteId: note.id, parent: target.id, title: target.title};
      }
      capturing = false; captureNote = pendingAttach ? "" : target ? `已记下，挂到「${target.title}」下。不会因此启动 AI。` : "已记下。不会因此启动 AI。";
      renderCapture(); box.focus();
    }
    // A tapped idea waits for its spot: a node takes it as a child, empty canvas as a new root.
    function renderArm() {
      const bar = $(".mm-arm");
      const item = armed === null ? null : ctx.byId(armed);
      if (!item || !map) { bar.hidden = true; bar.innerHTML = ""; return; }
      bar.hidden = false;
      bar.innerHTML = `<span>点一个节点，把「${esc(ctx.M.title(item))}」挂到它下面；点空白处放成新的根</span><button type="button" class="btn sm ghost" data-mm="disarm">取消</button>`;
    }
    function arm(noteId) { armed = armed === noteId ? null : noteId; render(); }
    function nodeHtml(id) {
      const node = t.nodes.get(id), level = MM.depth(t, id);
      if (node.draft || renaming === id) {
        const value = node.draft ? "" : node.label;
        return `<div class="mm-node topic editing${level === 0 ? " root" : ""}" data-node="${esc(id)}" role="treeitem" aria-level="${level + 1}"><input class="mm-input" maxlength="40" value="${esc(value)}" placeholder="${node.draft ? "新主题，回车确定" : ""}" aria-label="${node.draft ? "新主题名称，回车确定，Esc 放弃" : "主题名称，回车保存，Esc 取消"}"></div>`;
      }
      const item = note(id), kids = node.children.length, hiddenKids = node.collapsed ? MM.descendants(t, id).length : 0;
      const branch = MM.branch(t, id), tone = branch === null ? "" : ` style="--bc:var(--tag-c${MM.parentList(t, branch).indexOf(branch) % 10}-fg)"`;
      const classes = ["mm-node", level === 0 ? "root" : "", node.note_id === null ? "topic" : "idea", selected === id ? "sel" : "",
        item && !matchesFilters(item) ? "dim" : "",
        item && item.state !== "open" ? "muted" : "", picking?.has(id) ? "picked" : "", picking && pickCovered.has(id) && !picking.has(id) ? "inpick" : ""].filter(Boolean).join(" ");
      const badges = [];
      if (item) {
        if (item.merged_into) badges.push(`<span class="mm-badge">已并入</span>`);
        else if (item.stage === "promoted") badges.push(`<span class="mm-badge">已转任务</span>`);
        else if (item.state === "archived") badges.push(`<span class="mm-badge">已归档</span>`);
        else if (item.stage === "discussed") badges.push(`<span class="mm-badge">讨论过</span>`);
      }
      const links = t.links.get(id) || [];
      if (links.length) badges.push(`<span class="mm-badge linked" title="已转成架构图：${esc(links.map(link => `${ctx.projectName(link.project)} › ${link.domain_name}`).join("；"))}">${icon(ICON.board)}${links.length > 1 ? links.length : "已转"}</span>`);
      const dots = item?.tags.length ? `<span class="mm-dots" aria-hidden="true">${item.tags.filter(name => ctx.tagInfo(name)).slice(0, 4).map(name => `<i class="tag-${esc(ctx.tagInfo(name).color)}"></i>`).join("")}</span>` : "";
      const label = `${node.note_id === null ? "主题" : "想法"}：${nodeTitle(id)}${item?.tags.length ? `，标签 ${item.tags.join("、")}` : ""}${links.length ? "，已转成架构图" : ""}`;
      const fold = kids ? `<button type="button" class="mm-fold${node.collapsed ? " closed" : ""}" data-mm="fold" tabindex="-1" aria-label="${node.collapsed ? `展开 ${hiddenKids} 个下级` : "折叠下级"}" title="${node.collapsed ? `展开（${hiddenKids} 个下级）` : "折叠"}">${node.collapsed ? `+${hiddenKids}` : icon(ICON.minus)}</button>` : "";
      const check = picking ? `<span class="mm-check" aria-hidden="true">${picking.has(id) ? "✓" : ""}</span>` : "";
      return `<div class="${classes}" data-node="${esc(id)}"${tone} role="treeitem" aria-level="${level + 1}" aria-label="${esc(label)}" aria-selected="${selected === id}"${kids ? ` aria-expanded="${!node.collapsed}"` : ""} tabindex="${selected === id ? 0 : -1}">`
        + `${check}${item?.stale && item.state === "open" ? '<i class="mm-stale" title="超过回顾期没动" aria-hidden="true"></i>' : ""}<span class="mm-text">${esc(nodeTitle(id))}</span>${dots}${badges.join("")}${fold}</div>`;
    }
    let pickCovered = new Set();
    // Tag and workspace filters dim ideas that do not match; the state view (open / archived …) only narrows the tray.
    function matchesFilters(item) {
      const own = item.stage === "promoted" ? "promoted" : item.state === "archived" ? "archived" : "open";
      return ctx.M.filter([item], {...ctx.view(), view: own}).length > 0;
    }
    function renderCanvas() {
      pickCovered = picking ? MM.branches(t, picking).covered : new Set();
      const order = MM.visible(t), nodesBox = $(".mm-nodes"), wires = $(".mm-wires");
      nodesBox.innerHTML = order.map(nodeHtml).join("");
      const elements = new Map([...nodesBox.children].map(el => [el.dataset.node, el]));
      const sizes = id => { const el = elements.get(id); return {w: el.offsetWidth, h: el.offsetHeight}; };
      layoutInfo = MM.layout(t, sizes);
      for (const [id, el] of elements) { const p = layoutInfo.pos.get(id); el.style.transform = `translate(${p.x}px,${p.y}px)`; }
      wires.setAttribute("width", layoutInfo.width); wires.setAttribute("height", layoutInfo.height);
      wires.innerHTML = order.filter(id => t.nodes.get(id).parent_id !== null).map(id => {
        const branch = MM.branch(t, id), tone = MM.parentList(t, branch).indexOf(branch) % 10;
        return `<path class="${pickCovered.has(id) && pickCovered.has(t.nodes.get(id).parent_id) ? "picked" : ""}" style="--bc:var(--tag-c${tone}-fg)" d="${MM.wire(layoutInfo.pos.get(t.nodes.get(id).parent_id), layoutInfo.pos.get(id))}"/>`;
      }).join("");
      applyZoom();
      const editor = $(".mm-input");
      if (editor) { editor.focus(); editor.select(); editor.addEventListener("keydown", onEditorKey); editor.addEventListener("blur", onEditorBlur); }
      else if (focusAfter && selected !== null) { focusAfter = false; focusNode(selected); }
    }
    function applyZoom() {
      if (!layoutInfo) return;
      $(".mm-plane").style.transform = `scale(${zoom})`;
      $(".mm-world").style.width = `${layoutInfo.width * zoom}px`;
      $(".mm-world").style.height = `${layoutInfo.height * zoom}px`;
      $(".mm-zoom-value").textContent = `${Math.round(zoom * 100)}%`;
    }
    function focusNode(id) {
      const el = host.querySelector(`.mm-node[data-node="${CSS.escape(id)}"]`);
      if (!el) return;
      el.focus({preventScroll: true});
      el.scrollIntoView({block: "nearest", inline: "nearest"});
    }
    function renderActions() {
      const bar = $(".mm-actions");
      if (!map || !ctx.loaded() || selected === null || picking || renaming !== null || draft) { bar.hidden = true; bar.innerHTML = ""; return; }
      const node = t.nodes.get(selected), item = note(selected), list = MM.parentList(t, selected), index = list.indexOf(selected);
      const button = (act, label, glyph, extra = "") => `<button type="button" data-mm="${act}" ${extra}>${icon(glyph)}<span>${label}</span></button>`;
      // Moves are icon-only on wide screens (their names stay in the tooltip and for screen readers).
      const move = (act, label, glyph, keys, enabled) => `<button type="button" class="mm-move" data-mm="${act}" title="${label}（${keys}）" aria-label="${label}" aria-keyshortcuts="${keys.replace(/ /g, "")}" ${enabled ? "" : "disabled"}>${icon(glyph)}<span>${label}</span></button>`;
      bar.hidden = false;
      bar.setAttribute("aria-label", `「${nodeTitle(selected)}」的操作`);
      bar.innerHTML = button("child", "子主题", ICON.child, 'aria-keyshortcuts="Tab"') + button("sibling", "同级", ICON.sibling, 'aria-keyshortcuts="Enter"')
        + (node.children.length ? button("fold", node.collapsed ? "展开" : "折叠", node.collapsed ? ICON.unfold : ICON.fold, 'aria-keyshortcuts="Space"') : "")
        + button("rename", node.note_id === null ? "改名" : "编辑", ICON.rename, 'aria-keyshortcuts="F2"')
        + move("up", "上移", ICON.up, "Alt + ArrowUp", index > 0) + move("down", "下移", ICON.down, "Alt + ArrowDown", index < list.length - 1)
        + move("outdent", "升级", ICON.left, "Alt + ArrowLeft", node.parent_id !== null) + move("indent", "降级", ICON.right, "Alt + ArrowRight", index > 0)
        + button("remove", "移出", ICON.remove, 'aria-keyshortcuts="Delete"')
        + (item ? `<span class="mm-actions-gap" aria-hidden="true"></span>${item.state === "open" ? `<a class="mm-act-link" href="${esc(ctx.chatUrl(item))}" data-mm="chat">${icon(ICON.chat)}<span>去聊聊</span></a>${item.leader === null ? button("promote", "转任务", ICON.task) : item.leader.status !== "completed" ? button("recover", "核实转任务", ICON.task) : ""}` : ""}<button type="button" class="mm-more" data-mm="menu" aria-haspopup="menu" aria-expanded="false" aria-label="这条想法的更多操作">⋯</button>` : "");
    }

    // ---- actions ----------------------------------------------------------------------------------------------------
    function select(id, focus = true) {
      selected = id; attachCapture = true;
      render(); ctx.onSelection();
      if (focus && id !== null) focusNode(id);
    }
    function startDraft(parent_id, index) {
      if (parent_id !== null && t.nodes.get(parent_id).collapsed) { void edit([{op: "collapse", id: parent_id, collapsed: false}], "展开").then(result => { if (result) startDraft(parent_id, index); }); return; }
      draft = {parent_id, index}; renaming = null; sync(); render();
    }
    async function commitDraft(value) {
      const spot = draft; draft = null; sync();
      const label = value.trim();
      if (!label) { render(); if (selected !== null) focusNode(selected); return; }
      const result = await edit([{op: "create", label, parent_id: spot.parent_id, index: spot.index}], "新建主题");
      if (result) { selected = result.effects[0].id; focusAfter = true; render(); }
    }
    async function commitRename(id, value) {
      renaming = null;
      const label = value.trim();
      if (!label || label === t.nodes.get(id).label) { focusAfter = true; render(); return; }
      focusAfter = true;
      await edit([{op: "label", id, label}], "改名");
    }
    function onEditorKey(event) {
      if (event.isComposing) return;
      event.stopPropagation();
      if (event.key === "Enter") { event.preventDefault(); finishEditor(true); }
      else if (event.key === "Escape") { event.preventDefault(); finishEditor(false); }
    }
    function onEditorBlur() { if (draft || renaming !== null) finishEditor(true); }
    function finishEditor(save) {
      const input = $(".mm-input"); if (!input) return;
      input.removeEventListener("blur", onEditorBlur);
      if (draft) { if (save) void commitDraft(input.value); else { draft = null; sync(); focusAfter = true; render(); } }
      else if (renaming !== null) { if (save) void commitRename(renaming, input.value); else { renaming = null; focusAfter = true; render(); } }
    }
    function rename(id) {
      const item = note(id);
      if (item) { ctx.editDialog(item); return; }
      renaming = id; render();
    }
    async function toggleFold(id) {
      const node = t.nodes.get(id);
      if (!node.children.length) return;
      focusAfter = true;
      await edit([{op: "collapse", id, collapsed: !node.collapsed}], node.collapsed ? "展开" : "折叠");
    }
    async function structural(ops, label) { if (!ops) return; focusAfter = true; await edit(ops, label); }
    async function removeNode(id) {
      const title = nodeTitle(id), siblings = MM.parentList(t, id), index = siblings.indexOf(id);
      const next = siblings[index + 1] ?? siblings[index - 1] ?? t.nodes.get(id).parent_id;
      const result = await edit([{op: "remove", id}], "移出导图");
      if (!result) return;
      const effect = result.effects[0];
      selected = next !== undefined && t.nodes.has(next) ? next : null; focusAfter = selected !== null; render();
      ctx.toast(`已把「${title}」移出导图${effect.children.length ? `，${effect.children.length} 个下级上移一级` : ""}。想法本身还在。`,
        {label: "撤销", run: async () => { const undone = await edit(MM.undoRemove(effect), "撤销移出"); if (undone) select(effect.id); }});
    }
    // Puts an idea under the selected node (or as a new root) — the tray's 上图 and quick capture both use it.
    async function place(noteId, parent = selected) {
      armed = null;
      const parent_id = parent !== null && t.nodes.has(parent) ? parent : null;
      if (parent_id !== null && t.nodes.get(parent_id).collapsed) await edit([{op: "collapse", id: parent_id, collapsed: false}], "展开");
      const index = parent_id === null ? t.roots.length : t.nodes.get(parent_id).children.length;
      const result = await edit([{op: "create", note_id: noteId, parent_id, index}], "放进导图");
      if (result) { selected = result.effects[0].id; focusAfter = true; render(); ctx.onSelection(); }
      return result;
    }

    // ---- keyboard ---------------------------------------------------------------------------------------------------
    function onKey(event) {
      if (event.isComposing || event.target.closest("input,select,textarea")) return;
      const key = event.key, alt = event.altKey, mod = event.ctrlKey || event.metaKey;
      if (!map || mod) return;
      if (!alt && (key === "=" || key === "+" || key === "-" || key === "0")) { event.preventDefault(); setZoom(key === "0" ? 1 : zoom + (key === "-" ? -.1 : .1)); return; }
      if (picking) {
        if (key === "Escape") { event.preventDefault(); stopPicking(); }
        else if ((key === " " || key === "Enter") && selected !== null) { event.preventDefault(); togglePick(selected); }
        else if (key.startsWith("Arrow") && !alt) moveSelection(event);
        return;
      }
      if (selected === null) {
        if (key.startsWith("Arrow") && t.roots.length) { event.preventDefault(); select(t.roots[0]); }
        return;
      }
      if (alt && key.startsWith("Arrow")) {
        event.preventDefault();
        const ops = key === "ArrowUp" ? MM.reorder(t, selected, -1) : key === "ArrowDown" ? MM.reorder(t, selected, 1) : key === "ArrowLeft" ? MM.outdent(t, selected) : MM.indent(t, selected);
        void structural(ops, "调整位置");
      } else if (key.startsWith("Arrow")) moveSelection(event);
      else if ((key === "Tab" && !event.shiftKey) || key === "Insert") { event.preventDefault(); startDraft(selected, t.nodes.get(selected).children.length); }
      else if (key === "Enter") { event.preventDefault(); const parent = t.nodes.get(selected).parent_id; startDraft(parent, MM.indexOf(t, selected) + 1); }
      else if (key === "F2") { event.preventDefault(); rename(selected); }
      else if (key === " ") { event.preventDefault(); void toggleFold(selected); }
      else if (key === "Delete" || key === "Backspace") { event.preventDefault(); void removeNode(selected); }
      else if (key === "Escape") { event.preventDefault(); selected = null; render(); ctx.onSelection(); $(".mm-scroll").focus({preventScroll: true}); }
    }
    function moveSelection(event) {
      const next = MM.navigate(t, selected, event.key);
      event.preventDefault();
      if (next !== null && next !== undefined) select(next);
    }
    function onWheel(event) {
      if (!(event.ctrlKey || event.metaKey)) return;
      event.preventDefault();
      setZoom(zoom * (event.deltaY < 0 ? 1.1 : 1 / 1.1));
    }
    function setZoom(value) {
      zoom = Math.round(Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, value)) * 100) / 100;
      localStorage.setItem(ZOOM_KEY, String(zoom));
      applyZoom();
    }
    function onTrayKey(event) {
      if (event.key === "Escape" && armed !== null) { event.preventDefault(); armed = null; render(); return; }
      const item = event.target.closest(".mm-tray-item");
      if (!item || event.target !== item || event.key !== "Enter" || event.isComposing) return;
      event.preventDefault(); void place(item.dataset.note);
    }

    // ---- pointer: select, drag to hang ------------------------------------------------------------------------------
    function onPointerDown(event) {
      if (event.button !== 0 || event.pointerType === "touch" || picking || draft || renaming !== null) return;
      if (event.target.closest("button,a,input,select")) return;
      const nodeEl = event.target.closest(".mm-node"), trayEl = event.target.closest(".mm-tray-item");
      if (!nodeEl && !trayEl) return;
      const source = nodeEl ? {id: nodeEl.dataset.node} : {note_id: trayEl.dataset.note};
      window.getSelection()?.removeAllRanges();
      drag = {source, from: nodeEl || trayEl, x: event.clientX, y: event.clientY, active: false, pointer: event.pointerId};
      window.addEventListener("pointermove", onPointerMove);
      window.addEventListener("pointerup", onPointerUp);
      window.addEventListener("pointercancel", cancelDrag);
    }
    function onPointerMove(event) {
      if (!drag || event.pointerId !== drag.pointer) return;
      if (!drag.active) {
        if (Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < SLOP) return;
        drag.active = true;
        drag.ghost = document.createElement("div");
        drag.ghost.className = "mm-ghost";
        drag.ghost.textContent = drag.source.id !== undefined ? nodeTitle(drag.source.id) : ctx.M.title(ctx.byId(drag.source.note_id));
        document.body.append(drag.ghost);
        drag.from.classList.add("dragging");
        host.classList.add("mm-dragging");
      }
      event.preventDefault();
      drag.ghost.style.transform = `translate(${event.clientX + 12}px,${event.clientY + 10}px)`;
      drag.target = dropTarget(event);
      paintDrop(event);
    }
    function dropTarget(event) {
      const under = document.elementFromPoint(event.clientX, event.clientY);
      const nodeEl = under?.closest(".mm-node"), source = drag.source;
      if (nodeEl && host.contains(nodeEl)) {
        const id = nodeEl.dataset.node, zone = MM.dropZone(nodeEl.getBoundingClientRect(), event.clientY);
        const ops = MM.dropOps(t, source, id, zone);
        return ops ? {kind: "node", id, zone, ops, el: nodeEl} : {kind: "invalid", el: nodeEl};
      }
      if (under?.closest(".mm-tray") && source.id !== undefined) return {kind: "remove"};
      if (under?.closest(".mm-scroll")) return {kind: "root", ops: MM.rootOps(t, source)};
      return null;
    }
    function paintDrop(event) {
      for (const el of host.querySelectorAll(".drop-child,.drop-before,.drop-after,.drop-invalid")) el.classList.remove("drop-child", "drop-before", "drop-after", "drop-invalid");
      host.querySelector(".mm-tray").classList.toggle("drop-remove", drag.target?.kind === "remove");
      $(".mm-scroll").classList.toggle("drop-root", drag.target?.kind === "root");
      const label = $(".mm-drop-label"), target = drag.target;
      if (!target) { label.hidden = true; return; }
      if (target.kind === "node") target.el.classList.add(`drop-${target.zone}`);
      if (target.kind === "invalid") target.el.classList.add("drop-invalid");
      const name = target.id ? nodeTitle(target.id) : "";
      label.textContent = target.kind === "node" ? (target.zone === "child" ? `挂到「${name}」下` : `放到「${name}」${target.zone === "before" ? "前面" : "后面"}`)
        : target.kind === "remove" ? "移出导图（想法还在）" : target.kind === "root" ? "放成一个新的根" : "不能挂到自己或自己的下级下";
      label.hidden = false;
      const box = host.querySelector(".mm-stage").getBoundingClientRect();
      label.style.transform = `translate(${Math.min(event.clientX - box.left + 14, box.width - 220)}px,${event.clientY - box.top + 30}px)`;
    }
    function cancelDrag() {
      if (!drag) return;
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", cancelDrag);
      drag.ghost?.remove(); drag.from.classList.remove("dragging"); host.classList.remove("mm-dragging");
      for (const el of host.querySelectorAll(".drop-child,.drop-before,.drop-after,.drop-invalid")) el.classList.remove("drop-child", "drop-before", "drop-after", "drop-invalid");
      host.querySelector(".mm-tray").classList.remove("drop-remove"); $(".mm-scroll").classList.remove("drop-root");
      $(".mm-drop-label").hidden = true;
      drag = null;
    }
    async function onPointerUp(event) {
      const current = drag;
      if (!current || event.pointerId !== current.pointer) return;
      cancelDrag();
      if (!current.active) return;
      // The click that follows this pointerup belongs to the drag, not to the canvas.
      suppressClick = true; setTimeout(() => { suppressClick = false; });
      const target = current.target;
      if (!target || target.kind === "invalid") return;
      if (target.kind === "remove") { await removeNode(current.source.id); return; }
      const result = await edit(target.ops, current.source.id !== undefined ? "移动" : "放进导图");
      if (result) { selected = current.source.id ?? result.effects[0].id; focusAfter = true; render(); ctx.onSelection(); }
    }

    // ---- clicks -----------------------------------------------------------------------------------------------------
    function onClick(event) {
      if (suppressClick) { suppressClick = false; return; }
      const control = event.target.closest("[data-mm]"), nodeEl = event.target.closest(".mm-node");
      const act = control?.dataset.mm;
      if (act === "chat") return;
      if (act === "fold" && nodeEl) { event.preventDefault(); void toggleFold(nodeEl.dataset.node); return; }
      if (act) { event.preventDefault(); void action(act, control, event); return; }
      // Tap-to-place works with touch, pen and mouse alike: pick in the tray, then tap where it goes.
      const trayEl = event.target.closest(".mm-tray-item");
      if (trayEl && !picking && map) { arm(trayEl.dataset.note); return; }
      if (armed !== null && !picking) {
        if (nodeEl && !nodeEl.classList.contains("editing")) { void place(armed, nodeEl.dataset.node); return; }
        if (event.target.closest(".mm-scroll")) { void place(armed, null); return; }
      }
      if (nodeEl && !nodeEl.classList.contains("editing")) {
        if (picking) togglePick(nodeEl.dataset.node);
        else if (selected !== nodeEl.dataset.node) select(nodeEl.dataset.node);
        return;
      }
      if (event.target.closest(".mm-scroll") && selected !== null && !picking) { selected = null; render(); ctx.onSelection(); }
    }
    function onDoubleClick(event) {
      const nodeEl = event.target.closest(".mm-node");
      if (!nodeEl || picking || event.target.closest("[data-mm]") || nodeEl.classList.contains("editing")) return;
      rename(nodeEl.dataset.node);
    }
    function onChange() {}
    async function action(act, control) {
      if (act === "reload") { await load(); return; }
      if (act === "tray") { trayOpen = !trayOpen; renderTray(); return; }
      if (act === "zoom-in" || act === "zoom-out") { setZoom(zoom + (act === "zoom-in" ? .1 : -.1)); return; }
      if (act === "zoom-reset") { setZoom(1); return; }
      if (act === "place") { await place(control.closest(".mm-tray-item").dataset.note); return; }
      if (act === "disarm") { armed = null; render(); return; }
      if (act === "capture-detach") { attachCapture = false; renderCapture(); $(".mm-capture-input").focus(); return; }
      if (act === "retry-attach" && pendingAttach) {
        const {noteId, parent, title} = pendingAttach;
        if (!t.nodes.has(parent)) { pendingAttach = null; captureNote = `「${title}」已不在导图上，想法留在下面。`; renderCapture(); return; }
        const result = await place(noteId, parent);
        if (result) { pendingAttach = null; captureNote = `已挂到「${title}」下。`; }
        renderCapture(); return;
      }
      if (act === "drop-attach") { pendingAttach = null; captureNote = "想法已记下，留在下面没挂。"; renderCapture(); return; }
      if (act === "new-root") { startDraft(null, t.roots.length); return; }
      if (act === "convert") { startPicking(); return; }
      if (act === "pick-cancel") { stopPicking(); return; }
      if (act === "pick-preview") { openPreview(); return; }
      if (selected === null) return;
      const id = selected, node = t.nodes.get(id);
      if (act === "child") startDraft(id, node.children.length);
      else if (act === "sibling") startDraft(node.parent_id, MM.indexOf(t, id) + 1);
      else if (act === "fold") await toggleFold(id);
      else if (act === "rename") rename(id);
      else if (act === "up") await structural(MM.reorder(t, id, -1), "上移");
      else if (act === "down") await structural(MM.reorder(t, id, 1), "下移");
      else if (act === "outdent") await structural(MM.outdent(t, id), "升级");
      else if (act === "indent") await structural(MM.indent(t, id), "降级");
      else if (act === "remove") await removeNode(id);
      else if (act === "promote") ctx.promoteDialog(note(id));
      else if (act === "recover") void ctx.recoverDialog(note(id));
      else if (act === "menu") ctx.openNoteMenu(control, note(id));
    }

    // ---- conversion -------------------------------------------------------------------------------------------------
    function startPicking() {
      picking = new Set(selected !== null ? [selected] : []); armed = null;
      render();
      $(".mm-scroll").focus({preventScroll: true});
    }
    function stopPicking() { picking = null; render(); if (selected !== null) focusNode(selected); }
    function togglePick(id) {
      if (picking.has(id)) picking.delete(id); else picking.add(id);
      selected = id; render(); focusNode(id);
    }
    const STATUS = {new: "新建", merge: "并入", linked: "已转", same: ""};
    function openPreview() {
      const {tops} = MM.branches(t, picking);
      const request = {project: ctx.project, roots: tops, parent_id: null, decisions: {}, names: {}, leaders: {}};
      const node = ctx.dialog("mm-convert", `${ctx.head(`转成架构图 · ${esc(ctx.workspaceName())}`, "预览只读，不改任何东西；确认后一次写入，之后可在架构图里继续调整。")}<div class="mm-cv-body" aria-live="polite"><p class="nw-loading" role="status">正在计算改动…</p></div><p role="status" class="nw-form-status"></p><div class="nw-dialog-acts"><button type="button" class="btn ghost" data-close>取消</button><button type="button" class="btn primary" data-convert disabled>确认转换</button></div>`, "转成架构图");
      const body = node.querySelector(".mm-cv-body");
      let plan = null, side = "after", busy = false, first = true;
      async function refreshPlan(message = "") {
        busy = true; node.querySelector("[data-convert]").disabled = true;
        try { plan = await client.preview(request); ctx.formStatus(node, message); }
        catch (failure) { plan = null; body.innerHTML = `<p class="nw-form-error" role="alert">${esc(reason(failure))}</p>`; ctx.formStatus(node, ""); busy = false; return; }
        busy = false; paint();
      }
      function outline(parts, editable) {
        const children = new Map();
        for (const part of parts) { const key = part.parent_id ?? ""; if (!children.has(key)) children.set(key, []); children.get(key).push(part); }
        const rows = new Map(plan.rows.map(row => [row.action === "create" ? `new:${row.node_id}` : row.domain_id, row]));
        const walk = (key, level) => (children.get(key) || []).map(part => item(part, level) + walk(part.id, level + 1)).join("");
        const item = (part, level) => {
          const row = editable ? rows.get(part.id) : null, status = editable ? part.status : "same";
          const leaders = part.leaders.map(person => `<span class="mm-cv-leader${person.moved_in ? " in" : ""}${person.moved_out ? " out" : ""}${person.lead ? " lead" : ""}">${esc(person.name)}${person.moved_in ? " · 移入" : person.moved_out ? " · 移出" : person.lead ? " · 负责" : ""}</span>`).join("");
          let controls = "";
          if (row?.conflict) {
            const choices = [["merge", `并入已有「${row.conflict.name}」`], ["create", "仍新建"], ["skip", "跳过"]];
            controls += `<select data-cv="decision" data-node="${esc(row.node_id)}" aria-label="「${esc(row.name)}」和已有 part 同名，怎么处理">${choices.map(([value, text]) => `<option value="${value}" ${row.action === value ? "selected" : ""}>${text}</option>`).join("")}</select>`;
          } else if (row?.action === "create") controls += `<button type="button" class="btn sm ghost mm-cv-skip" data-cv="skip" data-node="${esc(row.node_id)}" title="不建这个 part，下级上移一级">跳过</button>`;
          if (row) {
            const current = row.leader?.session_id ?? "";
            controls += `<select data-cv="leader" data-node="${esc(row.node_id)}" aria-label="放进「${esc(row.name)}」的 leader"><option value="">${row.slot ? "建议挂 Leader…" : "不放 leader"}</option>${plan.leaders.map(person => `<option value="${esc(person.session_id)}" ${person.session_id === current ? "selected" : ""}>${esc(person.name)}${person.domain_id ? ` · 在「${esc(plan.before.find(p => p.id === person.domain_id)?.name || "")}」` : " · 未归属"}</option>`).join("")}</select>`;
            if (row.leader?.source === "promoted") controls += `<span class="mm-cv-note">转任务时建的讨论</span>`;
          }
          const name = row && row.action === "create" ? `<input data-cv="name" data-node="${esc(row.node_id)}" value="${esc(row.name)}" maxlength="40" aria-label="新 part 名称">` : `<span class="mm-cv-name">${esc(part.name)}</span>`;
          return `<div class="mm-cv-part ${status}" style="--level:${level}">${STATUS[status] ? `<span class="mm-cv-status">${STATUS[status]}</span>` : ""}${name}${leaders ? `<span class="mm-cv-leaders">${leaders}</span>` : ""}${controls ? `<span class="mm-cv-controls">${controls}</span>` : ""}</div>`;
        };
        return walk("", 0) || `<p class="nw-quiet">这个工作区还没有架构图。</p>`;
      }
      function paint() {
        const sum = plan.summary, focusKey = document.activeElement?.dataset?.cv ? [document.activeElement.dataset.cv, document.activeElement.dataset.node] : null;
        const parents = plan.before.map(part => {
          const path = []; for (let item = part; item; item = plan.before.find(p => p.id === item.parent_id)) path.unshift(item.name);
          return [part.id, path.join(" › ")];
        });
        const skipped = plan.rows.filter(row => row.action === "skip");
        const inherited = plan.rows.filter(row => row.depth === 0 && row.parent.kind === "part" && row.parent.id !== plan.parent_id);
        body.innerHTML = `<div class="mm-cv-top"><label>分支顶端挂到<select data-cv="parent"><option value="">顶层</option>${parents.map(([id, path]) => `<option value="${esc(id)}" ${plan.parent_id === id ? "selected" : ""}>${esc(path)}</option>`).join("")}</select></label>`
          + `<p class="mm-cv-sum"><b>新建 ${sum.create}</b> · 并入 ${sum.merge} · 已转 ${sum.linked} · 跳过 ${sum.skip} · 放入 leader ${sum.leaders}</p></div>`
          + `<div class="nw-seg mm-cv-switch" role="radiogroup" aria-label="对照"><button type="button" role="radio" aria-checked="${side === "after"}" data-side="after">改后</button><button type="button" role="radio" aria-checked="${side === "before"}" data-side="before">改前</button></div>`
          + `<div class="mm-cv-grid" data-showing="${side}"><section class="mm-cv-col before"><h3>改前 · 现在的架构图</h3>${outline(plan.before, false)}</section><section class="mm-cv-col after"><h3>改后</h3>${outline(plan.after, true)}${skipped.length ? `<p class="mm-cv-skipped">跳过（不建 part，下级上移）：${skipped.map(row => `<button type="button" class="nw-tag" data-cv="unskip" data-node="${esc(row.node_id)}" title="改回新建">${esc(row.name)} ↺</button>`).join("")}</p>` : ""}</section></div>`
          + inherited.map(row => `<p class="mm-cv-hint">「${esc(row.name)}」的上级以前已转过，会挂到「${esc(row.parent.name)}」下面。</p>`).join("");
        const button = node.querySelector("[data-convert]");
        button.disabled = plan.nothing || busy;
        button.textContent = plan.nothing ? "没有需要转换的改动" : `确认转换（新建 ${sum.create}${sum.merge ? ` · 并入 ${sum.merge}` : ""}${sum.leaders ? ` · 放入 ${sum.leaders} 位 leader` : ""}）`;
        if (focusKey) body.querySelector(`[data-cv="${focusKey[0]}"]${focusKey[1] ? `[data-node="${CSS.escape(focusKey[1])}"]` : ""}`)?.focus();
        else if (first) body.querySelector("[data-cv=parent]").focus();
        first = false;
      }
      body.addEventListener("change", event => {
        const control = event.target.closest("[data-cv]"); if (!control || busy) return;
        const id = control.dataset.node, kind = control.dataset.cv;
        if (kind === "parent") request.parent_id = control.value || null;
        else if (kind === "decision") request.decisions[id] = control.value;
        else if (kind === "leader") request.leaders[id] = control.value || null;
        else if (kind === "name") { const value = control.value.trim(); if (value && value !== plan.rows.find(row => row.node_id === id).default_name) request.names[id] = value; else delete request.names[id]; }
        void refreshPlan();
      });
      body.addEventListener("keydown", event => { if (event.key === "Enter" && event.target.matches("input[data-cv=name]") && !event.isComposing) { event.preventDefault(); event.target.blur(); } });
      body.addEventListener("click", event => {
        const sideButton = event.target.closest("button[data-side]");
        if (sideButton) { side = sideButton.dataset.side; paint(); return; }
        const unskip = event.target.closest("[data-cv=unskip]");
        if (unskip && !busy) { delete request.decisions[unskip.dataset.node]; void refreshPlan(); return; }
        const skip = event.target.closest("[data-cv=skip]");
        if (skip && !busy) { request.decisions[skip.dataset.node] = "skip"; delete request.names[skip.dataset.node]; delete request.leaders[skip.dataset.node]; void refreshPlan(); }
      });
      node.querySelector("[data-convert]").addEventListener("click", async () => {
        if (!plan || busy) return;
        busy = true; ctx.lockForm(node, true); ctx.formStatus(node, "正在写入架构图…");
        let result;
        try { result = await client.convert(request, plan); }
        catch (failure) {
          busy = false; ctx.lockForm(node, false);
          if (failure.status === 409) { map = await client.load(); sync(); render(); await refreshPlan("导图或架构图刚被改动过，已重新预览，请再核对一次。"); return; }
          ctx.formStatus(node, reason(failure)); paint(); return;
        }
        map = result.map; picking = null; sync(); render();
        node.close();
        const project = request.project;
        ctx.toast(`已转进「${ctx.projectName(project)}」的架构图：新建 ${result.summary.create} 个 part${result.summary.leaders ? `，放入 ${result.summary.leaders} 位 leader` : ""}。`, {label: "打开架构图", run: () => { location.href = `/?project=${encodeURIComponent(project)}#architecture`; }});
      });
      void refreshPlan();
    }

    // ---- page interface ---------------------------------------------------------------------------------------------
    return {
      active: () => host !== null,
      mount(element) { host = element; scaffold(); render(); void load(); },
      unmount() { cancelDrag(); if (host) host.innerHTML = ""; host = null; picking = null; draft = null; renaming = null; armed = null; },
      render() { if (host) { sync(); render(); const box = $(".mm-capture-input"); if (document.activeElement !== box && box.value !== ctx.draft.get()) { box.value = ctx.draft.get(); resizeBox(); } } },
      refresh: () => host ? load() : Promise.resolve(),
      // The node the tray box would hang a new idea under, or null.
      captureTarget: () => host ? attachTarget() : null,
      // The idea behind the selected node, for the page's T / 1–9 shortcuts.
      selectedNote: () => host && map && selected !== null ? note(selected) || null : null
    };
  }
  return {create};
})();
