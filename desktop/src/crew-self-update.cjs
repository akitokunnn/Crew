'use strict';
// Crew self-update. Finds the latest formal GitHub Release of the fixed Crew
// repository, downloads this platform's package, checks it against the same
// release's SHA256SUMS.txt and hands the replacement to a helper that runs
// after Crew has exited. Nothing here accepts an address from a page.
const fs = require('node:fs');
const fsp = fs.promises;
const path = require('node:path');
const crypto = require('node:crypto');
const { execFile, spawn } = require('node:child_process');
const { promisify } = require('node:util');
const { compareVersions } = require('./crew-latest.cjs');

const REPOSITORY = 'akitokunnn/Crew';
const RELEASE_API = 'https://api.github.com/repos/' + REPOSITORY + '/releases/latest';
const RELEASE_PAGE = 'https://github.com/' + REPOSITORY + '/releases/tag/';
const DOWNLOAD_PATH = '/' + REPOSITORY + '/releases/download/';
const ASSET_HOSTS = new Set(['objects.githubusercontent.com', 'release-assets.githubusercontent.com']);
const SUMS = 'SHA256SUMS.txt';
const MAX_PACKAGE_BYTES = 800 * 1024 * 1024;
const MAX_SMALL_BYTES = 1024 * 1024;
const MAX_NOTES = 4000;
const BUNDLE_ID = 'local.crew.desktop';
const READY_TIMEOUT_SECONDS = 180;
const CHECK_DELAY_MS = 15000;
const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000;
const VERSION = /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/;
const execute = promisify(execFile);

const ASSETS = {
  mac: version => 'Crew-' + version + '-mac-arm64.zip',
  'mac-manual': version => 'Crew-' + version + '-mac-arm64.dmg',
  installer: version => 'Crew-' + version + '-setup.exe',
  zip: version => 'Crew-' + version + '-win-x64.zip',
  portable: version => 'Crew-' + version + '-portable.exe'
};
const RESTARTS = new Set(['mac', 'installer']);

function writable(target) {
  try { fs.accessSync(target, fs.constants.W_OK); return true; }
  catch (_) { return false; }
}

// How this copy of Crew was installed decides what an update can do.
function installKind({ packaged, platform = process.platform, arch = process.arch, execPath = process.execPath,
  env = process.env, exists = fs.existsSync, canWrite = writable } = {}) {
  if (!packaged) return { kind: 'development' };
  // Paths follow the described platform, not the host running this check.
  if (platform === 'darwin') {
    const posix = path.posix;
    const app = posix.resolve(execPath, '..', '..', '..');
    if (posix.extname(app) !== '.app') return { kind: 'development' };
    if (arch !== 'arm64') return { kind: 'unsupported', reason: '目前只发布 Apple 芯片（arm64）的 Mac 包。' };
    // A translocated or read-only copy (for example still inside the dmg) cannot be replaced in place.
    const replaceable = !app.includes('/AppTranslocation/') && canWrite(posix.dirname(app)) && canWrite(app);
    return { kind: replaceable ? 'mac' : 'mac-manual', app };
  }
  if (platform === 'win32') {
    if (env.PORTABLE_EXECUTABLE_FILE) return { kind: 'portable', file: env.PORTABLE_EXECUTABLE_FILE };
    const directory = path.win32.dirname(execPath);
    return { kind: exists(path.win32.join(directory, 'Uninstall Crew.exe')) ? 'installer' : 'zip', directory, executable: execPath };
  }
  return { kind: 'unsupported', reason: '这个系统没有 Crew 发布包。' };
}

function parseRelease(value) {
  if (!value || typeof value !== 'object' || value.draft || value.prerelease) throw new Error('GitHub 没有返回有效的正式发布');
  const tag = value.tag_name;
  if (typeof tag !== 'string' || tag[0] !== 'v' || !VERSION.test(tag.slice(1))) throw new Error('正式发布的版本标签无效：' + String(tag));
  const version = tag.slice(1);
  compareVersions(version, '0.0.0');
  if (!Array.isArray(value.assets)) throw new Error('正式发布缺少附件列表');
  const assets = {};
  for (const asset of value.assets) {
    if (!asset || typeof asset.name !== 'string' || !/^[0-9A-Za-z._-]+$/.test(asset.name) || !Number.isSafeInteger(asset.size) || asset.size <= 0) continue;
    const url = 'https://github.com' + DOWNLOAD_PATH + tag + '/' + asset.name;
    // Only assets served from this release's own download path are used.
    if (asset.browser_download_url !== url) continue;
    assets[asset.name] = { name: asset.name, size: asset.size, url };
  }
  const published = typeof value.published_at === 'string' && Number.isFinite(Date.parse(value.published_at)) ? value.published_at : null;
  return { version, tag, url: RELEASE_PAGE + tag, published_at: published,
    notes: typeof value.body === 'string' ? value.body.slice(0, MAX_NOTES) : '', assets };
}

