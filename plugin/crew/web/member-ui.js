"use strict";
(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const clone = value => JSON.parse(JSON.stringify(value));
  function effective(leader, globalChoice) {
    return clone(leader.member_defaults ?? globalChoice ?? {runtime:null,model:null,effort:null});
  }
  const validate=window.CrewRuntimeCatalog.validateSelection;
  function create(options) {
    const overlay = document.createElement("div"); overlay.className = "session-config-backdrop"; overlay.hidden = true; document.body.append(overlay);
    let owner = null, mode = "create", draft = null, name = "", task = "", inherit = true, busy = false, error = "", returnTo = null;
    const custom = new Set();
    const fields = () => window.CrewSettings.selectionMarkup("member",draft,options.catalog(),custom);
    function controls() {
      const submit = overlay.querySelector('[type="submit"]');
      if (submit) { submit.disabled = busy || mode === "create" && (!name.trim() || !task.trim()); submit.textContent = busy ? (mode === "create" ? "正在创建…" : "正在保存…") : mode === "create" ? "创建并开始" : "保存默认设置"; }
    }
    function render() {
      overlay.innerHTML = `<section class="session-config-dialog member-dialog" role="dialog" aria-modal="true" aria-labelledby="member-dialog-title"><div class="session-config-head"><div><span class="member-eyebrow">${esc(owner.name)} 组</span><h2 id="member-dialog-title">${mode === "create" ? "新建组员" : "组员默认大脑"}</h2></div><button type="button" class="icon-btn" data-member-action="close" aria-label="关闭" ${busy ? "disabled" : ""}>×</button></div><form>${mode === "create" ? `<p class="session-config-description">直接加入本组，并开始你填写的任务。</p><label class="session-config-label" for="member-name">组员名字</label><input id="member-name" name="name" value="${esc(name)}" placeholder="例如：前端实现" required maxlength="512" autocomplete="off"><label class="session-config-label" for="member-task">它要做什么</label><textarea id="member-task" name="task" rows="3" placeholder="写清任务和完成标准" required maxlength="20000">${esc(task)}</textarea><div class="member-brain-heading"><h3>本次使用的大脑</h3><button type="button" class="member-refresh" data-member-action="refresh" ${busy ? "disabled" : ""}>重新检测</button></div>` : `<p class="session-config-description">仅用于这个 leader 之后新建的组员，手动新建和 AI 召唤都生效。</p><label class="member-inherit"><input type="checkbox" name="inherit" ${inherit ? "checked" : ""} ${busy ? "disabled" : ""}>使用全局组员设置</label><p class="session-config-hint">未明确配置时，由后台按成员角色、全局规则和原生设置解析，不复制 leader 的当前大脑。</p>`}${mode === "defaults" && inherit ? '<p class="member-follow">本组将使用当前全局规则。保存不会运行 AI。</p>' : `<div data-member-brain>${fields()}</div>`}<p class="error-line" data-member-error role="alert">${esc(error)}</p><div class="session-config-actions"><p class="session-config-hint">${mode === "create" ? "创建后立即执行任务；默认在当前工作区运行。" : "保存只影响以后新建的组员，已有组员保持各自设置。"}</p><span class="gap"></span><button type="button" class="btn ghost" data-member-action="close" ${busy ? "disabled" : ""}>取消</button><button type="submit" class="btn primary"></button></div></form></section>`;
      if (busy) for (const field of overlay.querySelectorAll("input,textarea,select,button")) field.disabled = true;
      controls();
    }
    function close() { if (busy) return; overlay.hidden = true; owner = null; if (returnTo?.isConnected) returnTo.focus({preventScroll:true}); }
    function open(id, nextMode) {
      const leader = options.getLeader(id); if (!leader) { options.toast("请先选择当前工作区的 leader。"); return; }
      owner = leader; mode = nextMode; draft = effective(owner,options.globalChoice()); name = ""; task = "";
      inherit = owner.member_defaults == null; error = ""; custom.clear(); busy = false; returnTo = document.activeElement;
      overlay.hidden = false; render(); overlay.querySelector(mode === "create" ? "#member-name" : 'input[name="inherit"]').focus();
    }
    overlay.addEventListener("input", event => {
      if (busy) return;
      if (event.target.name === "name") name = event.target.value;
      if (event.target.name === "task") task = event.target.value;
      if (event.target.dataset.aiField) draft[event.target.dataset.aiField] = event.target.value.trim() || null;
      error = ""; overlay.querySelector('[data-member-error]').textContent = ""; controls();
    });
    overlay.addEventListener("change", event => {
      if (busy) return;
      if (event.target.name === "inherit") { inherit = event.target.checked; render(); overlay.querySelector('input[name="inherit"]').focus(); return; }
      const field = event.target.dataset.aiField; if (!field) return;
      draft[field] = event.target.value.trim() || null;
      if (field === "runtime") { draft.model = null; draft.effort = null; }
      if (field === "runtime" || field === "model") {
        overlay.querySelector('[data-member-brain]').innerHTML = fields();
        overlay.querySelector(`[data-ai-field="${field}"]`).focus();
      }
    });
    overlay.addEventListener("click", async event => {
      if (event.target === overlay) { close(); return; }
      const button = event.target.closest("button"); if (!button || button.disabled || busy) return;
      if (button.dataset.memberAction === "close") close();
      if (button.dataset.aiCustom) {
        const key = button.dataset.aiCustom; if (custom.has(key)) custom.delete(key); else custom.add(key);
        overlay.querySelector('[data-member-brain]').innerHTML = fields();
        overlay.querySelector(`[data-ai-field="${key.split(":")[1]}"]`).focus();
      }
      if (button.dataset.memberAction === "refresh") {
        busy = true; render();
        try { await options.refresh(); error = ""; } catch (caught) { error = caught.message; }
        finally { busy = false; if (owner) render(); }
      }
    });
    overlay.addEventListener("submit", async event => {
      event.preventDefault(); if (busy) return;
      let choice;
      try {
        choice = mode === "defaults" && inherit ? null : validate(draft,options.catalog());
        if (mode === "defaults" && choice !== null) {
          choice = {runtime:null,model:null,effort:null,...choice};
          if (Object.values(choice).every(value => value === null)) choice = null;
        }
      }
      catch (caught) { error = caught.message; render(); return; }
      if (mode === "create" && (!name.trim() || !task.trim())) return;
      busy = true; error = ""; render();
      try {
        const result = mode === "defaults" ? await options.api('/api/member-defaults',{leader_session_id:owner.session_id,selection:choice}) : await options.api('/api/spawn',{name:name.trim(),task:task.trim(),leader_session_id:owner.session_id,role:"member",...choice});
        const parent = owner.session_id, created = mode === "create"; busy = false; close();
        const resolution = created ? window.CrewRuntimeCatalog.selectionResolutionLabel(result.selection_resolution) : "";
        options.toast(created ? `组员已创建，执行状态以会话回执为准。${resolution ? " " + resolution : ""}` : "本组组员默认大脑已保存。");
        await options.onChanged(created ? result.session_id : null,parent);
      } catch (caught) { busy = false; if (owner) { error = caught.message; render(); } else options.toast("操作已完成，刷新失败：" + caught.message); }
    });
    overlay.addEventListener("keydown", event => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); }
      if (event.key === "Tab") {
        const items = [...overlay.querySelectorAll('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled)')], first = items[0], last = items[items.length-1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    });
    return {openCreate:id=>open(id,"create"),openDefaults:id=>open(id,"defaults"),close};
  }
  window.CrewMembers = {create,effective,validate};
})();
