'use strict';
const { test } = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');

const window = {};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../frontend/settings-ui.js'), 'utf8'), { window });
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/home-onboarding.js'), 'utf8'), { window });
const raw = window.CrewOnboarding;
// Objects made inside the vm context have its own prototypes; compare their plain values.
const plain = value => JSON.parse(JSON.stringify(value));
const guide = { ...raw, ...Object.fromEntries(['status', 'initialDraft', 'changeRuntime', 'changeModel'].map(name => [name, (...args) => plain(raw[name](...args))])) };

const catalog = [
  { id: 'claude', name: 'Claude Code', available: true, models: [{ id: 'opus', name: 'Opus', efforts: ['high'] }] },
  { id: 'codex', name: 'Codex', available: true, models: [
    { id: 'fast-model', name: 'Fast', efforts: ['low', 'high'], speeds: [{ id: 'priority', name: 'Priority', description: '更快，用量翻倍' }] },
    { id: 'plain-model', name: 'Plain', efforts: ['low'], speeds: [] }] }];
const ready = (id, extra) => ({ id, name: id === 'claude' ? 'Claude Code' : 'Codex', installed: true, logged_in: true, detail: null, error: null, ...extra });
const state = extra => ({ step: 0, checking: false, readiness: [ready('claude'), ready('codex')], readinessError: '', catalog, snapshot: null, settingsError: '',
  draft: { runtime: 'codex', model: null, effort: null, speed: null }, mode: 'system', fontScale: 1, saving: false, error: '', closeFailed: false, ...extra });

test('the guide opens by itself only on this computer\'s first start', () => {
  const first = { remote: false, projects: 0, pendingAction: null, status: 'pending' };
  assert.equal(guide.shouldOpen(first), true);
  for (const change of [{ remote: true }, { projects: 1 }, { pendingAction: 'new' }, { status: 'completed' }, { status: 'skipped' }])
    assert.equal(guide.shouldOpen({ ...first, ...change }), false, JSON.stringify(change));
});

test('each tool shows its real state and only signed-in, listed tools can be the default brain', () => {
  assert.deepEqual(guide.status(ready('claude', { detail: '登录方式 claude.ai' })), { tone: 'ok', label: '已登录', hint: '登录方式 claude.ai' });
  assert.deepEqual(guide.status(ready('codex', { logged_in: false })), { tone: 'warn', label: '未登录', hint: '在终端运行 codex login，登录后点“重新检测”。' });
  assert.equal(guide.status(ready('claude', { logged_in: false })).hint, '在终端运行 claude，按提示登录后点“重新检测”。');
  assert.equal(guide.status(ready('codex', { installed: false, logged_in: null })).label, '未安装');
  assert.deepEqual(guide.status(ready('codex', { logged_in: null, error: '登录状态检测失败：超时' })), { tone: 'error', label: '检测失败', hint: '登录状态检测失败：超时' });
  assert.equal(guide.usable(ready('codex'), catalog), true);
  assert.equal(guide.usable(ready('codex', { logged_in: false }), catalog), false);
  assert.equal(guide.usable(ready('codex', { installed: false }), catalog), false);
  // An unknown sign-in state does not hide a tool the catalog lists; its reason stays on the card.
  assert.equal(guide.usable(ready('codex', { logged_in: null, error: 'x' }), catalog), true);
  assert.equal(guide.usable(ready('codex'), [{ id: 'codex', available: false }]), false);
});

test('the draft keeps a saved brain, picks the only ready tool, and leaves a real choice to the user', () => {
  const none = { runtime: null, model: null, effort: null, speed: null };
  const saved = { runtime: 'claude', model: 'opus', effort: 'high', speed: null };
  assert.deepEqual(guide.initialDraft(saved, [ready('claude'), ready('codex')], catalog), saved);
  assert.deepEqual(guide.initialDraft(none, [ready('claude', { logged_in: false }), ready('codex')], catalog), { runtime: 'codex', model: null, effort: null, speed: null });
  assert.deepEqual(guide.initialDraft(none, [ready('claude'), ready('codex')], catalog), none);
  assert.deepEqual(guide.initialDraft(none, [ready('claude', { installed: false }), ready('codex', { logged_in: false })], catalog), none);
});

