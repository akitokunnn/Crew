'use strict';
const path = require('node:path');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const execute = promisify(execFile);
const cancelled = () => Object.assign(new Error('只读检查已停止'), { name:'AbortError' });

async function stopReadOnlyChild(child) {
  if (!Number.isSafeInteger(child?.pid) || child.pid < 1 || child.exitCode !== null || child.signalCode !== null) return;
  if (process.platform === 'win32') {
    const taskkill = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'taskkill.exe');
    try {
      await execute(taskkill, ['/PID', String(child.pid), '/T', '/F'], { windowsHide:true, timeout:10000, maxBuffer:65536 });
    } catch (error) {
      // The command may have completed naturally while taskkill was starting.
      if (child.exitCode === null && child.signalCode === null) throw error;
    }
  } else {
    // Read helpers own a separate process group, including their version probes.
    try { process.kill(-child.pid, 'SIGKILL'); }
    catch (error) { if (error.code !== 'ESRCH') throw error; }
  }
}

class ReadOnlyCommands {
  constructor({ run = execute, stopReadOnly = stopReadOnlyChild } = {}) {
    this.execute = run; this.stopReadOnly = stopReadOnly; this.controller = new AbortController(); this.pending = new Set();
  }
  get signal() { return this.controller.signal; }
  run(executable, args, options) {
    if (this.signal.aborted) return Promise.reject(this.signal.reason);
    let command;
    try { command = this.execute(executable, args, { ...options, ...(process.platform === 'win32' ? {} : { detached:true }) }); }
    catch (error) { return Promise.reject(error); }
    let stopping = Promise.resolve();
    const onAbort = () => {
      // Do not use execFile's signal option: it kills only the parent before
      // Windows can enumerate its child tree. This object owns this exact PID.
      stopping = Promise.resolve().then(() => this.stopReadOnly(command.child));
      stopping.catch(() => {});
    };
    this.signal.addEventListener('abort',onAbort,{ once:true });
    const result = (async () => {
      try {
        const value = await command;
        this.signal.throwIfAborted();
        return value;
      } catch (error) {
        if (this.signal.aborted) throw this.signal.reason;
        throw error;
      } finally {
        this.signal.removeEventListener('abort',onAbort);
        // execFile resolves/rejects after close, so stdout/stderr and owned
        // descendants have finished before a cancelled check can finish.
        await stopping;
      }
    })();
    this.pending.add(result);
    result.then(() => this.pending.delete(result), () => this.pending.delete(result));
    return result;
  }
  async close() {
    this.controller.abort(cancelled());
    const results = await Promise.allSettled([...this.pending]);
    const failure = results.find(result => result.status === 'rejected' && result.reason?.name !== 'AbortError');
    if (failure) throw failure.reason;
  }
}

module.exports = { ReadOnlyCommands };
