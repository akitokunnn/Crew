'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const { spawn, execFile } = require('node:child_process');
const { promisify } = require('node:util');
const run = promisify(execFile);

function loopback(value) {
  const url = new URL(value);
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) ||
      url.username || url.password || !url.port || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('Crew 服务必须使用本机 loopback 地址');
  }
  return url.origin;
}

function options(args) {
  const result = { home: path.join(os.homedir(), '.crew'), python: 'python', port: 0 };
  const fields = { '--home': 'home', '--python': 'python', '--port': 'port', '--backend-dir': 'backendDir', '--smoke-output': 'smokeOutput' };
  for (let index = 0; index < args.length; index++) {
    const argument = args[index];
    if (argument === '--isolated-sources') { result.isolated = true; continue; }
    if (argument === '--smoke-test') { result.smoke = true; continue; }
    if (!(argument in fields)) continue; // Electron may add its own switches.
    const value = args[++index];
    if (!value || value.startsWith('--')) throw new Error('参数缺少值：' + argument);
    result[fields[argument]] = argument === '--port' ? Number(value) : value;
  }
  if (!Number.isInteger(result.port) || result.port < 0 || result.port > 65535) throw new Error('端口必须在 0 到 65535 之间');
  if (result.smoke && (!args.includes('--home') || !result.smokeOutput)) throw new Error('smoke 必须显式指定 --home 和 --smoke-output');
  result.home = path.resolve(result.home);
  if (result.smoke && (result.home === path.resolve(os.homedir(), '.crew') || result.port === 47811)) throw new Error('smoke 必须使用隔离 home 和测试端口');
  return result;
}

