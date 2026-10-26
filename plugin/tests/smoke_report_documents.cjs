'use strict';
// Isolated document reader: no Crew service, model, or production home.
const {app,BrowserWindow}=require('electron');
const fs=require('node:fs/promises'),path=require('node:path'),os=require('node:os'),http=require('node:http');
const {execFileSync}=require('node:child_process'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..'),output=path.join(root,'desktop/smoke-output/report-documents');
const temp=require('node:fs').mkdtempSync(path.join(os.tmpdir(),'crew-report-documents-'));
app.setPath('userData',temp);app.setPath('sessionData',temp);
app.commandLine.appendSwitch('force-device-scale-factor','1');
const samplePaths=process.argv.slice(2).filter(value=>value.endsWith('.html'));
let win,server,origin,current,policy;const requests=[],steps=[];
const document=`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>短句模型选型 · 隔离样例</title><style>body{max-width:1000px;margin:40px auto;padding:0 24px;font:17px/1.7 system-ui;color:#24342f;background:#fafbf8}h1{font-size:36px}button{padding:10px 18px;border:1px solid #48765f;border-radius:8px;background:white;color:#30533f;cursor:pointer}table{width:100%;border-collapse:collapse}td,th{padding:12px;text-align:left;border-bottom:1px solid #ddd}.note{padding:20px;background:#e8f0e6;border-radius:12px}svg{width:100%;max-width:600px}</style>
<p>模型选型 · 演示数据，非实测结论</p><h1>先看推荐，再比较代价</h1><p class="note">方案 A 在这个演示中兼顾识别效果与耗时。你可以排序比较，再展开下方验收记录核对依据。</p>
<h2>准确率与耗时</h2><svg viewBox="0 0 600 170" role="img" aria-label="两方案示意"><rect x="80" y="30" width="420" height="40" fill="#48765f"/><text x="10" y="57">方案 A</text><rect x="80" y="100" width="300" height="40" fill="#b7cfa3"/><text x="10" y="127">方案 B</text></svg>
<h2>按你的优先级比较</h2><button id="sort">按耗时排序</button> <button id="filter">只看更准确的</button><table><thead><tr><th>方案</th><th>耗时（示意）</th><th>认出率（示意）</th></tr></thead><tbody id="rows"></tbody></table>
<h2>边界</h2><p>这份样例只验证报告阅读与交互，不代表任何模型的真实表现。</p>
<script>const models=[['方案 A',50,85],['方案 B',28,70]];let filtered=false;function draw(){document.querySelector('#rows').innerHTML=models.filter(row=>!filtered||row[2]>80).map(row=>'<tr><td>'+row[0]+'</td><td>'+row[1]+' ms</td><td>'+row[2]+'%</td></tr>').join('')}document.querySelector('#sort').onclick=()=>{models.sort((a,b)=>a[1]-b[1]);draw()};document.querySelector('#filter').onclick=()=>{filtered=!filtered;draw()};draw();window.chartReady=true;</script></html>`;
const run=code=>win.webContents.executeJavaScript(code);
const wait=async(check,label)=>{for(let i=0;i<150;i++){if(await check())return;await new Promise(resolve=>setTimeout(resolve,40));}throw Error('等待超时：'+label);};
const frames=()=>win.webContents.mainFrame.framesInSubtree;
async function findFrame(selector,text){let result;await wait(async()=>{for(const frame of frames()){try{if(await frame.executeJavaScript(`!!document.querySelector(${JSON.stringify(selector)}) && (${text===undefined?'true':`document.querySelector(${JSON.stringify(selector)}).textContent.includes(${JSON.stringify(text)})`})`)){result=frame;return true;}}catch{}}return false;},selector);return result;}
async function shot(name){await fs.writeFile(path.join(output,name),(await win.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true})).toPNG());}
async function route(request,response){
  requests.push(request.url);
  if(request.url==='/goal-ui.js'){response.setHeader('Content-Type','text/javascript');return response.end(await fs.readFile(path.join(root,'plugin/crew/web/goal-ui.js')));}
  if(request.url==='/report'||request.url==='/direct'){response.setHeader('Content-Type','text/html; charset=utf-8');response.setHeader('Content-Security-Policy','sandbox allow-scripts; '+policy);return response.end(current);}
  if(request.url==='/')return response.end('<!doctype html><style>html,body{height:100%;margin:0}iframe{width:100%;height:100%;border:0;display:block}</style><iframe id="reader" title="报告预览" sandbox="allow-scripts"></iframe><script src="/goal-ui.js"></script><script>window.hostState="kept";fetch("/report").then(r=>r.text()).then(html=>document.querySelector("iframe").srcdoc=CrewGoals.reportDocument(html))</script>');
  response.statusCode=404;response.end('unexpected request');
}
async function interact(label){
  const content=await findFrame('#sort');
  assert(await content.executeJavaScript('window.chartReady'));
  await content.executeJavaScript('document.querySelector("#sort").click()');
  assert((await content.executeJavaScript('document.querySelector("tbody tr").textContent')).includes('方案 B'));
  await content.executeJavaScript('document.querySelector("#filter").click()');
  assert.equal(await content.executeJavaScript('document.querySelectorAll("tbody tr").length'),1);
  assert((await content.executeJavaScript('document.querySelector("tbody").textContent')).includes('方案 A'));
  const isolation=await content.executeJavaScript('(async()=>{let parentBlocked=false,networkBlocked=false;try{void parent.document.body}catch{parentBlocked=true}try{await fetch("'+origin+'/probe")}catch{networkBlocked=true}return {parentBlocked,networkBlocked,origin:self.origin}})()');
  assert.deepEqual(isolation,{parentBlocked:true,networkBlocked:true,origin:'null'});
  const viewer=await findFrame('.crew-evidence');
  assert.equal(await viewer.executeJavaScript('document.querySelector("details").open'),false);
  await viewer.executeJavaScript('document.querySelector("summary").click()');
  assert.equal(await viewer.executeJavaScript('getComputedStyle(document.querySelector("iframe")).display'),'none');
  assert((await viewer.executeJavaScript('document.querySelector(".crew-evidence-body").textContent')).includes('保留实际证据'));
  await viewer.executeJavaScript('document.querySelector("summary").click()');
  assert.equal(await content.executeJavaScript('document.querySelectorAll("tbody tr").length'),1,'切换证据不会重载正文');
  steps.push(label+'：排序、筛选、证据展开/返回、隔离与网络阻断通过');
  return content;
}
async function main(){
  await app.whenReady();await fs.mkdir(output,{recursive:true});
  const source=[{title:'短句模型选型',document,evidence:'<h1>验收记录</h1><p>保留实际证据；演示数据不冒充实测。</p>'}];
  for(const file of samplePaths)source.push({title:path.basename(file,'.html'),document:await fs.readFile(file,'utf8'),evidence:'<h1>参考文档阅读验收</h1><p>用户提供的历史样例，未重新核验文中业务数据。</p>'});
  const env={...process.env,PYTHONUTF8:'1'};delete env.CREW_SESSION_ID;delete env.CLAUDE_CODE_SESSION_ID;
  const generated=JSON.parse(execFileSync('python',['-c','import json,sys; from crew.report_documents import render,VIEWER_POLICY; rows=json.load(sys.stdin); print(json.dumps({"policy":VIEWER_POLICY,"reports":[render(r["title"],r["document"],r["evidence"]) for r in rows]},ensure_ascii=False))'],{cwd:path.join(root,'plugin'),env,input:JSON.stringify(source),encoding:'utf8',maxBuffer:8*1024*1024}));
  policy=generated.policy;current=generated.reports[0];
  server=http.createServer((req,res)=>route(req,res).catch(error=>{res.statusCode=500;res.end(error.stack)}));await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));origin='http://127.0.0.1:'+server.address().port;
  win=new BrowserWindow({width:1440,height:1000,useContentSize:true,show:false,webPreferences:{nodeIntegration:false,contextIsolation:true,sandbox:true}});
  await win.loadURL(origin+'/');await interact('Crew 主区/资料预览');await shot('01-正文-宽.png');
  assert.equal(await run('window.hostState'),'kept');
  win.setContentSize(390,844);await shot('02-正文-窄.png');
  const content=await findFrame('#sort');assert(await content.executeJavaScript('document.documentElement.scrollWidth<=innerWidth+1'),'窄窗口无横向溢出');
  const viewer=await findFrame('.crew-evidence');await viewer.executeJavaScript('document.querySelector("summary").click()');await shot('03-验收记录-窄.png');
  win.setContentSize(1440,1000);await win.loadURL(origin+'/direct');await interact('直接打开 HTTP 报告');
  const offline=path.join(output,'offline-report.html');await fs.writeFile(offline,current);await win.loadFile(offline);await interact('独立离线 HTML');
  for(let index=1;index<source.length;index++){
    current=generated.reports[index];await win.loadURL(origin+'/');const frame=await findFrame('h1',source[index].title);
    assert((await frame.executeJavaScript('document.querySelector("h1").textContent')).includes(source[index].title));
    if(await frame.executeJavaScript('!!document.querySelector("#fCommercial")')){
      const before=await frame.executeJavaScript('document.querySelectorAll("tbody tr").length');
      await frame.executeJavaScript('document.querySelector("#fCommercial").click()');
      assert((await frame.executeJavaScript('document.querySelectorAll("tbody tr").length'))<before,'选型样例筛选减少候选');
      assert((await frame.executeJavaScript('document.querySelectorAll("svg").length'))>0,'选型样例图表已生成');
    }
    if(await frame.executeJavaScript('!!document.querySelector("#f-A")')){
      await frame.executeJavaScript('document.querySelector("#f-A").click()');
      assert(await frame.executeJavaScript('[...document.querySelectorAll("section.fgrp tr[data-g]:not([hidden])")].every(row=>row.dataset.g==="A")'),'清单样例按可用状态筛选');
      assert((await frame.executeJavaScript('document.querySelectorAll("section.fgrp tr[data-g][hidden]").length'))>0);
      await frame.executeJavaScript('document.querySelector("#f-all").click()');
    }
    await shot(`参考-${source[index].title}.png`);
    const controls=await frame.executeJavaScript('document.querySelectorAll("select,input,button").length');
    steps.push(source[index].title+'：原始 HTML 完整嵌入，'+controls+' 个交互控件，实际筛选与宽窗口阅读通过');
  }
  assert(!requests.some(url=>url==='/probe'),'报告脚本未访问本地 API');
  await fs.writeFile(path.join(output,'result.json'),JSON.stringify({ok:true,steps,requests},null,2));
  process.stdout.write(JSON.stringify({ok:true,output,steps},null,2)+'\n');
}
main().catch(async error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;await fs.mkdir(output,{recursive:true});await fs.writeFile(path.join(output,'result.json'),JSON.stringify({ok:false,error:error.stack,steps},null,2));}).finally(async()=>{if(win&&!win.isDestroyed())win.destroy();if(server)await new Promise(resolve=>server.close(resolve));app.exit(process.exitCode||0);});