test('changing tool or model follows the settings page rules for effort and speed', () => {
  const codex = catalog[1], speeds = window.CrewSettings.speedChoices;
  const draft = { runtime: 'codex', model: 'fast-model', effort: 'high', speed: 'priority' };
  assert.deepEqual(guide.changeRuntime(draft, 'claude'), { runtime: 'claude', model: null, effort: null, speed: null });
  assert.equal(raw.changeRuntime(draft, 'codex'), draft);
  assert.deepEqual(guide.changeModel(draft, codex, 'plain-model', speeds), { runtime: 'codex', model: 'plain-model', effort: null, speed: null });
  assert.deepEqual(guide.changeModel({ ...draft, effort: 'low', speed: 'default' }, codex, 'plain-model', speeds), { runtime: 'codex', model: 'plain-model', effort: 'low', speed: 'default' });
  assert.deepEqual(guide.changeModel(draft, codex, '', speeds), { runtime: 'codex', model: null, effort: 'high', speed: null });
});

test('step one shows every tool card, the brain fields and the real reasons, escaped', () => {
  const html = guide.markup(state({ readiness: [ready('claude', { logged_in: null, error: '<b>坏了</b>' }), ready('codex', { detail: 'Logged in using ChatGPT' })],
    draft: { runtime: 'codex', model: 'fast-model', effort: null, speed: 'priority' } }));
  assert.match(html, /第 1 步 · 共 4 步/);
  assert.match(html, /data-runtime="codex" aria-checked="true"/);
  assert.match(html, /&lt;b&gt;坏了&lt;\/b&gt;/);
  assert.doesNotMatch(html, /<b>坏了/);
  assert.match(html, /<option value="fast-model" selected>Fast<\/option>/);
  assert.match(html, /data-speed="" aria-checked="false">跟随 Codex</);
  assert.match(html, /data-speed="default" aria-checked="false">标准</);
  assert.match(html, /data-speed="priority" aria-checked="true">快速</);
  assert.match(html, /更快，用量翻倍/);
  assert.doesNotMatch(html, /undefined|NaN|null/);
  const claude = guide.markup(state({ draft: { runtime: 'claude', model: null, effort: null, speed: null } }));
  assert.match(claude, /速度档位只适用于 Codex/);
  assert.doesNotMatch(claude, /data-ob-act="speed"/);
});

test('without a usable tool the guide says how to get one and still lets the user move on', () => {
  const html = guide.markup(state({ readiness: [ready('claude', { installed: false, logged_in: null }), ready('codex', { logged_in: false })], draft: { runtime: null, model: null, effort: null, speed: null } }));
  assert.match(html, /还没有可用的执行载体/);
  assert.equal((html.match(/aria-disabled="true"/g) || []).length, 2);
  assert.match(html, /data-ob-act="next"/);
  const failed = guide.markup(state({ readiness: null, readinessError: 'Crew 服务返回 500', catalog: [], settingsError: '运维响应无法读取' }));
  assert.match(failed, /载体检测失败：Crew 服务返回 500/);
  assert.match(failed, /读取 AI 设置失败：运维响应无法读取/);
  assert.match(guide.markup(state({ checking: true, readiness: null })), /正在检测本机的 Claude Code 和 Codex/);
});

test('appearance, features and the workspace step carry their choices; the last step finishes', () => {
  const appearance = guide.markup(state({ step: 1, mode: 'dark', fontScale: 1.2 }));
  assert.match(appearance, /data-mode="dark" aria-checked="true"/);
  assert.match(appearance, /120%/);
  assert.match(appearance, /value="1.2" data-ob-field="font"/);
  assert.doesNotMatch(appearance, /style=/);
  assert.match(guide.markup(state({ step: 1, fontScale: 1 })), /data-ob-act="font-reset" disabled/);
  const features = guide.markup(state({ step: 2 }));
  for (const name of ['多载体混搭', '上下文压缩小环', '离开模式远程提醒', '拍板与自主推进']) assert.match(features, new RegExp(name));
  const workspace = guide.markup(state({ step: 3 }));
  assert.match(workspace, /data-ob-act="open-existing"/);
  assert.match(workspace, /data-ob-act="new-folder"/);
  assert.match(workspace, /不需要 Git/);
  assert.match(workspace, /「自己搭」.*「助手开局」/);
  assert.match(workspace, /data-ob-act="finish"[^>]*>完成引导/);
  assert.match(workspace, /data-step="2" data-done="true"/);
});

test('a failed save keeps the real reason and offers an honest way out', () => {
  const html = guide.markup(state({ error: '没能记下引导状态：后台尚未连接', closeFailed: true }));
  assert.match(html, /role="alert">没能记下引导状态：后台尚未连接/);
  assert.match(html, /data-ob-act="dismiss"[^>]*>先关闭，下次启动再提醒/);
  assert.match(guide.markup(state({ saving: true })), /data-ob-act="next" disabled>正在保存…/);
});
