'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { EventEmitter } = require('node:events');
const { spawnSync } = require('node:child_process');
const { CrewSelfUpdate, installKind, parseRelease, parseSums, checkAssetUrl, MAC_HELPER } = require('../src/crew-self-update.cjs');

const DOWNLOAD = 'https://github.com/akitokunnn/Crew/releases/download/v0.4.0/';
const PACKAGE = Buffer.from('new crew installer bytes');
const sha = value => crypto.createHash('sha256').update(value).digest('hex');

function temporary(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'crew-self-update-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  return directory;
}
function release(overrides = {}) {
  const asset = name => ({ name, size: name === 'SHA256SUMS.txt' ? 200 : PACKAGE.length, browser_download_url: DOWNLOAD + name });
  return { tag_name: 'v0.4.0', draft: false, prerelease: false, published_at: '2026-11-21T08:00:00Z', body: '修复若干问题',
    assets: [asset('Crew-0.4.0-setup.exe'), asset('Crew-0.4.0-mac-arm64.zip'), asset('SHA256SUMS.txt')], ...overrides };
}
// Serves the release API and its assets the way GitHub does: the download path redirects to the asset host.
function github({ latest = release(), bytes = PACKAGE, sums, redirect = 'https://release-assets.githubusercontent.com/blob' } = {}) {
  const requests = [];
  const fetchImpl = async (url, options) => {
    requests.push(url);
    if (url === 'https://api.github.com/repos/akitokunnn/Crew/releases/latest') {
      assert.equal(options.redirect, 'error');
      return new Response(JSON.stringify(latest), { status: 200 });
    }
    assert.equal(options.redirect, 'manual');
    if (url.startsWith(DOWNLOAD)) return new Response(null, { status: 302, headers: { location: redirect + '?name=' + url.slice(DOWNLOAD.length) } });
    const name = new URL(url).searchParams.get('name');
    if (name === 'SHA256SUMS.txt') return new Response(sums ?? sha(PACKAGE) + '  Crew-0.4.0-setup.exe\n' + sha(PACKAGE) + '  Crew-0.4.0-mac-arm64.zip\n', { status: 200 });
    return new Response(bytes, { status: 200, headers: { 'content-length': String(bytes.length) } });
  };
  return { fetchImpl, requests };
}
function child() {
  const value = new EventEmitter(); value.unref = () => { value.unrefed = true; };
  setImmediate(() => value.emit('spawn'));
  return value;
}
function updater(t, options = {}) {
  const home = options.home || temporary(t), events = { spawned: [], quits: 0, drained: [] };
  const manager = new CrewSelfUpdate({ home, version: '0.3.17', install: options.install || { kind: 'installer', directory: 'C:\\Crew', executable: 'C:\\Crew\\Crew.exe' },
    fetchImpl: options.fetchImpl || github().fetchImpl, pid: 4242, readyTimeout: 1,
    run: options.run || (async () => { throw new Error('unexpected command'); }),
    spawnProcess: (command, args, spawnOptions) => { events.spawned.push({ command, args, spawnOptions }); return child(); },
    prepareQuit: async () => { const value = options.drain ? options.drain() : { ok: true, backup: { path: 'backup.db' }, cancel: async () => { events.drained.push('cancel'); } }; return value; },
    quit: () => { events.quits++; }, showItem: file => { events.shown = file; } });
  t.after(() => manager.close());
  return { manager, home, events };
}

test('install kinds follow how this copy of Crew was installed', () => {
  assert.deepEqual(installKind({ packaged: false }), { kind: 'development' });
  const mac = { packaged: true, platform: 'darwin', arch: 'arm64', execPath: '/Applications/Crew.app/Contents/MacOS/Crew' };
  assert.deepEqual(installKind({ ...mac, canWrite: () => true }), { kind: 'mac', app: '/Applications/Crew.app' });
  assert.equal(installKind({ ...mac, canWrite: target => target !== '/Applications' }).kind, 'mac-manual');
  assert.equal(installKind({ ...mac, execPath: '/private/var/folders/x/AppTranslocation/1/d/Crew.app/Contents/MacOS/Crew', canWrite: () => true }).kind, 'mac-manual');
  assert.equal(installKind({ ...mac, arch: 'x64' }).kind, 'unsupported');
  const windows = { packaged: true, platform: 'win32', execPath: 'C:\\Users\\a\\AppData\\Local\\Programs\\Crew\\Crew.exe' };
  assert.equal(installKind({ ...windows, env: { PORTABLE_EXECUTABLE_FILE: 'D:\\Crew.exe' } }).kind, 'portable');
  assert.equal(installKind({ ...windows, env: {}, exists: file => file.endsWith('Uninstall Crew.exe') }).kind, 'installer');
  assert.equal(installKind({ ...windows, env: {}, exists: () => false }).kind, 'zip');
});

