'use strict';
// Exercise normal Electron quit in the source app or an actual packaged binary.
// The private backend must survive; only this runner explicitly stops it later.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { spawn } = require('node:child_process');
const core = require('../src/core.cjs');
const { Administrator } = require('../src/admin.cjs');
const desktop = path.resolve(__dirname, '..');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
function alive(pid) {
  try { process.kill(pid, 0); return true; }
  catch (error) { if (error.code === 'ESRCH') return false; throw error; }
}
async function main() {
  const binary = process.argv[2] ? path.resolve(process.argv[2]) : null;
  const executable = binary || require('electron');
  const kind = binary ? (binary.endsWith('-portable.exe') ? 'portable' : 'packaged') : 'source';
  await fs.mkdir(path.join(desktop, '.cache'), { recursive: true });
  const temporary = await fs.mkdtemp(path.join(desktop, '.cache', 'background-smoke-'));
  const home = path.join(temporary, 'home'), guard = path.join(temporary, 'guard');
  const output = path.join(desktop, 'smoke-output', 'background-' + kind + '.json');
  const evidence = path.join(temporary, 'normal-quit.json');
  await fs.mkdir(guard);
  await fs.mkdir(path.dirname(output), { recursive: true });
  await fs.writeFile(path.join(guard, 'sitecustomize.py'), `import builtins,sys,shutil
from pathlib import Path
original_which=shutil.which
def isolated_which(command,*args,**kwargs):
 if Path(command).stem.lower() in ('claude','codex'):return None
 return original_which(command,*args,**kwargs)
shutil.which=isolated_which
original_import=builtins.__import__
def guarded_import(*args,**kwargs):
 result=original_import(*args,**kwargs)
 module=sys.modules.get('crew.chat')
 if module is not None and hasattr(module,'Manager') and not getattr(module.Manager,'_background_smoke_guard',False):
  original_init=module.Manager.__init__
  def guarded_init(self,home,plugin_root,agents_provider,process_factory=None):
   (Path(home)/'model-factory-guarded').write_text('installed',encoding='utf-8')
   def forbidden(*args,**kwargs):
    (Path(home)/'model-start-attempted').write_text('blocked',encoding='utf-8')
    raise OSError('Background smoke forbids real model execution')
   original_init(self,home,plugin_root,agents_provider,forbidden)
  module.Manager.__init__=guarded_init
  module.Manager._background_smoke_guard=True
 return result
builtins.__import__=guarded_import
`, 'utf8');
  const env = { ...process.env, PYTHONPATH: guard, PYTHONIOENCODING: 'utf-8',
    CREW_PERF_DESKTOP: desktop, CREW_PERF_OUTPUT: evidence, TEMP: temporary, TMP: temporary };
  for (const key of ['ELECTRON_RUN_AS_NODE', 'CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'CREW_GOAL_ID', 'CREW_EXECUTION_ID', 'CODEX_THREAD_ID']) delete env[key];
  const args = [...(!binary ? ['--require', path.join(__dirname, 'benchmark-lifecycle-hook.cjs'), desktop] : []),
    '--home', home, '--port', '0', '--isolated-sources'];
  const administrator = new Administrator(home);
  let result;
  try {
    const child = spawn(executable, args, { env, windowsHide: !binary, stdio: ['ignore', 'pipe', 'pipe'] });
    let errors = '';
    child.stdout.resume();
    child.stderr.on('data', value => { errors = (errors + value).slice(-10000); });
    const started = Date.now();
    const ended = new Promise((resolve, reject) => {
      const timer = setTimeout(() => { child.kill(); reject(Error('Private Electron background smoke timed out: ' + errors)); }, 60000);
      child.once('error', error => { clearTimeout(timer); reject(error); });
      child.once('exit', value => { clearTimeout(timer); resolve(value); });
    });
    ended.catch(() => {}); // The readiness probe awaits this same result below.
    let observed;
    if (binary) {
      // Packaged Electron filters --require/NODE_OPTIONS. Use the actual
      // native window's Quit action, with no injected application hook.
      const deadline = Date.now() + 30000;
      while (Date.now() < deadline && child.exitCode === null) {
        try {
          const registration = JSON.parse(await fs.readFile(path.join(home, 'web.json'), 'utf8'));
          const health = await core.request(registration.url, '/api/admin/health');
          if (health.home === home && health.ok) {
            observed = { packaged: true, health, service: { url: registration.url },
              exit_method: 'native window quit, no injected application code' };
            break;
          }
        } catch (error) { if (child.exitCode !== null) throw error; }
        await delay(50);
      }
      assert(observed, 'The packaged app must publish its private real backend');
      console.log(JSON.stringify({ ready_for_native_quit: true, kind, frontend_pid: child.pid, home,
        backend_pid: observed.health.pid, url: observed.service.url, window_title: 'Crew · 隔离测试' }));
    }
    const code = await ended;
    assert.equal(code, 0, errors);
    if (!binary) {
      observed = JSON.parse(await fs.readFile(evidence, 'utf8'));
      assert.deepEqual(observed.errors, []);
      assert.equal(observed.desktop_version, require('../package.json').version);
      assert.equal(observed.health.version, observed.desktop_version);
      assert.equal(observed.stop_calls.length, 0);
      assert.equal(observed.disconnect_calls.length, 1);
      assert.equal(observed.frontend_pipe_bridge, true);
    } else await fs.writeFile(evidence, JSON.stringify(observed, null, 2));
    assert.equal(observed.health.home, home);
    assert.equal(observed.packaged, Boolean(binary));
    assert.equal(observed.health.version, require('../package.json').version);
    assert(alive(observed.health.pid), 'The real backend must outlive the Electron OS process');
    assert(!['47811', '7122'].includes(new URL(observed.service.url).port));
    assert.equal(await fs.readFile(path.join(home, 'model-factory-guarded'), 'utf8'), 'installed');
    await assert.rejects(fs.access(path.join(home, 'model-start-attempted')), { code: 'ENOENT' });
    if (binary) assert(path.relative(home, observed.health.plugin_root).startsWith('desktop' + path.sep + 'backend' + path.sep));
    const reconnect = new core.Backend({ home, backendDir: observed.health.plugin_root, python: 'python', port: 0, isolated: true },
      { spawn() { throw Error('Reconnecting must not launch a second backend'); } });
    assert.deepEqual(await reconnect.start(), { url: observed.service.url, owned: false });
    const health = await core.request(reconnect.url, '/api/admin/health');
    assert.equal(health.pid, observed.health.pid);
    assert.equal(health.instance_id, observed.health.instance_id);
    result = { ok: true, kind, version: observed.health.version, executable, home,
      elapsed_ms: Date.now() - started, service_survived: true, reconnected_same_instance: true,
      model_turns: 0, evidence, health, stderr: errors };
  } finally {
    const status = await administrator.snapshot();
    if (!['stopped', 'unmanaged'].includes(status.state)) {
      await administrator.request('shutdown');
      const deadline = Date.now() + 20000;
      while (Date.now() < deadline) {
        const current = await administrator.snapshot();
        if (current.state === 'stopped' && !alive(current.service_pid) && !alive(current.supervisor_pid)) break;
        await delay(30);
      }
      const current = await administrator.snapshot();
      assert.equal(current.state, 'stopped');
      assert(!alive(current.service_pid) && !alive(current.supervisor_pid), 'Only this private runner must stop its real backend');
    }
  }
  result.cleanup_confirmed = true;
  await fs.writeFile(output, JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ ...result, output, stderr: result.stderr || undefined }));
}
main().catch(error => { console.error(error); process.exitCode = 1; });
