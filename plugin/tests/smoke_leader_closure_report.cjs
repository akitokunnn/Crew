/* Saved backend report HTML in real Chrome; no service or AI is started. */
const {chromium}=require('playwright');
const {spawnSync}=require('node:child_process');
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..'),output=path.join(root,'Temp/leader-closure-report');
async function main(){
  const env={...process.env,PYTHONIOENCODING:'utf-8'};
  for(const key of ['CREW_SESSION_ID','CLAUDE_CODE_SESSION_ID'])delete env[key];
  const fixture=spawnSync('python',[path.join(__dirname,'leader_closure_report_fixture.py')],{cwd:path.join(root,'plugin'),env,encoding:'utf8',windowsHide:true});
  assert.equal(fixture.status,0,fixture.stderr);
  const reports=JSON.parse(fixture.stdout),errors=[],screenshots=[];
  await fs.mkdir(output,{recursive:true});
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  try{
    const page=await browser.newPage({viewport:{width:1280,height:900}});
    page.on('pageerror',e=>errors.push(String(e)));
    page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
    for(const [kind,html] of Object.entries(reports)){
      await page.setContent(html);
      const shot=async suffix=>{const name=kind+'-'+suffix+'.png';await page.screenshot({path:path.join(output,name),fullPage:true});screenshots.push(name);};
      await shot('opened');
      if(kind==='authored')await page.locator('.crew-evidence>summary').click();
      await page.getByRole('heading',{name:'请你实测',exact:true}).waitFor();
      assert.equal(await page.locator('ol>li').count(),2);
      assert.equal(await page.locator('ol>li').first().innerText(),'打开 <设备页>，输入 A & B。\n\n预期：看到“准备就绪”。');
      assert((await page.locator('ol>li').nth(1).innerText()).includes('预期：看到“已保存”。'));
      await shot('steps');
      await page.setViewportSize({width:390,height:844});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
      assert(await page.getByRole('heading',{name:'请你实测',exact:true}).isVisible());
      await shot('mobile');
      await page.setViewportSize({width:1280,height:900});
    }
    assert.deepEqual(errors,[]);
    await fs.writeFile(path.join(output,'evidence.json'),JSON.stringify({checks:6,errors,screenshots},null,2));
    console.log(JSON.stringify({checks:6,errors:errors.length,output}));
  }finally{await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