function parseSums(text) {
  const sums = new Map();
  for (const line of String(text).split(/\r?\n/)) {
    const match = /^([0-9a-fA-F]{64}) [ *]?([0-9A-Za-z._-]+)\s*$/.exec(line.trim());
    if (match) sums.set(match[2], match[1].toLowerCase());
  }
  return sums;
}

function checkAssetUrl(value, first) {
  const url = new URL(value);
  const github = url.hostname === 'github.com' && url.pathname.startsWith(DOWNLOAD_PATH);
  if (url.protocol !== 'https:' || url.username || url.password || url.port || !(github || !first && ASSET_HOSTS.has(url.hostname))) {
    throw new Error('下载地址不属于 GitHub 上的 Crew 正式发布');
  }
  return url.href;
}

async function openAsset(fetchImpl, url, signal) {
  let target = checkAssetUrl(url, true);
  for (let hop = 0; hop <= 5; hop++) {
    let response;
    try {
      response = await fetchImpl(target, { redirect: 'manual', credentials: 'omit', signal, headers: { Accept: 'application/octet-stream' } });
    } catch (error) {
      signal?.throwIfAborted();
      throw new Error('下载连接失败：' + error.message, { cause: error });
    }
    if (![301, 302, 303, 307, 308].includes(response.status)) {
      if (!response.ok) throw new Error('下载失败（HTTP ' + response.status + '）');
      return response;
    }
    const location = response.headers.get('location');
    await response.body?.cancel?.().catch(() => {});
    if (!location) throw new Error('下载地址跳转无效');
    target = checkAssetUrl(new URL(location, target).href, false);
  }
  throw new Error('下载地址跳转次数过多');
}

async function readSmall(response, limit = MAX_SMALL_BYTES) {
  const text = await response.text();
  if (Buffer.byteLength(text) > limit) throw new Error('GitHub 返回内容过大');
  return text;
}

async function hashFile(file) {
  const hash = crypto.createHash('sha256');
  for await (const chunk of fs.createReadStream(file)) hash.update(chunk);
  return hash.digest('hex');
}

// Runs after Crew exits: swaps the bundle, opens the new version and puts the
// old one back when the new version does not report ready in time.
const MAC_HELPER = `#!/bin/sh
pid=$1; app=$2; new=$3; work=$4; version=$5; journal=$6; timeout=$7
shift 7
log() { printf '{"at":"%s","event":"%s","version":"%s","detail":"%s"}\\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$version" "$2" >>"$journal"; }
launch() { if [ $# -gt 0 ]; then open "$app" --args "$@"; else open "$app"; fi; }
waited=0
while kill -0 "$pid" 2>/dev/null; do
  waited=$((waited+1))
  if [ "$waited" -ge 1200 ]; then log aborted old-version-still-running; exit 1; fi
  sleep 0.1
done
if [ -e "$app.previous" ] && ! rm -rf "$app.previous"; then log aborted remove-stale-previous; launch "$@"; exit 1; fi
if ! mv "$app" "$app.previous"; then log aborted move-old-version; launch "$@"; exit 1; fi
if ! mv "$new" "$app"; then
  rm -rf "$app"; mv "$app.previous" "$app"
  log rolled-back move-new-version; launch "$@"; exit 1
fi
log swapped ok
launch "$@"
waited=0
while [ ! -f "$work/ready" ]; do
  waited=$((waited+1))
  if [ "$waited" -ge $((timeout*10)) ]; then
    # The new version reports its pid early; one that hung before that is found by its executable path.
    pids=$(cat "$work/started" 2>/dev/null; pgrep -f -- "$app/Contents/MacOS/" 2>/dev/null)
    for started in $pids; do
      case "$started" in
        ''|*[!0-9]*) ;;
        *) kill "$started" 2>/dev/null
           n=0; while kill -0 "$started" 2>/dev/null && [ "$n" -lt 300 ]; do n=$((n+1)); sleep 0.1; done
           kill -9 "$started" 2>/dev/null ;;
      esac
    done
    rm -rf "$app" && mv "$app.previous" "$app"
    log rolled-back new-version-not-ready
    launch "$@"; exit 1
  fi
  sleep 0.1
done
rm -rf "$app.previous"
log done ok
`;

