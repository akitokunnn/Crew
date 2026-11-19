'use strict';
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs/promises'), path = require('node:path'), os = require('node:os');
const { RemoteBackends } = require('../src/remote-backends.cjs');

const TOKEN = 'T'.repeat(43), NEW_TOKEN = 'N'.repeat(43);
// Reversible stand-in for DPAPI / Keychain: the saved text must never contain the token itself.
const safeStorage = (available = true, rotated = false) => ({ isAsyncEncryptionAvailable:async () => available,
  encryptStringAsync:async text => Buffer.from((rotated ? 'sealed2:' : 'sealed1:') + [...text].reverse().join('')),
  decryptStringAsync:async buffer => ({ result:[...buffer.toString().slice(8)].reverse().join(''), shouldReEncrypt:rotated && buffer.toString().startsWith('sealed1:') }) });
async function fixture(t, options = {}) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'crew-remote-backends-'));
  t.after(() => fs.rm(root, { recursive:true, force:true }));
  const file = path.join(root, 'Crew', 'remote-backends.json'), pairs = [];
  const tokens = [TOKEN, NEW_TOKEN];
  const store = new RemoteBackends(file, { safeStorage:safeStorage(options.available !== false, options.rotated), now:() => new Date('2026-11-19T08:00:00Z'),
    pair:async (url, body) => { pairs.push({ url, body }); if (options.refuse) throw Error('配对码无效或已过期，请在电脑上重新生成'); return { url, device_id:'device-' + pairs.length, token:tokens[pairs.length - 1] }; } });
  return { root, file, store, pairs };
}

test('pairing saves the host beside the launch settings with only an OS-encrypted token', async t => {
  const f = await fixture(t);
  assert.deepEqual(await f.store.list(), []);
  const saved = await f.store.pair({ url:' 192.168.1.20:47812 ', code:' 12345678 ', device_name:' 书房电脑 ' });
  assert.deepEqual(f.pairs, [{ url:'http://192.168.1.20:47812', body:{ code:'12345678', device_name:'书房电脑' } }]);
  assert.deepEqual(Object.keys(saved).sort(), ['device_id', 'id', 'name', 'paired_at', 'url']);
  assert.equal(saved.name, '192.168.1.20:47812'); assert.equal(saved.paired_at, '2026-11-19T08:00:00.000Z');
  const raw = await fs.readFile(f.file, 'utf8'), data = JSON.parse(raw);
  assert.equal(data.version, 1); assert.equal(data.backends.length, 1); assert(!raw.includes(TOKEN), 'the device token is never written as text');
  assert.deepEqual(await f.store.list(), [saved]);
  assert.equal((await f.store.credential(saved.id)).token, TOKEN);
});

test('pairing the same address again replaces its token and device in place', async t => {
  const f = await fixture(t);
  const first = await f.store.pair({ url:'http://192.168.1.20:47812', code:'12345678', device_name:'书房电脑' });
  const second = await f.store.pair({ url:'192.168.1.20:47812/', code:'87654321', device_name:'书房电脑' });
  assert.equal(second.id, first.id); assert.equal(second.device_id, 'device-2');
  assert.equal((await f.store.list()).length, 1); assert.equal((await f.store.credential(first.id)).token, NEW_TOKEN);
});

test('without OS secure storage, or with invalid input, no pairing request is made and nothing is saved', async t => {
  const f = await fixture(t, { available:false });
  await assert.rejects(f.store.pair({ url:'192.168.1.20:47812', code:'12345678', device_name:'x' }), /系统安全存储不可用/);
  const g = await fixture(t);
  for (const [body, message] of [[{ url:'8.8.8.8:47812', code:'12345678', device_name:'x' }, /局域网 IPv4/], [{ url:'192.168.1.20:47812', code:'1234', device_name:'x' }, /8 位配对码/],
    [{ url:'192.168.1.20:47812', code:'12345678', device_name:' ' }, /本机设备名/]]) await assert.rejects(g.store.pair(body), message);
  assert.deepEqual([...f.pairs, ...g.pairs], []);
  await assert.rejects(fs.access(f.file), { code:'ENOENT' }); await assert.rejects(fs.access(g.file), { code:'ENOENT' });
  const refused = await fixture(t, { refuse:true });
  await assert.rejects(refused.store.pair({ url:'192.168.1.20:47812', code:'12345678', device_name:'x' }), /配对码无效或已过期/);
  await assert.rejects(fs.access(refused.file), { code:'ENOENT' });
});

test('a damaged or hand-edited device file is reported and kept instead of being reset', async t => {
  const f = await fixture(t), saved = await f.store.pair({ url:'192.168.1.20:47812', code:'12345678', device_name:'x' });
  const valid = JSON.parse(await fs.readFile(f.file, 'utf8'));
  for (const value of ['broken', { version:2, backends:[] }, { ...valid, extra:true }, { version:1, backends:[{ ...valid.backends[0], url:'http://8.8.8.8:47812' }] },
    { version:1, backends:[valid.backends[0], { ...valid.backends[0], id:'other' }] }, { version:1, backends:[{ ...valid.backends[0], token:'' }] }]) {
    const text = typeof value === 'string' ? value : JSON.stringify(value);
    await fs.writeFile(f.file, text);
    await assert.rejects(f.store.list(), /无法读取已保存的局域网设备/);
    assert.equal(await fs.readFile(f.file, 'utf8'), text);
  }
  await fs.writeFile(f.file, JSON.stringify(valid));
  await assert.rejects(f.store.credential('missing'), /已保存的局域网设备不存在/);
  assert.equal((await f.store.credential(saved.id)).url, 'http://192.168.1.20:47812');
});

test('a token sealed with a rotated OS key is re-encrypted with the current key when read', async t => {
  const f = await fixture(t), saved = await f.store.pair({ url:'192.168.1.20:47812', code:'12345678', device_name:'x' });
  const before = JSON.parse(await fs.readFile(f.file, 'utf8')).backends[0].token;
  const rotated = new RemoteBackends(f.file, { safeStorage:safeStorage(true, true) });
  assert.equal((await rotated.credential(saved.id)).token, TOKEN);
  const after = JSON.parse(await fs.readFile(f.file, 'utf8')).backends[0];
  assert.notEqual(after.token, before); assert.equal(Buffer.from(after.token, 'base64').toString().startsWith('sealed2:'), true);
  assert.equal((await rotated.credential(saved.id)).token, TOKEN);
});
