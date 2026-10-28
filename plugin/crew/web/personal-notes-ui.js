"use strict";
(() => {
  const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const drafts=new Map();
  function create({api,project=null,projects=[],onOpen}) {
    let dialog=null,items=[],editing=null,request=null,starting=new Map(),archived=false,busy=false;
    const scope=project||"global",draftKey=`crew.notes.draft:${scope}`;
    const fresh=()=>({text:"",tags:"",request_id:crypto.randomUUID()});
    function draft(){if(!drafts.has(scope)){let value;try{value=JSON.parse(localStorage.getItem(draftKey));}catch{}drafts.set(scope,value&&typeof value.text==="string"?value:fresh());}return drafts.get(scope);}
    function remember(){const value={text:dialog.querySelector('[name="text"]').value,tags:dialog.querySelector('[name="tags"]').value,request_id:draft().request_id,fingerprint:draft().fingerprint};drafts.set(scope,value);try{localStorage.setItem(draftKey,JSON.stringify(value));}catch{}}
    function notice(message){dialog.querySelector('[role="status"]').textContent=message;}
    function paintList(){
      dialog.querySelector('.pn-items').innerHTML=items.length?items.map(item=>`<article class="pn-item" data-note-id="${esc(item.id)}"><p>${esc(item.text)}</p><small>${esc(item.project||"全局想法")}${item.tags.length?` · ${item.tags.map(esc).join(" · ")}`:""}</small>${item.original_text!==item.text?`<details><summary>最初记录</summary><p>${esc(item.original_text)}</p></details>`:""}<div class="pn-actions"><button type="button" class="btn ghost" data-note-action="edit">编辑</button><button type="button" class="btn ghost" data-note-action="archive">${item.state==="archived"?"移回想法":"收起来"}</button><span class="gap"></span>${item.leader && ["pending","unknown"].includes(item.leader.status)?`<button type="button" class="btn" data-note-action="recover">核实已有对话</button>`:item.leader?.session_id?`<button type="button" class="btn" data-note-action="open">进入对话 ↗</button>`:`${!project?`<select aria-label="讨论的工作区" data-note-project><option value="">选择工作区</option>${projects.map(p=>`<option value="${esc(p.name)}" ${p.name===item.project?"selected":""}>${esc(p.display_name||p.name)}</option>`).join("")}</select>`:""}<button type="button" class="btn" data-note-action="start">开始聊</button>`}</div>${item.leader?.error?`<p class="error-line">${esc(item.leader.error)}</p>`:""}</article>`).join(""):'<p class="note">还没有想法。先记下来，准备好了再开始聊。</p>';
    }
    async function load(){const result=await api(`/api/personal/notes?include_archived=${archived?1:0}${project?`&project=${encodeURIComponent(project)}`:""}`);if(!dialog)return;items=result.notes;paintList();}
    function fill(item){editing=item;request=null;const value=item?{text:item.text,tags:item.tags.join(", ")}:draft();dialog.querySelector('[name="text"]').value=value.text;dialog.querySelector('[name="tags"]').value=value.tags;dialog.querySelector('[type="submit"]').textContent=item?"保存修改":"记下来";dialog.querySelector('[data-note-cancel]').hidden=!item;dialog.querySelector('[name="text"]').focus();}
    function lock(value){busy=value;for(const button of dialog.querySelectorAll('button,input,textarea,select'))button.disabled=value;}
    async function save(event){
      event.preventDefault();if(busy)return;
      const text=dialog.querySelector('[name="text"]').value.trim(),tags=dialog.querySelector('[name="tags"]').value.split(/[,，]/).map(v=>v.trim()).filter(Boolean);if(!text)return;
      const data={action:editing?"update":"capture",text,tags,project:editing?editing.project:project,...(editing?{note_id:editing.id,expected_version:editing.version,state:editing.state}:{})};
      const fingerprint=JSON.stringify(data);if(!request||request.fingerprint!==fingerprint){request={fingerprint,id:editing||request||(draft().fingerprint&&draft().fingerprint!==fingerprint)?crypto.randomUUID():draft().request_id};if(!editing){drafts.set(scope,{...draft(),request_id:request.id,fingerprint});remember();}}
      lock(true);notice("正在保存…");
      try{await api('/api/personal/notes',{...data,request_id:request.id});if(!editing){drafts.set(scope,fresh());try{localStorage.removeItem(draftKey);}catch{}}fill(null);await load();notice("已记下。不会因此启动 AI。");}
      catch(error){notice(error.message);}
      finally{if(dialog)lock(false);}
    }
    function openLeader(value){busy=false;onOpen?.(value.project,value.session_id);close();}
    async function action(event){
      const button=event.target.closest('[data-note-action]');if(!button||busy)return;const row=button.closest('[data-note-id]'),item=items.find(n=>n.id===row.dataset.noteId);if(!item)return;
      if(button.dataset.noteAction==="edit"){fill(item);return;}if(button.dataset.noteAction==="open"){openLeader(item.leader);return;}
      lock(true);
      try{
        if(button.dataset.noteAction==="recover"){
          const result=await api(`/api/personal/notes/recovery?note_id=${encodeURIComponent(item.id)}`);row.querySelector('.pn-recovery')?.remove();const panel=document.createElement('div');panel.className="pn-recovery";panel.innerHTML=result.leaders.length?`<p class="note">选择你已经核实的原讨论。这里只建立关联，不发送想法，也不新建会话。</p><select data-recovery-session aria-label="已存在的原讨论">${result.leaders.map(person=>`<option value="${esc(person.session_id)}">${esc(person.crew_name)} · ${esc(person.task||person.session_id.slice(0,8))}</option>`).join("")}</select><button type="button" class="btn" data-note-action="recover-bind">确认这是原讨论</button>`:'<p class="note">未找到可关联的对话。该次创建结果仍需核实，暂不重复创建。</p>';row.append(panel);notice("");
        }
        else if(button.dataset.noteAction==="recover-bind"){
          const session_id=row.querySelector('[data-recovery-session]').value;await api('/api/personal/notes/recovery',{note_id:item.id,session_id,request_id:`note-recovery:${item.id}:${session_id}`,reason:"用户在快速想法中核实并选择了原讨论"});await load();notice("已关联原讨论，没有重新发送内容。");
        }
        else if(button.dataset.noteAction==="archive"){await api('/api/personal/notes',{action:"update",note_id:item.id,expected_version:item.version,project:item.project,text:item.text,tags:item.tags,state:item.state==="archived"?"open":"archived",request_id:crypto.randomUUID()});await load();notice("已更新。");}
        else{const target=project||row.querySelector('[data-note-project]').value;if(!target)throw Error("先选择讨论这个想法的工作区。");if(!starting.has(item.id))starting.set(item.id,crypto.randomUUID());notice("正在准备对话…");const result=await api('/api/personal/notes/start',{note_id:item.id,project:target,request_id:starting.get(item.id)});openLeader(result);}
      }catch(error){notice(error.message);}
      finally{if(dialog)lock(false);}
    }
    function close(){if(!dialog||busy)return;if(!editing)remember();dialog.close();dialog.remove();dialog=null;}
    return {async open(){if(dialog){dialog.focus();return;}dialog=document.createElement('dialog');dialog.className="personal-notes-dialog";dialog.innerHTML=`<header><div><h2>快速想法</h2><p>随手记下，准备好再交给 leader。</p></div><button class="btn ghost" data-note-close aria-label="关闭快速想法">×</button></header><form><label>想到了什么<textarea name="text" rows="3" required placeholder="一个点子、一件之后想做的事…"></textarea></label><label class="pn-tags">标签<input name="tags" placeholder="可选，用逗号分隔"></label><div class="pn-actions"><button type="button" class="btn ghost" data-note-cancel hidden>取消编辑</button><span class="gap"></span><button class="btn primary" type="submit">记下来</button></div></form><p role="status" aria-live="polite"></p><label class="pn-archived"><input type="checkbox"> 显示收起来的想法</label><div class="pn-items"></div>`;document.body.append(dialog);dialog.showModal();fill(null);dialog.querySelector('form').onsubmit=save;dialog.querySelector('form').oninput=()=>{if(!editing)remember();};dialog.querySelector('[data-note-close]').onclick=close;dialog.querySelector('[data-note-cancel]').onclick=()=>fill(null);dialog.querySelector('.pn-archived input').onchange=async event=>{archived=event.target.checked;try{await load();}catch(error){notice(error.message);}};dialog.querySelector('.pn-items').onclick=action;dialog.oncancel=event=>{event.preventDefault();close();};try{await load();}catch(error){notice(error.message);}},close};
  }
  window.CrewPersonalNotes={create};
})();
