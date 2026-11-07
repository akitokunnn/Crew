'use strict';
// Real, isolated Electron integration test. Never borrows a running Crew service.
const fs = require('node:fs/promises');
const path = require('node:path');
const { spawn } = require('node:child_process');

(async () => {
  const desktop = path.resolve(__dirname, '..');
  await fs.mkdir(path.join(desktop, '.cache'), { recursive:true });
  const home = await fs.mkdtemp(path.join(desktop, '.cache/workspaces-smoke-'));
  const output = path.join(desktop, 'smoke-output/workspaces');
  const env = { ...process.env };
  delete env.ELECTRON_RUN_AS_NODE;
  delete env.CREW_SESSION_ID; delete env.CLAUDE_CODE_SESSION_ID;
  const child = spawn(require('electron'), [
    '--require', path.join(__dirname, 'workspaces-smoke-hook.cjs'), desktop,
    '--smoke-test', '--home', home, '--smoke-output', path.join(output, 'unused.json'), '--isolated-sources'
  ], { cwd:desktop, env, windowsHide:true, stdio:['ignore', 'pipe', 'pipe'] });
  child.stdout.pipe(process.stdout); child.stderr.pipe(process.stderr);
  const timer = setTimeout(() => { child.kill(); process.exitCode = 1; }, 120000);
  child.once('error', error => { clearTimeout(timer); console.error(error); process.exitCode = 1; });
  child.once('exit', async code => {
    clearTimeout(timer); child.stdout.destroy(); child.stderr.destroy();
    try {
      const evidence = JSON.parse(await fs.readFile(path.join(output, 'evidence.json'), 'utf8'));
      if (code !== 0 || !evidence.ok || evidence.home !== home || evidence.errors.length) throw new Error('Workspace smoke failed; see ' + output);
      try { await fs.access(path.join(home, 'web.json')); throw new Error('Owned service registry remains after application exit'); }
      catch (error) { if (error.code !== 'ENOENT') throw error; }
      console.log(JSON.stringify({ ok:true, checks:evidence.checks.length, evidence:path.join(output, 'evidence.json'), ownedServiceExited:true }));
    } catch (error) { console.error(error); process.exitCode = 1; }
  });
})().catch(error => { console.error(error); process.exitCode = 1; });
