'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const { spawn } = require('node:child_process');
(async () => {
  const desktop = path.resolve(__dirname, '..');
  await fs.mkdir(path.join(desktop, '.cache'), {recursive:true});
  const home = await fs.mkdtemp(path.join(desktop, '.cache', 'crew-update-smoke-'));
  const output = path.join(desktop, 'smoke-output', 'crew-updates');
  const env = {...process.env}; delete env.ELECTRON_RUN_AS_NODE;
  const child = spawn(require('electron'), ['--require', path.join(__dirname,'crew-updates-smoke-hook.cjs'), desktop,
    '--smoke-test','--home',home,'--smoke-output',path.join(output,'unused.json'),'--isolated-sources'],
    {cwd:desktop,env,windowsHide:true,stdio:['ignore','pipe','pipe']});
  child.stdout.pipe(process.stdout);child.stderr.pipe(process.stderr);
  const timer=setTimeout(()=>{child.kill();process.exitCode=1;},120000);
  child.once('error',error=>{clearTimeout(timer);console.error(error);process.exitCode=1;});
  child.once('exit',async code=>{
    clearTimeout(timer);
    try {
      const evidence=JSON.parse(await fs.readFile(path.join(output,'evidence.json'),'utf8'));
      if(code!==0||!evidence.ok||evidence.home!==home)throw new Error('Crew 更新 smoke 失败：'+JSON.stringify(evidence));
      try{await fs.access(path.join(home,'web.json'));throw new Error('隔离后台仍在运行');}catch(error){if(error.code!=='ENOENT')throw error;}
      console.log(JSON.stringify({ok:true,evidence:path.join(output,'evidence.json'),samePort:evidence.before.port===evidence.after.port,backup:evidence.backup.path}));
    }catch(error){console.error(error);process.exitCode=1;}
  });
})().catch(error=>{console.error(error);process.exitCode=1;});
