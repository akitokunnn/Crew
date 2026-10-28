"use strict";
(() => {
  const selectionRules=typeof module!=="undefined"&&module.exports?require("./runtime-catalog.js"):window.CrewRuntimeCatalog;
  const esc=value=>String(value??"").replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
  function createDraft({catalog,selection}) {
    const configured=selection()||{};
    let draft={name:"",task:"",runtime:catalog.taskRuntime("leader"),model:configured.model||null,effort:configured.effort||null,explicit:false};
    function reconcile(){const selected=catalog.taskRuntime("leader");if(!draft.explicit&&draft.runtime!==selected){const configured=selection()||{};draft={...draft,runtime:selected,model:configured.model||null,effort:configured.effort||null};}}
    return {snapshot:()=>({...draft}),reconcile,
      input(field,value){if(!["name","task"].includes(field))throw Error("Unknown leader input");draft[field]=value;},
      select(field,value){if(!["runtime","model","effort"].includes(field))throw Error("Unknown leader option");draft.explicit=true;draft[field]=value||null;if(field==="runtime"){draft.model=null;draft.effort=null;}},
      payload(){reconcile();const selected=selectionRules.validateSelection({runtime:draft.runtime,model:draft.model,effort:draft.effort},catalog.list());return{name:draft.name.trim(),task:draft.task.trim(),...selected};}};
  }
  function create(options) {
    const doc=options.document||document,overlay=options.portal||doc.createElement("div"),catalog=options.catalog;
    overlay.className="backdrop leader-create-overlay";if(!options.portal)doc.body.append(overlay);
    let draft=null,returnTo=null,error="",busy=false,destroyed=false,generation=0;
    function controls() {
      if(!draft||destroyed)return;draft.reconcile();const value=draft.snapshot(),inherited=!value.runtime,runtime=inherited?{id:"",available:false,models:[],efforts:[]}:catalog.info(value.runtime),status=catalog.status();
      overlay.querySelector("#leader-runtime-options").innerHTML=`<button type="button" class="model-option runtime-option ${inherited?"on":""}" data-act="leader-runtime" data-runtime="" aria-pressed="${inherited}">按后台角色设置</button>`+catalog.list().map(item=>`<button type="button" class="model-option runtime-option ${item.id===runtime.id?"on":""}" data-act="leader-runtime" data-runtime="${esc(item.id)}" aria-pressed="${item.id===runtime.id}">${esc(item.name)}${item.available?"":'<small>未就绪</small>'}</button>`).join("");
      const note=overlay.querySelector("#leader-runtime-status");note.className=(!inherited&&!runtime.available)||status.error?"runtime-error":"runtime-note";
      note.textContent=status.error||(inherited?catalog.prompt():!runtime.available?runtime.error||`${runtime.name} 暂不可用，请检查安装和登录。`:status.loading?"正在检测执行工具和模型…":"");
      const refresh=overlay.querySelector('[data-act="leader-refresh"]');refresh.hidden=!status.canRefresh;refresh.disabled=status.loading;
      const models=[...(runtime.models||[])];if(value.model&&!models.some(item=>item.id===value.model))models.push({id:value.model,name:`自定义：${value.model}`});
      overlay.querySelector("#leader-model-options").innerHTML=[{id:null,name:"原生默认"},...models].map(model=>`<button type="button" class="model-option ${value.model===model.id?"on":""}" data-act="leader-model" data-model="${esc(model.id||"")}" aria-pressed="${value.model===model.id}" ${runtime.available?"":"disabled"}>${esc(model.name)}</button>`).join("");
      const efforts=inherited?[]:catalog.efforts(runtime.id,value.model),unsupported=!inherited&&efforts.length===0;
      overlay.querySelector("#leader-effort-note").hidden=!unsupported;overlay.querySelector("#leader-effort-options").setAttribute("aria-disabled",String(unsupported||!runtime.available));
      const shown=[null,...efforts];if(value.effort&&!shown.includes(value.effort))shown.push(value.effort);
      overlay.querySelector("#leader-effort-options").innerHTML=shown.map(effort=>`<button type="button" class="model-option ${value.effort===effort?"on":""}" data-act="leader-effort" data-effort="${esc(effort||"")}" aria-pressed="${value.effort===effort}" ${runtime.available?"":"disabled"}>${esc(effort||"原生默认")}</button>`).join("");
      const form=overlay.querySelector("#leader-form");for(const field of ["runtime","model","effort"])form.elements[field].value=value[field]||"";
      form.querySelector('[type="submit"]').disabled=!inherited&&!runtime.available||busy;overlay.querySelector("#leader-error").textContent=error;
    }
    function open() {
      if(destroyed)return;returnTo=options.focus.capture();options.beforeOpen?.();generation++;error="";
      draft=createDraft({catalog,selection:options.selection});const value=draft.snapshot(),initialRuntime=value.runtime,initialModel=value.model||"",initialEffort=value.effort||"";
      overlay.innerHTML=`<form class="modal leader-modal" id="leader-form" role="dialog" aria-modal="true" aria-labelledby="leader-title"><h3 id="leader-title">起一个 leader</h3><p class="d">它在后台运行，你在这里跟它聊。下属可以以后再收编进来，它也能自己开新下属。</p><div class="field"><label for="leader-name">叫它</label><input id="leader-name" name="name" placeholder="比如：打包" required></div><div class="field"><label for="leader-task">它要管什么</label><textarea id="leader-task" name="task" rows="3" placeholder="一两句话，比如：安卓打包和推包流程跑通，能无人值守出包" required></textarea></div><div class="field"><span class="field-label" id="leader-runtime-label">执行工具</span><input type="hidden" name="runtime" value="${esc(initialRuntime)}"><div class="model-options runtime-options" id="leader-runtime-options" role="group" aria-labelledby="leader-runtime-label"></div><p id="leader-runtime-status" role="status"></p><button type="button" class="runtime-refresh" data-act="leader-refresh">重新检测执行工具和模型</button></div><div class="field"><span class="field-label" id="leader-model-label">用什么模型</span><input type="hidden" name="model" value="${esc(initialModel)}"><div class="model-options" id="leader-model-options" role="group" aria-labelledby="leader-model-label"></div></div><div class="field" id="leader-effort-field"><span class="field-label" id="leader-effort-label">effort<span class="effort-note" id="leader-effort-note" hidden>此模型不支持</span></span><input type="hidden" name="effort" value="${esc(initialEffort)}"><div class="model-options effort-options" id="leader-effort-options" role="group" aria-labelledby="leader-effort-label" aria-disabled="false"></div></div><p class="error-line" id="leader-error"></p><div class="acts"><button class="btn ghost" type="button" data-act="leader-create-close">取消</button><button class="btn primary" type="submit">起 leader</button></div></form>`;
      overlay.classList.add("show");controls();overlay.querySelector("#leader-name").focus();void catalog.refresh();
    }
    function close(){if(!draft)return false;draft=null;generation++;overlay.classList.remove("show");options.focus.restore(returnTo);return true;}
    function input(event){if(draft&&["name","task"].includes(event.target.name)){draft.input(event.target.name,event.target.value);error="";overlay.querySelector("#leader-error").textContent="";}}
    function select(field,value){if(!draft)return;draft.select(field,value);error="";controls();const action="leader-"+field;[...overlay.querySelectorAll(`[data-act="${action}"]`)].find(button=>button.dataset[field]===(value||""))?.focus();}
    async function submit() {
      if(!draft||busy||destroyed)return;const selected=draft,opened=generation;let payload;
      try{payload=selected.payload();}catch(failure){error=failure.message;controls();return;}
      busy=true;controls();
      try{
        const result=await options.createLeader(payload);if(destroyed)return;
        const current=draft===selected&&generation===opened;if(current&&result.selection_resolution)options.notify?.(selectionRules.selectionResolutionLabel(result.selection_resolution));if(current)close();const closedGeneration=generation;await options.refresh();
        if(!destroyed&&current&&draft===null&&generation===closedGeneration)options.navigate(result.session_id);
      }catch(failure){if(!destroyed&&draft===selected&&generation===opened){error=failure.message;controls();}}
      finally{busy=false;controls();}
    }
    function formSubmit(event){if(event.target.id==="leader-form"){event.preventDefault();void submit();}}
    function click(event){if(event.target===overlay)close();}
    function keydown(event){if(!draft||event.isComposing)return;if(event.key==="Escape"){event.preventDefault();close();return;}if(event.key!=="Tab")return;const items=[...overlay.querySelectorAll('button:not([disabled]),input:not([type="hidden"]),textarea')],first=items[0],last=items.at(-1);if(event.shiftKey&&doc.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&doc.activeElement===last){event.preventDefault();first?.focus();}}
    const unsubscribe=catalog.subscribe(controls);
    overlay.addEventListener("input",input);overlay.addEventListener("submit",formSubmit);overlay.addEventListener("click",click);doc.addEventListener("keydown",keydown);
    const unregister=options.actions?.register("leader-create",{"modal-leader":open,"leader-create-close":close,"leader-refresh":()=>catalog.refresh(),"leader-runtime":({element})=>select("runtime",element.dataset.runtime),"leader-model":({element})=>select("model",element.dataset.model),"leader-effort":({element})=>select("effort",element.dataset.effort)});
    const unregisterVisibility=options.visibility?.register("leader-create",()=>draft!==null);
    return {open,close,submit,select,isOpen:()=>draft!==null,snapshot:()=>({draft:draft?.snapshot()||null,busy,error}),
      destroy(){destroyed=true;draft=null;generation++;unsubscribe();unregister?.();unregisterVisibility?.();overlay.removeEventListener("input",input);overlay.removeEventListener("submit",formSubmit);overlay.removeEventListener("click",click);doc.removeEventListener("keydown",keydown);if(!options.portal)overlay.remove();else overlay.classList.remove("show");}};
  }
  const exported={create,createDraft};if(typeof module!=="undefined"&&module.exports)module.exports=exported;else window.CrewLeaderCreate=exported;
})();
