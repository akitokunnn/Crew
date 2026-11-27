"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm"), fs = require("node:fs"), path = require("node:path");
const web = file => fs.readFileSync(path.join(__dirname, "../../frontend", file), "utf8");
// Same realm as the assertions, so arrays and objects compare structurally.
globalThis.window = globalThis.window || {};
vm.runInThisContext(web("notes-model.js"));
vm.runInThisContext(web("mindmap-model.js"));
const N = globalThis.window.CrewNotesModel, MM = globalThis.window.CrewMindmapModel;

// root ─┬─ a ─┬─ a1
//       │     └─ a2
//       └─ b
// solo (second root)
const node = (id, parent_id, sort_order, extra = {}) => ({id, parent_id, note_id: null, label: id, sort_order, collapsed: false, ...extra});
const MAP = {version: 3, nodes: [node("root", null, 0), node("solo", null, 1), node("a", "root", 0), node("b", "root", 1), node("a1", "a", 0), node("a2", "a", 1)],
  links: [{node_id: "a", project: "demo", domain_id: "p1", domain_name: "A"}]};
const size = () => ({w: 100, h: 30});

test("tree keeps sibling order, depth, branch and visibility follow the structure", () => {
  const t = MM.tree(MAP);
  assert.deepEqual(t.roots, ["root", "solo"]);
  assert.deepEqual(t.nodes.get("a").children, ["a1", "a2"]);
  assert.deepEqual([MM.depth(t, "root"), MM.depth(t, "a2")], [0, 2]);
  assert.deepEqual([MM.branch(t, "root"), MM.branch(t, "a"), MM.branch(t, "a2"), MM.branch(t, "b")], [null, "a", "a", "b"], "the colour comes from the first level under a root");
  assert.deepEqual(MM.visible(t), ["root", "a", "a1", "a2", "b", "solo"]);
  assert.deepEqual(MM.descendants(t, "root"), ["a", "a1", "a2", "b"]);
  assert.deepEqual(t.links.get("a").map(link => link.domain_name), ["A"]);
  const folded = MM.tree({...MAP, nodes: MAP.nodes.map(item => item.id === "a" ? {...item, collapsed: true} : item)});
  assert.deepEqual(MM.visible(folded), ["root", "a", "b", "solo"]);
  const drafted = MM.tree(MAP, {parent_id: "a", index: 1});
  assert.deepEqual(drafted.nodes.get("a").children, ["a1", MM.DRAFT, "a2"], "an inline draft takes its slot before it exists");
});

test("layout runs left to right, centres parents on their children and never overlaps", () => {
  const t = MM.tree(MAP), {pos, width, height} = MM.layout(t, size);
  const a = pos.get("a"), a1 = pos.get("a1"), a2 = pos.get("a2"), root = pos.get("root"), solo = pos.get("solo");
  assert.equal(a.x, root.x + 100 + MM.GAP_X);
  assert.equal(a1.x, a.x + 100 + MM.GAP_X);
  assert.equal(a.y + 15, (a1.y + 15 + a2.y + 15) / 2);
  assert.equal(a2.y - a1.y, 30 + MM.GAP_Y);
  assert(solo.y >= Math.max(...[...pos.values()].filter(p => p !== solo).map(p => p.y + p.h)) + MM.ROOT_GAP - 1, "roots keep a wider gap");
  const boxes = [...pos.values()];
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
    const p = boxes[i], q = boxes[j];
    assert(!(p.x < q.x + q.w && q.x < p.x + p.w && p.y < q.y + q.h && q.y < p.y + p.h), "nodes overlap");
  }
  assert(width >= a1.x + 100 && height >= solo.y + 30);
  const tall = MM.layout(t, id => id === "a" ? {w: 100, h: 120} : size());
  assert(tall.pos.get("b").y >= tall.pos.get("a").y + 120, "a tall parent pushes the next sibling down");
  assert.equal(MM.wire({x: 0, y: 0, w: 100, h: 30}, {x: 144, y: 0, w: 100, h: 30}), "M100 15H144");
  assert.match(MM.wire({x: 0, y: 0, w: 100, h: 30}, {x: 144, y: 80, w: 100, h: 30}), /^M100 15H111Q118 15 118 22V88Q118 95 125 95H144$/);
});

