'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const http = require('node:http');
const { createWriteStream } = require('node:fs');
const { pipeline } = require('node:stream/promises');
const { spawn, execFile } = require('node:child_process');
const { promisify } = require('node:util');
const { defaultPython, checkPythonVersion } = require('./python.cjs');
const { samePath } = require('./paths.cjs');
const run = promisify(execFile);

function loopback(value) {
  const url = new URL(value);
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) ||
      url.username || url.password || !url.port || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('Crew 服务必须使用本机 loopback 地址');
  }
  return url.origin;
}

// A LAN host is addressed as shown in its settings: "192.168.1.20:47812" or
// "http://192.168.1.20:47812". Only private IPv4 hosts with an explicit port.
function lanAddress(value) {
  if (typeof value !== 'string' || !value.trim()) throw new Error('请填写主机地址，例如 192.168.1.20:47812');
  const text = value.trim();
  let url;
  try { url = new URL(text.includes('://') ? text : 'http://' + text); }
  catch (error) { if (error instanceof TypeError) throw new Error('主机地址格式不正确：' + text); throw error; }
  const parts = url.hostname.split('.').map(Number);
  const privateHost = /^\d{1,3}(\.\d{1,3}){3}$/.test(url.hostname) && parts.every(part => part <= 255) &&
    (parts[0] === 10 || parts[0] === 127 || parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31 || parts[0] === 192 && parts[1] === 168);
  if (url.protocol !== 'http:' || url.username || url.password || !url.port || url.pathname !== '/' || url.search || url.hash || !privateHost) {
    throw new Error('主机地址必须是局域网 IPv4 地址加端口，例如 192.168.1.20:47812');
  }
  return url.origin;
}

const DEVICE_TOKEN = /^[A-Za-z0-9_-]{43}$/;
// The one LAN host this window is connected to. Its device token is attached
// only to requests for exactly this origin.
let remote = null;
function authorizeRemote(value) {
  if (value === null) { remote = null; return; }
  if (!value || !DEVICE_TOKEN.test(value.token || '') || typeof value.onUnauthorized !== 'function') throw new TypeError('局域网主机连接需要设备令牌和失效处理');
  remote = { origin:lanAddress(value.origin), token:value.token, onUnauthorized:value.onUnauthorized };
}
function serviceOrigin(base) {
  if (remote && base === remote.origin) return remote.origin;
  return loopback(base);
}

function options(args, {launchSettingsPath, platform = process.platform} = {}) {
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
  const product=require('./product.cjs');
  const defaults={home:path.join(os.homedir(),product.homeDirectory),python:defaultPython(platform),port:0};
  const file=explicit.launchSettingsPath || (explicit.isolated || explicit.smoke ? path.join(path.resolve(explicit.home || defaults.home),'desktop','launch-settings.json') : launchSettingsPath);
  const saved=require('./launch-settings.cjs').readSync(file);
  const stored=saved ? {home:saved.home,python:saved.python,port:saved.port,backendSource:saved.source,
    ...(saved.source==='custom'?{backendDir:saved.backend_directory}:{}),...(saved.mode==='existing'?{serviceUrl:saved.service_url}:{}),
    ...(saved.mode==='remote'?{remoteBackend:saved.remote_backend}:{})} : {};
  const result={...defaults,...stored,...explicit,...(file?{launchSettingsPath:path.resolve(file)}:{})};
  // Explicit local launch arguments start this computer's backend instead of a saved connection.
  if (['home','backendDir','port','serviceUrl'].some(key=>key in explicit)) delete result.remoteBackend;
  if (!('serviceUrl' in explicit) && ['home','backendDir','port'].some(key=>key in explicit)) delete result.serviceUrl;
  result.backendSource=explicit.backendDir ? 'custom' : stored.backendSource || 'bundled';
  if (!Number.isInteger(result.port) || result.port < 0 || result.port > 65535) throw new Error('端口必须在 0 到 65535 之间');
  if (result.smoke && (!args.some(value=>value==='--home'||value.startsWith('--home=')) || !result.smokeOutput)) throw new Error('smoke 必须显式指定 --home 和 --smoke-output');
  if (result.smokeVisual && !result.smoke) throw new Error('视觉验收只允许使用隔离 smoke 模式');
  result.home = path.resolve(result.home);
  const normalHome = path.resolve(os.homedir(), product.homeDirectory);
  const sameHome = samePath(result.home, normalHome, platform);
  result.instanceName = result.isolated || result.smoke ? product.name+' · 隔离测试' : sameHome ? product.name : product.name+' · 独立数据';
  if (result.smoke && (sameHome || result.port === 47811)) throw new Error('smoke 必须使用隔离 home 和测试端口');
  if (result.serviceUrl) {
    result.serviceUrl = loopback(result.serviceUrl);
    if (result.smoke && new URL(result.serviceUrl).port === '47811') throw new Error('smoke 不得连接正式端口');
  }
  if (result.profile && (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(result.profile) || workspaceName(result.profile) !== result.profile)) throw new Error('profile 必须是安全的短名称');
  result.userData = result.profile ? path.join(result.home, 'desktop', 'profiles', result.profile) : path.join(result.home, 'desktop', 'ui');
  return result;
}

