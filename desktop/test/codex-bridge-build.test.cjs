'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { buildBridge } = require('../scripts/build-codex-bridge.cjs');

async function directory(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'crew-bridge-build-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  return root;
}

test('missing .NET compiler fails the build even if an old executable exists', async t => {
  const root = await directory(t), source = path.join(root, 'Bridge.cs'), output = path.join(root, 'Bridge.exe');
  await fs.writeFile(source, 'class Bridge { static void Main() {} }');
  await fs.writeFile(output, 'old executable must not count as a successful build');
  assert.throws(() => buildBridge({ source, output, systemRoot: path.join(root, 'no Windows') }), /csc\.exe/);
});

test('the real compiler produces an executable from source paths with spaces and Unicode', { skip: process.platform !== 'win32' }, async t => {
  const root = await directory(t), source = path.join(root, '桥接 source.cs'), output = path.join(root, 'output folder', 'Bridge.exe');
  await fs.writeFile(source, 'using System; using System.Web.Script.Serialization; class Bridge { static void Main() { Console.Write(new JavaScriptSerializer().Serialize(new { ok = true })); } }');
  assert.equal(buildBridge({ source, output }), output);
  assert.deepEqual(JSON.parse(execFileSync(output, [], { windowsHide: true, encoding: 'utf8' })), { ok: true });
  await fs.writeFile(source, 'this is not C#');
  assert.throws(() => buildBridge({ source, output }), /编译失败/);
});