test('only formal releases with assets from their own download path are accepted', () => {
  const parsed = parseRelease(release({ assets: [...release().assets, { name: 'Crew-0.4.0-win-x64.zip', size: 9, browser_download_url: 'https://example.com/Crew-0.4.0-win-x64.zip' }] }));
  assert.equal(parsed.version, '0.4.0'); assert.equal(parsed.url, 'https://github.com/akitokunnn/Crew/releases/tag/v0.4.0');
  assert.deepEqual(Object.keys(parsed.assets).sort(), ['Crew-0.4.0-mac-arm64.zip', 'Crew-0.4.0-setup.exe', 'SHA256SUMS.txt']);
  assert.throws(() => parseRelease(release({ prerelease: true })), /正式发布/);
  assert.throws(() => parseRelease(release({ draft: true })), /正式发布/);
  assert.throws(() => parseRelease(release({ tag_name: 'latest' })), /版本标签无效/);
  assert.deepEqual([...parseSums('A'.repeat(64) + '  one.zip\r\n' + 'b'.repeat(64) + ' *two.exe\nnoise')], [['one.zip', 'a'.repeat(64)], ['two.exe', 'b'.repeat(64)]]);
  assert.equal(checkAssetUrl(DOWNLOAD + 'x.zip', true), DOWNLOAD + 'x.zip');
  assert.equal(checkAssetUrl('https://objects.githubusercontent.com/x', false), 'https://objects.githubusercontent.com/x');
  for (const [url, first] of [['https://objects.githubusercontent.com/x', true], ['http://github.com/akitokunnn/Crew/releases/download/v1/x', true],
    ['https://github.com/someone/Crew/releases/download/v1/x', true], ['https://evil.example/x', false], ['https://u:p@github.com/akitokunnn/Crew/releases/download/v1/x', true]]) {
    assert.throws(() => checkAssetUrl(url, first), /不属于/);
  }
});

test('a newer release downloads through GitHub redirects, verifies SHA-256 and waits for idle work before restarting', async t => {
  const reasons = [['leader 正在回复']];
  const f = updater(t, { drain: () => reasons.length ? { ok: false, reasons: reasons.shift() } : { ok: true, backup: { path: 'backup.db' }, cancel: async () => {} } });
  const checked = await f.manager.check();
  assert.equal(checked.phase, 'available'); assert.equal(checked.latest.version, '0.4.0'); assert.equal(checked.can_download, true);
  const ready = await f.manager.fetchUpdate();
  assert.equal(ready.phase, 'ready'); assert.equal(ready.error, null);
  assert.equal(fs.readFileSync(ready.file, 'utf8'), PACKAGE.toString());
  assert.equal(ready.restart, true);
  const blocked = await f.manager.installUpdate();
  assert.equal(blocked.phase, 'ready'); assert.deepEqual(blocked.blocked, ['leader 正在回复']);
  assert.equal(f.events.spawned.length, 0); assert.equal(f.events.quits, 0);
  await f.manager.installUpdate();
  assert.equal(f.events.quits, 1);
  const [helper] = f.events.spawned;
  assert.equal(helper.command, 'powershell.exe'); assert.equal(helper.spawnOptions.detached, true);
  assert.equal(helper.spawnOptions.env.CREW_UPDATE_PID, '4242'); assert.equal(helper.spawnOptions.env.CREW_UPDATE_SETUP, ready.file);
  const pending = JSON.parse(fs.readFileSync(path.join(f.home, 'desktop', 'updates', 'pending.json'), 'utf8'));
  assert.deepEqual([pending.from, pending.to, pending.kind], ['0.3.17', '0.4.0', 'installer']);
  const journal = fs.readFileSync(path.join(f.home, 'desktop', 'logs', 'crew-self-update.jsonl'), 'utf8');
  assert.match(journal, /"event":"downloaded"/); assert.match(journal, /"event":"install-start"/);
});

