"use strict";
(() => {
  const HAIKU="claude-haiku-4-5-20251001";
  const runtimeId=item=>typeof item==="string"?item:item?.runtime||"claude";
  function validateSelection(choice,catalog) {
    const selected=JSON.parse(JSON.stringify(choice)),available=catalog.filter(item=>item.available);
    if(!selected.runtime&&available.length===1)selected.runtime=available[0].id;
    const runtime=catalog.find(item=>item.id===selected.runtime);
    if(!runtime?.available)throw Error(runtime?.error||"请选择已就绪的执行载体；可重新检测后再试。");
    const model=runtime.models?.find(item=>item.id===selected.model);
    if(selected.effort&&model?.efforts&&!model.efforts.includes(selected.effort))throw Error("这个模型不支持所选思考强度，请重新选择。");
    return selected;
  }
  function create(options) {
    const snapshot=options.snapshot;let cache=null,pending=null,loading=false,error=null,destroyed=false;
    const listeners=new Set();
    const changed=()=>{if(!destroyed)for(const listener of listeners)listener();};
    function list(){const current=snapshot()||{};return cache||(Array.isArray(current.catalog)?current.catalog:[{id:"claude",name:"Claude Code",available:true,error:null,models:current.legacyModels||[],efforts:current.legacyEfforts||[]}]);}
  function runtimeInfo(item) {
    const id = runtimeId(item);
    return list().find(runtime => runtime.id === id) || {id, name:id === "codex" ? "Codex" : id, available:false, error:"这个执行工具还不可用，请重新检测。", models:[], efforts:[]};
  }
  function defaultRuntime(catalog = list()) {
    if (snapshot()?.choices?.default?.runtime) return snapshot().choices.default.runtime;
    const available = catalog.filter(item => item.available);
    return available.length === 1 ? available[0].id : "";
  }
  function taskRuntime(purpose) { const configured = snapshot()?.choices?.[purpose]; return configured === null || configured === undefined ? defaultRuntime() : configured.runtime || defaultRuntimeForUnconfigured(); }
  function defaultRuntimeForUnconfigured() { const available = list().filter(item => item.available); return available.length === 1 ? available[0].id : ""; }
  const runtimePrompt = () => list().some(item => item.available) ? "请在设置中选择默认载体，或在这里选择本次使用的工具。" : noRuntime;
  const noRuntime = "没有可用的执行工具，请安装并登录 Codex 或 Claude Code，再重新检测。";
  function modelName(id, item = "claude") { const model = (runtimeInfo(item).models || []).find(model => model.id === id); return model === undefined ? id : model.name; }
  function modelEfforts(item, modelId) {
    if (runtimeId(item) === "claude" && modelId === HAIKU) return [];
    const runtime = runtimeInfo(item), model = (runtime.models || []).find(model => model.id === modelId);
    return model && Array.isArray(model.efforts) ? model.efforts : runtime.efforts || [];
  }
  const currentOptions = item => item.applied == null ? item : item.applied;
  function optionsLabel(item) { const options = currentOptions(item), model = options.model == null ? "默认" : modelName(options.model, item); return modelEfforts(item, options.model).length === 0 ? model : `${model} · ${options.effort == null ? "默认" : options.effort}`; }
  function optionChange(item, field, value) {
    const runtime = runtimeInfo(item), options = currentOptions(item);
    if (!runtime.available) throw new Error(runtime.error || `${runtime.name} 暂不可用，请检查安装和登录。`);
    const changes = {[field]:value};
    if (field === "model") {
      if (value !== null && !(runtime.models || []).some(model => model.id === value)) throw new Error("模型列表已更新，请重新选择。");
      const efforts = modelEfforts(item, value), oldEffort = item.effort ?? options.effort;
      if (runtime.id === "codex" && oldEffort != null && !efforts.includes(oldEffort)) {
        if (item.where === "bg" || item.running) throw new Error("这个模型不支持当前 effort，请先选择兼容的 effort，或停下会话后切换。");
        changes.effort = null;
      }
    } else if (!modelEfforts(item, item.model ?? options.model).includes(value)) throw new Error("这个模型不支持所选 effort，请重新选择。");
    return changes;
  }

    async function refresh() {
      if(destroyed||!Array.isArray(snapshot()?.catalog))return;
      if(pending)return pending;
      loading=true;error=null;
      pending=Promise.resolve().then(()=>options.discover()).then(result=>{
        if(!Array.isArray(result.runtimes))throw Error("执行工具列表格式无效");if(!destroyed)cache=result.runtimes;
      }).catch(failure=>{if(!destroyed)error=failure.message;}).finally(()=>{loading=false;pending=null;changed();});
      changed();return pending;
    }
    return {list,id:runtimeId,currentOptions,info:runtimeInfo,defaultRuntime,taskRuntime,prompt:runtimePrompt,modelName,efforts:modelEfforts,optionsLabel,optionChange,refresh,
      status:()=>({loading,error,canRefresh:Array.isArray(snapshot()?.catalog)}),
      replace(catalog){if(!Array.isArray(catalog))throw Error("执行工具列表格式无效");cache=catalog;changed();},
      subscribe(listener){listeners.add(listener);return()=>listeners.delete(listener);},
      invalidate(){cache=null;changed();},destroy(){destroyed=true;listeners.clear();}};
  }
  const exported={create,runtimeId,validateSelection};if(typeof module!=="undefined"&&module.exports)module.exports=exported;else window.CrewRuntimeCatalog=exported;
})();
