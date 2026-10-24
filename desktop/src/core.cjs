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
  const fields = { '--home': 'home', '--python': 'python', '--port': 'port', '--backend-dir': 'backendDir', '--smoke-output': 'smokeOutput', '--service-url': 'serviceUrl', '--profile': 'profile' };
  for (let index = 0; index < args.length; index++) {
    const argument = args[index], separator=argument.indexOf('=');
    const key=separator<0?argument:argument.slice(0,separator);
    if (argument === '--isolated-sources') { result.isolated = true; continue; }
    if (argument === '--smoke-test') { result.smoke = true; continue; }
    if (argument === '--smoke-visual') { result.smokeVisual = true; continue; }
    if (!(key in fields)) continue; // Electron may add its own switches.
    const value = separator<0?args[++index]:argument.slice(separator+1);
    if (!value || value.startsWith('--')) throw new Error('参数缺少值：' + key);
    result[fields[key]] = key === '--port' ? Number(value) : value;
  }
  if (!Number.isInteger(result.port) || result.port < 0 || result.port > 65535) throw new Error('端口必须在 0 到 65535 之间');
  if (result.smoke && (!args.some(value=>value==='--home'||value.startsWith('--home=')) || !result.smokeOutput)) throw new Error('smoke 必须显式指定 --home 和 --smoke-output');
  if (result.smokeVisual && !result.smoke) throw new Error('视觉验收只允许使用隔离 smoke 模式');
  result.home = path.resolve(result.home);
  if (result.smoke && (result.home === path.resolve(os.homedir(), '.crew') || result.port === 47811)) throw new Error('smoke 必须使用隔离 home 和测试端口');
  if (result.serviceUrl) {
    result.serviceUrl = loopback(result.serviceUrl);
    if (result.smoke && new URL(result.serviceUrl).port === '47811') throw new Error('smoke 不得连接正式端口');
  }
  if (result.profile && (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(result.profile) || workspaceName(result.profile) !== result.profile)) throw new Error('profile 必须是安全的短名称');
  result.userData = result.profile ? path.join(result.home, 'desktop', 'profiles', result.profile) : path.join(result.home, 'desktop', 'ui');
  return result;
}

async function request(base, route, body) {
  const origin = loopback(base);
  if (!route.startsWith('/api/')) throw new Error('不支持的 Crew API');
  const response = await fetch(origin + route, { method: body === undefined ? 'GET' : 'POST',
    headers: { 'X-Crew': '1', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body), redirect: 'error', signal: AbortSignal.timeout(5000) });
  let payload;
  try { payload = await response.json(); }
  catch (_) { const error = new Error(`Crew 服务返回了无效内容 (${response.status})`); error.status = response.status; throw error; }
  if (!response.ok) { const error = new Error(payload.error || `Crew 服务返回 ${response.status}`); error.status = response.status; throw error; }
  return payload;
}

async function projects(base) {
  let data;
  try { data = await request(base, '/api/projects'); }
  catch (error) {
    if (![404,405].includes(error.status)) throw error;
    data = await request(base, '/api/state');
    if (!Array.isArray(data.leaders) || !Array.isArray(data.members)) throw error;
  }
  if (!Array.isArray(data.projects) || data.projects.some(item => !item || typeof item.name !== 'string' || !item.name)) {
    throw new Error('此地址没有返回有效的 Crew 工作区列表');
  }
  return Promise.all(data.projects.map(async item => {
    if (typeof item.root === 'string' && item.root) return item;
    const state = await request(base, '/api/state?project=' + encodeURIComponent(item.name));
    if (state.project?.name !== item.name || typeof state.project?.root !== 'string' || !state.project.root) throw new Error('Crew 工作区没有有效的目录信息');
    return { ...item, ...state.project };
  }));
}