test('a checksum mismatch, oversized body or foreign redirect discards the download', async t => {
  for (const [options, message] of [[{ sums: 'f'.repeat(64) + '  Crew-0.4.0-setup.exe\n' }, /SHA-256/],
    [{ sums: '' }, /校验清单里没有/], [{ redirect: 'https://evil.example/blob' }, /不属于/], [{ bytes: Buffer.concat([PACKAGE, PACKAGE]) }, /大小/]]) {
    const f = updater(t, { fetchImpl: github(options).fetchImpl });
    await f.manager.check();
    const result = await f.manager.fetchUpdate();
    assert.equal(result.phase, 'error'); assert.match(result.error, message);
    assert.equal(fs.existsSync(path.join(f.home, 'desktop', 'updates', '0.4.0')), false);
  }
});

test('older or missing releases and manual install kinds never restart Crew', async t => {
  const same = updater(t, { fetchImpl: github({ latest: release({ tag_name: 'v0.3.17' }) }).fetchImpl });
  assert.equal((await same.manager.check()).phase, 'latest');
  const zip = updater(t, { install: { kind: 'zip', directory: 'C:\\Crew' } });
  assert.equal((await zip.manager.check()).can_download, false);
  const missing = updater(t, { fetchImpl: async () => new Response('{}', { status: 404 }) });
  const none = await missing.manager.check();
  assert.equal(none.phase, 'latest'); assert.match(none.message, /还没有正式发布/);
  const portable = updater(t, { install: { kind: 'portable', file: 'D:\\Crew.exe' },
    fetchImpl: github({ latest: release({ assets: [{ name: 'Crew-0.4.0-portable.exe', size: PACKAGE.length, browser_download_url: DOWNLOAD + 'Crew-0.4.0-portable.exe' }, release().assets[2]] }),
      sums: sha(PACKAGE) + '  Crew-0.4.0-portable.exe\n' }).fetchImpl });
  await portable.manager.check();
  const ready = await portable.manager.fetchUpdate();
  assert.equal(ready.restart, false);
  await portable.manager.installUpdate();
  assert.equal(portable.events.shown, ready.file); assert.equal(portable.events.quits, 0); assert.equal(portable.events.spawned.length, 0);
});

test('a Mac package is unpacked and its signature, bundle id and version are checked before it is ready', async t => {
  const commands = [];
  const run = async (command, args) => {
    commands.push([command, args[0]]);
    if (command === 'ditto') { fs.mkdirSync(path.join(args[3], 'Crew.app', 'Contents'), { recursive: true }); return { stdout: '' }; }
    if (command === 'plutil') return { stdout: args[1] === 'CFBundleIdentifier' ? 'local.crew.desktop\n' : '0.4.0\n' };
    return { stdout: '' };
  };
  const f = updater(t, { run, install: { kind: 'mac', app: '/Applications/Crew.app' } });
  await f.manager.check();
  const ready = await f.manager.fetchUpdate();
  assert.equal(ready.phase, 'ready', ready.error);
  assert.deepEqual(commands.map(item => item[0]), ['ditto', 'codesign', 'plutil', 'plutil']);
  await f.manager.installUpdate();
  const [helper] = f.events.spawned;
  assert.equal(helper.command, '/bin/sh');
  assert.deepEqual(helper.args.slice(1, 6), ['4242', '/Applications/Crew.app', path.join(f.home, 'desktop', 'updates', '0.4.0', 'staging', 'Crew.app'),
    path.join(f.home, 'desktop', 'updates', '0.4.0', 'install'), '0.4.0']);
  const wrong = updater(t, { run: async (command, args) => command === 'plutil' && args[1] === 'CFBundleShortVersionString' ? { stdout: '0.3.9' } : run(command, args),
    install: { kind: 'mac', app: '/Applications/Crew.app' } });
  await wrong.manager.check();
  assert.match((await wrong.manager.fetchUpdate()).error, /版本号是 0\.3\.9/);
});