async function send(origin, token, route, body, timeoutMs, signal) {
  if (!route.startsWith('/api/')) throw new Error('不支持的 Crew API');
  const deadline=AbortSignal.timeout(timeoutMs),requestSignal=signal?AbortSignal.any([signal,deadline]):deadline;
  const response = await fetch(origin + route, { method: body === undefined ? 'GET' : 'POST',
    headers: { 'X-Crew': '1', ...(token ? { Authorization:'Bearer ' + token } : {}), ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body), redirect: 'error',
    signal: requestSignal });
  if (token && remote?.token === token && response.status === 401) remote.onUnauthorized();
  let payload;
  try { payload = await response.json(); }
  catch (_) {
    if(requestSignal.aborted)throw requestSignal.reason;
    const error = new Error(`Crew 服务返回了无效内容 (${response.status})`); error.status = response.status; throw error;
  }
  if (!response.ok) {
    const message=response.status===404&&route.startsWith('/api/workspace-data/')
      ? '当前后台版本尚不支持工作区数据功能。请在工具与更新中统一后台与插件，或退出旧 Crew 后重新打开新版。'
      : payload.error || `Crew 服务返回 ${response.status}`;
    const error = new Error(message); error.status = response.status; throw error;
  }
  return payload;
}

async function request(base, route, body, timeoutMs = 5000, signal) {
  const origin = serviceOrigin(base);
  return send(origin, remote?.origin === origin ? remote.token : null, route, body, timeoutMs, signal);
}

// Checks a saved LAN host before it becomes this window's connection.
function remoteRequest(credential, route, body, timeoutMs = 8000) {
  if (!DEVICE_TOKEN.test(credential?.token || '')) throw new TypeError('局域网主机请求需要设备令牌');
  return send(lanAddress(credential.url), credential.token, route, body, timeoutMs);
}

// Streams a host download to a local file; a failed transfer never leaves a partial file at the destination.
// node:http rather than fetch: undici asserts (uncaught, in the main process) when the host closes
// its connection while a large body is paused by disk backpressure.
async function download(base, route, destination, idleTimeoutMs = 60000) {
  const origin = serviceOrigin(base), token = remote?.origin === origin ? remote.token : null;
  if (!route.startsWith('/api/')) throw new Error('不支持的 Crew API');
  const response = await new Promise((resolve, reject) => {
    const request = http.get(origin + route, { headers:{ 'X-Crew':'1', ...(token ? { Authorization:'Bearer ' + token } : {}) }, timeout:idleTimeoutMs }, resolve);
    request.on('timeout', () => request.destroy(new Error('主机在 ' + idleTimeoutMs / 1000 + ' 秒内没有继续发送下载内容')));
    request.on('error', reject);
  });
  if (token && response.statusCode === 401) remote.onUnauthorized();
  if (response.statusCode !== 200) {
    const chunks = []; for await (const chunk of response) chunks.push(chunk);
    let payload;
    try { payload = JSON.parse(Buffer.concat(chunks).toString('utf8')); }
    catch (_) { const error = new Error(`Crew 服务返回了无效内容 (${response.statusCode})`); error.status = response.statusCode; throw error; }
    const error = new Error(payload.error || `Crew 服务返回 ${response.statusCode}`); error.status = response.statusCode; throw error;
  }
  const temporary = destination + '.' + crypto.randomUUID() + '.download';
  try {
    try { await pipeline(response, createWriteStream(temporary, { flags:'wx' })); }
    catch (error) { if (error.code === 'ECONNRESET') throw new Error('主机在下载完成前断开了连接，请重新导出', { cause:error }); throw error; }
    if (response.headers['content-length'] !== undefined && (await fs.stat(temporary)).size !== Number(response.headers['content-length'])) throw new Error('下载内容不完整，请重新导出');
    await fs.rename(temporary, destination);
  } finally { await fs.rm(temporary, { force:true }); }
  return { path:destination, bytes:(await fs.stat(destination)).size };
}

