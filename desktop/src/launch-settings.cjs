'use strict';
const fs = require('node:fs/promises');
const sync = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const net = require('node:net');
const {execFile} = require('node:child_process');
const {promisify} = require('node:util');
const execute = promisify(execFile);
const FIELDS = ['mode','home','source','backend_directory','service_url','port','python','remote_backend'];
const { samePath } = require('./paths.cjs');
const { checkPythonVersion } = require('./python.cjs');

function shape(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length !== FIELDS.length || FIELDS.some(key => !(key in value))) throw Error('启动设置必须完整填写连接方式、目录、地址、端口、Python 与局域网设备。');
  if (!['local','existing','remote'].includes(value.mode) || !['bundled','custom'].includes(value.source)) throw Error('请选择有效的连接方式和后台来源。');
  for (const key of ['home','backend_directory','service_url','python','remote_backend']) if (typeof value[key] !== 'string' || value[key].includes('\0')) throw Error(key+' 必须是有效文本。');
  if ((value.mode === 'remote') !== Boolean(value.remote_backend)) throw Error(value.mode === 'remote' ? '请选择要连接的局域网设备。' : '只有连接局域网主机时才选择设备。');
  if (!value.python.trim()) throw Error('请填写 Python 可执行程序。');
  if (!Number.isInteger(value.port) || value.port < 0 || value.port > 65535) throw Error('端口必须在 0 到 65535 之间，0 表示自动选择。');
  return {...value,python:value.python.trim()};
}

// Settings saved before LAN hosts existed have no remote_backend field. Reading
// adds it explicitly; the next save writes the current structure.
function migrate(value) {
  const legacy = FIELDS.filter(key => key !== 'remote_backend');
  if (value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length === legacy.length && legacy.every(key => key in value)) return {...value,remote_backend:''};
  return value;
}

function readSync(file) {
  if (!file) return null;
  let raw;
  try { raw = sync.readFileSync(file,'utf8'); }
  catch (error) { if (error.code === 'ENOENT') return null; throw error; }
  try { return shape(migrate(JSON.parse(raw))); }
  catch (error) { throw Error('无法读取 Crew 启动设置 '+file+'：'+error.message); }
}

function fromConfig(config, backend) {
  return {mode:config.remoteBackend ? 'remote' : config.serviceUrl ? 'existing' : 'local',home:config.home,source:config.backendSource || 'bundled',
    backend_directory:config.backendSource === 'custom' ? config.backendDir : config.bundledBackendDir || config.backendDir,
    service_url:config.serviceUrl || '',port:config.port,python:config.python,remote_backend:config.remoteBackend || ''};
}

function relaunchArgs(args, settingsPath) {
  const removed = new Set(['--home','--backend-dir','--service-url','--port','--python','--launch-settings']);
  const result = [];
  for (let index = 0; index < args.length; index++) {
    const argument = args[index], key = argument.split('=',1)[0];
    if (removed.has(key)) { if (!argument.includes('=')) index++; continue; }
    result.push(argument);
  }
  return [...result,'--launch-settings',settingsPath];
}

async function save(file, settings) {
  await fs.mkdir(path.dirname(file),{recursive:true});
  const temporary = file+'.'+crypto.randomUUID()+'.tmp';
  await fs.writeFile(temporary,JSON.stringify(settings,null,2)+'\n','utf8');
  await fs.rename(temporary,file);
}

// Workspace pages keep drafts, theme and layout in per-origin storage, so an
// automatically chosen port is kept for later launches. Only a local backend on
// "0 = automatic" with the normal settings file is remembered; explicit,
// isolated, smoke and LAN/existing-service launches stay untouched.
async function rememberPort(config, url) {
  if (config.smoke || config.isolated || config.remoteBackend || config.serviceUrl || config.port !== 0 || !config.launchSettingsPath) return false;
  const port = Number(new URL(url).port);
  if (!Number.isInteger(port) || port <= 0) return false;
  const current = readSync(config.launchSettingsPath);
  if (current && (current.mode !== 'local' || current.port !== 0)) return false;
  await save(config.launchSettingsPath, shape({...(current || fromConfig(config)), port}));
  config.port = port;
  return true;
}

async function availablePort(port) {
  await new Promise((resolve,reject)=>{
    const server=net.createServer();
    server.once('error',reject);
    server.listen({host:'127.0.0.1',port,exclusive:true},()=>server.close(error=>error?reject(error):resolve()));
  });
}