test('after a restart the new version confirms itself, an unapplied update is reported and a verified download stays ready', async t => {
  const home = temporary(t), updates = path.join(home, 'desktop', 'updates');
  fs.mkdirSync(path.join(updates, '0.3.17', 'install'), { recursive: true });
  fs.mkdirSync(path.join(updates, '0.3.16'), { recursive: true });
  fs.writeFileSync(path.join(updates, 'pending.json'), JSON.stringify({ from: '0.3.16', to: '0.3.17', kind: 'mac' }));
  const applied = updater(t, { home, install: { kind: 'mac', app: '/Applications/Crew.app' } });
  const state = await applied.manager.initialize();
  assert.deepEqual(state.updated, { from: '0.3.16', to: '0.3.17' });
  assert.equal(fs.readFileSync(path.join(updates, '0.3.17', 'install', 'started'), 'utf8'), '4242');
  await applied.manager.confirmReady(true);
  assert.ok(fs.existsSync(path.join(updates, '0.3.17', 'install', 'ready')), 'the helper still needs the ready marker');
  assert.equal(fs.existsSync(path.join(updates, 'pending.json')), false);
  assert.equal(fs.existsSync(path.join(updates, '0.3.16')), false);

  fs.writeFileSync(path.join(updates, 'pending.json'), JSON.stringify({ from: '0.3.17', to: '0.4.0', kind: 'mac' }));
  const failed = updater(t, { home, install: { kind: 'mac', app: '/Applications/Crew.app' } });
  const reported = await failed.manager.initialize();
  assert.equal(reported.phase, 'error'); assert.match(reported.error, /0\.4\.0 没有完成/);

  const first = updater(t, { home });
  await first.manager.check(); await first.manager.fetchUpdate();
  const restored = await updater(t, { home }).manager.initialize();
  assert.equal(restored.phase, 'ready'); assert.equal(restored.latest.version, '0.4.0');
});

test('the Mac helper swaps the bundle, keeps the old one until ready, and rolls back when the new version never reports', { skip: process.platform === 'win32' }, async t => {
  const root = temporary(t), bin = path.join(root, 'bin');
  fs.mkdirSync(bin);
  // A stand-in for macOS `open` that records what was opened.
  fs.writeFileSync(path.join(bin, 'open'), '#!/bin/sh\nprintf "%s|" "$@" >> "$OPEN_LOG"; cat "$1/version" >> "$OPEN_LOG"; echo >> "$OPEN_LOG"\n', { mode: 0o755 });
  const script = path.join(root, 'replace.sh');
  fs.writeFileSync(script, MAC_HELPER, { mode: 0o700 });
  function bundle(file, version) { fs.mkdirSync(file, { recursive: true }); fs.writeFileSync(path.join(file, 'version'), version); }
  const exited = spawnSync(process.execPath, ['-e', 'process.pid']).pid;
  function run(name, before) {
    const app = path.join(root, name, 'Crew.app'), staging = path.join(root, name, 'staging', 'Crew.app'), work = path.join(root, name, 'work');
    bundle(app, 'old'); bundle(staging, 'new'); fs.mkdirSync(work);
    before?.(work);
    const log = path.join(root, name, 'open.log'), journal = path.join(root, name, 'journal.jsonl');
    const result = spawnSync('/bin/sh', [script, String(exited), app, staging, work, '0.4.0', journal, '1', '--home', '/tmp/h'],
      { env: { ...process.env, PATH: bin + ':' + process.env.PATH, OPEN_LOG: log }, encoding: 'utf8' });
    return { app, result, opened: fs.readFileSync(log, 'utf8').trim().split('\n'), journal: fs.readFileSync(journal, 'utf8') };
  }
  const success = run('success', work => fs.writeFileSync(path.join(work, 'ready'), 'now'));
  assert.equal(success.result.status, 0, success.result.stderr);
  assert.equal(fs.readFileSync(path.join(success.app, 'version'), 'utf8'), 'new');
  assert.equal(fs.existsSync(success.app + '.previous'), false);
  assert.deepEqual(success.opened, [success.app + '|--args|--home|/tmp/h|new']);
  assert.match(success.journal, /"event":"swapped"/); assert.match(success.journal, /"event":"done"/);

  // The new version reported its pid but never became ready: it is stopped before the old one returns.
  const { spawn } = require('node:child_process');
  const hung = spawn('sleep', ['30'], { stdio: 'ignore' });
  t.after(() => hung.kill('SIGKILL'));
  const exitedHung = new Promise(resolve => hung.once('exit', resolve));
  const rollback = run('rollback', work => fs.writeFileSync(path.join(work, 'started'), String(hung.pid)));
  assert.equal(rollback.result.status, 1);
  assert.equal(await exitedHung, null);
  assert.equal(fs.readFileSync(path.join(rollback.app, 'version'), 'utf8'), 'old');
  assert.equal(fs.existsSync(rollback.app + '.previous'), false);
  assert.deepEqual(rollback.opened.map(line => line.split('|').at(-1)), ['new', 'old']);
  assert.match(rollback.journal, /"event":"rolled-back","version":"0.4.0","detail":"new-version-not-ready"/);
});
