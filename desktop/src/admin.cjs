'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');

function alive(pid) {
  if (!Number.isSafeInteger(pid) || pid < 1) return false;
  try { process.kill(pid, 0); return true; } catch (_) { return false; }
}
class Administrator {
  constructor(home) { this.directory = path.join(home, 'desktop', 'admin'); }
  async snapshot() {
    try {
      const maintenance = JSON.parse(await fs.readFile(path.join(this.directory, 'maintenance.json'), 'utf8'));
      return {state:'paused', checked_at:null, supervisor_pid:null, service_pid:null, url:null, recoveries:0,
        incidents:[{at:maintenance.at,level:'info',message:maintenance.reason || '用户已暂停 Crew'}],
        restart:{allowed:false,reason:'用户已暂停 Crew，修复完成前保持停止'}};
    } catch (error) { if (error.code !== 'ENOENT') return this.unavailable('维护记录无法读取，请检查日志'); }
    let value;
    try { value = JSON.parse(await fs.readFile(path.join(this.directory, 'status.json'), 'utf8')); }
    catch (error) { return this.unavailable(error.code === 'ENOENT' ? '系统管理员尚未启动' : '管理员状态记录无法读取'); }
    if (!value || !/^[a-zA-Z0-9_-]{8,80}$/.test(value.instance_id || '') || !Array.isArray(value.incidents)) return this.unavailable('管理员状态记录无效');
    const fresh = Number.isFinite(Date.parse(value.checked_at)) && Math.abs(Date.now() - Date.parse(value.checked_at)) < 20000;
    const running = alive(value.supervisor_pid);
    if (!fresh || !running) return {...value, state:value.state==='stopped'?'stopped':'unknown',
      restart:{allowed:false,reason:running?'管理员状态已过期，暂不执行恢复':'系统管理员未运行，请重新打开 Crew'}};
    return {...value,incidents:value.incidents.slice(-50),restart:{allowed:value.restart?.allowed===true,reason:value.restart?.reason || ''}};
  }
  unavailable(reason) {
    return {state:'unmanaged',checked_at:null,supervisor_pid:null,service_pid:null,url:null,recoveries:0,incidents:[],restart:{allowed:false,reason}};
  }
  async request(action) {
    if (!['check','recover'].includes(action)) throw new Error('不支持的管理员操作');
    const current = await this.snapshot();
    if (!current.instance_id || !alive(current.supervisor_pid) || ['paused','unknown','stopped','unmanaged'].includes(current.state)) throw new Error(current.restart.reason || '管理员暂时不可用');
    if (action === 'recover' && !current.restart.allowed) throw new Error(current.restart.reason || '后台仍在运行，不能安全恢复');
    const request = {instance_id:current.instance_id,request_id:crypto.randomUUID(),action};
    const temporary = path.join(this.directory,'control-'+request.request_id+'.tmp');
    await fs.writeFile(temporary,JSON.stringify(request),'utf8');
    try { await fs.rename(temporary,path.join(this.directory,'control.json')); }
    finally { await fs.unlink(temporary).catch(()=>{}); }
    return {ok:true,status:'requested',request_id:request.request_id};
  }
}
module.exports = {Administrator};
