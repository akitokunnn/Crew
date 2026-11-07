'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

function findCompiler(systemRoot = process.env.SystemRoot || process.env.WINDIR) {
  if (systemRoot) {
    for (const framework of ['Framework64', 'Framework']) {
      const candidate = path.join(systemRoot, 'Microsoft.NET', framework, 'v4.0.30319', 'csc.exe');
      if (fs.existsSync(candidate)) return candidate;
    }
  }
  throw new Error('Crew Codex 桌面桥接构建失败：未找到 Windows .NET Framework 4 的 csc.exe。请安装或启用 .NET Framework 后重新构建。');
}

function buildBridge(options = {}) {
  const desktop = path.resolve(__dirname, '..');
  const source = options.source || path.join(desktop, 'native', 'CrewCodexBridge.cs');
  const output = options.output || path.join(desktop, 'assets', 'CrewCodexBridge.exe');
  const compiler = findCompiler(options.systemRoot);
  if (!fs.existsSync(source)) throw new Error('Crew Codex 桌面桥接源码不存在：' + source);
  fs.mkdirSync(path.dirname(output), { recursive: true });
  const result = spawnSync(compiler, [
    '/nologo', '/target:exe', '/optimize+', '/platform:anycpu',
    '/reference:System.Web.Extensions.dll', '/out:' + output, source,
  ], { windowsHide: true, encoding: 'utf8', timeout: 120000 });
  if (result.error) throw new Error('Crew Codex 桌面桥接编译器启动失败：' + result.error.message);
  if (result.status !== 0) throw new Error('Crew Codex 桌面桥接编译失败：\n' + [result.stdout, result.stderr].filter(Boolean).join('\n'));
  if (!fs.existsSync(output)) throw new Error('Crew Codex 桌面桥接编译器未生成文件：' + output);
  return output;
}

if (require.main === module) {
  try { console.log('Codex desktop bridge: ' + buildBridge()); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}

module.exports = { buildBridge, findCompiler };
