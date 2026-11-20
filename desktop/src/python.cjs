'use strict';
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const run = promisify(execFile);

class PythonVersionError extends Error {}

function defaultPython(platform = process.platform) {
  return platform === 'darwin' ? 'python3' : 'python';
}

async function checkPythonVersion(command, {platform = process.platform, execute = run} = {}) {
  const suffix = platform === 'darwin' ? ' 可用 Homebrew 安装：brew install python' : '';
  const prefix = `Crew 需要 Python 3.11 或更新版本，当前 ${command}`;
  let result;
  try {
    result = await execute(command, ['-c', "import sys;print('%d.%d'%sys.version_info[:2])"],
      {windowsHide:true, timeout:10000, encoding:'utf8'});
  } catch (error) {
    throw new PythonVersionError(`${prefix} 无法运行：${error.name}: ${error.message}。${suffix}`, {cause:error});
  }
  const version = result.stdout.trim();
  const match = /^(\d+)\.(\d+)$/.exec(version);
  if (!match) throw new PythonVersionError(`${prefix} 无法运行：Python 版本探测返回了无效输出 ${JSON.stringify(version)}。${suffix}`);
  const [, major, minor] = match.map(Number);
  if (major < 3 || major === 3 && minor < 11) throw new PythonVersionError(`${prefix} 是 ${version}。${suffix}`);
}

module.exports = { defaultPython, checkPythonVersion, PythonVersionError };