// Desktop pairing: the host returns a device token in JSON and never sets a cookie.
async function pairDesktop(address, { code, device_name }) {
  const origin = lanAddress(address);
  const response = await fetch(origin + '/remote/login', { method:'POST', redirect:'error', signal:AbortSignal.timeout(10000),
    headers:{ 'X-Crew':'1', 'Content-Type':'application/json' }, body:JSON.stringify({ code, device_name, kind:'desktop' }) });
  let payload;
  try { payload = await response.json(); }
  catch (_) { throw new Error(`此地址没有返回 Crew 配对结果 (${response.status})，请确认填写的是主机设置里显示的地址`); }
  if (!response.ok) { const error = new Error(payload.error || `主机返回 ${response.status}`); error.status = response.status; throw error; }
  if (payload.ok !== true || typeof payload.device_id !== 'string' || !payload.device_id || !DEVICE_TOKEN.test(payload.token || '')) throw new Error('主机返回的配对结果无效，请确认地址是 Crew 主机');
  return { url:origin, device_id:payload.device_id, token:payload.token };
}

function remoteHealth(value) {
  if (value?.ok !== true || value.service !== 'crew' || typeof value.version !== 'string' || !value.version ||
      typeof value.home !== 'string' || !value.home || !value.instance_id || typeof value.host_name !== 'string' || !value.host_name.trim()) throw new Error('此地址没有返回有效的 Crew 主机信息，请确认地址和主机版本');
  return value;
}

const DIRECTORY_STATES = ['ready', 'missing', 'error'];
async function projects(base, includeRemoved = false) {
  const data = await request(base, '/api/projects' + (includeRemoved ? '?include_removed=1' : ''));
  // Directory state comes from the backend that owns the folders (this computer or a LAN host).
  if (!Array.isArray(data.projects) || data.projects.some(item => !item || typeof item.name !== 'string' || !item.name ||
      typeof item.root !== 'string' || !item.root || (item.read_only !== true && !DIRECTORY_STATES.includes(item.directory_state?.status)))) {
    throw new Error('此地址没有返回有效的 Crew 工作区列表');
  }
  return data.projects;
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
    const {stdout} = await run(config.python || defaultPython(), [script, '--home', config.home, '--probe'],
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
      catch (error) { throw new Error('指定的 Crew 服务不可用：' + error.message, {cause:error}); }
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
    await checkPythonVersion(this.config.python);
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
    const env = { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' };
    this.child = this.spawn(this.config.python, args, { cwd: this.config.backendDir, windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe'], env });
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

// Folder checks, Git root resolution and folder creation run in the backend
// that owns the folders, so this computer and a LAN host share one implementation.
function existingWorkspace(directory, base, {newProject=false} = {}) {
  return request(base, '/api/host/workspace/existing', { directory, new_project:newProject }, 30000);
}

function newWorkspace(parent, name, base) {
  return request(base, '/api/host/workspace/new', { parent, name }, 30000);
}

function hostFolders(base, directory) {
  if (typeof directory !== 'string') throw new TypeError('文件夹路径必须是文本');
  return request(base, '/api/host/folders?' + new URLSearchParams({ path:directory }), undefined, 15000);
}

// A saved LAN host. Nothing runs or is stopped on this computer.
class RemoteBackend {
  constructor(id, { credentials, onUnauthorized }) {
    this.id = id; this.credentials = credentials; this.onUnauthorized = onUnauthorized;
    this.remote = true; this.owned = false; this.child = null; this.url = 'http://127.0.0.1:0';
    this.name = null; this.health = null; this.startupError = null;
  }
  async start() {
    const credential = await this.credentials.credential(this.id);
    this.name = credential.name;
    authorizeRemote({ origin:credential.url, token:credential.token, onUnauthorized:this.onUnauthorized });
    this.url = credential.url;
    const context = await request(this.url, '/api/access/context', undefined, 8000);
    if (context?.client !== 'desktop') throw new Error('主机没有把这台电脑识别为 Crew 桌面客户端，请重新配对');
    this.health = remoteHealth(await request(this.url, '/api/admin/health', undefined, 8000));
    return { url:this.url, owned:false };
  }
  async stop() {}
}

module.exports = { loopback, lanAddress, authorizeRemote, serviceOrigin, options, request, remoteRequest, download, pairDesktop, remoteHealth,
  projects, hostFolders, Backend, RemoteBackend, workspaceName, existingWorkspace, newWorkspace, materializeBackend, connectionRefused };
