"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let mounted=null, opened=false, loading=false, timer=null, toastTimer=null, assistantSession=null, note=null, notePrefill=false;
  const NOTE_OPENING="帮我把这个想法想清楚";
  const systemTheme=matchMedia("(prefers-color-scheme: dark)");
  function theme(){let selected="system";if(!window.crewDesktopTheme)try{selected=localStorage.getItem("crew.theme")||selected;}catch{}document.documentElement.dataset.theme=selected==="system"?(systemTheme.matches?"dark":"light"):selected;}
  theme();systemTheme.addEventListener("change",theme);window.addEventListener("storage",theme);window.addEventListener("crew:theme-change",theme);
  const toast=message=>{const node=$("assistant-toast");node.textContent=message;node.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>{node.hidden=true;},5000);};
  document.addEventListener("crew:sound-error",event=>toast(event.detail));
  async function api(path,body) {
    const response = await fetch(path, body === undefined ? {method:"GET", headers:{"X-Crew":"1"}} : window.CrewBrowser.postOptions(body));
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
    window.CrewGlobalRail?.update({projects: state.projects || [], todos: state.todos || [], goals: state.goals || []});
    const node=$("assistant-overview");node.replaceChildren(
      section("我的 TODO",(state.todos||[]).filter(item=>item.status==null||item.status==="open"),item=>item.title||item.text||"待办",item=>item.project,"暂时没有需要你动手的事项。"),
      section("进行中的任务",(state.goals||[]).filter(item=>!["completed","cancelled"].includes(item.status)),item=>item.title,item=>item.project,"你可以让我汇总项目近况，或记下新想法。"),
      section("工作区",state.projects||[],item=>item.display_name||item.name,item=>item.name,"先打开一个文件夹，开始项目。")
    );
  }
  // High-risk operations an AI asked for wait here until the user confirms or rejects them.
  const ROLE_LABELS={assistant:"助手",leader:" leader ",execution:"任务执行",git_admin:" Git 助手"};
  function argumentLine(args){return Object.entries(args||{}).filter(([key])=>key!=="request_id").map(([key,value])=>`${key}: ${typeof value==="string"?value:JSON.stringify(value)}`).join("，");}
  function confirmations(requests){
    const node=$("assistant-confirmations");node.replaceChildren();node.hidden=!requests.length;
    if(!requests.length)return;
    const heading=document.createElement("h2");heading.textContent=`等你确认（${requests.length}）`;node.append(heading);
    for(const item of requests){
      const card=document.createElement("article");card.className="assistant-confirmation";
      const title=document.createElement("strong");title.textContent=item.summary;
      const meta=document.createElement("p");meta.textContent=`由${ROLE_LABELS[item.role]||item.role}发起${item.project?` · 工作区 ${item.project}`:""}`;
      const detail=document.createElement("p");detail.className="assistant-confirmation-args";detail.textContent=argumentLine(item.args)||"无参数";
      const actions=document.createElement("div");actions.className="assistant-confirmation-actions";
      for(const [label,approve,kind] of [["确认执行",true,"btn primary"],["拒绝",false,"btn ghost"]]){
        const button=document.createElement("button");button.className=kind;button.textContent=label;
        button.onclick=async()=>{for(const other of actions.querySelectorAll("button"))other.disabled=true;
          try{const result=await api("/api/op/interaction.resolve",{id:item.id,approve});
            toast(result.status==="approved"?"已确认并执行":result.status==="rejected"?"已拒绝":`执行失败：${result.error}`);}
          catch(failure){toast(failure.message);}
          await loadConfirmations();};
        actions.append(button);
      }
      card.append(title,meta,detail,actions);node.append(card);
    }
  }
  async function loadConfirmations(){try{confirmations((await api("/api/op/interaction.list",{})).requests);}catch(failure){toast(`确认请求读取失败：${failure.message}`);}}
  async function refresh() {
    if(loading || window.CrewAccess?.expired)return;loading=true;clearTimeout(timer);
    try {
      if(!opened){await api("/api/op/assistant.open",{});opened=true;}
      const state=await api("/api/op/assistant.state");assistantSession=state.assistant.session_id;CrewSound.observe(state.sounds,state.now);overview(state);error("");
      await loadConfirmations();
      const identity=state.assistant.session_id;
      if(mounted!==identity){CrewChat.unmount();CrewChat.mount({host:$("assistant-chat"),session:state.assistant,state,api,toast,onRouted:()=>{},onNoteCleared:dropNote});mounted=identity;attachNote();}
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
    try{result=await api("/api/op/notes.list?include_archived=1");}catch(failure){toast(`想法读取失败：${failure.message}`);return;}
    const found=result.notes.find(item=>item.id===id);
    if(!found){toast("没有找到这条想法，可能已被删除。");dropNote();return;}
    note={id,title:found.title||found.text.split("\n").map(line=>line.trim()).find(Boolean)};notePrefill=true;attachNote();
  }
  window.addEventListener("hashchange",()=>{void readNote();});
  $("assistant-prompts").onclick=()=>{const dialog=document.createElement("dialog");dialog.className="personal-notes-dialog assistant-prompts-dialog";dialog.innerHTML='<header><h2>助手行为配置</h2><button class="btn ghost" data-close aria-label="关闭行为配置">×</button></header><div data-prompts></div>';document.body.append(dialog);dialog.showModal();const cleanup=CrewBehaviorPrompts.mount(dialog.querySelector('[data-prompts]'),{...window.CrewBehaviorPrompts.requests(api),session:assistantSession});const close=()=>{cleanup();dialog.close();dialog.remove();};dialog.querySelector('[data-close]').onclick=close;dialog.oncancel=event=>{event.preventDefault();close();};};
  $("assistant-notes").onclick=()=>CrewPersonalNotes.create({api}).open();
  (async()=>{if(window.CrewAccess && !await CrewAccess.ready)return;$("assistant-prompts").hidden=Boolean(window.CrewAccess&&!CrewAccess.local_controls);CrewChat.initFiles(api,toast);void refresh();void readNote();})();
})();
