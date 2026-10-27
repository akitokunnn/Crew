"use strict";
// Stable-person actions own their cards, forms, confirmations and receipts.
// The application supplies navigation and sibling features, never their state.
(() => {
  const esc = value => String(value??"").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  function create(options) {
    const doc=options.document||document;
    const root=options.portal||doc.createElement("div");
    if(!options.portal)doc.body.append(root);
    root.innerHTML='<div class="card-back" id="card-back"></div><section class="card" id="card" role="dialog" aria-modal="true" aria-label="下属卡片" tabindex="-1"></section><div class="backdrop people-overlay" id="backdrop"></div>';
    const card=root.querySelector("#card"),cardBack=root.querySelector("#card-back"),overlay=root.querySelector("#backdrop");
    const {icon:ic,avatar:av,status:st,context:contextControls,model:memberModel,activity:memberActivity,inline,task:taskMarkup,date,time,runtime:runtimeBadge}=options.ui;
    let cardId=null,cardReturn=null,handoff=null,dialog=null,generation=0,destroyed=false;
    const pending=new Set();
    const compactBusy=item=>item.busy||item.status==="working";
    const editorHandoffReason=item=>options.runtimeId(item)==="codex"?"Codex 会话在 Crew 内继续；暂不支持转到 VS Code":"";
    function captureFocus(){return options.focus.capture(options.menu.contains(doc.activeElement)?options.menu.anchor():doc.activeElement);}
    function openCard(id){
      if(destroyed||!options.person(id))return;
      options.beforeOpen?.();
      cardReturn=captureFocus();
      generation++;
      cardId=id;
      handoff=null;
      renderCard();
      options.changed();
      card.querySelector('[data-act="card-close"]')?.focus({preventScroll:true});
    }
    function closeCard(restore=true){
      if(cardId===null)return false;
      cardId=null;
      handoff=null;
      generation++;
      card.classList.remove("show");
      cardBack.classList.remove("show");
      options.menu.hide();
      options.changed();
      if(restore)options.focus.restore(cardReturn);
      return true;
    }
  function renderCard() {
    if (cardId === null) return;
    const item = options.person(cardId);
    if (item === undefined) { closeCard(); return; }
    const background = item.where === "bg", canStop = background && item.status === "working";
    const stopReason = background ? "这一轮没在工作" : item.where === "vscode" ? "开在 VS Code 里，去那边停" : "会话没在运行";
    const name = esc(item.name);
    options.replace(card, `<div class="card-head" data-session-menu="${esc(item.session_id)}" tabindex="0">${av(item.name, 44, true)}<div style="min-width:0;flex:1"><div class="nm">${name}${contextControls(item)}</div><div class="meta">${runtimeBadge(item)}${st(item.status)}<span>${ic(background ? "cloud" : item.where === "vscode" ? "code" : "clock", "xs")} ${background ? "后台运行" : item.where === "vscode" ? "开在 VS Code" : "没在运行"}</span><span>归 ${esc(item.group)}</span>${memberModel(item)}</div></div><button class="icon-btn" data-act="card-close" aria-label="关闭下属卡片">${ic("x")}</button></div>
      ${background && item.activity !== null ? `<div class="card-activity">${memberActivity(item)}</div>` : ""}
      <div class="card-actions"><button class="btn primary" data-act="open-vscode" ${editorHandoffReason(item) ? `disabled title="${esc(editorHandoffReason(item))}"` : item.status === "working" ? 'disabled title="会话正在工作，请等它空闲后再打开"' : ""}>${ic("code", "sm")}在 VS Code 打开</button><span class="gap"></span><button class="icon-btn" data-act="rename" title="改名" aria-label="改名">${ic("pencil")}</button><button class="icon-btn" data-act="move-menu" title="换组" aria-label="换组">${ic("swap")}</button><button class="icon-btn" data-act="archive" title="归档" aria-label="归档">${ic("archive")}</button><button class="icon-btn" data-act="stop" ${canStop ? "" : "disabled"} title="${canStop ? "停下这一轮" : stopReason}" aria-label="停下这一轮">${ic("stop")}</button><button class="icon-btn" data-act="more-menu" title="更多" aria-label="更多">${ic("more")}</button></div>
      ${!options.inTask() ? options.runtimeSwitch?.entry(item) || "" : '<p class="editor-handoff-note">更换执行工具请回本组总对话，并先暂停该人员仍在推进的全部目标。</p>'}
      ${editorHandoffReason(item) ? `<p class="editor-handoff-note">${esc(editorHandoffReason(item))}</p>` : ""}
      ${handoff === null ? "" : `<div class="step"><div class="row"><span class="n">${ic("check", "xs")}</span><span>已打开目录，等 VS Code 窗口准备好<br><code>${esc(handoff.cwd)}</code></span></div><div class="row"><span class="n todo">2</span><span style="flex:1">窗口好了以后，${handoff.needs_confirmation ? "停掉后台或终端这一份，在 VS Code 里接上同一个会话" : "在 VS Code 里接上同一个会话"}<br><button class="btn primary" data-act="handoff-go" style="margin-top:8px">窗口好了，接上</button><button class="btn ghost" data-act="handoff-cancel" style="margin-top:8px">先不了</button></span></div></div>`}
      <div class="card-body"><div class="sec"><h4>负责范围</h4><p>${item.scope === null || item.scope === "" ? "还没写" : inline(item.scope, item.session_id)}</p></div><div class="sec"><h4>现在在做</h4><p>${item.task === null ? "还没写" : inline(item.task, item.session_id)}</p></div><div class="sec"><h4>完成标准</h4><p>${item.done_when === null || item.done_when === "" ? "还没写" : inline(item.done_when, item.session_id)}</p></div><div class="sec"><h4>最近进展</h4>${item.progress.length === 0 ? '<p class="note">还没写</p>' : `<div class="tl">${item.progress.map(progress => `<div class="tl-i"><span class="t" title="${esc(date(progress.at))}">${time(progress.at)}</span><span>${inline(progress.text, item.session_id)}</span></div>`).join("")}</div>`}</div><div class="sec"><h4>临时子代理与派活</h4><div class="chips">${item.codex.map(task => taskMarkup(task, "chip", item.session_id)).join("")}${item.codex_older > 0 ? `<div class="chip" style="color:var(--text-3)">更早 ${item.codex_older} 个</div>` : ""}${item.codex.length === 0 && item.codex_older === 0 ? '<p class="note">还没有</p>' : ""}</div></div><div class="sec"><h4>设备和文件</h4><div class="chips">${item.leases.map(lease => `<div class="chip" title="${esc(lease.purpose)} · ${date(lease.taken_at)} 至 ${lease.until_at === null ? "未写归还时间" : date(lease.until_at)}">${ic("device", "xs")}${esc(lease.resource)}<span class="grow"></span>${esc(lease.purpose)}</div>`).join("")}${item.claims.map(claim => `<div class="chip" title="${esc(claim.pattern)}${claim.note === null || claim.note === "" ? "" : ` · ${esc(claim.note)}`}">${ic("file", "xs")}<code>${esc(claim.pattern)}</code></div>`).join("")}${item.leases.length === 0 && item.claims.length === 0 ? '<p class="note">没有占用的设备和文件</p>' : ""}</div></div></div>
      <div class="card-foot"><button class="text-link" data-act="member-history">${ic("msg", "xs")}进入本人对话</button><span class="gap"></span>${ic("branch", "xs")}<code title="${item.branch === null ? "无分支" : esc(item.branch)}">${item.branch === null ? "无分支" : esc(item.branch)}</code><code title="${esc(item.session_id)}">${esc(item.session_id)}</code></div>`);
    for (const button of card.querySelectorAll('[data-act]')) button.dataset.id = item.session_id;
    card.classList.add("show"); cardBack.classList.add("show");
  }
  function menuButton(item, act, label, reason = "", danger = false, extra = "") {
    return `<button role="menuitem" data-act="${act}" data-id="${esc(item.session_id)}" ${reason ? `disabled title="${esc(reason)}"` : ""} ${danger ? 'class="danger"' : ""} ${extra}>${label}</button>`;
  }
  function submenu(item, label, html, danger = false) {
    return `<div class="menu-branch">${menuButton(item, "submenu", `${label}<span class="grow"></span>▸`, "", danger, 'aria-haspopup="menu" aria-expanded="false"')}<div class="menu sub-menu" role="menu" hidden>${html}</div></div>`;
  }
  function sessionMenu(item) {
    const isLeader = options.leaders().some(person => person.session_id === item.session_id), stopReason = item.where !== "bg" ? item.where === "vscode" ? "开在 VS Code 里，去那边停" : "会话没在运行" : !compactBusy(item) ? "这一轮没在工作" : "";
    const compactReason = item.where === "vscode" ? "去 VS Code 里输入 /compact" : compactBusy(item) ? "正在干活，等这一轮结束再压缩" : "";
    const btn = (act, label, reason = "", danger = false, extra = "") => menuButton(item, act, label, reason, danger, extra);
    let html = isLeader ? btn("leader", "打开对话") : btn("member-history", "进入本人对话") + btn("card", "资料与操作") + btn("open-vscode", "在 VS Code 打开", editorHandoffReason(item) || (item.status === "working" ? "会话正在工作，请等它空闲后再打开" : ""));
    if (options.runtimeSwitch) html += btn("runtime-switch", "更换执行工具…", !options.inTask() ? options.runtimeSwitch.reason(item) : "请回到本组总对话，先暂停该人员仍在推进的全部目标，再更换执行工具");
    html += btn("stop", "停下这一轮", stopReason) + btn("compact", "压缩上下文", compactReason) + submenu(item, "模型和 effort", options.modelMenu(item)) + btn("comments", "评论") + btn("session-rename", "改名");
    if (isLeader) {
      html += btn("member-create", "新建组员") + btn("member-defaults", "组员默认大脑");
      html += btn("compact-department", "压缩整个部门…");
      html += btn("leader-trust", "本组信任模式") + btn("leader-trust-report", "本组信任报告");
      const siblings = options.ordering.sortable(item), index = siblings.indexOf(item), members = options.members(item.session_id), busy = members.filter(compactBusy).length;
      html += submenu(item, "设为优先", [1, 2, 3, 0].map(rank => `<button role="menuitemradio" aria-checked="${item.priority === (rank === 0 ? null : rank)}" data-act="set-priority" data-id="${esc(item.session_id)}" data-rank="${rank}"><span class="model-check">${item.priority === (rank === 0 ? null : rank) ? ic("check", "sm") : ""}</span>${rank === 0 ? "取消优先" : `第 ${rank} 优先`}</button>`).join(""));
      html += btn("leader-up", "上移", index === 0 ? "已经在最上面" : "") + btn("leader-down", "下移", index === siblings.length - 1 ? "已经在最下面" : "") + btn("group-tree", "看本组架构图") + btn("open-vscode", "转到 VS Code 里聊", editorHandoffReason(item) || (compactBusy(item) ? "会话正在工作，请等它空闲后再打开" : ""));
      if (item.is_git_admin) html += `<button role="menuitem" class="git-menu-link" data-act="git-settings">项目 Git 设置</button>`;
      else html += submenu(item, "结束这个 leader", members.length === 0 ? btn("close-group", "结束（组里没有下属）", "", true, 'data-with-members="false"') : btn("close-group", `<span>连下属一起归档<small>连 ${members.length} 个下属一起归档，其中 ${busy} 个在干活</small></span>`, "", true, 'data-with-members="true"') + btn("close-group", `<span>只结束 leader，下属退回待收编<small>${members.length} 个下属退回待收编，其中 ${busy} 个在干活</small></span>`, "", true, 'data-with-members="false"'), true);
    } else {
      const groups = options.leaders().filter(group => group.session_id !== item.leader_session_id);
      html += groups.length ? submenu(item, "换组", groups.map(group => btn("move-to", esc(group.name), "", false, `data-group="${esc(group.name)}"`)).join("")) : btn("move-menu", "换组", "没有其他协作组");
      html += btn("leave-group", "退组", item.leader_session_id === null ? "当前没有加入协作组" : "") + btn("archive", "归档", "", true) + btn("remove-session", "移出 Crew", "", true);
      if (item.where === "offline") html += btn("wake-session", "叫醒");
    }
    if (editorHandoffReason(item)) html += `<p class="editor-handoff-note">${esc(editorHandoffReason(item))}</p>`;
    return html;
  }
    function closeDialog(force=false){
      if(!dialog)return false;
      if(dialog.type==="rename"&&dialog.saving&&!force)return false;
      const previous=dialog;
      dialog=null;
      generation++;
      overlay.classList.remove("show");
      overlay.innerHTML="";
      previous.resolve?.(false);
      if(!destroyed)options.focus.restore(previous.returnTo);
      return true;
    }
    function showDialog(type,html,initial={}){
      options.beforeOpen?.();
      closeDialog(true);
      options.menu.hide();
      dialog={type,returnTo:captureFocus(),...initial};
      generation++;
      overlay.innerHTML=html;
      overlay.classList.add("show");
      return dialog;
    }
    function openRename(id){
      const item=options.person(id);if(!item||destroyed)return;
      const isLeader=options.leaders().some(person=>person.session_id===id);
      showDialog("rename",`<form class="modal rename-modal" id="session-rename-form" data-id="${esc(id)}" data-original="${esc(item.name)}" role="dialog" aria-modal="true" aria-labelledby="rename-title"><div class="rename-heading"><span class="rename-icon">${ic("pencil")}</span><div><h3 id="rename-title">给${isLeader?" leader ":"组员"}改名</h3><p class="d">${isLeader?"本组 leader":esc(item.group)+" 组的组员"}</p></div><button class="icon-btn" type="button" data-act="modal-close" aria-label="关闭改名">${ic("x")}</button></div><div class="rename-current">${av(item.name,32,!isLeader)}<div><span>当前名字</span><strong>${esc(item.name)}</strong></div></div><div class="field"><label for="session-new-name">新名字</label><input id="session-new-name" name="name" value="${esc(item.name)}" placeholder="取一个好辨认的名字" required maxlength="512" autocomplete="off"></div><p class="error-line" id="rename-error" role="alert"></p><div class="acts"><button class="btn ghost" type="button" data-act="modal-close">取消</button><button class="btn primary" type="submit" disabled>保存名字</button></div></form>`,{id,original:item.name,draft:item.name,saving:false,error:""});
      overlay.querySelector("#session-new-name").select();
    }
    function renameControls(){
      if(dialog?.type!=="rename")return;
      const form=overlay.querySelector("#session-rename-form");if(!form)return;
      for(const node of form.querySelectorAll("input,button"))node.disabled=dialog.saving;
      form.querySelector('[type="submit"]').disabled=dialog.saving||!dialog.draft.trim()||dialog.draft.trim()===dialog.original;
      overlay.querySelector("#rename-error").textContent=dialog.error;
    }
    function setRename(value){
      if(dialog?.type!=="rename")return;
      dialog.draft=value;
      dialog.error="";
      renameControls();
    }
    async function saveRename(){
      const current=dialog;
      if(current?.type!=="rename"||current.saving)return;
      const name=current.draft.trim();
      if(!name||name===current.original)return;
      current.saving=true;
      current.error="";
      renameControls();
      let result;
      try{
        result=await options.api("/api/session/rename",{session_id:current.id,name});
      }catch(error){
        current.saving=false;
        current.error=error.message;
        if(!destroyed&&dialog===current){
          renameControls();
          overlay.querySelector("#session-new-name").focus();
        }
        return;
      }
      current.saving=false;
      if(destroyed)return;
      if(dialog===current)closeDialog();
      // Once accepted, a refresh failure must never turn this into a new write.
      try{
        await options.refresh();
        if(!destroyed)options.notify(result.native_warnings?.length?"名字已更新；"+result.native_warnings.join("；"):"名字已更新");
      }catch(error){
        if(!destroyed)options.notify("名字已更新，刷新失败："+error.message);
      }
    }
    function openHandoff(id,result){
      const current=showDialog("handoff",`<div class="modal" role="dialog" aria-modal="true" aria-labelledby="handoff-title"><h3 id="handoff-title">在 VS Code 接续对话</h3><p>已打开目录，等 VS Code 窗口准备好：<code>${esc(result.cwd)}</code></p><p>${result.needs_confirmation?"接续时会停掉后台或终端这一份。":"接上同一个会话。"}</p><div class="acts"><button class="btn ghost" data-act="modal-close">先不了</button><button class="btn primary" data-act="session-handoff" data-id="${esc(id)}">窗口好了，接上</button></div></div>`,{id,result});
      overlay.querySelector('[data-act="session-handoff"]').focus();return current;
    }
    function confirmStop(item){
      if(destroyed)return Promise.resolve(false);
      const affected=options.relatedGoals(item);if(!affected.length)return Promise.resolve(true);
      const current=showDialog("stop",`<div class="modal" role="dialog" aria-modal="true" aria-labelledby="stop-person-title"><h3 id="stop-person-title">停止「${esc(item.name)}」？</h3><p class="d">将停止此人的 Crew 执行，并暂停它负责的以下任务及相关成员执行。对话、草稿和交付物会保留。</p><ul>${affected.map(goal=>`<li>${esc(goal.title)}</li>`).join("")}</ul><div class="acts"><button class="btn ghost" type="button" data-act="modal-close">返回</button><button class="btn" type="button" data-act="confirm-stop-person">确认停止此人</button></div></div>`);
      overlay.querySelector('[data-act="modal-close"]').focus();return new Promise(resolve=>{current.resolve=resolve;});
    }
    function acceptStop(){
      if(dialog?.type!=="stop")return;
      const resolve=dialog.resolve;
      dialog.resolve=null;
      closeDialog();
      resolve(true);
    }
    function openCompact(id){
      const item=options.person(id);if(!item||destroyed)return;
      showDialog("compact",`<div class="modal compact-modal" role="dialog" aria-modal="true" aria-labelledby="compact-title"><h3 id="compact-title">压缩「${esc(item.name)}」的上下文？</h3><p class="d">压缩后它只保留摘要，细节以白板为准。</p><div class="acts"><button class="btn ghost" type="button" data-act="modal-close">取消</button><button class="btn primary" type="button" data-act="compact-confirm" data-id="${esc(id)}">压缩</button></div></div>`,{id});
      overlay.querySelector('[data-act="modal-close"]').focus();
    }
    async function run(key,operation){
      if(destroyed||pending.has(key))return;
      pending.add(key);
      try{return await operation();}
      finally{pending.delete(key);}
    }
    async function openEditor(id){
      const item=options.person(id);
      if(!item)return;
      const reason=editorHandoffReason(item);
      if(reason)throw Error(reason);
      const opened=generation;
      return run(`open:${id}`,async()=>{
        const result=await options.api("/api/open",{session_id:id});
        if(destroyed)return;
        if(result.needs_window_ready){
          if(generation!==opened)return;
          if(cardId===id){
            handoff={...result,session_id:id};
            renderCard();
          }else openHandoff(id,result);
        }else options.notify("已在 VS Code 打开");
      });
    }
    async function continueEditor(id){
      const current=dialog?.type==="handoff"&&dialog.id===id?dialog:null;
      const receipt=current?.result||(handoff?.session_id===id?handoff:null);
      if(!receipt)return;
      return run(`continue:${id}`,async()=>{
        await options.api("/api/open/continue",{session_id:id,confirm:receipt.needs_confirmation});
        if(destroyed)return;
        if(current&&dialog===current)closeDialog();
        if(handoff===receipt){handoff=null;renderCard();}
        await options.refresh();
        if(!destroyed)options.notify("已在 VS Code 接上同一个会话");
      });
    }
    function cancelHandoff(){handoff=null;renderCard();}
    async function changeSession(id,path,label,undoable){
      return run(`session:${id}`,async()=>{
        const result=await options.api(path,{session_id:id});
        if(destroyed)return;
        options.menu.hide();
        if(cardId===id&&path!=="/api/session/stop")closeCard();
        await options.refresh();
        if(destroyed)return;
        const undo=undoable?async()=>{
          await options.api("/api/session/undo",{session_id:id,before:result.before});
          if(destroyed)return;
          await options.refresh();
          if(!destroyed)options.notify("已撤销");
        }:null;
        options.notify(label,undo);
      });
    }
    async function moveTo(id,group){
      const item=options.person(id);
      if(!item)return;
      const body={session_id:id,name:item.name,from:item.group,to:group};
      return run(`roster:${id}`,async()=>{
        await options.api("/api/roster/move",body);
        if(destroyed)return;
        options.menu.hide();
        await options.refresh();
        if(!destroyed)options.notify(`已换到 ${group} 组`);
      });
    }
    async function leave(id){
      const item=options.person(id);
      if(!item)return;
      const body={session_id:id,name:item.name,group:item.group};
      return run(`roster:${id}`,async()=>{
        await options.api("/api/roster/remove",body);
        if(destroyed)return;
        options.menu.hide();
        if(cardId===id)closeCard();
        await options.refresh();
        if(!destroyed)options.notify("已退组，可以在待收编里重新收编");
      });
    }
    async function closeGroup(id,withMembers){
      const item=options.person(id);
      if(!item)return;
      const group=item.name;
      return run(`group:${id}`,async()=>{
        const result=await options.api("/api/group/close",{group,with_members:withMembers});
        if(destroyed)return;
        closeCard();
        await options.refresh();
        if(destroyed)return;
        options.notify("已结束 leader",async()=>{
          await options.api("/api/group/undo",{group,before:result.before});
          if(destroyed)return;
          await options.refresh();
          if(!destroyed)options.notify("已撤销");
        });
      });
    }
    function moveMenu(element){
      const item=options.person(element.dataset.id);if(!item)return;
      options.menu.show(element,options.leaders().filter(group=>group.session_id!==item.leader_session_id).map(group=>`<button data-act="move-to" data-id="${esc(item.session_id)}" data-group="${esc(group.name)}">${av(group.name,20)}${esc(group.name)}</button>`).join(""));
    }
    function sessionMenuOpen(anchor,id,point=null){
      const item=options.person(id);
      if(item&&!destroyed)options.menu.show(anchor,sessionMenu(item),point);
    }
    const sessionSurface=target=>target.closest('[data-session-menu],#sidebar [data-act="leader"],[data-act="card"]');
    function contextmenu(event){
      const surface=sessionSurface(event.target);
      if(!surface)return;
      event.preventDefault();
      sessionMenuOpen(surface,surface.dataset.sessionMenu??surface.dataset.id,{x:event.clientX,y:event.clientY});
    }
    function input(event){if(event.target.closest?.("#session-rename-form")&&event.target.name==="name")setRename(event.target.value);}
    function submit(event){if(event.target.id==="session-rename-form"){event.preventDefault();void saveRename();}}
    function click(event){if(event.target===overlay)closeDialog();else if(event.target===cardBack)closeCard();}
    function keydown(event){
      if(destroyed||event.defaultPrevented||event.isComposing||options.interactionBlocked?.())return;
      if((event.key==="ContextMenu"||event.shiftKey&&event.key==="F10")&&!dialog){
        const surface=sessionSurface(event.target);
        if(surface){
          event.preventDefault();
          sessionMenuOpen(surface,surface.dataset.sessionMenu??surface.dataset.id);
          return;
        }
      }
      if(options.menu.isOpen())return;
      if(event.key==="Escape"&&(dialog||cardId!==null)){
        event.preventDefault();
        if(dialog)closeDialog();else closeCard();
        return;
      }
      if(event.key!=="Tab")return;
      const surface=dialog?overlay:cardId!==null?card:null;
      if(!surface)return;
      const items=[...surface.querySelectorAll('button:not([disabled]),input:not([disabled]),textarea:not([disabled]),a[href]')].filter(item=>item.getClientRects().length),first=items[0],last=items.at(-1);
      if(event.shiftKey&&doc.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&doc.activeElement===last){event.preventDefault();first?.focus();}
    }
    const handlers={contextmenu,input,submit,click,keydown};
    for(const [type,handler]of Object.entries(handlers))doc.addEventListener(type,handler);
    function confirmCompact(element){
      const id=element.dataset.id;
      closeDialog();
      return options.compact(id,{afterTurn:false});
    }
    function wakeSession(id){
      return run(`wake:${id}`,async()=>{
        await options.api("/api/wake",{session_id:id,text:"用户叫醒了你，请继续当前任务。"});
        if(destroyed)return;
        await options.refresh();
        if(!destroyed)options.notify("已叫醒");
      });
    }
    const unregister=options.actions?.register("people",{
      "session-menu":({element})=>sessionMenuOpen(element,element.dataset.id),
      card:({element})=>openCard(element.dataset.id),
      "card-close":()=>closeCard(),
      "session-rename":({element})=>openRename(element.dataset.id),
      rename:()=>openRename(cardId),
      "modal-close":()=>closeDialog(),
      "confirm-stop-person":acceptStop,
      compact:({element})=>openCompact(element.dataset.id),
      "compact-confirm":({element})=>confirmCompact(element),
      "move-menu":({element})=>moveMenu(element),
      "more-menu":({element})=>options.menu.show(element,`<button data-act="leave-group" data-id="${esc(element.dataset.id)}">${ic("leave","sm")}退组</button><button class="danger" data-act="remove-session" data-id="${esc(element.dataset.id)}">${ic("x","sm")}移出 Crew</button>`),
      "move-to":({element})=>moveTo(element.dataset.id,element.dataset.group),
      "leave-group":({element})=>leave(element.dataset.id),
      archive:({element})=>changeSession(element.dataset.id,"/api/session/archive","已归档",true),
      "remove-session":({element})=>changeSession(element.dataset.id,"/api/session/remove","已移出 Crew",true),
      "close-group":({element})=>closeGroup(element.dataset.id,element.dataset.withMembers==="true"),
      stop:({element})=>changeSession(element.dataset.id,"/api/session/stop","已请求中断本轮，正在等待停止确认",false),
      "wake-session":({element})=>wakeSession(element.dataset.id),
      "open-vscode":({element})=>openEditor(element.dataset.id),
      "handoff-go":({element})=>continueEditor(element.dataset.id),
      "session-handoff":({element})=>continueEditor(element.dataset.id),
      "handoff-cancel":cancelHandoff
    });
    const unregisterVisibility=options.visibility?.register("people",()=>cardId!==null||dialog!==null);
    return {openCard,closeCard,openRename,setRename,saveRename,openEditor,continueEditor,confirmStop,acceptStop,openCompact,closeDialog,sessionMenu,sessionMenuOpen,editorHandoffReason,moveTo,leave,closeGroup,changeSession,
      update(){if(!destroyed)renderCard();},cardId:()=>cardId,isOpen:()=>cardId!==null||dialog!==null,confirmingStop:()=>dialog?.type==="stop",
      snapshot:()=>({cardId,handoff:handoff?{...handoff}:null,dialog:dialog?{type:dialog.type,id:dialog.id,draft:dialog.draft,saving:dialog.saving,error:dialog.error}:null,pending:[...pending]}),
      close(){
        closeDialog(true);
        closeCard(false);
        generation++;
      },
      destroy(){
        destroyed=true;
        closeDialog(true);
        cardId=null;
        handoff=null;
        unregister?.();
        unregisterVisibility?.();
        for(const[type,handler]of Object.entries(handlers))doc.removeEventListener(type,handler);
        if(!options.portal)root.remove();
        else{
          card.classList.remove("show");
          cardBack.classList.remove("show");
        }
      }};
  }
  const exported={create};
  if(typeof module!=="undefined"&&module.exports)module.exports=exported;
  else window.CrewPeopleUI=exported;
})();
