'use strict';
// Saved LAN hosts live beside the launch settings, outside every Crew HOME.
// Device tokens are stored only as OS-encrypted bytes (DPAPI / Keychain), through
// Electron's asynchronous safeStorage API (the synchronous one is deprecated).
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const core = require('./core.cjs');

const FIELDS = ['id', 'name', 'url', 'device_id', 'token', 'paired_at'];

function validate(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || value.version !== 1 || Object.keys(value).length !== 2 || !Array.isArray(value.backends)) throw Error('文件结构无效');
  const ids = new Set(), urls = new Set();
  for (const item of value.backends) {
    if (!item || typeof item !== 'object' || Array.isArray(item) || Object.keys(item).length !== FIELDS.length ||
        FIELDS.some(key => typeof item[key] !== 'string' || !item[key])) throw Error('设备记录不完整');
    if (core.lanAddress(item.url) !== item.url || Number.isNaN(Date.parse(item.paired_at)) || ids.has(item.id) || urls.has(item.url)) throw Error('设备记录无效：' + item.name);
    ids.add(item.id); urls.add(item.url);
  }
  return value;
}

class RemoteBackends {
  constructor(file, { safeStorage, pair = core.pairDesktop, health = credential => core.remoteRequest(credential, '/api/admin/health'), now = () => new Date() }) {
    this.file = file; this.safeStorage = safeStorage; this.pairDesktop = pair; this.health = health; this.now = now;
  }
  async _read() {
    let raw;
    try { raw = await fs.readFile(this.file, 'utf8'); }
    catch (error) { if (error.code === 'ENOENT') return { version:1, backends:[] }; throw error; }
    try { return validate(JSON.parse(raw)); }
    catch (error) { throw Error('无法读取已保存的局域网设备 ' + this.file + '：' + error.message); }
  }
  async list() {
    return (await this._read()).backends.map(({ token:_token, ...item }) => item);
  }
  async credential(id) {
    const data = await this._read(), item = data.backends.find(backend => backend.id === id);
    if (!item) throw Error('已保存的局域网设备不存在，请在连接设置中重新选择后台');
    const { result, shouldReEncrypt } = await this.safeStorage.decryptStringAsync(Buffer.from(item.token, 'base64'));
    if (shouldReEncrypt) {
      // The OS key was rotated: keep the stored copy readable with the current key.
      const token = (await this.safeStorage.encryptStringAsync(result)).toString('base64');
      await this._save({ version:1, backends:data.backends.map(backend => backend === item ? { ...item, token } : backend) });
    }
    return { ...item, token:result };
  }
  async pair({ url, code, device_name }) {
    if (!(await this.safeStorage.isAsyncEncryptionAvailable())) throw Error('系统安全存储不可用，不能保存设备令牌；请确认已登录系统账户后重试');
    const origin = core.lanAddress(url);
    if (typeof code !== 'string' || !/^\d{8}$/.test(code.trim())) throw Error('请输入主机上显示的 8 位配对码');
    if (typeof device_name !== 'string' || !device_name.trim()) throw Error('请填写本机设备名');
    const paired = await this.pairDesktop(origin, { code:code.trim(), device_name:device_name.trim() });
    // The host's own computer name, read with the new token, is how this device is listed and shown.
    const host = core.remoteHealth(await this.health({ url:paired.url, token:paired.token }));
    const data = await this._read(), previous = data.backends.find(item => item.url === paired.url);
    const record = { id:previous?.id || crypto.randomUUID(), name:host.host_name.trim(), url:paired.url,
      device_id:paired.device_id, token:(await this.safeStorage.encryptStringAsync(paired.token)).toString('base64'), paired_at:this.now().toISOString() };
    await this._save({ version:1, backends:previous ? data.backends.map(item => item === previous ? record : item) : [...data.backends, record] });
    const { token:_token, ...saved } = record;
    return saved;
  }
  async _save(value) {
    validate(value);
    await fs.mkdir(path.dirname(this.file), { recursive:true });
    const temporary = this.file + '.' + crypto.randomUUID() + '.tmp';
    await fs.writeFile(temporary, JSON.stringify(value, null, 2) + '\n', { encoding:'utf8', mode:0o600 });
    await fs.rename(temporary, this.file);
  }
}

module.exports = { RemoteBackends, validate };
