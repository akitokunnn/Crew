"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let mounted=null, opened=false, loading=false, timer=null, toastTimer=null, assistantSession=null, note=null, notePrefill=false;
  const NOTE_OPENING="帮我把这个想法想清楚";
  const systemTheme=matchMedia("(prefers-color-scheme: dark)");
  function theme(){let selected="system";try{selected=localStorage.getItem("crew.theme")||selected;}catch{}document.documentElement.dataset.theme=selected==="system"?(systemTheme.matches?"dark":"light"):selected;}
  theme();systemTheme.addEventListener("change",theme);window.addEventListener("storage",theme);
  const toast=message=>{const node=$("assistant-toast");node.textContent=message;node.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>{node.hidden=true;},5000);};
  document.addEventListener("crew:sound-error",event=>toast(event.detail));
  async function api(path,body) {
    const response=await fetch(path,{method:body===undefined?"GET":"POST",headers:{"X-Crew":"1",...(body===undefined?{}:{"Content-Type":"application/json"})},...(body===undefined?{}:{body:JSON.stringify(body)})});
    const value=await response.json();if(!response.ok)throw Error(value.error||`请求失败 (${response.status})`);return value;
  }
  function error(message) {
    const node=$("assistant-error");node.replaceChildren();node.hidden=!message;
    if(message){node.append(document.createTextNode(message));const retry=document.createElement("button");retry.className="btn ghost";retry.textContent="重试";retry.onclick=()=>{void refresh();};node.append(retry);}
  }
  function section(title,items,label,projectName,empty) {
    const node=document.createElement("section"),heading=document.createElement("h3");heading.textContent=title;node.append(heading);
    if(!items.length){const quiet=document.createElement("p");quiet.className="quiet";quiet.textContent=empty;node.append(quiet);}
    for(const item of items.slice(0,12)){
      const name=projectName(item);if(!name)continue;
      const link=document.createElement("a"),url=new URL("/",location.href);url.searchParams.set("project",name);
      if(item.leader_session_id)url.hash=new URLSearchParams({leader:item.leader_session_id,...(item.id&&title==="进行中的任务"?{goal:item.id}:{})}).toString();
      link.href=url.href;link.textContent=label(item);const detail=document.createElement("small");detail.textContent=name;link.append(detail);node.append(link);
    }
    return node;
  }
  function overview(state) {
    const node=$("assistant-overview");node.replaceChildren(
      section("我的 TODO",(state.todos||[]).filter(item=>item.status==null||item.status==="open"),item=>item.title||item.text||"待办",item=>item.project,"暂时没有需要你动手的事项。"),
      section("进行中的任务",(state.goals||[]).filter(item=>!["completed","cancelled"].includes(item.status)),item=>item.title,item=>item.project,"你可以让我汇总项目近况，或记下新想法。"),
      section("工作区",state.projects||[],item=>item.display_name||item.name,item=>item.name,"先打开一个文件夹，开始项目。")
    );
  }
  async function refresh() {
    if(loading || window.CrewAccess?.expired)return;loading=true;clearTimeout(timer);
    try {
      if(!opened){await api("/api/assistant/open",{});opened=true;}
      const state=await api("/api/assistant/state");assistantSession=state.assistant.session_id;CrewSound.observe(state.sounds,state.now);overview(state);error("");
      $("assistant-native").innerHTML=CrewNativeHandoff.markup(state.assistant);
      const readonly=Boolean(state.assistant.native_handoff?.external),identity=`${state.assistant.session_id}:${readonly}`;
      if(mounted!==identity){CrewChat.unmount();CrewChat.mount({host:$("assistant-chat"),session:state.assistant,state,api,toast,readonly,readonlyReason:"会话已交给 Codex 桌面。结束桌面输入后，点“回到 Crew”同步并继续。",onRouted:()=>{},onNoteCleared:dropNote});mounted=identity;attachNote();}
      else CrewChat.update(state);
    }catch(failure){error(failure.message||"暂时连不上助手");}
    finally{loading=false;timer=setTimeout(refresh,4000);}
  }
  window.addEventListener("pagehide",()=>{clearTimeout(timer);CrewChat.unmount();});
  // "去聊聊" on the idea wall opens #note=<id>: attach the idea above the
  // composer and prefill an opening line; nothing is sent until the user sends.
  function attachNote(){if(mounted===null||note===null)return;CrewChat.setNote(note,notePrefill?NOTE_OPENING:null);notePrefill=false;}
  function dropNote(){note=null;notePrefill=false;if(new URLSearchParams(location.hash.slice(1)).has("note"))history.replaceState(null,"",location.pathname+location.search);}
  async function readNote(){
    const id=new URLSearchParams(location.hash.slice(1)).get("note");
    if(!id)return;
    let result;
    try{result=await api("/api/personal/notes?include_archived=1");}catch(failure){toast(`想法读取失败：${failure.message}`);return;}
    const found=result.notes.find(item=>item.id===id);
    if(!found){toast("没有找到这条想法，可能已被删除。");dropNote();return;}
    note={id,title:found.title||found.text.split("\n").map(line=>line.trim()).find(Boolean)};notePrefill=true;attachNote();
  }
  window.addEventListener("hashchange",()=>{void readNote();});
  $("assistant-native").addEventListener("click",async event=>{
    const button=event.target.closest("[data-act]");if(!button||button.disabled)return;button.disabled=true;
    try{await CrewNativeHandoff.act(button.dataset.act.slice(7),button.dataset.id,null,{api,refresh,notice:toast});}
    catch(failure){error(failure.message);button.disabled=false;}
  });
  $("assistant-prompts").onclick=()=>{const dialog=document.createElement("dialog");dialog.className="personal-notes-dialog assistant-prompts-dialog";dialog.innerHTML='<header><h2>助手行为配置</h2><button class="btn ghost" data-close aria-label="关闭行为配置">×</button></header><div data-prompts></div>';document.body.append(dialog);dialog.showModal();const cleanup=CrewBehaviorPrompts.mount(dialog.querySelector('[data-prompts]'),{read:query=>api("/api/behavior-prompts?"+new URLSearchParams(query)),save:body=>api("/api/behavior-prompts",body),session:assistantSession});const close=()=>{cleanup();dialog.close();dialog.remove();};dialog.querySelector('[data-close]').onclick=close;dialog.oncancel=event=>{event.preventDefault();close();};};
  $("assistant-notes").onclick=()=>CrewPersonalNotes.create({api}).open();
  (async()=>{if(window.CrewAccess && !await CrewAccess.ready)return;$("assistant-prompts").hidden=Boolean(window.CrewAccess&&!CrewAccess.local_controls);CrewChat.initFiles(api,toast);void refresh();void readNote();})();
})();
