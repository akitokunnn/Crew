"use strict";
(() => {
  const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const drafts=new Map();
  async function edit({api,goal,report,onSaved}) {
    const dialog=document.createElement('dialog');dialog.className="personal-notes-dialog stage-report-dialog";dialog.innerHTML='<header><h2>编辑阶段说明</h2><button class="btn ghost" data-close aria-label="关闭编辑">×</button></header><p role="status">正在读取…</p><div data-editor></div>';document.body.append(dialog);dialog.showModal();let saved,request,busy=false;
    const close=()=>{if(!busy){dialog.close();dialog.remove();}};dialog.querySelector('[data-close]').onclick=close;dialog.oncancel=event=>{event.preventDefault();close();};
    const key=`${goal.id}:${report.id}`,status=dialog.querySelector('[role=status]');
    try{
      saved=await api(`/api/goals/report?${new URLSearchParams({goal_id:goal.id,report_id:report.id,format:"json"})}`);if(!dialog.isConnected)return;
      const data=drafts.get(key)||saved.data;
      dialog.querySelector('[data-editor]').innerHTML=`<p class="note">保存后生成包含新说明和原验收记录的新报告。旧版正文与交互内容仍保留在历史报告中。</p><form>${[["summary","结论",false],["delivered","已交付内容（每行一项）",true],["unresolved","尚未解决（每行一项）",true],["follow_up","后续建议",false]].map(([field,label,list])=>`<label>${label}<textarea name="${field}" rows="${field==="summary"?4:3}" ${field==="summary"?"required":""}>${esc(list?(data[field]||[]).join("\n"):data[field])}</textarea></label>`).join("")}<div class="pn-actions"><span class="gap"></span><button type="submit" class="btn primary">保存新版本</button></div></form>`;
      status.textContent="";const form=dialog.querySelector('form');
      const values=()=>{const {document_html,...base}=saved.data;return({...base,summary:form.elements.summary.value,delivered:form.elements.delivered.value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean),unresolved:form.elements.unresolved.value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean),follow_up:form.elements.follow_up.value,outcome:"progress"});};
      form.oninput=()=>drafts.set(key,values());form.onsubmit=async event=>{event.preventDefault();if(busy)return;const value=values(),fingerprint=JSON.stringify(value);if(!request||request.fingerprint!==fingerprint)request={fingerprint,id:crypto.randomUUID()};drafts.set(key,value);busy=true;for(const control of dialog.querySelectorAll('button,textarea'))control.disabled=true;status.textContent="正在保存…";
        try{await api('/api/goals/agent',{actor_session_id:"user",action:"report.publish",data:{goal_id:goal.id,request_id:request.id,expected_version:report.version,report:value,source:{type:"user-report-edit",report_id:report.id}}});drafts.delete(key);await onSaved?.();busy=false;close();}
        catch(error){status.textContent=error.message;}
        finally{busy=false;if(dialog.isConnected)for(const control of dialog.querySelectorAll('button,textarea'))control.disabled=false;}
      };
    }catch(error){if(dialog.isConnected)status.textContent=error.message;}
  }
  window.CrewStageReport={edit};
})();
