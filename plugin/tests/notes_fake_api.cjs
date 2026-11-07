'use strict';
// In-memory stand-in for the idea-wall contract (想法墙方案 · 接口约定) used by
// the idea-wall smoke until the backend lands. Field and action names follow
// the contract exactly; nothing here touches a Crew HOME.
const crypto = require('node:crypto');

const DAY = 86400000, STALE_DAYS = 14;
const palette = Array.from({length: 10}, (_, index) => 'c' + index);
const colorFor = name => palette[[...name].reduce((sum, char) => (sum * 31 + char.codePointAt(0)) >>> 0, 7) % 10];

function createNotesApi({now = () => Date.now()} = {}) {
  const at = days => new Date(now() - days * DAY).toISOString();
  // "Today" fixtures stay on today's date whatever time the smoke runs.
  const today = minutes => { const start = new Date(now()); start.setHours(0, 0, 0, 0); return new Date(Math.max(start.getTime(), now() - minutes * 60000)).toISOString(); };
  const projects = [{name: 'crew', display_name: 'Crew', root: 'C:/fixture/crew', theme: 'green'},
    {name: 'mib', display_name: 'MIB 协作', root: 'C:/fixture/mib', theme: 'green'}];
  const tags = new Map(), receipts = new Map(), sent = [], calls = [];
  let notes = [], serial = 0, failNext = null;
  function make(patch) {
    const text = patch.text;
    return {id: patch.id || 'note-' + ++serial, project: null, original_text: text, text, title: null, tags: [], state: 'open', stage: 'new',
      version: 1, created_at: at(1), updated_at: at(1), source: {type: 'human', surface: 'personal-notes'}, leader: null,
      pinned_at: null, snoozed_until: null, merged_into: null, merged_from: [], last_discussed_at: null, reviewed_at: null,
      summaries: [], discussions: {count: 0, last_at: null, session_id: null, last_seq: null}, ...patch};
  }
  notes = [
    make({id: 'pin-sidebar', title: 'Leader 栏放任务，不放组员', text: '组员随派工增减，任务生命周期清楚，更适合当导航。', tags: ['产品', '体验'], created_at: at(9), updated_at: at(2), pinned_at: at(1),
      stage: 'discussed', last_discussed_at: at(2), discussions: {count: 2, last_at: at(2), session_id: 'assistant', last_seq: 40},
      summaries: [{id: 's1', text: '先在侧栏试点任务导航，组员入口放进任务详情。', at: at(2), source: {type: 'assistant', session_id: 'assistant', seq: 40}}]}),
    make({id: 'stale-qq', text: 'QQ 通知合并成每日摘要', tags: ['通知'], created_at: at(30), updated_at: at(21)}),
    make({id: 'stale-pdf', text: '任务报告支持导出 PDF\n给不用 Crew 的同事看。', tags: ['产品'], project: 'crew', created_at: at(25), updated_at: at(18)}),
    make({id: 'stale-search', text: '会话库支持全文搜索', tags: [], created_at: at(40), updated_at: at(16)}),
    make({id: 'wall', title: '想法墙', text: '卡片能维护，有自定义标签，“去聊聊”直接进全局助手。', tags: ['产品', 'AI'], created_at: today(30), updated_at: today(20),
      source: {source: 'human', message_id: 'm1', session_id: 'assistant', seq: 12, verified_by: 'session-journal'}}),
    make({id: 'git-cards', text: 'Git 页分支做成卡片', tags: ['体验'], project: 'crew', created_at: today(40), updated_at: today(40)}),
    make({id: 'git-cards-2', text: 'Git 页的分支做成卡片', tags: [], project: 'crew', created_at: at(3), updated_at: at(3)}),
    make({id: 'handoff', text: '组员会话断了自动交接\n原会话找不到时明确告诉我，新开会话并交接背景。', tags: ['稳定性'], project: 'crew', created_at: at(4), updated_at: at(4)}),
    make({id: 'long', text: '切页要有翻页动画\n硬切让人不知道发生了什么。\n第三行补充\n第四行补充\n第五行补充\n第六行补充\n第七行会被收起\n第八行也被收起', tags: ['体验'], created_at: at(8), updated_at: at(8)}),
    make({id: 'archived-old', text: '换一个更亮的主题色', tags: ['体验'], state: 'archived', created_at: at(60), updated_at: at(50)}),
    make({id: 'promoted-old', text: '工作区首页显示本周交付', tags: ['产品'], state: 'archived', stage: 'promoted', project: 'crew', created_at: at(20), updated_at: at(10),
      leader: {project: 'crew', session_id: 'leader-weekly', status: 'completed', error: null, source: {}}}),
  ];
  const registered = ['产品', '体验', '通知', '稳定性', 'AI'];
  registered.forEach((name, index) => tags.set(name, {name, color: palette[(index * 3) % 10], pinned: index < 2, sort_order: index, version: 1}));

  const byId = id => notes.find(note => note.id === id);
  const conflict = () => Object.assign(Error('个人想法已变化，请刷新后重试'), {status: 409});
  const bad = message => Object.assign(Error(message), {status: 400});
  function stale(note) {
    if (note.state !== 'open' || note.stage === 'promoted') return false;
    if (note.snoozed_until && Date.parse(note.snoozed_until) > now()) return false;
    const idle = Math.max(...[note.updated_at, note.last_discussed_at, note.reviewed_at].filter(Boolean).map(Date.parse));
    return idle < now() - STALE_DAYS * DAY;
  }
  const view = note => ({...note, stale: stale(note)});
  function register(names) { for (const name of names) if (!tags.has(name)) tags.set(name, {name, color: colorFor(name), pinned: false, sort_order: tags.size, version: 1}); }
  function check(note, version) { if (!note) throw bad('个人想法不存在'); if (note.version !== version) throw conflict(); }
  function touch(note, changes, {updated = true} = {}) { Object.assign(note, changes, {version: note.version + 1}); if (updated) note.updated_at = new Date(now()).toISOString(); }
  const cleanTags = list => [...new Set(list.map(name => String(name).trim()).filter(Boolean))];

  function noteAction(action, data) {
    const note = byId(data.note_id);
    if (action === 'capture') {
      if (!String(data.text || '').trim()) throw bad('想法内容不能为空');
      const created = make({id: crypto.randomUUID(), text: data.text, title: data.title || null, tags: cleanTags(data.tags || []), project: data.project ?? null,
        created_at: new Date(now()).toISOString(), updated_at: new Date(now()).toISOString(), source: {type: 'human', surface: 'notes-wall'}});
      notes.push(created); register(created.tags); return {ok: true, note: view(created)};
    }
    if (action === 'merge') {
      const primary = byId(data.primary_id), others = data.note_ids.map(byId);
      check(primary, data.expected_versions[primary.id]); for (const other of others) check(other, data.expected_versions[other?.id]);
      const appended = others.map(other => `[${other.id}] ${other.text}`).join('\n');
      touch(primary, {text: primary.text + '\n\n合并来源：\n' + appended, tags: cleanTags([...primary.tags, ...others.flatMap(other => other.tags)]), merged_from: [...primary.merged_from, ...others.map(other => other.id)]});
      for (const other of others) touch(other, {state: 'archived', merged_into: primary.id});
      return {ok: true, note: view(primary)};
    }
    if (action === 'bulk') {
      const list = data.note_ids.map(byId);
      for (const item of list) check(item, data.expected_versions[item?.id]);
      for (const item of list) {
        if (data.op === 'tag_add') touch(item, {tags: cleanTags([...item.tags, data.value])});
        else if (data.op === 'tag_remove') touch(item, {tags: item.tags.filter(tag => tag !== data.value)});
        else if (data.op === 'archive') touch(item, {state: 'archived'});
        else if (data.op === 'project') touch(item, {project: data.value});
        else throw bad('未知批量操作');
      }
      if (data.op === 'tag_add') register([data.value]);
      return {ok: true, notes: list.map(view)};
    }
    if (action === 'annotate') {
      if (!note) throw bad('个人想法不存在');
      note.summaries = [...note.summaries, {id: 'sum-' + ++serial, text: data.text, at: new Date(now()).toISOString(), source: {type: 'human'}}];
      note.version += 1; return {ok: true, note: view(note)};
    }
    check(note, data.expected_version);
    if (action === 'update') {
      const changes = {};
      for (const key of ['text', 'title', 'project', 'state']) if (Object.hasOwn(data, key)) changes[key] = data[key];
      if (Object.hasOwn(data, 'tags')) { changes.tags = cleanTags(data.tags); register(changes.tags); }
      touch(note, changes);
    } else if (action === 'pin') touch(note, {pinned_at: data.pinned ? new Date(now()).toISOString() : null}, {updated: false});
    else if (action === 'snooze') touch(note, {snoozed_until: data.until}, {updated: false});
    else if (action === 'keep') touch(note, {reviewed_at: new Date(now()).toISOString()}, {updated: false});
    else if (action === 'archive') touch(note, {state: 'archived'});
    else if (action === 'restore') touch(note, {state: 'open'});
    else if (action === 'unmerge') {
      const primary = byId(note.merged_into);
      touch(note, {state: 'open', merged_into: null});
      touch(primary, {merged_from: primary.merged_from.filter(id => id !== note.id)}, {updated: false});
    } else throw bad('未知想法操作：' + action);
    return {ok: true, note: view(note)};
  }

  function tagAction(action, data) {
    const tag = tags.get(data.name), affected = name => notes.filter(note => note.tags.includes(name)).length;
    const versioned = () => { if (!tag) throw bad('标签不存在'); if (tag.version !== data.expected_version) throw Object.assign(Error('标签已变化，请刷新后重试'), {status: 409}); };
    if (action === 'create') { if (tags.has(data.name)) throw Object.assign(Error('标签已存在'), {status: 409}); register([data.name]); if (data.color) tags.get(data.name).color = data.color; return {ok: true, tag: tags.get(data.name)}; }
    if (action === 'reorder') { data.names.forEach((name, index) => { tags.get(name).sort_order = index; tags.get(name).version += 1; }); return {ok: true}; }
    if (action === 'merge') {
      for (const from of data.from) {
        for (const note of notes) if (note.tags.includes(from)) touch(note, {tags: cleanTags(note.tags.map(name => name === from ? data.into : name))});
        tags.delete(from);
      }
      return {ok: true, tag: tags.get(data.into)};
    }
    versioned();
    if (action === 'recolor') { if (!palette.includes(data.color)) throw bad('颜色必须是 c0–c9'); tag.color = data.color; }
    else if (action === 'pin') tag.pinned = Boolean(data.pinned);
    else if (action === 'rename') {
      if (tags.has(data.new_name)) return {status: 409, body: {error: '标签已存在，需要合并', merge_required: true, target: data.new_name, affected: affected(data.name)}};
      for (const note of notes) if (note.tags.includes(data.name)) touch(note, {tags: note.tags.map(name => name === data.name ? data.new_name : name)});
      tags.delete(data.name); tags.set(data.new_name, {...tag, name: data.new_name});
    } else if (action === 'remove') {
      for (const note of notes) if (note.tags.includes(data.name)) touch(note, {tags: note.tags.filter(name => name !== data.name)});
      tags.delete(data.name); return {ok: true};
    } else throw bad('未知标签操作：' + action);
    tag.version += 1;
    return {ok: true, tag: tags.get(data.new_name || data.name)};
  }

  const assistant = {session_id: 'assistant', crew_name: '助手', name: '助手', runtime: 'claude', model: null, effort: null, running: true, busy: false, activity: null,
    applied: null, context: null, last_seq: 0, web_chat: true, native_handoff: null, where: 'bg', status: 'idle', source: 'crew', chat_unread: 0,
    goals: [], active_goal_id: null, active_goal_ids: [], delegation: [], subagents: [], configured: true};
  function idempotent(body, run) {
    const key = JSON.stringify([body.action, body.data]);
    if (receipts.has(body.request_id)) {
      const previous = receipts.get(body.request_id);
      if (previous.key !== key) throw Object.assign(Error('此操作标识已经用于不同的想法操作'), {status: 409});
      return {...previous.result, duplicate: true};
    }
    const result = run();
    receipts.set(body.request_id, {key, result}); return result;
  }

  // Returns {status, body} or null when the path is not an API route.
  function handle(method, url, body) {
    const name = url.pathname;
    calls.push({method, name, query: Object.fromEntries(url.searchParams), body});
    if (failNext && failNext.path === name) { const failure = failNext; failNext = null; return {status: failure.status, body: {error: failure.error}}; }
    if (name === '/api/access/context') return {status: 200, body: {remote: false, local_controls: true, local_desktop: true, authenticated: true}};
    if (name === '/api/projects') return {status: 200, body: {projects}};
    if (name === '/api/personal/notes' && method === 'GET') {
      const archived = url.searchParams.get('include_archived') === '1', project = url.searchParams.get('project');
      return {status: 200, body: {notes: notes.filter(note => (archived || note.state === 'open') && (!project || note.project === project)).map(view), stale_days: STALE_DAYS}};
    }
    if (name === '/api/personal/notes') {
      if (typeof body.request_id !== 'string' || !body.request_id || typeof body.data !== 'object') throw bad('请求必须是 {action, data, request_id}');
      return {status: 200, body: idempotent(body, () => noteAction(body.action, body.data))};
    }
    if (name === '/api/personal/tags' && method === 'GET') {
      register(notes.flatMap(note => note.tags));
      return {status: 200, body: {tags: [...tags.values()].map(tag => ({...tag, count: notes.filter(note => note.state === 'open' && note.tags.includes(tag.name)).length})), palette}};
    }
    if (name === '/api/personal/tags') {
      if (typeof body.request_id !== 'string' || typeof body.data !== 'object') throw bad('请求必须是 {action, data, request_id}');
      const result = tagAction(body.action, body.data);
      return result.status ? result : {status: 200, body: result};
    }
    if (name === '/api/personal/notes/start') {
      const note = byId(body.note_id);
      if (!projects.some(project => project.name === body.project)) throw bad('工作区不存在');
      const session_id = 'leader-' + note.id;
      touch(note, {leader: {project: body.project, session_id, status: 'completed', error: null, source: {}}, stage: 'promoted', state: 'archived'});
      return {status: 200, body: {ok: true, note: view(note), session_id, project: body.project, duplicate: false}};
    }
    if (name === '/api/assistant/open') return {status: 200, body: {ok: true}};
    if (name === '/api/assistant/state') return {status: 200, body: {scope: 'personal', project: null, now: new Date(now()).toISOString(), assistant, leaders: [], members: [assistant], projects,
      todos: [], todo_count: 0, goals: [], ai: {}, sounds: null, models: [], efforts: [], decisions: [], attention: [], native_approvals: [], agents_error: null, overview: {}}};
    if (name === '/api/chat/history') return {status: 200, body: {events: [{seq: 1, type: 'text', id: 'hello', text: '需要我帮你理一理哪条想法？'}], busy: false, read_seq: 1, first_seq: 1, has_more: false}};
    if (name === '/api/chat/read') return {status: 200, body: {read_seq: body.seq}};
    if (name === '/api/chat/send') { sent.push(body); return {status: 200, body: {ok: true, accepted: true, message_id: body.message_id}}; }
    return {status: 404, body: {error: 'Unexpected fixture route: ' + name}};
  }
  return {handle, notes: () => notes, tags: () => [...tags.values()], sent, calls, failOnce: (path, status, error) => { failNext = {path, status, error}; }};
}

module.exports = {createNotesApi};
