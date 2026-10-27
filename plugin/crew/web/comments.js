"use strict";
(() => {
  const esc=value=>String(value??"").replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
  function create(options) {
    const doc=options.document||document, overlay=options.portal||doc.createElement("div");
    overlay.className="backdrop comments-overlay";
    if(!options.portal)doc.body.append(overlay);
    const cache=new Map();let selected=null,returnTo=null,destroyed=false;
    const {icon:ic,date,ago}=options.ui;
    const dataFor=id=>{if(!cache.has(id))cache.set(id,{comments:[],draft:"",readError:null,writeError:null,loading:false,saving:false,request:null});return cache.get(id);};
    const view=data=>({...data,error:data.writeError||data.readError});
    const person=id=>options.people().leaders.concat(options.people().members).find(item=>item.session_id===id);
  function commentsMarkup(item, data) {
    const isLeader = options.people().leaders.some(person => person.session_id === item.session_id), owner = isLeader ? item : options.people().leaders.find(person=>person.session_id===item.leader_session_id);
    const destination = owner ? `${owner.name} 组 · 评论会转告 leader` : "这条评论会保存在原会话下";
    return `<div class="modal comments-modal" id="comments-dialog" data-session-id="${esc(item.session_id)}" role="dialog" aria-modal="true" aria-labelledby="comments-title"><div class="report-head"><h3 id="comments-title">${esc(item.name)}${isLeader ? "组" : ""}的评论</h3><button class="icon-btn" data-act="comments-close" aria-label="关闭评论">${ic("x")}</button></div><p class="d">${esc(destination)}</p><div class="comments-list" role="log" aria-label="评论记录">${data.comments.length === 0 ? `<p class="note">${data.loading ? "正在读取评论…" : "还没有评论。可以说说你的想法或补充要求。"}</p>` : data.comments.map(comment => `<article class="comment-entry"><div class="comment-meta"><span>${esc(comment.author_name || comment.author || "用户")}</span>${comment.created_at ? `<time title="${esc(date(comment.created_at))}">${ago(comment.created_at)}</time>` : ""}</div><div class="markdown">${options.markdown(comment.text, item.session_id)}</div></article>`).join("")}</div>${data.error ? `<p class="error-line" role="alert">${esc(data.error)}</p>` : ""}<form id="comments-form" data-session-id="${esc(item.session_id)}"><label class="sr-only" for="comment-text">写评论</label><textarea id="comment-text" name="text" data-preserve="comment-draft-${esc(item.session_id)}" rows="3" placeholder="写给这个组或成员，补充背景、要求或反馈" required ${data.saving ? "disabled" : ""}>${esc(data.draft)}</textarea><div class="acts"><button class="btn ghost" type="button" data-act="comments-refresh" data-id="${esc(item.session_id)}" ${data.loading ? "disabled" : ""}>刷新评论</button><button class="btn primary" type="submit" ${data.saving ? "disabled" : ""}>${data.saving ? "正在发送…" : "提交评论"}</button></div></form></div>`;
  }

    function render(id,initial=false) {
      if(destroyed||selected!==id)return;
      const item=person(id);if(!item)return;
      if(initial)delete overlay.dataset.render;
      options.replace(overlay,commentsMarkup(item,view(dataFor(id))));
    }
    async function load(id) {
      if(destroyed)return;
      const data=dataFor(id);if(data.loading)return data.request;
      data.loading=true;data.readError=null;render(id);
      data.request=(async()=>{
        try{const result=await options.api(`/api/comments?session_id=${encodeURIComponent(id)}`);if(!Array.isArray(result.comments))throw Error("评论列表格式无效");if(!destroyed)data.comments=result.comments;}
        catch(error){if(!destroyed)data.readError=error.message;}
        finally{data.loading=false;data.request=null;render(id);}
      })();return data.request;
    }
    function open(id) {
      if(destroyed||!person(id))return;
      if(selected===null)returnTo=options.focus.capture();options.beforeOpen?.();selected=id;
      render(id,true);overlay.classList.add("show");overlay.querySelector("#comment-text")?.focus();void load(id);
    }
    function close() {if(selected===null)return false;selected=null;overlay.classList.remove("show");options.focus.restore(returnTo);return true;}
    function setDraft(id,value){dataFor(id).draft=value;}
    async function post(id,text) {
      if(destroyed)return;
      const data=dataFor(id),value=text.trim();if(!value||data.saving)return;
      data.draft=text;data.writeError=null;data.saving=true;render(id);
      try{
        await options.api("/api/comments",{session_id:id,text:value});
        data.draft="";if(destroyed)return;
        const form=overlay.querySelector("#comments-form");if(form?.dataset.sessionId===id)form.elements.text.value="";
        if(data.request)await data.request;await load(id);if(!destroyed&&selected===id)options.notify("评论已保存");
      }catch(error){if(!destroyed)data.writeError=error.message;}
      finally{data.saving=false;render(id);}
    }
    function input(event){const form=event.target.closest?.("#comments-form");if(form&&event.target.name==="text")setDraft(form.dataset.sessionId,event.target.value);}
    function submit(event){const form=event.target;if(form.id!=="comments-form")return;event.preventDefault();void post(form.dataset.sessionId,form.elements.text.value);}
    function click(event){if(event.target===overlay)close();}
    function keydown(event){
      if(selected===null||event.isComposing)return;
      if(event.key==="Escape"){event.preventDefault();close();return;}
      if(event.key!=="Tab")return;
      const items=[...overlay.querySelectorAll('button:not([disabled]),textarea:not([disabled])')],first=items[0],last=items.at(-1);
      if(event.shiftKey&&doc.activeElement===first){event.preventDefault();last?.focus();}
      else if(!event.shiftKey&&doc.activeElement===last){event.preventDefault();first?.focus();}
    }
    overlay.addEventListener("input",input);overlay.addEventListener("submit",submit);overlay.addEventListener("click",click);doc.addEventListener("keydown",keydown);
    const unregister=options.actions?.register("comments",{comments:({element})=>open(element.dataset.id),"comments-refresh":({element})=>load(element.dataset.id),"comments-close":close});
    const unregisterVisibility=options.visibility?.register("comments",()=>selected!==null);
    const button=(item,group=false)=>`<button type="button" class="session-comment" data-act="comments" data-id="${esc(item.session_id)}" aria-label="${esc(group?item.name+"组的评论":"评论"+item.name)}">${ic("msg","xs")}${group?"组评论":"评论"}</button>`;
    return {open,close,load,post,setDraft,button,isOpen:()=>selected!==null,
      markup:commentsMarkup,snapshot(id){const {request,...data}=view(dataFor(id));return{...data,comments:data.comments.map(item=>({...item}))};},
      destroy(){destroyed=true;selected=null;unregister?.();unregisterVisibility?.();overlay.removeEventListener("input",input);overlay.removeEventListener("submit",submit);overlay.removeEventListener("click",click);doc.removeEventListener("keydown",keydown);if(!options.portal)overlay.remove();else overlay.classList.remove("show");}};
  }
  const exported={create};if(typeof module!=="undefined"&&module.exports)module.exports=exported;else window.CrewComments=exported;
})();
