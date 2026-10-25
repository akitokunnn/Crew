'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const execute = promisify(execFile);
const IDS = ['claude', 'codex'];
const names = { claude:'Claude Code', codex:'Codex' };
const clone = value => JSON.parse(JSON.stringify(value));

class ToolUpdates {
  constructor(config, { onChange = () => {}, run = execute, clock = () => new Date().toISOString() } = {}) {
    this.config = config; this.onChange = onChange; this.run = run; this.clock = clock;
    this.file = path.join(config.home, 'desktop', 'tool-update-settings.json');
    this.autoUpdate = false; this.queue = new Map(); this.records = new Map();
    for (const id of IDS) this.records.set(id, { id, name:names[id], installed_version:null, latest_version:null,
      installation:'unknown', update_available:null, can_update:false, error:null, blocked_reason:null, phase:'idle', message:null });
    this.checkedAt = null; this.lastError = null; this._checking = null; this._draining = null; this._saving = Promise.resolve(); this.closed = false; this.timers = [];
  }
  snapshot() {
    return clone({ checking:!!this._checking, checked_at:this.checkedAt, autoUpdate:this.autoUpdate,
      tools:IDS.map(id => ({ ...this.records.get(id), ...(this.queue.has(id) && this.records.get(id).phase !== 'updating' ? { phase:'queued' } : {}) })),
      lastError:this.lastError });
  }
  _emit() { if (!this.closed) this.onChange(this.snapshot()); }
  async initialize() {
    try {
      const saved = JSON.parse(await fs.readFile(this.file, 'utf8'));
      this.autoUpdate = saved.autoUpdate === true;
      if (Array.isArray(saved.queue)) for (const entry of saved.queue) {
        if (IDS.includes(entry?.id) && ['manual', 'auto'].includes(entry.source) && (entry.source === 'manual' || this.autoUpdate)) this.queue.set(entry.id, entry.source);
      }
    } catch (error) { if (error.code !== 'ENOENT') this.lastError = '更新设置读取失败：' + error.message; }
    this._emit();
    return this.snapshot();
  }
  start() {
    if (this.closed || this.timers.length) return;
    const later = (fn, ms) => { const timer = setTimeout(fn, ms); timer.unref(); this.timers.push(timer); };
    const repeat = (fn, ms) => { const timer = setInterval(fn, ms); timer.unref(); this.timers.push(timer); };
    later(() => { void this.check(); }, 5000);
    repeat(() => { void this.check(); }, 6 * 60 * 60 * 1000);
    repeat(() => { void this._drain(); }, 60000);
  }
  _save() {
    const contents = JSON.stringify({ autoUpdate:this.autoUpdate, queue:[...this.queue].map(([id, source]) => ({id, source})) }, null, 2);
    this._saving = this._saving.catch(() => {}).then(async () => {
      await fs.mkdir(path.dirname(this.file), { recursive:true });
      const temporary = this.file + '.' + process.pid + '.tmp';
      await fs.writeFile(temporary, contents);
      await fs.rename(temporary, this.file);
    });
    return this._saving;
  }
  async _helper(operation, id) {
    const backendDir=this.config.helperBackendDir || this.config.backendDir;
    const args = [path.join(backendDir, 'crew_tool_versions.py'), '--home', this.config.home, operation];
    if (id !== undefined) args.push(id);
    const result = await this.run(this.config.python || 'python', args, { cwd:backendDir, windowsHide:true,
      timeout:operation === 'update' ? 0 : 45000, maxBuffer:1024 * 1024,
      env:{...process.env, PYTHONUNBUFFERED:'1', PYTHONIOENCODING:'utf-8'} });
    const value = JSON.parse(result.stdout);
    if (!value || value.operation !== operation || typeof value.ok !== 'boolean') throw new Error('版本工具返回了无效结果');
    return value;
  }
  _record(value) {
    if (!IDS.includes(value?.id)) throw new Error('版本工具返回了未知工具');
    const previous = this.records.get(value.id);
    this.records.set(value.id, { ...previous, ...value, name:names[value.id] });
  }
  check() {
    if (this.closed || this._draining) return Promise.resolve(this.snapshot());
    if (this._checking) return this._checking;
    this._checking = Promise.resolve().then(async () => {
      try {
        const result = await this._helper('check');
        if (!result.ok || !Array.isArray(result.runtimes) || result.runtimes.length !== 2 ||
          new Set(result.runtimes.map(item => item.id)).size !== 2) throw new Error(result.error || '版本检查结果不完整');
        for (const item of result.runtimes) {
          this._record(item);
          if (!this.queue.has(item.id) && this.records.get(item.id).phase !== 'updating') {
            this.records.set(item.id, { ...this.records.get(item.id), phase:item.error ? 'error' : 'idle', message:null });
          }
        }
        this.checkedAt = result.checked_at || this.clock(); this.lastError = null;
        if (this.autoUpdate) for (const item of result.runtimes) if (item.update_available && (item.can_update || item.can_queue) && !this.queue.has(item.id)) this.queue.set(item.id, 'auto');
        await this._save();
      } catch (error) { this.lastError = '检查失败：' + error.message; }
      finally { this._checking = null; this._emit(); }
      if (!this.lastError && this.queue.size) await this._drain();
      return this.snapshot();
    });
    this._emit();
    return this._checking;
  }
  async update(id) {
    if (!IDS.includes(id)) throw new Error('不支持的工具');
    if (this.closed) throw new Error('工具更新已停止');
    this.queue.set(id, 'manual');
    this.records.set(id, { ...this.records.get(id), phase:'queued', message:'等待检查运行状态后更新', error:null });
    await this._save(); this._emit();
    if (!this._checking) await this._drain();
    return this.snapshot();
  }
  async _drain() {
    if (this.closed || this._checking) return this.snapshot();
    if (this._draining) return this._draining;
    this._draining = Promise.resolve().then(async () => {
      for (const id of [...this.queue.keys()]) {
        if (this.closed || !this.queue.has(id)) continue;
        if (this.queue.get(id) === 'auto' && !this.autoUpdate) {
          this.queue.delete(id);
          this.records.set(id, { ...this.records.get(id), phase:'idle', message:'自动更新已关闭', blocked_reason:null });
          await this._save(); this._emit(); continue;
        }
        this.records.set(id, { ...this.records.get(id), phase:'updating', error:null, message:'正在检查运行状态并更新…' }); this._emit();
        try {
          const result = await this._helper('update', id);
          if (result.runtime !== id || !['updated','already_current','blocked','failed'].includes(result.status)) throw new Error('更新结果与目标工具不一致');
          const record = result.after || result.before;
          if (record) { if (record.id !== id) throw new Error('更新后的工具身份不一致'); this._record(record); }
          if (result.status === 'blocked') {
            if (this.queue.get(id) === 'auto' && !this.autoUpdate) {
              this.queue.delete(id);
              this.records.set(id, { ...this.records.get(id), phase:'idle', message:'自动更新已关闭', blocked_reason:null });
            } else this.records.set(id, { ...this.records.get(id), phase:'queued', blocked_reason:result.blocked_reason,
              running_count:result.running_count, message:result.blocked_reason || '等待使用这个版本的会话退出后更新' });
          } else {
            this.queue.delete(id);
            const failed = result.status === 'failed' || !result.ok;
            this.records.set(id, { ...this.records.get(id), phase:failed ? 'error' : 'done', blocked_reason:null,
              error:failed ? (result.error || '更新失败') : null,
              message:failed ? (result.error || '更新失败，请重试') : result.status === 'already_current' ? '已是最新版本' : '更新完成', log_file:result.log_file || null });
          }
        } catch (error) {
          this.queue.delete(id);
          this.records.set(id, { ...this.records.get(id), phase:'error', error:error.message, message:'更新失败，请重试' });
        }
        try { await this._save(); } catch (error) { this.lastError = '更新设置保存失败：' + error.message; }
        this._emit();
      }
      return this.snapshot();
    }).finally(() => { this._draining = null; });
    return this._draining;
  }
  async cancel(id) {
    if (!IDS.includes(id)) throw new Error('不支持的工具');
    if (this.records.get(id).phase === 'updating') throw new Error('更新已经开始，请等待完成');
    this.queue.delete(id);
    this.records.set(id, { ...this.records.get(id), phase:'idle', message:'已取消排队更新', blocked_reason:null });
    await this._save(); this._emit(); return this.snapshot();
  }
  async settings(value) {
    if (!value || typeof value.autoUpdate !== 'boolean' || Object.keys(value).some(key => key !== 'autoUpdate')) throw new Error('更新设置无效');
    this.autoUpdate = value.autoUpdate;
    if (!this.autoUpdate) for (const [id, source] of this.queue) if (source === 'auto' && this.records.get(id).phase !== 'updating') {
      this.queue.delete(id);
      this.records.set(id, { ...this.records.get(id), phase:'idle', message:'自动更新已关闭', blocked_reason:null });
    }
    await this._save(); this._emit();
    if (this.autoUpdate) void this.check();
    return this.snapshot();
  }
  async close() {
    this.closed = true;
    for (const timer of this.timers) { clearTimeout(timer); clearInterval(timer); }
    this.timers = [];
    // Finish an installer already in progress; never kill it halfway through.
    await Promise.allSettled([this._checking, this._draining]);
    await this._saving.catch(() => {});
  }
}

module.exports = { ToolUpdates };