class LaunchSettings {
  constructor(config, {backend,updates,operations,remotes,bundledBackend,request,remoteRequest,run=execute,probePort=availablePort,onApplied=()=>{}}) {
    this.config=config; this.backend=backend; this.updates=updates; this.operations=operations; this.remotes=remotes;
    this.bundledBackend=bundledBackend; this.request=request || require('./core.cjs').request; this.remoteRequest=remoteRequest || require('./core.cjs').remoteRequest;
    this.run=run; this.probePort=probePort; this.onApplied=onApplied; this.pending=null;
  }
  reconnectRequired() {
    return Boolean(this.backend.startupError || this.backend.owned && this.backend.child &&
      (this.backend.child.exitCode !== null || this.backend.child.signalCode));
  }
  ownsRunningBackend() {
    return Boolean(this.backend.owned && Number.isSafeInteger(this.backend.child?.pid) && this.backend.child.exitCode === null && !this.backend.child.signalCode);
  }
  async get() {
    const settings=fromConfig(this.config,this.backend);
    let actual_service=null,actual_service_error=null;
    if(this.backend.url && new URL(this.backend.url).port!=='0'){
      try { actual_service=this.backend.remote ? require('./core.cjs').remoteHealth(await this.request(this.backend.url,'/api/admin/health')) : await this._health(this.backend.url); }
      catch(error){actual_service_error=error.message;}
    }else actual_service_error=this.backend.startupError || '后台尚未连接，实际运行来源未知。';
    return {current:{...settings,backend_directory:this.config.backendDir,service_url:this.backend.remote ? '' : this.backend.url || settings.service_url},
      settings,settings_path:this.config.launchSettingsPath,owned:this.backend.owned,applying:Boolean(this.pending),actual_service,actual_service_error,reconnect_required:this.reconnectRequired(),
      remote_backends:await this.remotes.list(),device_name:os.hostname()};
  }
  async pair(body) {
    if (!body || typeof body !== 'object' || Array.isArray(body) || Object.keys(body).some(key => !['url','code','device_name'].includes(key))) throw Error('配对信息只能包含主机地址、配对码和本机设备名。');
    const backend=await this.remotes.pair(body);
    return {backend,remote_backends:await this.remotes.list()};
  }
  async _currentService(checkHealth, impact) {
    if (!this.ownsRunningBackend() || this.backend.startupError) return null;
    const currentService=await checkHealth(this.backend.url);
    if (!samePath(currentService.home,this.config.home)) throw Error('当前后台的数据目录与桌面不一致，切换已停止。');
    let plan;
    try{plan=await this.request(this.backend.url,'/api/admin/connection/plan');}
    catch(error){if([404,405].includes(error.status))throw Error('当前后台版本不支持连接切换预览，请先升级后台。');throw error;}
    if (!Array.isArray(plan.sessions) || !Number.isInteger(plan.goals_count) || !Number.isInteger(plan.running_count)) throw Error('当前后台不支持连接切换预览；请先升级后台。');
    Object.assign(impact,plan);
    return currentService;
  }
  // A LAN host only replaces the connection; this computer's own HOME, source,
  // port and Python stay saved for switching back, and are not checked here.
  async _validateRemote(selected, checkHealth) {
    const core=require('./core.cjs'),current=fromConfig(this.config,this.backend);
    const credential=await this.remotes.credential(selected.remote_backend);
    const context=await this.remoteRequest(credential,'/api/access/context');
    if (context?.client !== 'desktop') throw Error('主机没有把这台电脑识别为 Crew 桌面客户端，请重新配对。');
    const service=core.remoteHealth(await this.remoteRequest(credential,'/api/admin/health'));
    const manifest=JSON.parse(await fs.readFile(path.join(this.bundledBackend,'.claude-plugin','plugin.json'),'utf8'));
    const settings={...current,mode:'remote',service_url:'',remote_backend:credential.id};
    const impact={home_changed:false,source_changed:false,connection_changed:current.mode !== 'remote' || current.remote_backend !== credential.id,
      copy_required:false,sessions:[],goals_count:0,running_count:0,reconnect_required:this.reconnectRequired()};
    const currentService=await this._currentService(checkHealth,impact);
    return {settings,impact,service,current_service:currentService,inspection:null,source_version:service.version,
      remote:{id:credential.id,name:credential.name,url:credential.url,version:service.version,local_version:manifest.version}};
  }
  async _health(url) {
    const health=await this.request(url,'/api/admin/health');
    if (health?.ok !== true || health.service !== 'crew' || !Number.isSafeInteger(health.pid) || !health.instance_id || !health.version || typeof health.plugin_root!=='string' ||
        typeof health.home !== 'string' || !path.isAbsolute(health.home)) throw Error('此服务未返回有效的 Crew 身份；请检查地址或先升级后台。');
    return health;
  }
  async _registeredService(home, checkHealth=url=>this._health(url)) {
    let record;
    try { record=JSON.parse(await fs.readFile(path.join(home,'web.json'),'utf8')); }
    catch(error){if(error.code==='ENOENT')return null;throw error;}
    const core=require('./core.cjs'),url=core.loopback(record.url);
    let health;
    try { health=await checkHealth(url); }
    catch(error){if(core.connectionRefused(error))return null;throw error;}
    if(!samePath(health.home,home))throw Error('目标目录的服务登记与实际 Crew 身份不一致，请先处理旧登记。');
    return health;
  }
  async validate(body) {
    if (!body || !['copy','existing'].includes(body.home_action)) throw Error('请选择复制现有数据，或使用目标目录已有数据。');
    const settings=shape(body.settings), core=require('./core.cjs');
    const checked=new Map(),checkHealth=url=>{
      url=core.loopback(url);
      if(!checked.has(url))checked.set(url,this._health(url));
      return checked.get(url);
    };
    if (settings.mode === 'remote') return this._validateRemote(settings,checkHealth);
    let service=null;
    if (settings.mode === 'existing') {
      settings.service_url=core.loopback(settings.service_url);
      service=await checkHealth(settings.service_url);
      settings.home=await fs.realpath(service.home);
    } else {
      if (!settings.home || !path.isAbsolute(settings.home)) throw Error('数据目录必须是绝对路径。');
      settings.home=path.resolve(settings.home);
      settings.service_url='';
    }
    const directory=settings.mode==='existing' || settings.source === 'bundled' ? this.bundledBackend : settings.backend_directory;
    if (typeof directory !== 'string' || !path.isAbsolute(directory)) throw Error('后台程序目录必须是绝对路径。');
    const controlDirectory=await fs.realpath(directory);
    if(settings.mode==='local')settings.backend_directory=controlDirectory;
    for (const name of ['crew_supervisor.py',path.join('crew','web.py')]) {
      if (!(await fs.stat(path.join(controlDirectory,name))).isFile()) throw Error('后台源码不完整：缺少 '+name);
    }
    const manifest=JSON.parse(await fs.readFile(path.join(controlDirectory,'.claude-plugin','plugin.json'),'utf8'));
    if(manifest.name!=='crew'||typeof manifest.version!=='string'||!manifest.version.trim())throw Error('后台目录未提供有效的 Crew 插件名称和版本。');
    if(settings.mode==='local' && settings.port!==0){
      const url=core.loopback('http://127.0.0.1:'+settings.port);
      if(!(this.backend.owned && this.backend.child?.exitCode===null && !this.backend.child.signalCode && this.backend.url===url))await this.probePort(settings.port);
    }
    await checkPythonVersion(settings.python, {execute:this.run});
    const current=fromConfig(this.config,this.backend), homeChanged=!samePath(settings.home,current.home);
    const sourceChanged=settings.mode==='local' && (settings.source !== current.source || settings.source === 'custom' && !samePath(settings.backend_directory,current.backend_directory));
    const impact={home_changed:homeChanged,source_changed:sourceChanged,
      connection_changed:settings.mode !== current.mode || settings.service_url !== current.service_url || settings.port !== current.port || settings.python !== current.python,
      copy_required:settings.mode === 'local' && homeChanged && body.home_action === 'copy',sessions:[],goals_count:0,running_count:0,reconnect_required:this.reconnectRequired()};
    const borrowedHome='当前 HOME 的后台由其他窗口启动，不能复制正在使用的数据。请先退出原后台，或选择使用目标目录数据 / 连接已有服务。';
    if(impact.copy_required && this.backend.remote){
      // A LAN connection never ran this computer's HOME; another local window still may.
      if(await this._registeredService(this.config.home,checkHealth))throw Error(borrowedHome);
    }else if(impact.copy_required && !this.backend.owned && this.backend.url && new URL(this.backend.url).port!=='0'){
      try{await checkHealth(this.backend.url);throw Error(borrowedHome);}
      catch(error){if(!core.connectionRefused(error))throw error;}
    }
    if(settings.mode==='local'){
      const targetService=!homeChanged&&this.backend.owned?null:await this._registeredService(settings.home,checkHealth);
      if(targetService && (homeChanged || !this.backend.owned && (sourceChanged || impact.connection_changed)))throw Error('目标 HOME 已有运行中的后台。请改用“连接已有服务”，或先退出原后台，再应用本地来源、端口或 Python 设置。');
      if(!this.backend.remote && !this.backend.owned && !homeChanged && (sourceChanged || impact.connection_changed) && this.backend.url && new URL(this.backend.url).port!=='0'){
        try { await checkHealth(this.backend.url); throw Error('当前后台由其他窗口启动，不能替它更改程序来源、端口或 Python。请先退出原后台，或选择其他 HOME / 已有服务。'); }
        catch(error){if(!core.connectionRefused(error))throw error;}
      }
    }else if(this.backend.owned && this.backend.url===settings.service_url){
      throw Error('这个地址正由本窗口的本地后台提供。请保留本地模式；需要固定地址时修改本地端口。');
    }
    let inspection=null;
    if (settings.mode === 'local' && homeChanged) {
      inspection=await this.operations.request('home-inspect',{source:this.config.home,target:settings.home},{python:settings.python});
      if (impact.copy_required ? !inspection.can_copy && !inspection.already_copied : !inspection.can_connect && !['empty','missing'].includes(inspection.target_kind)) throw Error(inspection.reason || '目标数据目录不能用于这次切换。');
    }
    const currentService=await this._currentService(checkHealth,impact);
    return {settings,impact,service,current_service:currentService,inspection,source_version:service?service.version:manifest.version};
  }
  apply(body) {
    if (this.pending) return Promise.reject(Error('正在切换后台连接，请等待完成。'));
    this.pending=this._apply(body).finally(()=>{this.pending=null;});
    return this.pending;
  }
  async _apply(body) {
    const preview=await this.validate(body);
    if (!preview.impact.home_changed && !preview.impact.source_changed && !preview.impact.connection_changed && !preview.impact.reconnect_required) return {ok:true,restarting:false,...preview};
    if (preview.settings.mode === 'remote' && !this.ownsRunningBackend()) {
      // Nothing of this computer is running: no lock, stop, copy or Python is involved.
      await save(this.config.launchSettingsPath,preview.settings);
      await this.onApplied(preview.settings);
      return {ok:true,restarting:true,...preview};
    }
    if (this.updates.pending) throw Error('Crew 正在更新，请等待更新结束后再切换。');
    if (this.updates.checking) await this.updates.checking;
    this.updates.cancel();
    const lock=await this.updates._lock(preview.settings.python);
    try {
      lock.assert();
      if (this.ownsRunningBackend()) {
        let port=Number(new URL(this.backend.url).port),pids=[this.backend.child.pid];
        if(this.backend.startupError){
          if(!this.backend.supervised)throw Error('启动失败的后台不由独立管理员控制，请先退出原 Crew。');
        }else{
          const health=await this._health(this.backend.url);
          if (!preview.current_service || health.instance_id !== preview.current_service.instance_id || health.pid !== preview.current_service.pid) throw Error('当前后台已变化，请重新检查后再切换。');
          let prepared;
          try{prepared=await this.request(this.backend.url,'/api/admin/connection/prepare',{instance_id:health.instance_id},180000);}
          catch(error){if([404,405].includes(error.status))throw Error('当前后台版本不支持安全停止会话并切换连接，请先升级后台。');throw error;}
          if (!prepared.ok || prepared.instance_id !== health.instance_id || prepared.pid !== health.pid || !Array.isArray(prepared.managed_pids)) throw Error('后台没有确认会话已停止；启动设置未修改。');
          lock.assert();port=health.port || port;pids.push(health.pid,...prepared.managed_pids);
        }
        const stopping=this.backend.stop(); stopping.catch(()=>{});
        await this.updates._helper('stopped',['--port',String(port),...pids.filter(Number.isSafeInteger).flatMap(pid=>['--pid',String(pid)]),'--timeout','20'],preview.settings.python);
        await stopping; lock.assert();
      }
      if (preview.impact.copy_required) {
        await this.operations.request('home-copy',{source:this.config.home,target:preview.settings.home},{python:preview.settings.python});
        lock.assert();
      }
      if(preview.settings.mode==='local'){
        await this.operations.request('prepare-home',{home:preview.settings.home,
          reset_deployment:Boolean(preview.impact.home_changed || preview.impact.source_changed || !this.backend.remote && this.backend.startupError || this.config.serviceUrl)},{python:preview.settings.python});
        lock.assert();
      }
      await save(this.config.launchSettingsPath,preview.settings);
      await this.onApplied(preview.settings);
      return {ok:true,restarting:true,...preview};
    } finally { await lock.release(); }
  }
}

module.exports={LaunchSettings,shape,migrate,readSync,fromConfig,relaunchArgs,save,availablePort,rememberPort};
