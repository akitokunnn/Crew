'use strict';
// Compare every packaged desktop asset, backend source and bundled skill with this checkout.
const assert=require('node:assert/strict');
const fs=require('node:fs/promises'),path=require('node:path');
const {createHash}=require('node:crypto');
const desktop=path.resolve(__dirname,'..');
const digest=bytes=>createHash('sha256').update(bytes).digest('hex');
async function files(directory,prefix='') {
  const result=[];
  for(const entry of await fs.readdir(path.join(directory,prefix),{withFileTypes:true})) {
    if(entry.name==='__pycache__'||entry.name.includes('-test-')||entry.name.endsWith('.pyc')||!prefix&&entry.name==='tests')continue;
    const relative=path.join(prefix,entry.name);
    if(entry.isDirectory())result.push(...await files(directory,relative));
    else if(entry.isFile())result.push(relative);
    else throw Error('Unexpected source file type: '+relative);
  }
  return result.sort();
}
(async()=>{
  const asar=await import('@electron/asar');
  const application=path.resolve(process.argv[2]||path.join(desktop,'dist/win-unpacked'));
  const archive=path.join(application,'resources/app.asar'),backend=path.join(application,'resources/backend');
  const expected=JSON.parse(await fs.readFile(path.join(desktop,'package.json'),'utf8'));
  const packaged=JSON.parse(asar.extractFile(archive,'package.json'));
  for(const key of ['name','version','main'])assert.equal(packaged[key],expected[key]);
  const rows=[];
  async function compare(label,source,target) {
    const bytes=await fs.readFile(source);
    assert.equal(digest(target),digest(bytes),'Packaged content differs from source: '+label);
    rows.push({path:label,sha256:digest(bytes),bytes:bytes.length});
  }
  for(const folder of ['src','assets'])for(const relative of await files(path.join(desktop,folder))) {
    const name=folder+'/'+relative.replaceAll('\\','/');
    await compare(name,path.join(desktop,folder,relative),asar.extractFile(archive,path.join(folder,relative)));
  }
  const source=path.resolve(desktop,'../plugin');
  for(const relative of await files(source))await compare('backend/'+relative.replaceAll('\\','/'),path.join(source,relative),await fs.readFile(path.join(backend,relative)));
  const manifest=JSON.parse(await fs.readFile(path.join(backend,'.claude-plugin/plugin.json'),'utf8'));
  assert.equal(manifest.version,expected.version);
  await assert.rejects(fs.access(path.join(backend,'tests')),{code:'ENOENT'});
  const result={ok:true,version:expected.version,application,files:rows.length,source_digest:digest(Buffer.from(JSON.stringify(rows))),verified:rows};
  const output=path.join(desktop,'smoke-output/bundle-source.json');
  await fs.mkdir(path.dirname(output),{recursive:true});await fs.writeFile(output,JSON.stringify(result,null,2));
  console.log(JSON.stringify({ok:true,version:result.version,files:rows.length,source_digest:result.source_digest,output}));
})().catch(error=>{console.error(error);process.exitCode=1;});
