'use strict';
const fs = require('node:fs/promises'); const path = require('node:path'); const { spawn } = require('node:child_process');
const { Backend, projects, newWorkspace } = require('../src/core.cjs');
(async () => {
  const values = process.argv.slice(2), reuse = values.includes('--reuse'), visual = values.includes('--visual'), binary = values.find(value => !['--reuse','--visual'].includes(value));
  const root = path.resolve(__dirname, '..'), executable = binary ? path.resolve(binary) : require('electron');
  const kind = (binary ? (executable.endsWith('-portable.exe') ? 'portable' : 'packaged') : 'source') + (reuse ? '-reuse' : '') + (visual ? '-visual' : '');
  await fs.mkdir(path.join(root, '.cache'), { recursive:true });
  const home = await fs.mkdtemp(path.join(root, '.cache', 'smoke-'));
  const output = path.join(root, 'smoke-output', kind + '.json');
  const env = { ...process.env }; delete env.ELECTRON_RUN_AS_NODE;
  const extractTemp = path.join(root, '.cache', 'portable-temp'); await fs.mkdir(extractTemp, { recursive: true });
  env.TEMP = extractTemp; env.TMP = extractTemp;
  const args = [...(!binary ? [root] : []), '--smoke-test', '--home', home, '--smoke-output', output, '--isolated-sources'];
  if (visual) args.push('--smoke-visual');
  let borrowed = null;
  if (reuse || visual) {
    const preparer = new Backend({ home, backendDir: path.resolve(root, '../plugin'), python: 'python', port: 0, isolated: true, smoke: true });
    await preparer.start();
    try {
      if (visual) {
        const parent = path.join(home,'workspaces'); await fs.mkdir(parent);
        for (const name of ['示例项目','内容工具','新产品']) await newWorkspace(parent,name,preparer.url);
      }
      if (reuse) { borrowed = preparer; args.push('--service-url='+preparer.url,'--profile','smoke-borrowed'); }
      else await preparer.stop();
    } catch (error) { await preparer.stop(); throw error; }
  }
  try {
  const child = spawn(executable, args, { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], env });
  let errors = ''; child.stderr.on('data', data => { errors = (errors + data).slice(-12000); });
  // Fresh Windows sandboxed profiles can take over a minute to initialize;
  // retain all completion/ownership assertions and bound every variant alike.
  const timeout = 120000;
  const code = await new Promise((resolve, reject) => { const timer = setTimeout(() => { child.kill(); reject(new Error('Electron smoke timed out: ' + errors)); }, timeout); child.once('error', error => { clearTimeout(timer); reject(error); }); child.once('exit', value => { clearTimeout(timer); resolve(value); }); });
  if (code !== 0) throw new Error('Electron exited ' + code + ': ' + errors);
  const result = JSON.parse(await fs.readFile(output, 'utf8'));
  if (result.home !== home || !result.window || !result.contextIsolation || result.nodeIntegration || !result.sandbox || result.renderer.nodeAvailable || !result.ownedServiceExited || !result.chrome?.ready || result.chrome.height !== 36 || result.menuBarVisible) throw new Error('Desktop smoke invariant failed: ' + JSON.stringify(result));
  const workbench=result.workbench,workbenchURL=workbench&&new URL(workbench.url);
  if(!workbench?.ready||!workbench.selected||workbench.mode!=='real'||workbenchURL.origin!==result.service.url||workbenchURL.pathname!=='/'||workbenchURL.searchParams.size||workbench.nodeAvailable||workbench.bridgeAvailable||workbench.preload||!workbench.contextIsolation||workbench.nodeIntegration||!workbench.sandbox||!workbench.webSecurity||!Number.isFinite(result.timing?.workbench_ready_at)||result.screenshotScope!=='real-service-workbench')throw Error('Default service workbench invariant failed: '+JSON.stringify(workbench));
  if(!result.screenshot||result.screenshotError||(await fs.stat(result.screenshot)).size<100)throw Error('实际工作台截图未生成：'+String(result.screenshotError));
  if (visual && (!result.visual?.workbenchNavigation || result.visual.workspaceChrome.page !== 'home' || !result.visual.workspaceChrome.activeProject)) throw new Error('Desktop navigation chrome failed');
  if (borrowed && (result.service.owned || !result.borrowedServiceStillAvailable || !Array.isArray(await projects(borrowed.url)))) throw new Error('Borrowed service was stopped by the app');
  console.log(JSON.stringify({ executable, evidence: output, ...result }, null, 2));
  } finally { if (borrowed) await borrowed.stop(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