test("keys move through the tree and structural edits become server ops", () => {
  const t = MM.tree(MAP);
  assert.deepEqual(["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].map(key => MM.navigate(t, "a", key)), ["root", "a1", null, "b"]);
  assert.equal(MM.navigate(t, "root", "ArrowDown"), "solo");
  assert.deepEqual(MM.reorder(t, "b", -1), [{op: "move", id: "b", parent_id: "root", index: 0}]);
  assert.equal(MM.reorder(t, "a", -1), null);
  assert.deepEqual(MM.outdent(t, "a2"), [{op: "move", id: "a2", parent_id: "root", index: 1}], "right after the former parent");
  assert.equal(MM.outdent(t, "root"), null);
  assert.deepEqual(MM.indent(t, "b"), [{op: "move", id: "b", parent_id: "a", index: 2}], "last child of the previous sibling");
  assert.equal(MM.indent(t, "a"), null);
});

test("drops hang, insert or refuse, and a removal can be undone from its effect", () => {
  const t = MM.tree(MAP), rect = {top: 100, height: 40};
  assert.deepEqual([105, 120, 135].map(y => MM.dropZone(rect, y)), ["before", "child", "after"]);
  assert.deepEqual(MM.dropOps(t, {note_id: "n1"}, "a", "child"), [{op: "create", note_id: "n1", parent_id: "a", index: 2}]);
  assert.deepEqual(MM.dropOps(t, {id: "b"}, "a1", "before"), [{op: "move", id: "b", parent_id: "a", index: 0}]);
  assert.deepEqual(MM.dropOps(t, {id: "a1"}, "a2", "after"), [{op: "move", id: "a1", parent_id: "a", index: 1}], "the dragged node is not counted at its old place");
  assert.deepEqual(MM.dropOps(t, {id: "a2"}, "a", "child"), [{op: "move", id: "a2", parent_id: "a", index: 1}]);
  assert.equal(MM.dropOps(t, {id: "root"}, "a1", "child"), null, "never under its own descendant");
  assert.equal(MM.dropOps(t, {id: "a"}, "a", "after"), null);
  assert.deepEqual(MM.rootOps(t, {id: "a"}), [{op: "move", id: "a", parent_id: null, index: 2}]);
  assert.deepEqual(MM.rootOps(t, {id: "solo"}), [{op: "move", id: "solo", parent_id: null, index: 1}]);
  assert.deepEqual(MM.undoRemove({id: "a", parent_id: "root", index: 0, children: ["a1", "a2"], note_id: null, label: "A", collapsed: true}), [
    {op: "create", id: "a", parent_id: "root", index: 0, label: "A"},
    {op: "move", id: "a1", parent_id: "a", index: 0}, {op: "move", id: "a2", parent_id: "a", index: 1},
    {op: "collapse", id: "a", collapsed: true}]);
  assert.deepEqual(MM.undoRemove({id: "x", parent_id: null, index: 1, children: [], note_id: "n9", label: null, collapsed: false}),
    [{op: "create", id: "x", parent_id: null, index: 1, note_id: "n9"}]);
});

test("picked branches drop picks inside other picks and cover every descendant", () => {
  const t = MM.tree(MAP);
  const {tops, covered} = MM.branches(t, new Set(["a1", "root", "gone"]));
  assert.deepEqual(tops, ["root"]);
  assert.deepEqual([...covered].sort(), ["a", "a1", "a2", "b", "root"]);
  assert.deepEqual(MM.branches(t, new Set(["a", "solo"])).tops, ["a", "solo"]);
});

test("the map is the default layout; the card wall is chosen in the hash next to the filters", () => {
  assert.equal(N.DEFAULTS.layout, "map");
  assert.equal(N.toHash({...N.DEFAULTS, layout: "map"}), "");
  assert.equal(N.toHash({...N.DEFAULTS, layout: "wall"}), "#layout=wall");
  assert.deepEqual(N.parseHash("#layout=wall&tags=a"), {...N.DEFAULTS, tags: ["a"], layout: "wall"});
  assert.deepEqual(N.parseHash("#layout=grid"), {...N.DEFAULTS, tags: []});
});

// Stand-in for the endpoints until web.py routes them (docs/MINDMAP_DESIGN.md 待接线): same paths, bodies,
// version checks and status codes as plugin/crew/mindmap_api.py.
function fakeServer() {
  const state = {map: {version: 0, nodes: [], links: []}, board: 4, calls: []};
  const conflict = message => { const error = Error(message); error.status = 409; return error; };
  const exact = (body, keys) => assert.deepEqual(Object.keys(body).sort(), [...keys].sort());
  async function api(pathname, body) {
    state.calls.push([pathname, body === undefined ? "GET" : "POST"]);
    if (pathname === "/api/op/mindmap.get?project=demo%20%26%20co" && body === undefined) return structuredClone(state.map);
    if (pathname === "/api/op/mindmap.edit") {
      exact(body, ["project", "ops", "base_version"]); assert.equal(body.project, "demo & co");
      if (body.base_version !== state.map.version) throw conflict("导图已在别处改动，请刷新后再改");
      const effects = body.ops.map(op => {
        assert.equal(op.op, "create");
        const id = op.id || `n${state.map.nodes.length + 1}`;
        state.map.nodes.push({id, parent_id: op.parent_id, note_id: op.note_id ?? null, label: op.label ?? null, sort_order: op.index, collapsed: false});
        return {op: "create", id};
      });
      state.map.version++;
      return {map: structuredClone(state.map), effects};
    }
    const keys = ["project", "roots", "parent_id", "decisions", "names", "leaders"];
    if (pathname === "/api/op/mindmap.preview") {
      exact(body, keys); assert.equal(body.project, "demo & co", "the map converts into its own workspace");
      return {map_version: state.map.version, board_version: state.board, rows: body.roots.map(id => ({node_id: id, action: "create"})), nothing: false};
    }
    if (pathname === "/api/op/mindmap.convert") {
      exact(body, [...keys, "map_version", "board_version"]); assert.equal(body.project, "demo & co");
      if (body.map_version !== state.map.version || body.board_version !== state.board) throw conflict("导图或架构图在预览之后又有改动，请重新预览");
      state.board++; state.map.version++;
      state.map.links.push(...body.roots.map(id => ({node_id: id, project: body.project, domain_id: `d-${id}`, domain_name: id})));
      return {map: structuredClone(state.map), board_version: state.board, summary: {create: body.roots.length}};
    }
    throw Error(`unrouted ${pathname}`);
  }
  return {state, api};
}

test("the map client speaks the documented endpoints for its own workspace, carries versions and surfaces conflicts", async () => {
  const server = fakeServer(), client = MM.client(server.api, "demo & co");
  let map = await client.load();
  const t = MM.tree(map);
  const created = await client.edit(MM.rootOps(t, {note_id: "note-1"}), map.version);
  assert.deepEqual(created.effects, [{op: "create", id: "n1"}]);
  map = created.map;
  const child = await client.edit(MM.dropOps(MM.tree(map), {note_id: "note-2"}, "n1", "child"), map.version);
  assert.deepEqual(child.map.nodes.map(item => [item.id, item.parent_id, item.note_id]), [["n1", null, "note-1"], ["n2", "n1", "note-2"]]);
  await assert.rejects(client.edit([{op: "create", label: "旧版本", parent_id: null, index: 0}], map.version), error => error.status === 409);
  // Even a stale request naming another workspace converts into this map's own workspace.
  const request = {project: "other", roots: ["n1"], parent_id: null, decisions: {}, names: {}, leaders: {}};
  const plan = await client.preview(request);
  const done = await client.convert(request, plan);
  assert.equal(done.board_version, 5);
  assert.deepEqual(done.map.links.map(link => link.node_id), ["n1"]);
  await assert.rejects(client.convert(request, plan), error => error.status === 409, "a second confirm with the old preview is refused");
  assert.deepEqual(server.state.calls.map(([pathname, method]) => `${method} ${pathname}`), [
    "GET /api/op/mindmap.get?project=demo%20%26%20co", "POST /api/op/mindmap.edit", "POST /api/op/mindmap.edit", "POST /api/op/mindmap.edit",
    "POST /api/op/mindmap.preview", "POST /api/op/mindmap.convert", "POST /api/op/mindmap.convert"]);
});
