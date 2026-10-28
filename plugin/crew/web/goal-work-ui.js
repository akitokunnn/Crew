"use strict";
(() => {
  const escape=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const names={planned:"待开始",doing:"进行中",blocked:"受阻",done:"已完成",cancelled:"已取消"};
  function markup(items,people=[]) {
    if(!items.length)return "";
    const active=items.filter(item=>item.status!=="cancelled"),done=active.filter(item=>item.status==="done"&&!item.needs_review).length;
    return `<section class="goal-work" aria-label="AI 工作清单"><header><h3>工作清单</h3><span>${done} / ${active.length} 已完成</span></header><ol>${items.map(item=>`<li data-work-status="${escape(item.status)}"><details${item.status==="blocked"||item.needs_review?' open':""}><summary><span class="goal-work-state">${escape(item.needs_review?"待复核":names[item.status]||item.status)}</span><strong>${escape(item.title)}</strong></summary><div class="goal-work-evidence"><p>完成条件：${escape(item.acceptance)}</p><p>负责：${escape(people.find(person=>person.session_id===item.owner_session_id)?.name||"任务成员")}</p>${item.dependencies?.length?`<p>依赖：${item.dependencies.map(key=>escape(items.find(candidate=>candidate.key===key)?.title||key)).join("、")}</p>`:""}${item.note?`<p>${escape(item.note)}</p>`:""}${item.needs_review?"<p>需求已变化，需要按当前要求重新核对。</p>":""}</div></details></li>`).join("")}</ol></section>`;
  }
  window.CrewGoalWork={markup};
})();