function connectionRefused(error) {
  if (Array.isArray(error?.errors) && error.errors.length > 0) return error.errors.every(connectionRefused);
  if (error?.cause) return connectionRefused(error.cause);
  return error?.code === 'ECONNREFUSED';
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
    if (this.config.serviceUrl) {
      const candidate = loopback(this.config.serviceUrl);
      if (this.config.smoke && new URL(candidate).port === '47811') throw new Error('smoke 不得连接正式端口');
      try { await projects(candidate); }
      catch (error) { throw new Error('指定的 Crew 服务不可用：' + error.message); }
      this.url = candidate;
      return { url: candidate, owned: false };
    }
    let record;
    try { record = JSON.parse(await fs.readFile(path.join(this.config.home, 'web.json'), 'utf8')); }
    catch (error) { if (error.code !== 'ENOENT') throw new Error('无法读取 Crew 服务登记：' + error.message); }
    if (record !== undefined) {
      let candidate;
      try { candidate = loopback(record.url); }
      catch (error) { throw new Error('Crew 服务登记无效：' + error.message); }
      if (this.config.smoke && new URL(candidate).port === '47811') throw new Error('smoke 不得连接正式端口');
      try { await projects(candidate); this.url = candidate; return { url:candidate, owned:false }; }
      catch (error) {
        // A reachable or ambiguous service may still own live sessions. Only
        // a refused connection proves that starting a replacement is safe.
        if (!connectionRefused(error)) throw new Error('现有 Crew 服务暂时不可用，请稍后重试：' + error.message);
      }
    }
    const script = path.join(this.config.backendDir, 'crew_desktop.py');
    await fs.access(script);
    await fs.mkdir(this.config.home, { recursive: true });
    const logHeader = 'Crew service started ' + new Date().toISOString() + '\n';
    this.logWrites=Promise.resolve();
    const saveLog=()=>{if(!this.logPath)return;const snapshot=logHeader+this.errors;this.logWrites=this.logWrites.then(()=>fs.writeFile(this.logPath,snapshot)).catch(()=>{});};
    const beginLog=async name=>{
      this.logPath=path.join(this.config.home,'desktop','logs',name);
      await fs.mkdir(path.dirname(this.logPath),{recursive:true});saveLog();
    };
    const args = [script, '--home', this.config.home, '--port', String(this.config.port)];
    if (this.config.isolated || this.config.smoke) args.push('--isolated-sources');
    this.child = this.spawn(this.config.python, args, { cwd: this.config.backendDir, windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe'], env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' } });
    this.owned = true;
    const child = this.child;
    child.on('error', error => { this.errors = (this.errors + '\n' + error.message).slice(-6000); saveLog(); });
    child.stderr.on('data', value => { this.errors = (this.errors + value.toString()).slice(-6000); saveLog(); });
    try {
      this.url = await new Promise((resolve, reject) => {
        let pending = '';
        const timer = setTimeout(() => finish(new Error('Crew 服务启动超时。' + this.errors)), 30000);
        const onError = error => finish(new Error('无法启动 Python：' + error.message));
        const onExit = code => { const error=new Error(`Crew 服务已退出 (${code})。${this.errors}`);error.exitCode=code;finish(error); };
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
      await beginLog('service.log');
      await projects(this.url);
      return { url: this.url, owned: true };
    } catch (error) {
      if(error.exitCode===75){
        // The Python helper lost the OS home lock before touching shared state.
        // Borrow the winner; never launch a second replacement on this path.
        this.owned=false;child.stdout.resume();
        const deadline=Date.now()+30000;
        let lastError=error;
        while(Date.now()<deadline){
          try{
            const record=JSON.parse(await fs.readFile(path.join(this.config.home,'web.json'),'utf8'));
            const candidate=loopback(record.url);
            if(this.config.smoke&&new URL(candidate).port==='47811')throw new Error('smoke 不得连接正式端口');
            await projects(candidate);this.url=candidate;return{url:candidate,owned:false};
          }catch(waitError){lastError=waitError;}
          await new Promise(resolve=>setTimeout(resolve,100));
        }
        throw new Error('另一个 Crew 正在启动，但尚未能连接，请稍后重试：'+lastError.message);
      }
      await beginLog('startup-'+process.pid+'.log');await this.stop();throw error;
    }
  }
  async stop() {
    if (!this.owned || !this.child || this.child.exitCode !== null) { await this.logWrites; return; }
    const child = this.child;
    await new Promise(resolve => {
      const timeout = setTimeout(() => { child.kill(); }, 6000);
      const deadline = setTimeout(() => { cleanup(); resolve(); }, 9000);
      const cleanup = () => { clearTimeout(timeout); clearTimeout(deadline); child.removeListener('exit', done); };
      const done = () => { cleanup(); resolve(); };
      child.once('exit', done);
      if (child.stdin.writable) child.stdin.end('shutdown\n'); else child.kill();
    });
    await this.logWrites;
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
