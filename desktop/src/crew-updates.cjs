'use strict';
const path = require('node:path');
const { spawn, execFile } = require('node:child_process');
const { promisify } = require('node:util');
const core = require('./core.cjs');
const {checkLatest:queryLatest,compareVersions}=require('./crew-latest.cjs');
const execute = promisify(execFile);
const samePath = (left, right) => typeof left === 'string' && typeof right === 'string' &&
  (process.platform === 'win32' ? path.resolve(left).toLowerCase() === path.resolve(right).toLowerCase() : path.resolve(left) === path.resolve(right));

class CrewUpdates {
  constructor(config, backend, { onChange = () => {}, onRestart = () => {}, run = execute, spawnProcess = spawn,
    request = core.request, acquireLock = null, checkLatest = queryLatest } = {}) {
    this.config = config; this.backend = backend; this.onChange = onChange; this.onRestart = onRestart;
    this.run = run; this.spawn = spawnProcess; this.request = request; this.acquireLock = acquireLock; this.checkLatest = checkLatest;
    this.state = { desktop_version: config.desktopVersion, target: null, backend: null, cli: null, plugin: null,
      phase: 'idle', message: '检查并统一到当前桌面自带版本。', error: null, blocked_reason: null,
      can_update: false, update_available: false, backup: null, checked_at: null,
      online:null, online_error:null, online_checking:false };
    this.pending = null; this.checking = null; this.queued = false; this.closed = false; this.timer = null;
  }
  snapshot() { return JSON.parse(JSON.stringify({ ...this.state, queued: this.queued })); }
  _emit() { this.onChange(this.snapshot()); }
  _phase(phase, message) { Object.assign(this.state, { phase, message, error: null }); this._emit(); }
  _helperArgs(operation, extra = []) { return [path.join(this.config.helperBackendDir || this.config.backendDir, 'crew_update.py'), '--home', this.config.home, operation, ...extra]; }
  async _helper(operation, extra = [], python = this.config.python) {
    let result;
    try {
      result = await this.run(python, this._helperArgs(operation, extra), {
        cwd: this.config.helperBackendDir || this.config.backendDir, windowsHide: true, timeout: operation === 'plugin-update' ? 150000 : 45000,
        maxBuffer: 1024 * 1024, env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1' }
      });
    } catch (error) {
      if (error.stdout) {
        let response;
        try { response = JSON.parse(error.stdout); } catch (_) { /* Preserve the original command failure. */ }
        if (response?.ok === false) throw new Error(response.error);
      }
      throw error;
    }
    const value = JSON.parse(result.stdout);
    if (!value || value.operation !== operation || value.ok !== true) throw new Error(value?.error || 'Crew 更新工具返回无效结果');
    return value;
  }
  async _health() {
    const value = await this.request(this.backend.url, '/api/admin/health');
    if (value?.service !== 'crew' || value.ok !== true || !samePath(value.home, this.config.home) ||
        !Number.isSafeInteger(value.pid) || !value.instance_id || !value.version || !value.plugin_root) {
      throw new Error('当前后台不支持安全更新；请先退出原 Crew，再打开当前桌面版本。');
    }
    return value;
  }
  async _inspect() {
    const value = await this._helper('inspect', ['--target', this.config.backendDir]);
    this.state.target = value.target; this.state.cli = value.cli; this.state.plugin = value.plugin;
    this.state.backend = await this._health();
    const blocked = !this.backend.owned ? '当前连接的是其他窗口启动的后台。请先退出原 Crew，再打开当前版本更新。' :
      !['current', 'update', 'not_installed'].includes(value.plugin.state) ? value.plugin.reason || '无法确认 Claude 插件安装来源。' : null;
    this.state.blocked_reason = blocked; this.state.can_update = !blocked;
    this.state.update_available = value.plugin.state === 'update' || value.cli.version !== value.target.version ||
      !samePath(value.cli.plugin_root, value.target.root) || this.state.backend.version !== value.target.version ||
      !samePath(this.state.backend.plugin_root, value.target.root);
    this.state.checked_at = new Date().toISOString();
    return value;
  }
  async _online() {
    this.state.online_checking=true;this.state.online_error=null;this.state.online=null;this._emit();
    try {
      const result=await this.checkLatest();
      this.state.online={...result,main_comparison:compareVersions(result.main.version,this.config.desktopVersion),
        release_comparison:result.release?compareVersions(result.release.version,this.config.desktopVersion):null};
    } catch(error) { this.state.online_error=error.message; }
    finally { this.state.online_checking=false;this._emit(); }
  }
  check() {
    if (this.pending || this.checking || this.closed) return this.checking || this.pending || Promise.resolve(this.snapshot());
    this.checking = (async () => {
      this._phase('checking', '正在检查线上版本和本机安装…');
      const [local]=await Promise.allSettled([this._inspect(),this._online()]);
      if(local.status==='fulfilled') {
        this._phase(this.queued ? 'queued' : 'idle', this.state.blocked_reason ||
          (this.state.update_available ? '可以把后台与插件统一到本机版本。' : '后台、命令入口和插件已统一。'));
      } else {
        Object.assign(this.state,{phase:'error',error:local.reason.message,can_update:false});this._emit();
      }
      this.checking=null;
      return this.snapshot();
    })();
    return this.checking;
  }
  async _lock(python = this.config.python) {
    if (this.acquireLock) return this.acquireLock();
    const child = this.spawn(python, this._helperArgs('lock'), { windowsHide: true,
      cwd: this.config.helperBackendDir || this.config.backendDir, stdio: ['pipe', 'pipe', 'pipe'], env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1' } });
    let exited = false, stderr = '';
    const exit = new Promise(resolve => child.once('exit', () => { exited = true; resolve(); }));
    child.stderr.on('data', value => { stderr = (stderr + value).slice(-3000); });
    try {
      await new Promise((resolve, reject) => {
        let pending = '';
        const timer = setTimeout(() => finish(new Error('取得 Crew 更新锁超时')), 10000);
        const onError = error => finish(error);
        const onExit = () => finish(new Error('Crew 更新锁已退出：' + stderr));
        const onData = value => {
          pending += value; if (!pending.includes('\n')) return;
          try { const result = JSON.parse(pending.split('\n')[0]); finish(result.ok && result.locked ? null : new Error(result.error || '另一个 Crew 正在更新')); }
          catch (error) { finish(error); }
        };
        function finish(error) {
          clearTimeout(timer); child.removeListener('error', onError); child.removeListener('exit', onExit); child.stdout.removeListener('data', onData);
          error ? reject(error) : resolve();
        }
        child.once('error', onError); child.once('exit', onExit); child.stdout.on('data', onData);
      });
    } catch (error) { child.stdin.end(); throw error; }
    child.stdout.resume();
    return { assert: () => { if (exited) throw new Error('Crew 更新锁已释放，本次更新停止'); },
      release: async () => { if (!exited) child.stdin.end(); await exit; } };
  }
  update() {
    if (this.pending || this.closed) return this.pending || Promise.resolve(this.snapshot());
    this.queued = true;
    this.pending = this._update().finally(() => { this.pending = null; });
    return this.pending;
  }
  async _update() {
    let lock, prepared, health, stopped = false;
    try {
      if (this.checking) await this.checking;
      this._phase('checking', '正在核对更新来源…');
      lock = await this._lock(); await this._inspect(); lock.assert();
      if (!this.queued || this.closed) { this._phase('idle', '已取消等待更新。'); return this.snapshot(); }
      if (this.state.blocked_reason) throw new Error(this.state.blocked_reason);
      health = this.state.backend;
      const target = this.state.target;
      this._phase('preparing', '正在等待已接受的任务完成…');
      prepared = await this.request(this.backend.url, '/api/admin/update/prepare', { instance_id: health.instance_id }, 15000);
      lock.assert();
      if (!this.queued || this.closed) { this._phase('idle', '已取消等待更新。'); return this.snapshot(); }
      if (prepared.status === 'busy') {
        this._phase('queued', (prepared.reasons || []).join('；') || '任务正在运行，等待空闲后更新。');
        if (!this.closed && this.queued && !this.timer) this.timer = setInterval(() => { if (this.queued && !this.pending) void this.update(); }, 10000);
        return this.snapshot();
      }
      if (!prepared.ok || prepared.instance_id !== health.instance_id || prepared.pid !== health.pid || !prepared.gate_id) throw new Error('后台更新准备结果无效，请重新检查。');
      this.queued = false; clearInterval(this.timer); this.timer = null;
      this._phase('backing-up', '正在备份 Crew 数据…');
      this.state.backup = await this._helper('backup'); lock.assert(); this._emit();
      // Recheck the source before the first installation change.
      const fresh = await this._helper('inspect', ['--target', this.config.backendDir]); lock.assert();
      if (fresh.target.digest !== target.digest) throw new Error('桌面自带源码在更新期间发生变化，请重新检查。');
      if (!['current', 'update', 'not_installed'].includes(fresh.plugin.state)) throw new Error(fresh.plugin.reason || '插件来源发生变化。');
      if (fresh.plugin.state === 'update') {
        this._phase('plugin', '正在同步 Claude 中的 Crew 插件…');
        await this._helper('plugin-update', ['--target', this.config.backendDir]); lock.assert();
      }
      const current = await this._health();
      if (current.instance_id !== health.instance_id || current.pid !== health.pid) throw new Error('后台实例已变化，更新停止。');
      this._phase('stopping', '正在等待原后台退出…');
      const stopping = this.backend.stop(); stopping.catch(() => {});
      const pids = [health.pid, this.backend.child?.pid, ...prepared.managed_pids].filter(pid => Number.isSafeInteger(pid));
      await this._helper('stopped', ['--port', String(health.port), ...pids.flatMap(pid => ['--pid', String(pid)]), '--timeout', '20']);
      await stopping; stopped = true; lock.assert();
      this._phase('switching', '正在切换 Crew 命令入口…');
      await this._helper('switch', ['--target', this.config.backendDir]); lock.assert();
      this._phase('starting', '正在连接更新后的后台…');
      this.backend.config.port = health.port;
      await this.backend.start(); lock.assert();
      const after = await this._health();
      if (!this.backend.owned || after.version !== target.version || !samePath(after.plugin_root, target.root) ||
          after.port !== health.port || after.instance_id === health.instance_id) throw new Error('新后台的版本、来源或端口验证未通过；请查看系统管理员。');
      await this.onRestart(after); this.state.backend = after;
      const applied = await this._helper('inspect', ['--target', this.config.backendDir]);
      if (applied.target.digest !== target.digest || !samePath(applied.cli.plugin_root, target.root) ||
          !['current', 'not_installed'].includes(applied.plugin.state)) throw new Error('更新后命令入口或插件来源验证未通过。');
      this.state.cli = applied.cli; this.state.plugin = applied.plugin; this.state.update_available = false;
      this._phase('done', '已统一到桌面自带版本。已有 Claude 会话请重载插件或重开。');
    } catch (error) {
      this.queued = false; clearInterval(this.timer); this.timer = null;
      Object.assign(this.state, { phase: 'error', error: error.message, message: stopped ? '后台切换未完成，请查看系统管理员；备份已保留。' : '更新未完成，备份（若已创建）已保留。' }); this._emit();
    } finally {
      if (prepared?.gate_id && !stopped) {
        try { await this.request(this.backend.url, '/api/admin/update/cancel', { instance_id: health.instance_id, gate_id: prepared.gate_id }); }
        catch (error) { this.state.error = [this.state.error, '解除更新准备失败：' + error.message].filter(Boolean).join('；'); this._emit(); }
      }
      if (lock) await lock.release();
    }
    return this.snapshot();
  }
  cancel() {
    this.queued = false; clearInterval(this.timer); this.timer = null;
    if (this.state.phase === 'queued') this._phase('idle', '已取消等待更新。');
    return this.snapshot();
  }
  async close() { this.closed = true; this.cancel(); if (this.pending) await this.pending; if (this.checking) await this.checking; }
}
module.exports = { CrewUpdates, samePath };
