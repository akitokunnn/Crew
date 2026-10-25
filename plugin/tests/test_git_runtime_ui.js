"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const {test}=require("node:test");
const web=path.join(__dirname,"../crew/web");
const tick=async()=>{for(let i=0;i<24;i++)await Promise.resolve();};
const catalog=(claude,codex)=>[{id:"claude",name:"Claude Code",available:claude},{id:"codex",name:"Codex",available:codex}];
function harness(initial){
  const handlers=new Map(),calls=[],button={disabled:false},status={textContent:"",hidden:false},popup={hidden:true};let current=initial,interval,form=null,markup="",changed=0;
  const host={get innerHTML(){return markup;},set innerHTML(value){markup=value;
    const selected=name=>{const content=value.match(new RegExp(`<select[^>]*name="${name}"[^>]*>([\\s\\S]*?)<\\/select>`))?.[1]||"";const options=[...content.matchAll(/<option value="([^"]*)"([^>]*)>/g)];return (options.find(item=>/\bselected\b/.test(item[2]))||options[0])?.[1]||"";};
    if(value.includes('id="git-enable-form"')){form={id:"git-enable-form",elements:{base:{value:selected("base")},stable:{value:selected("stable")},runtime:{id:"git-runtime",value:selected("runtime")}}};button.disabled=/data-git-act="enable"[^>]*disabled/.test(value);}
  },querySelector(selector){return selector==="#git-detail"?popup:selector==='[data-git-act="enable"]'?button:selector==="#git-runtime-status"?status:null;},querySelectorAll:()=>[],addEventListener:(name,fn)=>handlers.set(name,fn),removeEventListener:name=>handlers.delete(name)};
  const sandbox={document:{addEventListener(){},removeEventListener(){},getElementById:()=>null},location:{search:"?project=fixture"},URLSearchParams,
    setInterval:fn=>{interval=fn;return 1;},clearInterval(){},clearTimeout(){},window:{addEventListener(){},removeEventListener(){}}};
  vm.createContext(sandbox);
  const app=fs.readFileSync(path.join(web,"app.js"),"utf8").replace(/\r\n/g,"\n");
  vm.runInContext(app.replace('  setup();\n})();','window.runtimeFixture={defaultRuntime,set(value){state={runtimes:value};}};\n})();'),sandbox);
  sandbox.window.runtimeFixture.set(current);
  vm.runInContext(fs.readFileSync(path.join(web,"git.js"),"utf8")+'\nglobalThis.component=CrewGit;',sandbox);
  const data={enabled:false,repository:"fixture",choices:{branches:["develop","main"],base:"develop",stable:"main"}};
  const options={project:"fixture",catalog:()=>current,defaultRuntime:()=>sandbox.window.runtimeFixture.defaultRuntime(),
    api:async(route,body)=>{calls.push({route,body});return route.startsWith("/api/git?")?data:{ok:true};},toast(){},onChanged:async()=>{changed++;}};
  sandbox.component.mount(host,options);
  return {host,button,status,calls,get form(){return form;},get changed(){return changed;},ui:sandbox.component,
    choose(id){form.elements.runtime.value=id;handlers.get("change")({target:form.elements.runtime});},
    submit(){handlers.get("submit")({target:form,preventDefault(){}});},
    async refresh(next=current){current=next;sandbox.window.runtimeFixture.set(next);interval();await tick();}};
}

test("Codex-only Git setup selects Codex and submits it exactly once with the existing branch choices",async()=>{
  const h=harness(catalog(false,true));await tick();assert.equal(h.form.elements.runtime.value,"codex");assert.equal(h.button.disabled,false);
  h.submit();await tick();const posts=JSON.parse(JSON.stringify(h.calls.filter(item=>item.body)));assert.deepEqual(posts,[{route:"/api/git/enable",body:{project:"fixture",base:"develop",stable:"main",runtime:"codex"}}]);assert.equal(h.changed,1);h.ui.unmount();
});

test("both installed without settings requires a choice while an explicit Codex choice survives refresh",async()=>{
  const h=harness(catalog(true,true).reverse());await tick();assert.equal(h.form.elements.runtime.value,"");assert.equal(h.button.disabled,true);
  h.choose("codex");await h.refresh(catalog(true,true));assert.equal(h.form.elements.runtime.value,"codex");assert.equal(h.button.disabled,false);
  await h.refresh(catalog(true,false));assert.equal(h.form.elements.runtime.value,"codex");assert.equal(h.button.disabled,true);h.submit();await tick();assert(!h.calls.some(item=>item.body));assert(h.host.innerHTML.includes("执行工具不可用"));h.ui.unmount();
});

test("no available runtime has an empty selection, a clear error and no enable request",async()=>{
  const h=harness(catalog(false,false));await tick();assert.equal(h.form.elements.runtime.value,"");assert.equal(h.button.disabled,true);assert(h.host.innerHTML.includes("没有可用的执行工具"));h.submit();await tick();assert(!h.calls.some(item=>item.body));h.ui.unmount();
});

test("an untouched Git setup follows a newly available runtime without manufacturing a Claude fallback",async()=>{
  const h=harness(catalog(false,false));await tick();await h.refresh(catalog(false,true));assert.equal(h.form.elements.runtime.value,"codex");assert.equal(h.button.disabled,false);assert(!h.calls.some(item=>item.body));h.ui.unmount();
});
