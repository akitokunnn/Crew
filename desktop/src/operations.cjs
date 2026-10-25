'use strict';
const path = require('node:path');
const {execFile} = require('node:child_process');

const ACTIONS = new Set(['snapshot', 'send', 'interrupt', 'config-get', 'config-save', 'settings-get', 'settings-save']);

class OperationsClient {
  constructor(config, execute = execFile) {
    this.config = config;
    this.execute = execute;
  }

  request(action, body = {}) {
    if (!ACTIONS.has(action)) return Promise.reject(new Error('不支持的运维操作'));
    const env = {...process.env};
    for (const name of Object.keys(env)) {
      const key = name.toUpperCase();
      if (key.startsWith('CREW_') || key === 'CLAUDE_CODE_SESSION_ID' || key === 'CODEX_THREAD_ID') delete env[name];
    }
    env.PYTHONPATH = path.resolve(this.config.backendDir);
    env.PYTHONIOENCODING = 'utf-8';
    const args = ['-m', 'crew.operations_chat', '--home', this.config.home,
      '--backend', this.config.backendDir, '--action', action];
    const input = JSON.stringify(body);
    return new Promise((resolve, reject) => {
      let inputError;
      const child = this.execute(this.config.python || 'python', args, {
        cwd:this.config.backendDir, env, windowsHide:true, encoding:'utf8', timeout:action === 'settings-get' ? 60000 : 30000, maxBuffer:2 * 1024 * 1024
      }, (error, stdout, stderr) => {
        if (error) { reject(new Error(String(stderr || error.message).trim())); return; }
        if (inputError) { reject(inputError); return; }
        try { resolve(JSON.parse(stdout)); }
        catch (caught) { reject(new Error('运维响应无法读取：' + caught.message)); }
      });
      child.stdin.on('error', error => { inputError = error; });
      child.stdin.end(input);
    });
  }
}

module.exports = {OperationsClient};
