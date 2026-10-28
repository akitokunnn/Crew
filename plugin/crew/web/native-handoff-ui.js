"use strict";
(() => {
  const escape=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  function markup(person,execution=null) {
    const source=execution||person,control=source.native_handoff;
    if(source.runtime!=="codex"||!control?.native_id)return "";
    const external=control.state!=="crew",changing=["releasing","returning"].includes(control.state);
    return `<button class="btn ghost native-handoff-button" data-act="native-${external?"return":"handoff"}" data-id="${escape(person.session_id)}" ${changing?'disabled aria-busy="true"':""} title="${escape(changing?"正在切换会话控制权":external?"确认已停止在 Codex 桌面输入后，接回同一会话":"本轮结束后可交给 Codex 桌面；两边轮流使用同一会话")}">${changing?"正在切换…":external?"回到 Crew":"在 Codex 桌面继续"}</button>${control.last_error ? `<span class="native-handoff-error" role="status" title="${escape(control.last_error)}">${escape(control.last_error)}</span>` : ""}`;
  }
  async function act(action,sessionId,goalId,{api,refresh,open,notice}) {
    const result=await api(`/api/session/native-${action}`,{session_id:sessionId,...(goalId?{goal_id:goalId}:{})});
    await refresh();
    if(action==="handoff"){
      if(!/^codex:\/\/threads\/[0-9a-f-]{36}$/i.test(result.url||""))throw Error("原生会话地址无效，Crew 已保留交接状态");
      open(result.url);notice("已释放会话并请求打开 Codex。结束桌面续聊后，点“回到 Crew”。");
    }else notice("已接回同一会话，桌面新增消息已同步。");
    return result;
  }
  window.CrewNativeHandoff={markup,act};
})();
