"use strict";
// Pure projections for the idea wall: filters, grouping, local duplicate hints
// and the URL hash. The server owns note state; nothing here writes.
(() => {
  const DAY = 86400000;
  const SORTS = ["updated", "created", "discussed"], GROUPS = ["time", "stage"], VIEWS = ["open", "stale", "promoted", "archived"];
  const DEFAULTS = Object.freeze({tags: [], mode: "all", untagged: false, ws: null, global: false, sort: "updated", group: "time", view: "open"});
  const STAGES = [["new", "新想法"], ["discussed", "讨论过"], ["promoted", "已转任务"]];

  const firstLine = text => text.split("\n").map(line => line.trim()).find(Boolean) || "";
  const title = note => note.title || firstLine(note.text);
  // Text under the title: when the title is the first line, do not repeat it.
  function body(note) {
    if (note.title) return note.text.trim();
    const lines = note.text.split("\n"), index = lines.findIndex(line => line.trim());
    return lines.slice(index + 1).join("\n").trim();
  }

  function parseHash(hash) {
    const params = new URLSearchParams(hash.replace(/^#/, "")), state = {...DEFAULTS, tags: []};
    const tags = params.get("tags");
    if (tags) state.tags = [...new Set(tags.split(",").filter(Boolean))];
    if (params.get("mode") === "any") state.mode = "any";
    if (params.get("untagged") === "1") { state.untagged = true; state.tags = []; }
    if (params.get("ws")) state.ws = params.get("ws");
    else if (params.get("global") === "1") state.global = true;
    if (SORTS.includes(params.get("sort"))) state.sort = params.get("sort");
    if (GROUPS.includes(params.get("group"))) state.group = params.get("group");
    if (VIEWS.includes(params.get("view"))) state.view = params.get("view");
    return state;
  }
  function toHash(state) {
    const params = new URLSearchParams();
    if (state.tags.length) params.set("tags", state.tags.join(","));
    if (state.mode !== DEFAULTS.mode) params.set("mode", state.mode);
    if (state.untagged) params.set("untagged", "1");
    if (state.ws !== null) params.set("ws", state.ws);
    else if (state.global) params.set("global", "1");
    for (const key of ["sort", "group", "view"]) if (state[key] !== DEFAULTS[key]) params.set(key, state[key]);
    const text = params.toString();
    return text ? "#" + text : "";
  }

  // Each note belongs to exactly one view; promoted notes are archived by the
  // server but listed under their own view.
  function inView(note, view) {
    if (view === "promoted") return note.stage === "promoted";
    if (view === "archived") return note.state === "archived" && note.stage !== "promoted";
    if (view === "stale") return note.state === "open" && note.stale;
    return note.state === "open";
  }
  function matches(note, filter) {
    if (!inView(note, filter.view)) return false;
    if (filter.ws !== null && note.project !== filter.ws) return false;
    if (filter.global && note.project !== null) return false;
    if (filter.untagged) return note.tags.length === 0;
    if (!filter.tags.length) return true;
    return filter.mode === "any" ? filter.tags.some(tag => note.tags.includes(tag)) : filter.tags.every(tag => note.tags.includes(tag));
  }
  const filter = (notes, state) => notes.filter(note => matches(note, state));

  const sortTime = (note, sort) => sort === "created" ? note.created_at : sort === "discussed" ? note.last_discussed_at || note.updated_at : note.updated_at;
  function sorted(notes, sort) {
    const time = note => Date.parse(sortTime(note, sort));
    return notes.slice().sort((a, b) => sort === "discussed" && Boolean(a.last_discussed_at) !== Boolean(b.last_discussed_at)
      ? (a.last_discussed_at ? -1 : 1) : time(b) - time(a) || a.id.localeCompare(b.id));
  }

  function startOfDay(now) { const day = new Date(now); day.setHours(0, 0, 0, 0); return day.getTime(); }
  function startOfWeek(now) { const day = new Date(startOfDay(now)); day.setDate(day.getDate() - (day.getDay() + 6) % 7); return day.getTime(); }
  function timeBand(iso, now) {
    const at = Date.parse(iso);
    if (at >= startOfDay(now)) return "today";
    return at >= startOfWeek(now) ? "week" : "earlier";
  }

  // Sections in display order; empty sections are dropped.
  function group(notes, state, now) {
    const list = sorted(notes, state.sort), sections = [];
    const push = (key, label, items, tone = "") => { if (items.length) sections.push({key, label, items, tone}); };
    if (state.group === "stage") {
      const pinnedFirst = items => items.filter(note => note.pinned_at).concat(items.filter(note => !note.pinned_at));
      for (const [stage, label] of STAGES) push(stage, label, pinnedFirst(list.filter(note => note.stage === stage)));
      return sections;
    }
    let rest = list;
    if (state.view === "open") {
      const pinned = list.filter(note => note.pinned_at).sort((a, b) => Date.parse(b.pinned_at) - Date.parse(a.pinned_at));
      const stale = list.filter(note => !note.pinned_at && note.stale);
      push("pinned", "置顶", pinned);
      push("stale", "需要回顾", stale, "warn");
      rest = list.filter(note => !note.pinned_at && !note.stale);
    }
    for (const [band, label] of [["today", "今天"], ["week", "本周"], ["earlier", "更早"]])
      push(band, label, rest.filter(note => timeBand(sortTime(note, state.sort), now) === band));
    return sections;
  }

  function counts(notes) {
    const tags = new Map();
    let untagged = 0;
    for (const note of notes) {
      if (note.state !== "open") continue;
      if (!note.tags.length) untagged++;
      for (const tag of note.tags) tags.set(tag, (tags.get(tag) || 0) + 1);
    }
    return {tags, untagged, open: notes.filter(note => inView(note, "open")).length,
      stale: notes.filter(note => inView(note, "stale")).length,
      promoted: notes.filter(note => inView(note, "promoted")).length,
      archived: notes.filter(note => inView(note, "archived")).length};
  }

  // Local duplicate hint: Jaccard similarity of character bigrams, no model.
  const THRESHOLD = 0.6;
  function grams(text) {
    const clean = text.toLowerCase().replace(/[\s\p{P}\p{S}]+/gu, "");
    if (clean.length < 2) return new Set(clean ? [clean] : []);
    const set = new Set();
    for (let i = 0; i < clean.length - 1; i++) set.add(clean.slice(i, i + 2));
    return set;
  }
  function jaccard(x, y) {
    if (!x.size || !y.size) return 0;
    let shared = 0;
    for (const gram of x) if (y.has(gram)) shared++;
    return shared / (x.size + y.size - shared);
  }
  const similarity = (a, b) => jaccard(grams(a), grams(b));
  // Open note id -> ids of other open notes that read nearly the same.
  function duplicates(notes) {
    const prepared = notes.filter(note => note.state === "open").map(note => [note.id, grams(note.text)]), result = new Map();
    for (let i = 0; i < prepared.length; i++) for (let j = i + 1; j < prepared.length; j++) {
      const [a, x] = prepared[i], [b, y] = prepared[j];
      if (jaccard(x, y) < THRESHOLD) continue;
      if (!result.has(a)) result.set(a, []);
      if (!result.has(b)) result.set(b, []);
      result.get(a).push(b); result.get(b).push(a);
    }
    return result;
  }

  // Quick input: "#tag" tokens become tags and leave the text.
  function parseCapture(input) {
    const tags = [];
    const text = input.replace(/(^|\s)#([^\s#@]+)/g, (_match, space, tag) => { if (!tags.includes(tag)) tags.push(tag); return space; })
      .replace(/[ \t]+\n/g, "\n").replace(/[ \t]{2,}/g, " ").trim();
    return {text, tags};
  }
  // The token being typed right before the caret, for # and @ completion.
  function token(value, caret) {
    const match = /(^|\s)([#@])([^\s#@]*)$/.exec(value.slice(0, caret));
    return match ? {kind: match[2], query: match[3], start: caret - match[3].length - 1, end: caret} : null;
  }
  // Loose subsequence match for the tag panel.
  function fuzzy(name, query) {
    const text = name.toLowerCase(), wanted = query.toLowerCase().trim();
    let index = 0;
    for (const char of wanted) { index = text.indexOf(char, index); if (index < 0) return false; index++; }
    return true;
  }
  function orderTags(tags) {
    return tags.slice().sort((a, b) => Number(b.pinned) - Number(a.pinned) || a.sort_order - b.sort_order || a.name.localeCompare(b.name, "zh-CN"));
  }

  const idleSince = note => Math.max(...[note.updated_at, note.last_discussed_at, note.reviewed_at].filter(Boolean).map(Date.parse));
  const daysSince = (iso, now) => Math.floor((now - Date.parse(iso)) / DAY);
  function relative(iso, now) {
    const at = Date.parse(iso);
    if (at >= startOfDay(now)) return "今天";
    if (at >= startOfDay(now) - DAY) return "昨天";
    const days = Math.ceil((startOfDay(now) - at) / DAY);
    if (days < 30) return `${days} 天前`;
    const date = new Date(at);
    return new Date(now).getFullYear() === date.getFullYear() ? `${date.getMonth() + 1}月${date.getDate()}日` : `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`;
  }

  window.CrewNotesModel = {DEFAULTS, STAGES, THRESHOLD, title, body, parseHash, toHash, inView, matches, filter, sorted, group, counts,
    similarity, duplicates, parseCapture, token, fuzzy, orderTags, idleSince, daysSince, relative, timeBand};
})();
