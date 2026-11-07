"use strict";
// 随手记: one box to jot an idea down plus the three latest ones. Sorting,
// tags, review and promotion all live on the idea wall (/notes.html).
(() => {
  const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const draftKey="crew.notes.jot";
  const firstLine=text=>text.split("\n").map(line=>line.trim()).find(Boolean);
  function create({api}) {
    let dialog=null,pending=null,busy=false;
    const field=()=>dialog.querySelector('[name="text"]');
    function notice(message){dialog.querySelector('[role="status"]').textContent=message;}
    function remember(){try{localStorage.setItem(draftKey,field().value);}catch{}}
    function lock(value){busy=value;for(const control of dialog.querySelectorAll('textarea,button[type="submit"]'))control.disabled=value;}
    async function recent(){
      const result=await api('/api/personal/notes');if(!dialog)return;
      const items=result.notes.slice().sort((a,b)=>Date.parse(b.created_at)-Date.parse(a.created_at)).slice(0,3);
      dialog.querySelector('.pn-items').innerHTML=items.length?items.map(item=>`<article class="pn-item"><p>${esc(item.title||firstLine(item.text))}</p><small>${esc(item.project||"全局想法")}${item.tags.length?` · ${item.tags.map(esc).join(" · ")}`:""}</small></article>`).join(""):'<p class="note">还没有想法。记下的第一条会出现在这里。</p>';
    }
    async function save(event){
      event.preventDefault();if(busy)return;
      const text=field().value.trim();if(!text)return;
      // An unconfirmed capture retries with the same request id, so it lands once.
      if(pending?.text!==text)pending={text,id:window.CrewBrowser.requestId()};
      lock(true);notice("正在记下…");
      try{await api('/api/personal/notes',{action:"capture",data:{text},request_id:pending.id});}
      catch(error){notice(`没记下：${error.message}（内容还在输入框里）`);lock(false);return;}
      pending=null;field().value="";remember();lock(false);field().focus();notice("已记下。不会因此启动 AI。");
      try{await recent();}catch(error){notice(`已记下，但最近记录读取失败：${error.message}`);}
    }
    function close(){if(!dialog)return;remember();dialog.close();dialog.remove();dialog=null;}
    return {async open(){
      if(dialog){dialog.focus();return;}
      dialog=document.createElement('dialog');dialog.className="personal-notes-dialog jot-dialog";
      dialog.innerHTML=`<header><div><h2>随手记</h2><p>先记下来，整理、回顾和转任务都在想法墙。</p></div><button class="btn ghost" data-note-close aria-label="关闭随手记">×</button></header><form><textarea name="text" rows="3" required aria-label="随手记下一个想法" placeholder="一个点子、一件之后想做的事…（Enter 记下，Shift+Enter 换行）"></textarea><div class="pn-actions"><a class="text-link" href="/notes.html">打开想法墙 →</a><span class="gap"></span><button class="btn primary" type="submit">记下来</button></div></form><p role="status" aria-live="polite"></p><h3 class="pn-recent">最近 3 条</h3><div class="pn-items"><p class="note">正在读取…</p></div>`;
      document.body.append(dialog);dialog.showModal();
      try{field().value=localStorage.getItem(draftKey)||"";}catch{}
      field().focus();
      dialog.querySelector('form').onsubmit=save;
      field().oninput=remember;
      field().onkeydown=event=>{if(event.key==="Enter"&&!event.shiftKey&&!event.isComposing){event.preventDefault();dialog.querySelector('form').requestSubmit();}};
      dialog.querySelector('[data-note-close]').onclick=close;
      dialog.oncancel=event=>{event.preventDefault();close();};
      try{await recent();}catch(error){dialog.querySelector('.pn-items').innerHTML=`<p class="error-line">最近记录读取失败：${esc(error.message)}</p>`;}
    },close};
  }
  window.CrewPersonalNotes={create};
})();
