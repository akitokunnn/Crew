'use strict';
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const run = promisify(execFile);
const BEGIN = '__CREW_PATH_BEGIN__';
const END = '__CREW_PATH_END__';

function pathCommand() {
  return `printf '${BEGIN}%s${END}' "$PATH"`;
}

function parseShellPath(stdout) {
  const start = stdout.indexOf(BEGIN);
  const end = stdout.indexOf(END, start + BEGIN.length);
  if (start === -1 || end === -1) throw new Error('输出缺少完整的 PATH 标记');
  const value = stdout.slice(start + BEGIN.length, end);
  if (!value) throw new Error('登录 shell 返回的 PATH 为空');
  return value;
}

async function readLoginShellPath(shell = process.env.SHELL || '/bin/zsh', execute = run) {
  let output, exitError;
  const failure = (error, stderr) => {
    const summary = stderr?.trim().slice(-1500);
    return new Error(`无法读取登录 shell 的 PATH（${shell} -ilc）：${error.name}: ${error.message}${summary ? '；stderr：' + summary : ''}。请检查 ~/.zprofile、~/.zshrc 后重新打开 Crew。`, {cause:error});
  };
  try {
    output = await execute(shell, ['-ilc', pathCommand()], {encoding:'utf8', timeout:10000, killSignal:'SIGKILL'});
  } catch (error) {
    if (!Number.isInteger(error.code)) {
      const reason = error.killed && error.signal === 'SIGKILL' && error.code === null
        ? new Error('登录 shell 超时（10 秒）', {cause:error}) : error;
      throw failure(reason, error.stderr);
    }
    output = error;
    exitError = error;
  }
  try { return parseShellPath(output.stdout); }
  catch (error) {
    if (exitError) error.message += `（shell 退出码 ${exitError.code}）`;
    throw failure(error, output.stderr);
  }
}

module.exports = { pathCommand, parseShellPath, readLoginShellPath };