// Waits for Crew to exit, then runs the per-user NSIS installer silently into
// the remembered directory. When the installer fails the old Crew is reopened.
const WINDOWS_HELPER = [
  "Wait-Process -Id $env:CREW_UPDATE_PID -Timeout 120 -ErrorAction SilentlyContinue",
  "if (Get-Process -Id $env:CREW_UPDATE_PID -ErrorAction SilentlyContinue) { exit 1 }",
  "$installer = Start-Process -FilePath $env:CREW_UPDATE_SETUP -ArgumentList '/S','--updated','--force-run' -PassThru -Wait",
  "if ($installer.ExitCode -ne 0) { Start-Process -FilePath $env:CREW_UPDATE_APP; exit $installer.ExitCode }"
].join('; ');

class CrewSelfUpdate {
  constructor({ home, version, install, fetchImpl = globalThis.fetch, run = execute, spawnProcess = spawn,
    prepareQuit = async () => ({ ok: true }), quit = () => {}, openPath = async () => '', showItem = () => {},
    relaunchArgs = [], pid = process.pid, customBackend = false, onChange = () => {}, readyTimeout = READY_TIMEOUT_SECONDS } = {}) {
    if (!home || !version || !install?.kind) throw new TypeError('Crew 自更新需要 HOME、当前版本和安装形态');
    this.root = path.join(home, 'desktop', 'updates');
    this.journal = path.join(home, 'desktop', 'logs', 'crew-self-update.jsonl');
    this.install = install; this.fetch = fetchImpl; this.run = run; this.spawn = spawnProcess;
    this.prepareQuit = prepareQuit; this.quit = quit; this.openPath = openPath; this.showItem = showItem;
    this.relaunchArgs = relaunchArgs; this.pid = pid; this.onChange = onChange; this.readyTimeout = readyTimeout;
    this.release = null; this.download = null; this.pending = null; this.work = null; this.controller = null;
    this.timers = []; this.closed = false;
    this.state = { phase: 'idle', current: version, kind: install.kind, latest: null, progress: null, auto_download: false,
      can_download: false, file: null, message: '', error: null, blocked: [], updated: null, checked_at: null,
      custom_backend: Boolean(customBackend), restart: RESTARTS.has(install.kind) };
    this._idleMessage();
  }
  snapshot() { return JSON.parse(JSON.stringify(this.state)); }
  _emit() { if (!this.closed) this.onChange(this.snapshot()); }
  _set(values) { Object.assign(this.state, values); this._emit(); }
  _idleMessage() {
    const reason = this.install.kind === 'development' ? '开发版不自动更新，请用源码更新。' : this.install.kind === 'unsupported' ? this.install.reason : '';
    if (reason) this.state.phase = 'unsupported';
    this.state.message = reason || '启动时和每 6 小时检查一次 GitHub 上的正式发布。';
  }
  async _log(event, detail = {}) {
    try {
      await fsp.mkdir(path.dirname(this.journal), { recursive: true });
      await fsp.appendFile(this.journal, JSON.stringify({ at: new Date().toISOString(), event, from: this.state.current, ...detail }) + '\n');
    } catch (_) { /* The journal never blocks an update. */ }
  }
  _directory(version) {
    if (!VERSION.test(version)) throw new Error('版本号无效：' + version);
    return path.join(this.root, version);
  }
  async _readJson(file) {
    try { return JSON.parse(await fsp.readFile(file, 'utf8')); }
    catch (error) { if (error.code === 'ENOENT') return null; throw error; }
  }
  async _writeJson(file, value) {
    await fsp.mkdir(path.dirname(file), { recursive: true });
    const temporary = file + '.' + process.pid + '.tmp';
    await fsp.writeFile(temporary, JSON.stringify(value, null, 2));
    await fsp.rename(temporary, file);
  }

