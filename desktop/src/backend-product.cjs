'use strict';
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');

// First-party product metadata is separate from the Claude plugin manifest.
function requireGenerationSync(directory, expected) {
  if (expected === undefined || expected === null) return null;
  const failure = detail => Object.assign(new Error(`Crew V${expected} 不能使用这个后台来源：${detail}。请选择带有正确 crew-product.json 标记的 V${expected} 源码目录。`), {code:'CREW_PRODUCT_GENERATION'});
  if (typeof directory !== 'string' || !directory.trim()) throw failure('未提供后台目录');
  const target = path.join(directory, 'crew-product.json');
  let marker;
  try { marker = JSON.parse(fs.readFileSync(target, 'utf8')); }
  catch (error) {
    if (error.code === 'ENOENT') throw failure(`${directory} 缺少代际标记，可能是 V1`);
    throw failure(`${target} 无法读取：${error.message}`);
  }
  if (!marker || marker.generation !== expected) throw failure(`${directory} 的 generation 不是 ${expected}`);
  return marker;
}

function requireHomeGenerationSync(home, expected) {
  if (expected === undefined || expected === null) return;
  const fail = message => Object.assign(new Error(message), {code:'CREW_PRODUCT_GENERATION'});
  if (typeof home !== 'string' || !path.isAbsolute(home)) throw fail('无法核验 Crew V'+expected+' 的数据目录，请选择独立的绝对路径。');
  const real = directory => {
    try { return fs.realpathSync(directory); }
    catch (error) { if (error.code === 'ENOENT') return path.resolve(directory); throw error; }
  };
  const canonical = value => process.platform === 'win32' ? value.toLowerCase() : value;
  if (expected === 2 && canonical(real(home)) === canonical(real(path.join(os.homedir(),'.crew')))) {
    throw fail('Crew V2 不能使用 V1 的 ~/.crew 数据目录。请选择 ~/.crew-v2 或另一个独立数据目录；旧历史通过发现与导入读取。');
  }
  let runtime;
  try { runtime = JSON.parse(fs.readFileSync(path.join(home,'runtime.json'),'utf8')); }
  catch (error) {
    if (error.code === 'ENOENT') {
      const defaultV2=canonical(real(path.join(os.homedir(),'.crew-v2')));
      if(expected===2 && canonical(real(home))!==defaultV2 && fs.existsSync(path.join(home,'crew.db'))) {
        throw fail('此自定义数据目录已有 crew.db，但没有可核验的 runtime.json 来源记录。Crew V2 不会改写来源未知的旧数据库；请选择独立的新目录，再通过历史发现导入。');
      }
      return;
    }
    throw fail('无法核验数据目录的 runtime.json：'+error.message);
  }
  try { requireGenerationSync(runtime?.plugin_root,expected); }
  catch (error) { throw fail('此数据目录记录了不同代际或无法核验的后台来源，Crew V'+expected+' 不会覆盖它：'+error.message); }
}

async function requireGeneration(directory,expected) { return requireGenerationSync(directory,expected); }
async function requireHomeGeneration(home,expected) { return requireHomeGenerationSync(home,expected); }

module.exports = {requireGeneration,requireHomeGeneration,requireHomeGenerationSync};
