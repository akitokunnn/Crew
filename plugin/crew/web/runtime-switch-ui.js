"use strict";
(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[char]);
  const runtimeId = item => item.runtime || "claude";
  function reason(item) {
    if (!item) return "这个会话已不在当前列表中。";
    if (item.where === "vscode") return "会话开在 VS Code 中，请先在原工具退出。";
    if (item.busy || item.status === "working") return "会话正在工作，请等这一轮结束或确认暂停后更换。";
    return item.runtime_switch?.allowed ? "" : item.runtime_switch?.reason || "当前会话暂不支持更换执行工具。";
  }
  function targetPayload(member, target, catalog, handoff) {
    const blocked = reason(member); if (blocked) throw Error(blocked);
    if (!target.runtime || target.runtime === runtimeId(member)) throw Error("请选择另一个执行工具。");
    const runtime = catalog.find(item => item.id === target.runtime);
    if (!runtime?.available) throw Error(runtime?.error || "目标执行工具不可用，请检查安装和登录。");
    if (target.model !== null && !(runtime.models || []).some(item => item.id === target.model)) throw Error("目标模型不在这个工具的列表中，请重新选择。");
    if (target.runtime === "codex" && target.effort !== null && target.model === null) throw Error("为 Codex 指定 effort 前，请先选择具体模型。");
    const model = (runtime.models || []).find(item => item.id === target.model);
    const efforts = Array.isArray(model?.efforts) ? model.efforts : runtime.efforts || [];
    if (target.effort !== null && !efforts.includes(target.effort)) throw Error("目标模型不支持这个 effort，请重新选择。");
    return {session_id:member.session_id, runtime:target.runtime, model:target.model, effort:target.effort, ...(handoff === null ? {} : {handoff})};
  }
  function create(options) {
    const overlay = document.createElement("div"); overlay.className = "session-config-backdrop"; overlay.hidden = true; document.body.append(overlay);
    let memberId = null, target = null, preview = null, handoff = "", edited = false, dirty = false, busy = false, error = "", returnTo = null;
    const member = () => options.getMember(memberId);
    const catalog = () => options.catalog();
    const name = id => catalog().find(item => item.id === id)?.name || (id === "claude" ? "Claude Code" : id === "codex" ? "Codex" : id);
    const expired = () => preview && Date.parse(preview.expires_at) <= Date.now();
    function entry(item) {
      const blocked = reason(item);
      return `<div class="member-runtime-switch"><button class="btn ghost" data-act="runtime-switch" data-id="${esc(item.session_id)}" ${blocked ? "disabled" : ""}>更换执行工具…</button>${blocked ? `<span class="runtime-switch-reason">${esc(blocked)}</span>` : item.runtime_switch?.pending_handoff ? '<span class="runtime-switch-reason">交接已保存，下次发消息或唤醒时接着做</span>' : ""}</div>`;
    }
    function invalidate() { preview = null; dirty = false; error = ""; }
    function render() {
      const current = member(); if (!current || !target) return;
      const runtime = catalog().find(item => item.id === target.runtime), efforts = options.effortsFor(target.runtime, target.model);
      const effortDisabled = target.runtime === "codex" && target.model === null || efforts.length === 0;
      const blocked = reason(current), ready = preview && !dirty && !expired() && !blocked;
      const scope = preview?.scope;
      const scopeNotice = scope ? `<section class="runtime-switch-scope"><strong>更换范围：${esc(current.name)}本人的默认工具</strong><p>${esc(scope.notice)}</p>${scope.executions?.length ? `<ul>${scope.executions.map(item => `<li>${esc(item.title)} · ${esc({paused:"已暂停",queued:"尚未启动",waiting_user:"等待用户",awaiting_acceptance:"待验收"}[item.status] || item.status)}</li>`).join("")}</ul>` : '<p>没有需要配置的关联目标执行。</p>'}</section>` : "";
      overlay.innerHTML = `<section class="session-config-dialog runtime-switch-dialog" role="dialog" aria-modal="true" aria-labelledby="runtime-switch-title"><div class="session-config-head"><h2 id="runtime-switch-title">${esc(current.name)} · 更换执行工具</h2><button class="icon-btn" data-rs-act="close" aria-label="关闭更换执行工具" ${busy ? "disabled" : ""}>×</button></div><p class="session-config-description">更换只保存配置，不会立即启动模型。原生历史各自保留，用交接摘要传递任务和进度。</p><div class="runtime-switch-route"><span>当前 ${esc(name(runtimeId(current)))}</span><span aria-hidden="true">→</span><label><span class="sr-only">目标执行工具</span><select id="runtime-switch-runtime" ${busy ? "disabled" : ""}>${catalog().filter(item => item.id !== runtimeId(current)).map(item => `<option value="${esc(item.id)}" ${item.id === target.runtime ? "selected" : ""}>${esc(item.name)}${item.available ? "" : " · 未就绪"}</option>`).join("")}</select></label></div><div class="runtime-switch-options"><label>目标模型<select id="runtime-switch-model" ${busy || !runtime?.available ? "disabled" : ""}><option value="">由运行端决定</option>${(runtime?.models || []).map(item => `<option value="${esc(item.id)}" ${item.id === target.model ? "selected" : ""}>${esc(item.name)}</option>`).join("")}</select></label><label>effort<select id="runtime-switch-effort" ${busy || effortDisabled || !runtime?.available ? "disabled" : ""}><option value="">由运行端决定</option>${efforts.map(item => `<option value="${esc(item)}" ${item === target.effort ? "selected" : ""}>${esc(item)}</option>`).join("")}</select></label><button class="btn ghost" data-rs-act="catalog" ${busy ? "disabled" : ""}>重新检测工具</button></div>${!runtime?.available ? `<p class="error-line">${esc(runtime?.error || "目标工具不可用，请检查安装和登录。")}</p>` : ""}${target.runtime === "codex" && target.model === null ? '<p class="session-config-hint">选择具体 Codex 模型后，可以指定其支持的 effort。</p>' : ""}${preview || edited ? `<label class="session-config-label" for="runtime-switch-handoff">交接摘要 <span class="runtime-switch-chars">${dirty ? "已编辑，需重新生成预览" : `${preview?.handoff?.chars ?? Array.from(handoff).length} 个字符`}</span></label><textarea id="runtime-switch-handoff" rows="8" maxlength="16000" ${busy ? "disabled" : ""}>${esc(handoff)}</textarea>${preview?.handoff?.truncated ? '<p class="session-config-hint">摘要已按范围截取，请检查任务和关键进度是否完整。</p>' : ""}${preview?.to?.resume_existing ? `<p class="session-config-hint">将恢复原有 ${esc(name(preview.to.runtime))} 原生会话，并在下一次对话附上交接摘要。</p>` : ""}${preview?.to?.resume_existing && (preview.to.model == null || preview.to.effort == null) ? '<p class="session-config-hint">恢复原会话时可能沿用它原有的模型或档位，启动后的实际显示为准；要指定配置请明确选择。</p>' : ""}` : '<p class="session-config-hint">先生成交接预览，检查后再确认。生成预览不会调用模型。</p>'}${scopeNotice}<p class="runtime-switch-cost">${esc(preview?.cost?.notice || "下次发送消息或唤醒时，会重读交接上下文，可能增加用量。")}</p><p class="error-line" role="alert">${esc(error || blocked || (expired() ? "预览已过期，请重新生成后确认。" : ""))}</p><div class="session-config-actions"><button class="btn ghost" data-rs-act="close" ${busy ? "disabled" : ""}>取消</button><span class="gap"></span><button class="btn" data-rs-act="prepare" ${busy || !runtime?.available || blocked ? "disabled" : ""}>${busy ? "正在处理…" : preview || edited ? "重新生成预览" : "生成交接预览"}</button><button class="btn primary" data-rs-act="confirm" ${busy || !ready ? "disabled" : ""}>确认更换</button></div></section>`;
    }
    function close() { if (busy) return; overlay.hidden = true; memberId = null; if (returnTo?.isConnected) returnTo.focus({preventScroll:true}); }
    function open(id) {
      const current = options.getMember(id), blocked = reason(current); if (blocked) { options.toast(blocked); return; }
      const other = catalog().find(item => item.id !== runtimeId(current) && item.available);
      if (!other) { options.toast("没有可用的其他执行工具，请安装并登录后重新检测。"); return; }
      memberId = id; returnTo = document.activeElement; target = {runtime:other.id, model:null, effort:null}; preview = null; handoff = ""; edited = false; dirty = false; error = ""; busy = false; overlay.hidden = false; render(); overlay.querySelector("select").focus();
    }
    overlay.addEventListener("change", event => {
      if (!target || busy) return;
      if (event.target.id === "runtime-switch-runtime") { target = {runtime:event.target.value, model:null, effort:null}; invalidate(); render(); }
      else if (event.target.id === "runtime-switch-model") { target.model = event.target.value || null; target.effort = null; invalidate(); render(); }
      else if (event.target.id === "runtime-switch-effort") { target.effort = event.target.value || null; invalidate(); render(); }
    });
    overlay.addEventListener("input", event => {
      if (event.target.id !== "runtime-switch-handoff") return; handoff = event.target.value; edited = true; dirty = true;
      overlay.querySelector('[data-rs-act="confirm"]').disabled = true; overlay.querySelector(".runtime-switch-chars").textContent = "已编辑，需重新生成预览";
    });
    overlay.addEventListener("click", async event => {
      if (event.target === overlay) { close(); return; }
      const button = event.target.closest("[data-rs-act]"); if (!button || button.disabled || busy) return;
      const act = button.dataset.rsAct;
      if (act === "close") { close(); return; }
      busy = true; error = ""; render();
      try {
        if (act === "catalog") {
          await options.refreshCatalog();
          const runtime = catalog().find(item => item.id === target.runtime);
          if (target.model !== null && !(runtime?.models || []).some(item => item.id === target.model)) { target.model = null; target.effort = null; }
          else if (target.effort !== null && !options.effortsFor(target.runtime,target.model).includes(target.effort)) target.effort = null;
          invalidate();
        }
        else if (act === "prepare") {
          const body = targetPayload(member(), target, catalog(), edited ? handoff : null);
          const result = await options.prepare(body);
          if (!result.allowed) throw Error(result.reason || "当前会话不能更换执行工具。");
          if (!result.preview_id || result.starts_turn !== false || typeof result.handoff?.text !== "string" || !Number.isFinite(Date.parse(result.expires_at))) throw Error("预览结果不完整，请重新生成。");
          preview = result; handoff = result.handoff.text; dirty = false;
        } else if (act === "confirm") {
          const blocked = reason(member()); if (blocked) throw Error(blocked);
          if (!preview || dirty || expired()) throw Error("请重新生成交接预览后确认。");
          const result = await options.switchRuntime({session_id:memberId, preview_id:preview.preview_id, confirm:true});
          if (!result.ok || result.status !== "configured" || result.starts_turn !== false) throw Error("尚未确认配置结果，请刷新成员卡查看。");
          await options.onChanged(result); busy = false; close(); options.toast("已更换，下次发消息或唤醒时继续"); return;
        }
      } catch (caught) { error = caught.message || String(caught); if (caught.status === 409) { preview = null; dirty = true; edited = true; } }
      finally { busy = false; if (!overlay.hidden) render(); }
    });
    document.addEventListener("keydown", event => {
      if (overlay.hidden) return;
      if (event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); close(); }
      else if (event.key === "Tab") {
        const all = [...overlay.querySelectorAll('button:not([disabled]),select:not([disabled]),textarea:not([disabled])')], first = all[0], last = all[all.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    }, true);
    return {open, close, entry, reason};
  }
  window.CrewRuntimeSwitch = {create, reason, targetPayload};
})();