  // Runs before the backend starts, so a freshly installed Mac version can
  // tell the helper its process id early enough to be rolled back.
  async initialize() {
    const settings = await this._readJson(path.join(this.root, 'settings.json')).catch(() => null);
    this.state.auto_download = settings?.auto_download === true;
    const pending = await this._readJson(path.join(this.root, 'pending.json')).catch(() => null);
    if (pending && VERSION.test(pending.to || '')) {
      if (pending.to === this.state.current) {
        this.pending = pending;
        if (pending.kind === 'mac') await fsp.writeFile(path.join(this._directory(pending.to), 'install', 'started'), String(this.pid)).catch(() => {});
        this.state.updated = { from: pending.from, to: pending.to };
        this.state.message = '已从 ' + pending.from + ' 更新到 ' + pending.to + '。';
      } else {
        await fsp.rm(path.join(this.root, 'pending.json'), { force: true });
        this.state.phase = 'error';
        this.state.error = '上次更新到 ' + pending.to + ' 没有完成，当前仍是 ' + this.state.current + '。' +
          (pending.kind === 'mac' ? '新版本没能正常启动，已换回原版本；详情见日志文件夹里的 crew-self-update.jsonl。' : '安装包已保留，可以重试。');
        await this._log('not-applied', { to: pending.to, kind: pending.kind });
      }
    }
    await this._restoreDownload();
    this._emit();
    return this.snapshot();
  }
  // A package verified before a restart stays ready without downloading again.
  async _restoreDownload() {
    let names = [];
    try { names = await fsp.readdir(this.root); } catch (_) { return; }
    const versions = names.filter(name => VERSION.test(name) && compareVersions(name, this.state.current) > 0).sort(compareVersions);
    for (const version of versions.reverse()) {
      const record = await this._readJson(path.join(this.root, version, 'download.json')).catch(() => null);
      if (!record || record.version !== version || record.kind !== this.install.kind) continue;
      try { await fsp.access(record.file); if (record.app) await fsp.access(record.app); } catch (_) { continue; }
      this.download = record;
      this.release = { version, tag: 'v' + version, url: record.url, notes: record.notes || '', published_at: record.published_at || null, assets: {} };
      Object.assign(this.state, { phase: 'ready', latest: this._latest(this.release, record.asset), can_download: true, file: record.file });
      this._readyMessage();
      return;
    }
  }
  // Called once the window has loaded; healthy means the backend (or LAN host connection) came up.
  async confirmReady(healthy) {
    let keep = null;
    if (this.pending && healthy) {
      const pending = this.pending; this.pending = null; keep = pending.to;
      if (pending.kind === 'mac') await fsp.writeFile(path.join(this._directory(pending.to), 'install', 'ready'), new Date().toISOString()).catch(() => {});
      await fsp.rm(path.join(this.root, 'pending.json'), { force: true });
      await this._log('updated', { from: pending.from, to: pending.to, kind: pending.kind });
    }
    // The helper is still watching this version's ready marker; it is pruned on a later start.
    if (healthy) await this._prune(keep);
  }
  async _prune(keep) {
    let names = [];
    try { names = await fsp.readdir(this.root); } catch (_) { return; }
    for (const name of names) {
      if (VERSION.test(name) && compareVersions(name, this.state.current) <= 0 && name !== keep && name !== this.pending?.to) {
        await fsp.rm(path.join(this.root, name), { recursive: true, force: true }).catch(() => {});
      }
    }
  }