async function request(base, route, body) {
  const origin = loopback(base);
  if (!route.startsWith('/api/')) throw new Error('不支持的 Crew API');
  const response = await fetch(origin + route, { method: body === undefined ? 'GET' : 'POST',
    headers: { 'X-Crew': '1', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body), redirect: 'error', signal: AbortSignal.timeout(5000) });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Crew 服务返回 ${response.status}`);
  return payload;
}

async function projects(base) {
  let data;
  try { data = await request(base, '/api/projects'); }
  catch (error) {
    data = await request(base, '/api/state');
    if (!Array.isArray(data.leaders) || !Array.isArray(data.members)) throw error;
  }
  if (!Array.isArray(data.projects) || data.projects.some(item => typeof item.name !== 'string' || typeof item.root !== 'string')) {
    throw new Error('此地址没有返回有效的 Crew 工作区列表');
  }
  return data.projects;
}

async function materializeBackend(source, home) {
  // NSIS portable resources live in an extraction directory that disappears
  // after exit. Keep content-addressed backend resources so Crew launchers and
  // guard paths never point into a deleted portable extraction directory.
  const hash = crypto.createHash('sha256');
  async function digest(directory, relative = '') {
    const entries = await fs.readdir(directory, { withFileTypes: true });
    entries.sort((left, right) => left.name.localeCompare(right.name));
    for (const entry of entries) {
      const name = path.join(relative, entry.name), target = path.join(directory, entry.name);
      if (entry.isDirectory()) await digest(target, name);
      else if (entry.isFile()) { hash.update(name); hash.update(await fs.readFile(target)); }
      else throw new Error('后端包包含不支持的文件类型');
    }
  }
  await digest(source);
  const identity = hash.digest('hex').slice(0, 20);
  const parent = path.join(home, 'desktop', 'backend'), target = path.join(parent, identity);
  try { if (await fs.readFile(path.join(target, '.ready'), 'utf8') === identity) return target; } catch (_) { /* first use */ }
  // Windows antivirus can hold copied files open and deny a directory rename.
  // Use a completion marker instead; interrupted copies are safely retried.
  await fs.mkdir(target, { recursive: true });
  await fs.cp(source, target, { recursive: true });
  await fs.writeFile(path.join(target, '.ready'), identity);
  return target;
}

class Backend {
  constructor(config, dependencies = {}) { this.config = config; this.spawn = dependencies.spawn || spawn; this.child = null; this.url = null; this.owned = false; this.errors = ''; }
  async start() {
    try {
      const record = JSON.parse(await fs.readFile(path.join(this.config.home, 'web.json'), 'utf8'));
      const candidate = loopback(record.url);
      if (this.config.smoke && new URL(candidate).port === '47811') throw new Error('smoke 不得连接正式端口');
      await projects(candidate);
      this.url = candidate;
      return { url: candidate, owned: false };
    } catch (error) {
      if (this.config.smoke && error.message === 'smoke 不得连接正式端口') throw error;
    }
    const script = path.join(this.config.backendDir, 'crew_desktop.py');
    await fs.access(script);
    await fs.mkdir(this.config.home, { recursive: true });
    const args = [script, '--home', this.config.home, '--port', String(this.config.port)];
    if (this.config.isolated || this.config.smoke) args.push('--isolated-sources');
    this.child = this.spawn(this.config.python, args, { cwd: this.config.backendDir, windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe'], env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' } });
    this.owned = true;
    const child = this.child;
    child.on('error', error => { this.errors = (this.errors + '\n' + error.message).slice(-6000); });
    child.stderr.on('data', value => { this.errors = (this.errors + value.toString()).slice(-6000); });
    try {
      this.url = await new Promise((resolve, reject) => {
        let pending = '';
        const timer = setTimeout(() => finish(new Error('Crew 服务启动超时。' + this.errors)), 30000);
        const onError = error => finish(new Error('无法启动 Python：' + error.message));
        const onExit = code => finish(new Error(`Crew 服务已退出 (${code})。${this.errors}`));
        const onData = buffer => {
          pending += buffer.toString();
          let index;
          while ((index = pending.indexOf('\n')) >= 0) {
            const line = pending.slice(0, index).trim(); pending = pending.slice(index + 1);
            if (!/^http:\/\/127\.0\.0\.1:\d+\/?$/.test(line)) continue;
            try {
              const url = loopback(line);
              if (this.config.smoke && new URL(url).port === '47811') throw new Error('smoke 不得连接正式端口');
              finish(null, url);
            } catch (error) { finish(error); }
          }
        };
        function finish(error, value) {
          clearTimeout(timer); child.removeListener('error', onError); child.removeListener('exit', onExit); child.stdout.removeListener('data', onData);
          error ? reject(error) : resolve(value);
        }
        child.once('error', onError); child.once('exit', onExit); child.stdout.on('data', onData);
      });
      child.stdout.resume();
      await projects(this.url);
      return { url: this.url, owned: true };
    } catch (error) { await this.stop(); throw error; }
  }
  async stop() {
    if (!this.owned || !this.child || this.child.exitCode !== null) return;
    const child = this.child;
    await new Promise(resolve => {
      const timeout = setTimeout(() => { child.kill(); }, 6000);
      const deadline = setTimeout(() => { cleanup(); resolve(); }, 9000);
      const cleanup = () => { clearTimeout(timeout); clearTimeout(deadline); child.removeListener('exit', done); };
      const done = () => { cleanup(); resolve(); };
      child.once('exit', done);
      if (child.stdin.writable) child.stdin.end('shutdown\n'); else child.kill();
    });
  }
}

function workspaceName(value) {
  if (typeof value !== 'string' || !value.trim() || value.trim().length > 100 || /[<>:"/\\|?*\x00-\x1f]/.test(value) || /[. ]$/.test(value) ||
      /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(value.trim()) || ['.', '..'].includes(value.trim())) {
    throw new Error('请输入有效的工作区名称，不能包含路径分隔符或系统保留名称');
  }
  return value.trim();
}

async function existingWorkspace(directory, base) {
  const selected = await fs.realpath(directory);
  const { stdout } = await run('git', ['-C', selected, 'rev-parse', '--show-toplevel'], { windowsHide: true, timeout: 10000 });
  const root = await fs.realpath(stdout.trim());
  const registered = await projects(base);
  const normalize = value => process.platform === 'win32' ? path.resolve(value).toLowerCase() : path.resolve(value);
  const present = registered.find(item => normalize(item.root) === normalize(root));
  if (present) return present;
  const stem = path.basename(root);
  let name = stem, number = 2;
  while (registered.some(item => item.name === name)) name = `${stem} ${number++}`;
  return request(base, '/api/project/init', { name, root });
}

async function newWorkspace(parent, name, base) {
  name = workspaceName(name);
  const root = path.join(await fs.realpath(parent), name);
  await fs.mkdir(root); // Refuse an existing path; never initialize someone else's files.
  try { await run('git', ['init', '--initial-branch=main', root], { windowsHide: true, timeout: 10000 }); }
  catch (error) { throw new Error('工作区已创建，但 Git 初始化失败：' + error.message); }
  // A registration failure retains the new repository for retry, never deletes it.
  return request(base, '/api/project/init', { name, root });
}

module.exports = { loopback, options, request, projects, Backend, workspaceName, existingWorkspace, newWorkspace, materializeBackend };
