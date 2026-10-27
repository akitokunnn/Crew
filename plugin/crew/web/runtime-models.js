"use strict";
(() => {
  const esc=value=>String(value??"").replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
  function create(options){
    const catalog=options.catalog,ic=options.icon,runtimeBadge=options.runtimeBadge;
    const compactBusy=item=>item.busy||item.status==="working";
    const pending=new Set();let destroyed=false;
  function modelMenu(item) {
    const id = item.session_id, options = catalog.currentOptions(item), runtime = catalog.info(item), efforts = catalog.efforts(item, item.model ?? options.model), unsupported = efforts.length === 0;
    const error = catalog.status().error || (!runtime.available ? runtime.error || `${runtime.name} 暂不可用，请检查安装和登录。` : null);
    return `<div class="menu-runtime">${runtimeBadge(item)}<span>执行工具</span></div>${error ? `<div class="runtime-error" role="status">${esc(error)}</div>` : ""}<div role="group" aria-labelledby="menu-model-label"><div class="mtitle" id="menu-model-label">模型</div>${[{id:null, name:"默认"}].concat(runtime.models || []).map(model => `<button role="menuitemradio" aria-checked="${item.model === model.id}" data-act="set-model" data-id="${esc(id)}" data-model="${model.id === null ? "" : esc(model.id)}" ${runtime.available ? "" : "disabled"}><span class="model-check">${item.model === model.id ? ic("check", "sm") : ""}</span>${esc(model.name)}</button>`).join("")}</div><div role="group" aria-labelledby="menu-effort-label" aria-disabled="${unsupported}"><div class="mtitle" id="menu-effort-label">effort${unsupported ? " · 此模型不支持" : ""}</div>${efforts.map(effort => `<button role="menuitemradio" aria-checked="${options.effort === effort}" data-act="set-effort" data-id="${esc(id)}" data-effort="${esc(effort)}" ${runtime.available ? "" : "disabled"}><span class="model-check">${options.effort === effort ? ic("check", "sm") : ""}</span>${esc(effort)}</button>`).join("")}</div>${catalog.status().canRefresh ? `<button role="menuitem" class="runtime-refresh" data-act="runtimes-refresh" data-id="${esc(id)}" ${catalog.status().loading ? "disabled" : ""}>${catalog.status().loading ? "检测中…" : "重新检测执行工具和模型"}</button>` : ""}`;
  }
  function modelControls(item) {
    const id = item.session_id;
    return modelMenu(item) + `<div class="menu-context">${item.where === "vscode" ? '<span class="context-hint">去 VS Code 里输入 /compact</span>' : `<button role="menuitem" data-act="compact" data-id="${esc(id)}" ${compactBusy(item) ? 'disabled title="正在干活，等这一轮结束再压缩"' : ""}>压缩上下文（${item.context === null ? "当前用量未知" : `当前 ${Math.round(item.context.used / item.context.max * 100)}%`}）</button>`}</div>`;
  }

    async function open(element) {
      const id=element.dataset.id,item=options.findSession(id);if(!item||destroyed)return;
      const refreshing=catalog.refresh();options.menu.show(element,modelControls(item));
      await refreshing;if(!destroyed&&options.menu.isCurrent(element)){const current=options.findSession(id);if(current)options.menu.show(element,modelControls(current));}
    }
    async function refresh(element) {
      const anchor=options.menu.anchor()||element,id=element.dataset.id;await catalog.refresh();
      if(destroyed||!options.menu.isCurrent(anchor))return;
      const item=options.findSession(id);if(item)options.menu.show(anchor,modelControls(item));
      if(catalog.status().error)options.notify(catalog.status().error);
    }
    async function change(id,field,value) {
      if(destroyed||pending.has(id))return;const item=options.findSession(id);if(!item)return;
      const anchor=options.menu.anchor(),changes=catalog.optionChange(item,field,value);pending.add(id);
      try{
        await options.save(id,changes);if(destroyed)return;await options.refresh();if(destroyed)return;
        if(options.menu.isCurrent(anchor))options.menu.hide();
        options.notify(`下一步起用 ${field==="model"?value===null?"默认":catalog.modelName(value,item):value}${changes.effort!==undefined&&changes.effort!==null?` · ${changes.effort}`:""}`);
      }finally{pending.delete(id);}
    }
    const unregister=options.actions?.register("runtime-models",{"model-menu":({element})=>open(element),"runtimes-refresh":({element})=>refresh(element),
      "set-model":({element})=>change(element.dataset.id,"model",element.dataset.model||null),"set-effort":({element})=>change(element.dataset.id,"effort",element.dataset.effort)});
    return{markup:modelMenu,controls:modelControls,open,refresh,change,destroy(){destroyed=true;unregister?.();}};
  }
  const exported={create};if(typeof module!=="undefined"&&module.exports)module.exports=exported;else window.CrewRuntimeModels=exported;
})();