  start({ delay = CHECK_DELAY_MS, interval = CHECK_INTERVAL_MS } = {}) {
    if (this.state.phase === 'unsupported') return;
    const first = setTimeout(() => { void this.check(); }, delay);
    const repeat = setInterval(() => { void this.check(); }, interval);
    first.unref?.(); repeat.unref?.();
    this.timers.push(first, repeat);
  }
  _latest(release, asset) {
    return { version: release.version, url: release.url, notes: release.notes, published_at: release.published_at,
      asset: asset?.name || null, size: asset?.size || null };
  }
  async _fetchRelease(signal) {
    let response;
    try {
      response = await this.fetch(RELEASE_API, { redirect: 'error', credentials: 'omit', signal,
        headers: { Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' } });
    } catch (error) {
      signal?.throwIfAborted();
      throw new Error('连接 GitHub 失败：' + error.message, { cause: error });
    }
    if (response.status === 404) return null;
    if (!response.ok) {
      if ([403, 429].includes(response.status)) throw new Error('GitHub 查询受限（HTTP ' + response.status + '），请稍后重试');
      throw new Error('GitHub 查询失败（HTTP ' + response.status + '）');
    }
    let value;
    try { value = JSON.parse(await readSmall(response)); }
    catch (error) { throw new Error('GitHub 没有返回有效的发布信息', { cause: error }); }
    return parseRelease(value);
  }
  check() {
    if (this.work || this.closed || this.state.phase === 'unsupported') return this.work || Promise.resolve(this.snapshot());
    this.work = (async () => {
      const controller = new AbortController(); this.controller = controller;
      const timer = setTimeout(() => controller.abort(new Error('查询 GitHub 超时（15 秒）')), 15000);
      this._set({ phase: 'checking', message: '正在查询 GitHub 上的正式发布…', error: null, blocked: [] });
      try {
        const release = await this._fetchRelease(controller.signal);
        const checked_at = new Date().toISOString();
        if (!release || compareVersions(release.version, this.state.current) <= 0) {
          this.release = release;
          this._set({ phase: 'latest', latest: release ? this._latest(release) : null, can_download: false, checked_at, file: null,
            message: release ? '已是最新正式版。' : 'GitHub 上还没有正式发布。' });
          return this.snapshot();
        }
        const asset = release.assets[ASSETS[this.install.kind](release.version)];
        this.release = release;
        const can_download = Boolean(asset && release.assets[SUMS]);
        if (this.download?.version === release.version) {
          this._set({ phase: 'ready', latest: this._latest(release, asset), can_download, checked_at, file: this.download.file });
          this._readyMessage(); this._emit();
          return this.snapshot();
        }
        this._set({ phase: 'available', latest: this._latest(release, asset), can_download, checked_at, file: null,
          message: can_download ? '有新版本 ' + release.version + '。' : '有新版本 ' + release.version + '，但这个发布还没有适合本机的安装包。' });
      } catch (error) {
        this._set({ phase: 'error', error: controller.signal.aborted && this.closed ? null : error.message, message: '检查没有完成。' });
        return this.snapshot();
      } finally { clearTimeout(timer); this.controller = null; this.work = null; }
      if (this.state.auto_download && this.state.can_download) setImmediate(() => { void this.fetchUpdate().catch(() => {}); });
      return this.snapshot();
    })();
    return this.work;
  }
  fetchUpdate() {
    if (this.work || this.closed) return this.work || Promise.resolve(this.snapshot());
    if (!this.release || !this.state.can_download || compareVersions(this.release.version, this.state.current) <= 0) {
      return Promise.reject(new Error('请先检查更新'));
    }
    this.work = this._fetchUpdate().finally(() => { this.work = null; this.controller = null; });
    return this.work;
  }
  async _fetchUpdate() {
    const release = this.release, name = ASSETS[this.install.kind](release.version), asset = release.assets[name];
    const directory = this._directory(release.version), file = path.join(directory, name);
    const controller = new AbortController(); this.controller = controller;
    this._set({ phase: 'downloading', progress: { received: 0, total: asset.size }, error: null, blocked: [], message: '正在下载 ' + name + '…' });
    try {
      const sumsResponse = await openAsset(this.fetch, release.assets[SUMS].url, controller.signal);
      const expected = parseSums(await readSmall(sumsResponse, 64 * 1024)).get(name);
      if (!expected) throw new Error('发布的校验清单里没有 ' + name);
      await fsp.rm(directory, { recursive: true, force: true });
      await fsp.mkdir(directory, { recursive: true });
      const response = await openAsset(this.fetch, asset.url, controller.signal);
      const length = Number(response.headers.get('content-length'));
      if (length && length !== asset.size) throw new Error('下载大小与发布记录不一致');
      const hash = crypto.createHash('sha256');
      const part = file + '.part', handle = await fsp.open(part, 'w');
      let received = 0, reported = 0;
      try {
        const reader = response.body.getReader();
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          received += value.byteLength;
          if (received > asset.size || received > MAX_PACKAGE_BYTES) throw new Error('下载内容超过发布记录的大小');
          hash.update(value); await handle.write(value);
          if (received - reported > 1024 * 1024 || received === asset.size) { reported = received; this._set({ progress: { received, total: asset.size } }); }
        }
      } finally { await handle.close(); }
      if (received !== asset.size) throw new Error('下载不完整（' + received + ' / ' + asset.size + ' 字节）');
      const actual = hash.digest('hex');
      if (actual !== expected) throw new Error('下载文件的 SHA-256 与发布清单不一致，已丢弃');
      await fsp.rename(part, file);
      const record = { version: release.version, kind: this.install.kind, asset: name, file, sha256: actual, size: received,
        url: release.url, notes: release.notes, published_at: release.published_at, app: null };
      if (this.install.kind === 'mac') {
        this._set({ phase: 'verifying', message: '正在检查新版本的签名和包标识…' });
        record.app = await this._unpackMac(file, directory, release.version);
      }
      await this._writeJson(path.join(directory, 'download.json'), record);
      this.download = record;
      await this._log('downloaded', { to: release.version, asset: name, sha256: actual });
      this._set({ phase: 'ready', progress: null, file });
      this._readyMessage(); this._emit();
    } catch (error) {
      await fsp.rm(directory, { recursive: true, force: true }).catch(() => {});
      if (controller.signal.aborted) this._set({ phase: 'available', progress: null, message: this.closed ? '' : '已取消下载。' });
      else {
        await this._log('download-failed', { to: release.version, error: error.message });
        this._set({ phase: 'error', progress: null, error: error.message, message: '下载没有完成，当前安装没有改动，可以重试。' });
      }
    }
    return this.snapshot();
  }
  async _unpackMac(file, directory, version) {
    const staging = path.join(directory, 'staging');
    await fsp.rm(staging, { recursive: true, force: true });
    await this.run('ditto', ['-x', '-k', file, staging], { timeout: 300000 });
    const app = path.join(staging, 'Crew.app');
    await this._verifyMac(app, version);
    return app;
  }
  async _verifyMac(app, version) {
    try { await this.run('codesign', ['--verify', '--deep', '--strict', app], { timeout: 180000 }); }
    catch (error) { throw new Error('新版本签名检查未通过：' + String(error.stderr || error.message).trim(), { cause: error }); }
    const plist = path.join(app, 'Contents', 'Info.plist');
    const read = async key => String((await this.run('plutil', ['-extract', key, 'raw', '-o', '-', plist], { timeout: 10000 })).stdout).trim();
    if (await read('CFBundleIdentifier') !== BUNDLE_ID) throw new Error('新版本的包标识不是 ' + BUNDLE_ID);
    const found = await read('CFBundleShortVersionString');
    if (found !== version) throw new Error('新版本的版本号是 ' + found + '，与发布 ' + version + ' 不一致');
  }
  _readyMessage() {
    const version = this.download?.version || this.state.latest?.version;
    this.state.message = {
      mac: '新版本 ' + version + ' 已下载并校验。点「重启并更新」后，Crew 会等所有会话空闲、备份数据，再退出、替换并重新打开。',
      installer: '新版本 ' + version + ' 已下载并校验。点「重启并更新」后，Crew 会等所有会话空闲、备份数据，再退出并静默安装到原目录。',
      'mac-manual': '新版本 ' + version + ' 的 dmg 已下载并校验。当前 Crew 所在位置不能直接替换：打开 dmg，把 Crew 拖到「应用程序」覆盖旧版本，然后退出并重新打开 Crew。',
      zip: '新版本 ' + version + ' 的 ZIP 已下载并校验。退出 Crew 后，把它解压到新目录，运行里面的 Crew.exe。',
      portable: '新版本 ' + version + ' 的便携版已下载并校验。退出 Crew 后，用它替换原来的便携版文件再打开。'
    }[this.install.kind] || '';
  }
  cancel() {
    if (this.state.phase === 'downloading') this.controller?.abort(new Error('已取消下载'));
    return this.snapshot();
  }
  async settings(value) {
    if (!value || typeof value.auto_download !== 'boolean') throw new TypeError('需要有效的自动下载设置');
    await this._writeJson(path.join(this.root, 'settings.json'), { auto_download: value.auto_download });
    this._set({ auto_download: value.auto_download });
    if (value.auto_download && this.state.phase === 'available' && this.state.can_download) void this.fetchUpdate().catch(() => {});
    return this.snapshot();
  }
  installUpdate() {
    if (this.work || this.closed) return this.work || Promise.resolve(this.snapshot());
    if (this.state.phase !== 'ready' || !this.download) return Promise.reject(new Error('还没有下载好的新版本'));
    this.work = this._install().finally(() => { this.work = null; });
    return this.work;
  }
  async _install() {
    const record = this.download, kind = this.install.kind;
    try {
      this._set({ phase: 'installing', error: null, blocked: [], message: '正在复核下载的安装包…' });
      if (await hashFile(record.file) !== record.sha256) throw new Error('安装包在下载后被改动，请重新下载');
      if (!RESTARTS.has(kind)) {
        if (kind === 'mac-manual') {
          const failure = await this.openPath(record.file);
          if (failure) throw new Error('无法打开 dmg：' + failure);
        } else this.showItem(record.file);
        this._set({ phase: 'ready' }); this._readyMessage(); this._emit();
        return this.snapshot();
      }
      if (kind === 'mac') await this._verifyMac(record.app, record.version);
      this._set({ message: '正在确认所有会话空闲并备份数据…' });
      const drained = await this.prepareQuit();
      if (!drained?.ok) {
        const reasons = (drained?.reasons || []).filter(item => typeof item === 'string');
        this._set({ phase: 'ready', blocked: reasons, message: '以下工作还在进行，等它们结束后再点「重启并更新」。' });
        return this.snapshot();
      }
      try {
        await this._writeJson(path.join(this.root, 'pending.json'), { from: this.state.current, to: record.version, kind, at: new Date().toISOString() });
        await this._launchHelper(record);
      } catch (error) {
        await fsp.rm(path.join(this.root, 'pending.json'), { force: true }).catch(() => {});
        await drained.cancel?.().catch(() => {});
        throw error;
      }
      await this._log('install-start', { to: record.version, kind, backup: drained.backup?.path || null });
      this._set({ message: '正在退出 Crew 并安装 ' + record.version + '…' });
      this.quit();
    } catch (error) {
      await this._log('install-failed', { to: record.version, error: error.message });
      this._set({ phase: 'ready', error: error.message, message: '没有开始安装，当前版本保持不变。' });
    }
    return this.snapshot();
  }
  async _launchHelper(record) {
    const work = path.join(this._directory(record.version), 'install');
    await fsp.rm(work, { recursive: true, force: true });
    await fsp.mkdir(work, { recursive: true });
    await fsp.mkdir(path.dirname(this.journal), { recursive: true });
    let child;
    const output = fs.openSync(path.join(work, 'helper.log'), 'a');
    try {
      if (this.install.kind === 'mac') {
        const app = this.install.app;
        if (!path.isAbsolute(app) || path.extname(app) !== '.app' || path.dirname(app) === app) throw new Error('当前 Crew 位置无效');
        const script = path.join(work, 'replace.sh');
        await fsp.writeFile(script, MAC_HELPER, { mode: 0o700 });
        child = this.spawn('/bin/sh', [script, String(this.pid), app, record.app, work, record.version, this.journal,
          String(this.readyTimeout), ...this.relaunchArgs], { detached: true, stdio: ['ignore', output, output] });
      } else {
        child = this.spawn('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-Command', WINDOWS_HELPER],
          { detached: true, windowsHide: true, stdio: ['ignore', output, output],
            env: { ...process.env, CREW_UPDATE_PID: String(this.pid), CREW_UPDATE_SETUP: record.file, CREW_UPDATE_APP: this.install.executable } });
      }
      await new Promise((resolve, reject) => { child.once('spawn', resolve); child.once('error', reject); });
      child.unref();
    } finally { fs.closeSync(output); }
  }
  async close() {
    this.closed = true;
    for (const timer of this.timers) { clearTimeout(timer); clearInterval(timer); }
    this.controller?.abort(new Error('Crew 正在退出'));
    await Promise.allSettled([this.work]);
  }
}

module.exports = { CrewSelfUpdate, installKind, parseRelease, parseSums, checkAssetUrl, ASSETS, MAC_HELPER, WINDOWS_HELPER };
