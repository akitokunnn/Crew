"use strict";
(() => {
  const escape=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  function markup(person,execution=null) {
    const source=execution||person,control=source.native_handoff;
    if(source.runtime!=="codex"||!control?.native_id)return "";
    const external=control.state!=="crew",changing=["releasing","returning"].includes(control.state);
    return `<button class="btn ghost native-handoff-button" data-act="native-${external?"return":"handoff"}" data-id="${escape(person.session_id)}" ${changing?'disabled aria-busy="true"':""} title="${escape(changing?"正在切换会话控制权":external?"确认已停止在 Codex 桌面输入后，接回同一会话":"本轮结束后可交给 Codex 桌面；两边轮流使用同一会话")}">${changing?"正在切换…":external?"回到 Crew":"在 Codex 桌面继续"}</button>${control.last_error ? `<span class="native-handoff-error" role="status" title="${escape(control.last_error)}">${escape(control.last_error)}</span>` : ""}`;
  }
  async function act(action,sessionId,goalId,{api,refresh,notice}) {
    let result;
    try {
      result=await api(`/api/session/native-${action}`,{session_id:sessionId,...(goalId?{goal_id:goalId}:{})});
    } catch(error) {
      // The failed operation can still have saved a newer native error/state.
      // A secondary read failure must never replace its original explanation.
      try { await refresh(); } catch {}
      throw error;
    }
    let refreshError=null;
    try { await refresh(); } catch(error) { refreshError=error; }
    const suffix=refreshError?` 状态刷新失败：${refreshError.message||String(refreshError)}。操作已完成，请刷新页面核对。`:"";
    if(action==="handoff"){
      if(result.desktop_opened!==true)throw Error("受控 Codex 桌面尚未确认就绪，Crew 已保留原会话；请接回后重试。");
      notice("已交给 Codex 桌面。结束桌面续聊后，点“回到 Crew”；桌面窗口可以保持打开。"+suffix);
    }else notice("已接回同一会话，桌面新增消息已同步。"+suffix);
    return result;
  }
  window.CrewNativeHandoff={markup,act};
})();
