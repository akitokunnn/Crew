"use strict";
// Pure projections for the idea mind map: the tree from the server snapshot, the left-to-right layout,
// keyboard navigation, the ops for structural edits and drops, and the request shapes of the map endpoints.
// The server owns the map; nothing here writes.
(() => {
  const GAP_X = 44, GAP_Y = 10, ROOT_GAP = 26, ELBOW = 18, RADIUS = 7;
  const DRAFT = "__draft";

  // {nodes: Map id -> {...node, children: [ids]}, roots: [ids], links: Map node id -> [link]}
  function tree(map, draft = null) {
    const nodes = new Map(map.nodes.map(node => [node.id, {...node, children: []}])), roots = [];
    for (const node of map.nodes) (node.parent_id === null ? roots : nodes.get(node.parent_id).children).push(node.id);
    if (draft) {
      nodes.set(DRAFT, {id: DRAFT, parent_id: draft.parent_id, note_id: null, label: "", collapsed: false, children: [], draft: true});
      (draft.parent_id === null ? roots : nodes.get(draft.parent_id).children).splice(draft.index, 0, DRAFT);
    }
    const links = new Map();
    for (const link of map.links) { if (!links.has(link.node_id)) links.set(link.node_id, []); links.get(link.node_id).push(link); }
    return {nodes, roots, links};
  }
  const parentList = (t, id) => { const node = t.nodes.get(id); return node.parent_id === null ? t.roots : t.nodes.get(node.parent_id).children; };
  const indexOf = (t, id) => parentList(t, id).indexOf(id);
  function descendants(t, id) {
    const out = [], walk = nodeId => { for (const child of t.nodes.get(nodeId).children) { out.push(child); walk(child); } };
    walk(id); return out;
  }
  function depth(t, id) { let d = 0; for (let node = t.nodes.get(id); node.parent_id !== null; node = t.nodes.get(node.parent_id)) d++; return d; }
  function ancestors(t, id) { const out = []; for (let node = t.nodes.get(id); node.parent_id !== null; node = t.nodes.get(node.parent_id)) out.push(node.parent_id); return out; }
  // The second-level ancestor (a child of a root), which picks the branch colour; null for roots.
  function branch(t, id) {
    const chain = [id, ...ancestors(t, id)];
    return chain.length >= 2 ? chain[chain.length - 2] : null;
  }
  // Visible nodes in reading order: a collapsed node hides its descendants.
  function visible(t) {
    const out = [], walk = id => { out.push(id); if (!t.nodes.get(id).collapsed) for (const child of t.nodes.get(id).children) walk(child); };
    for (const root of t.roots) walk(root);
    return out;
  }

  // Left to right: a child starts GAP_X after its parent's right edge; siblings stack with GAP_Y; a parent is
  // centred on the span of its visible children. sizes(id) -> {w, h}.
  function layout(t, sizes, pad = 24) {
    const pos = new Map();
    let cursor = pad, width = 0;
    function place(id, x) {
      const size = sizes(id), node = t.nodes.get(id), kids = node.collapsed ? [] : node.children;
      if (!kids.length) {
        pos.set(id, {x, y: cursor, w: size.w, h: size.h});
        cursor += size.h + GAP_Y;
      } else {
        const top = cursor;
        for (const child of kids) place(child, x + size.w + GAP_X);
        const first = pos.get(kids[0]), last = pos.get(kids.at(-1));
        const centre = (first.y + first.h / 2 + last.y + last.h / 2) / 2;
        const y = Math.max(top, centre - size.h / 2);
        pos.set(id, {x, y, w: size.w, h: size.h});
        // A tall parent beside short children pushes the next sibling down.
        cursor = Math.max(cursor, y + size.h + GAP_Y);
      }
      width = Math.max(width, x + size.w);
    }
    t.roots.forEach((root, index) => { if (index) cursor += ROOT_GAP - GAP_Y; place(root, pad); });
    return {pos, width: width + pad, height: Math.max(cursor - GAP_Y + pad, pad * 2)};
  }
  // Rounded elbow from the parent's right edge to the child's left edge.
  function wire(from, to) {
    const x1 = from.x + from.w, y1 = from.y + from.h / 2, x2 = to.x, y2 = to.y + to.h / 2, mx = x1 + ELBOW;
    if (Math.abs(y2 - y1) < 1) return `M${x1} ${y1}H${x2}`;
    const r = Math.min(RADIUS, Math.abs(y2 - y1) / 2), s = Math.sign(y2 - y1);
    return `M${x1} ${y1}H${mx - r}Q${mx} ${y1} ${mx} ${y1 + s * r}V${y2 - s * r}Q${mx} ${y2} ${mx + r} ${y2}H${x2}`;
  }

  // Arrow keys: left = parent, right = first visible child, up / down = previous / next sibling.
  function navigate(t, id, key) {
    const node = t.nodes.get(id);
    if (key === "ArrowLeft") return node.parent_id;
    if (key === "ArrowRight") return !node.collapsed && node.children.length ? node.children[0] : null;
    const list = parentList(t, id), index = list.indexOf(id) + (key === "ArrowUp" ? -1 : 1);
    return list[index] ?? null;
  }

  // Structural edits as server ops; null when the move is not possible from here.
  function reorder(t, id, delta) {
    const index = indexOf(t, id) + delta;
    if (index < 0 || index >= parentList(t, id).length) return null;
    return [{op: "move", id, parent_id: t.nodes.get(id).parent_id, index}];
  }
  // Outdent: hang under the grandparent, right after the former parent.
  function outdent(t, id) {
    const parent = t.nodes.get(id).parent_id;
    if (parent === null) return null;
    return [{op: "move", id, parent_id: t.nodes.get(parent).parent_id, index: indexOf(t, parent) + 1}];
  }
  // Indent: become the last child of the previous sibling.
  function indent(t, id) {
    const index = indexOf(t, id);
    if (index === 0) return null;
    const target = parentList(t, id)[index - 1];
    return [{op: "move", id, parent_id: target, index: t.nodes.get(target).children.length}];
  }
  // Where a drop over a node lands: the top / bottom quarter inserts a sibling, the rest makes a child.
  function dropZone(rect, y) {
    const offset = (y - rect.top) / rect.height;
    return offset < .25 ? "before" : offset > .75 ? "after" : "child";
  }
  // source: {id} for a node already on the map, {note_id} for an idea from the tray.
  function dropOps(t, source, target, zone) {
    if (source.id !== undefined && (source.id === target || descendants(t, source.id).includes(target))) return null;
    let parent_id, index;
    if (zone === "child") { parent_id = target; index = t.nodes.get(target).children.filter(child => child !== source.id).length; }
    else {
      parent_id = t.nodes.get(target).parent_id;
      const list = parentList(t, target).filter(item => item !== source.id);
      index = list.indexOf(target) + (zone === "after" ? 1 : 0);
    }
    return [source.id !== undefined ? {op: "move", id: source.id, parent_id, index} : {op: "create", note_id: source.note_id, parent_id, index}];
  }
  // A drop on empty canvas: a new root at the end.
  function rootOps(t, source) {
    const index = t.roots.filter(item => item !== source.id).length;
    return [source.id !== undefined ? {op: "move", id: source.id, parent_id: null, index} : {op: "create", note_id: source.note_id, parent_id: null, index}];
  }
  // Puts a removed node back with the same id and gives it its children again.
  function undoRemove(effect) {
    const create = {op: "create", id: effect.id, parent_id: effect.parent_id, index: effect.index};
    if (effect.note_id !== null) create.note_id = effect.note_id; else create.label = effect.label;
    return [create, ...effect.children.map((child, index) => ({op: "move", id: child, parent_id: effect.id, index})),
      ...(effect.collapsed ? [{op: "collapse", id: effect.id, collapsed: true}] : [])];
  }

  // Branches picked for conversion: the picked ids not inside another picked branch, and every node they cover.
  function branches(t, picked) {
    const tops = [...picked].filter(id => t.nodes.has(id) && !ancestors(t, id).some(item => picked.has(item)));
    const covered = new Set(tops.flatMap(id => [id, ...descendants(t, id)]));
    return {tops, covered};
  }

  // Request shapes; api(path, body) is the page's JSON helper (GET without a body). Each workspace has its own map
  // and version; edit's parent_id is a map node, convert's parent_id is a part of this workspace's board.
  function client(api, project) {
    return {
      load: () => api(`/api/op/mindmap.get?project=${encodeURIComponent(project)}`),
      edit: (ops, base_version) => api("/api/op/mindmap.edit", {project, ops, base_version}),
      preview: request => api("/api/op/mindmap.preview", {...request, project}),
      convert: (request, versions) => api("/api/op/mindmap.convert", {...request, project, map_version: versions.map_version, board_version: versions.board_version})
    };
  }

  window.CrewMindmapModel = {GAP_X, GAP_Y, ROOT_GAP, DRAFT, tree, parentList, indexOf, descendants, depth, ancestors, branch, visible,
    layout, wire, navigate, reorder, outdent, indent, dropZone, dropOps, rootOps, undoRemove, branches, client};
})();
