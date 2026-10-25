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

function options(args, {launchSettingsPath} = {}) {
  const explicit = {};
  const fields = { '--home': 'home', '--python': 'python', '--port': 'port', '--backend-dir': 'backendDir', '--smoke-output': 'smokeOutput', '--service-url': 'serviceUrl', '--profile': 'profile', '--launch-settings':'launchSettingsPath' };
  for (let index = 0; index < args.length; index++) {
    const argument = args[index], separator=argument.indexOf('=');
    const key=separator<0?argument:argument.slice(0,separator);
    if (argument === '--isolated-sources') { explicit.isolated = true; continue; }
    if (argument === '--smoke-test') { explicit.smoke = true; continue; }
    if (argument === '--smoke-visual') { explicit.smokeVisual = true; continue; }
    if (!(key in fields)) continue; // Electron may add its own switches.
    const value = separator<0?args[++index]:argument.slice(separator+1);
    if (!value || value.startsWith('--')) throw new Error('参数缺少值：' + key);
    explicit[fields[key]] = key === '--port' ? Number(value) : value;
  }
  const defaults={home:path.join(os.homedir(),'.crew'),python:'python',port:0};
  const file=explicit.launchSettingsPath || (explicit.isolated || explicit.smoke ? path.join(path.resolve(explicit.home || defaults.home),'desktop','launch-settings.json') : launchSettingsPath);
  const saved=require('./launch-settings.cjs').readSync(file);
  const stored=saved ? {home:saved.home,python:saved.python,port:saved.port,backendSource:saved.source,
    ...(saved.source==='custom'?{backendDir:saved.backend_directory}:{}),...(saved.mode==='existing'?{serviceUrl:saved.service_url}:{})} : {};
  const result={...defaults,...stored,...explicit,...(file?{launchSettingsPath:path.resolve(file)}:{})};
  if (!('serviceUrl' in explicit) && ['home','backendDir','port'].some(key=>key in explicit)) delete result.serviceUrl;
  result.backendSource=explicit.backendDir ? 'custom' : stored.backendSource || 'bundled';
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

async function request(base, route, body, timeoutMs = 5000) {
  const origin = loopback(base);
  if (!route.startsWith('/api/')) throw new Error('不支持的 Crew API');
  const response = await fetch(origin + route, { method: body === undefined ? 'GET' : 'POST',
    headers: { 'X-Crew': '1', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body), redirect: 'error', signal: AbortSignal.timeout(timeoutMs) });
  let payload;
  try { payload = await response.json(); }
  catch (_) { const error = new Error(`Crew 服务返回了无效内容 (${response.status})`); error.status = response.status; throw error; }
  if (!response.ok) { const error = new Error(payload.error || `Crew 服务返回 ${response.status}`); error.status = response.status; throw error; }
  return payload;
}

async function projects(base, includeRemoved = false) {
  let data;
  try { data = await request(base, '/api/projects' + (includeRemoved ? '?include_removed=1' : '')); }
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

async function confirmedStopped(config, candidate) {
  if (!config.backendDir) return false;
  const script = path.join(config.backendDir, 'crew_supervisor.py');
  try {
    await fs.access(script);
    const {stdout} = await run(config.python || 'python', [script, '--home', config.home, '--probe'],
      {windowsHide:true,timeout:10000,maxBuffer:131072,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
    const probe = JSON.parse(stdout);
    return probe.can_start === true && probe.url === candidate;
  } catch (_) { return false; }
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
        // A timeout alone is ambiguous. The administrator's separate probe
        // also checks the recorded PID, port occupancy and the HOME OS lock.
        if (!connectionRefused(error) && !await confirmedStopped(this.config,candidate)) throw new Error('现有 Crew 服务暂时不可用，请稍后重试：' + error.message);
      }
    }
    let script = path.join(this.config.backendDir, 'crew_supervisor.py');
    try { await fs.access(script); this.supervised = true; }
    catch (error) {
      if (error.code !== 'ENOENT') throw error;
      script = path.join(this.config.backendDir, 'crew_desktop.py');
      this.supervised = false;
    }
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
        const timer = setTimeout(() => finish(new Error('Crew 服务启动超时。' + this.errors)), this.config.startupTimeoutMs ?? 30000);
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
      await beginLog('startup-'+process.pid+'.log');
      // A supervised startup failure must reach the file-backed health UI.
      // Keep its administrator alive; waiting for a hung backend to stop here
      // would prevent the window from ever opening. Explicit quit still owns
      // the shutdown request and normal exit waits for its actual completion.
      if (!this.supervised) await this.stop();
      throw error;
    }
  }
  async stop() {
    if (!this.owned || !this.child || this.child.exitCode !== null) { await this.logWrites; return; }
    const child = this.child;
    if (this.supervised) {
      // Never kill the administrator and strand its backend during shutdown.
      await new Promise(resolve => {
        const done = () => resolve();
        child.once('exit', done);
        if (child.exitCode !== null) { child.removeListener('exit',done); resolve(); }
        else if (child.stdin.writable) child.stdin.end('shutdown\n');
        else { child.removeListener('exit',done); resolve(); }
      });
      await this.logWrites;
      return;
    }
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

async function workspaceDirectory(directory) {
  const selected = await fs.realpath(directory);
  if (!(await fs.stat(selected)).isDirectory()) {
    const error = new Error('工作区路径不是文件夹：' + selected); error.code = 'ENOTDIR'; throw error;
  }
  return selected;
}

async function optionalGitRoot(selected) {
  let directory = selected;
  while (true) {
    try { await fs.lstat(path.join(directory, '.git')); break; }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    const parent = path.dirname(directory);
    if (parent === directory) return selected;
    directory = parent;
  }
  // A Git marker is explicit metadata. Corrupt repositories must keep their real Git error.
  const { stdout } = await run('git', ['-C', selected, 'rev-parse', '--show-toplevel'], { windowsHide: true, timeout: 10000 });
  return fs.realpath(stdout.trim());
}

async function projectLocations(items) {
  return Promise.all(items.map(async item=>{
    try { return {...item,directory_state:{status:(await fs.stat(item.root)).isDirectory()?'ready':'missing'}}; }
    catch(error) { return {...item,directory_state:['ENOENT','ENOTDIR'].includes(error.code)?{status:'missing'}:{status:'error',error:error.message}}; }
  }));
}

async function existingWorkspace(directory, base, {newProject=false} = {}) {
  const selected = await workspaceDirectory(directory);
  const registered = await projects(base, true);
  const normalize = value => process.platform === 'win32' ? path.resolve(value).toLowerCase() : path.resolve(value);
  const exact = registered.find(item => !item.removed_at && normalize(item.root) === normalize(selected)) || registered.find(item => normalize(item.root) === normalize(selected));
  if (exact) return exact.removed_at ? request(base, '/api/project/init', {name:exact.name, root:exact.root}) : exact;
  const root = await optionalGitRoot(selected);
  const present = registered.find(item => !item.removed_at && normalize(item.root) === normalize(root)) || registered.find(item => normalize(item.root) === normalize(root));
  if (present) return present.removed_at ? request(base, '/api/project/init', {name:present.name, root:present.root}) : present;
  if (!newProject) {
    const missing = (await projectLocations(registered.filter(item=>!item.removed_at))).filter(item=>item.directory_state.status==='missing');
    if (missing.length) return {relocation:{directory:root,projects:missing}};
  }
  const stem = path.basename(root);
  let name = stem, number = 2;
  while (registered.some(item => item.name === name)) name = `${stem} ${number++}`;
  return request(base, '/api/project/init', { name, root });
}

async function newWorkspace(parent, name, base) {
  name = workspaceName(name);
  const root = path.join(await workspaceDirectory(parent), name);
  await fs.mkdir(root); // Refuse an existing path; never overwrite someone else's files.
  // A registration failure retains the new folder for retry, never deletes it.
  return request(base, '/api/project/init', { name, root });
}

module.exports = { loopback, options, request, projects, projectLocations, Backend, workspaceName, existingWorkspace, newWorkspace, materializeBackend, connectionRefused };
