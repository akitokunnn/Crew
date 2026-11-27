"use strict";
// 随手记: one box to jot an idea down plus the three latest ones. Ideas belong to a workspace:
// inside one the jot goes there; on the administrator page the box asks which workspace first.
// Sorting, tags, review and the mind map all live on that workspace's ideas page (/notes.html?project=X).
(() => {
  const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const LAST="crew.notes.jot.workspace";
  // Drafts are kept per workspace, so switching the target never moves text between workspaces.
  const draftKey=project=>project?`crew.notes.draft:${project}`:"crew.notes.jot";
  const firstLine=text=>text.split("\n").map(line=>line.trim()).find(Boolean);
  const notesUrl=project=>`/notes.html?project=${encodeURIComponent(project)}`;
  function create({api,project=null,projects=null}) {
    let dialog=null,pending=null,busy=false,target=project,choices=[];
    const fixed=project!==null;
    const field=()=>dialog.querySelector('[name="text"]');
    function notice(message){dialog.querySelector('[role="status"]').textContent=message;}
    function read(key){try{return localStorage.getItem(key)||"";}catch{return "";}}
    function remember(){if(!target)return;try{localStorage.setItem(draftKey(target),field().value);}catch{}}
    function lock(value){busy=value;for(const control of dialog.querySelectorAll('textarea,select,button[type="submit"]'))control.disabled=value||!target&&control.type==="submit";}
    function label(name){const item=choices.find(entry=>entry.name===name);return item?item.display_name||item.name:name;}
    function paintTarget(){
      const link=dialog.querySelector('[data-notes-link]');link.hidden=!target;if(target)link.href=notesUrl(target);
      dialog.querySelector('button[type="submit"]').disabled=busy||!target;
      field().placeholder=target?`记到「${label(target)}」…（Enter 记下，Shift+Enter 换行）`:"先在上面选好工作区";
    }
    async function recent(){
      const list=dialog.querySelector('.pn-items');
      if(!target){list.innerHTML='<p class="note">选好工作区后，这里显示它最近记下的 3 条。</p>';return;}
      const asked=target,result=await api(`/api/op/notes.list?project=${encodeURIComponent(asked)}`);if(!dialog||asked!==target)return;
      const items=result.notes.slice().sort((a,b)=>Date.parse(b.created_at)-Date.parse(a.created_at)).slice(0,3);
      list.innerHTML=items.length?items.map(item=>`<article class="pn-item"><p>${esc(item.title||firstLine(item.text))}</p><small>${esc(label(asked))}${item.tags.length?` · ${item.tags.map(esc).join(" · ")}`:""}</small></article>`).join(""):'<p class="note">这个工作区还没有想法。记下的第一条会出现在这里。</p>';
    }
    async function choose(next){
      remember();target=next||null;pending=null;
      if(target)try{localStorage.setItem(LAST,target);}catch{}
      field().value=read(draftKey(target));paintTarget();notice("");
      try{await recent();}catch(error){dialog.querySelector('.pn-items').innerHTML=`<p class="error-line">最近记录读取失败：${esc(error.message)}</p>`;}
    }
    async function save(event){
      event.preventDefault();if(busy)return;
      const text=field().value.trim();if(!text)return;
      if(!target){notice("先选一个工作区，想法会记在那里。");return;}
      // An unconfirmed capture retries with the same request id, so it lands once.
      if(pending?.text!==text||pending.project!==target)pending={text,project:target,id:window.CrewBrowser.requestId()};
      lock(true);notice("正在记下…");
      try{await api('/api/op/note.capture',{project:target,data:{text},request_id:pending.id});}
      catch(error){notice(`没记下：${error.message}（内容还在输入框里）`);lock(false);paintTarget();return;}
      pending=null;field().value="";remember();lock(false);paintTarget();field().focus();notice(`已记到「${label(target)}」。不会因此启动 AI。`);
      try{await recent();}catch(error){notice(`已记下，但最近记录读取失败：${error.message}`);}
    }
    function close(){if(!dialog)return;remember();dialog.close();dialog.remove();dialog=null;}
    async function workspaces(){
      const known=typeof projects==="function"?projects():null;
      if(Array.isArray(known))return known;
      const listing=await api('/api/op/workspace.list');return listing.projects||[];
    }
    return {async open(){
      if(dialog){dialog.focus();return;}
      dialog=document.createElement('dialog');dialog.className="personal-notes-dialog jot-dialog";
      const where=fixed?"":`<label class="pn-target">记到哪个工作区<select name="workspace" required><option value="">正在读取工作区…</option></select></label>`;
      dialog.innerHTML=`<header><div><h2>随手记</h2><p>${fixed?"记进这个工作区；整理、回顾和导图都在它的想法页。":"想法属于工作区：先选一个，整理和导图都在它的想法页。"}</p></div><button type="button" class="icon-btn" data-note-close aria-label="关闭随手记">×</button></header><form>${where}<textarea name="text" rows="3" required aria-label="随手记下一个想法"></textarea><div class="pn-actions"><a class="btn ghost" data-notes-link href="#" hidden>打开想法 →</a><span class="gap"></span><button class="btn primary" type="submit">记下来</button></div></form><p role="status" aria-live="polite"></p><h3 class="pn-recent">最近 3 条</h3><div class="pn-items"><p class="note">正在读取…</p></div>`;
      document.body.append(dialog);dialog.showModal();
      dialog.querySelector('form').onsubmit=save;
      field().oninput=remember;
      field().onkeydown=event=>{if(event.key==="Enter"&&!event.shiftKey&&!event.isComposing){event.preventDefault();dialog.querySelector('form').requestSubmit();}};
      dialog.querySelector('[data-note-close]').onclick=close;
      dialog.oncancel=event=>{event.preventDefault();close();};
      if(!fixed){
        const select=dialog.querySelector('[name="workspace"]');
        select.onchange=()=>{void choose(select.value);};
        try{
          choices=(await workspaces()).filter(item=>!item.read_only);if(!dialog)return;
          let last="";try{last=localStorage.getItem(LAST)||"";}catch{}
          target=choices.some(item=>item.name===last)?last:choices.length===1?choices[0].name:null;
          select.innerHTML=(choices.length?`<option value="" ${target?"":"selected"} disabled>选择工作区</option>`:'<option value="" selected disabled>还没有工作区</option>')+choices.map(item=>`<option value="${esc(item.name)}" ${item.name===target?"selected":""}>${esc(item.display_name||item.name)}</option>`).join("");
        }catch(error){select.innerHTML='<option value="" selected disabled>工作区读取失败</option>';notice(`工作区读取失败：${error.message}`);}
      }
      field().value=read(draftKey(target));paintTarget();
      (fixed||target?field():dialog.querySelector('[name="workspace"]')).focus();
      try{await recent();}catch(error){dialog.querySelector('.pn-items').innerHTML=`<p class="error-line">最近记录读取失败：${esc(error.message)}</p>`;}
    },close};
  }
  window.CrewPersonalNotes={create,draftKey};
})();
