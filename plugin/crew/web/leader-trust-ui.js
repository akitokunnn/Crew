"use strict";
(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[char]);
  const trustFor = leader => leader?.trust || {enabled:false, goal:"", updated_at:null};
  function payload(id, enabled, goal) {
    const text = String(goal || "").trim();
    if (!id) throw Error("请先选择一个 leader。");
    if (enabled && !text) throw Error("开启信任模式前，请写清本组的目标和授权范围。");
    return {leader_session_id:id, enabled:Boolean(enabled), goal:text};
  }
  function indicator(leader) {
    if (!leader) return ""; const trust = trustFor(leader);
    return `<button class="leader-trust-indicator ${trust.enabled ? "on" : ""}" data-act="leader-trust" data-id="${esc(leader.session_id)}" title="设置 ${esc(leader.name)} 的信任模式">信任 · ${trust.enabled ? "开" : "关"}</button>`;
  }
  function note(leader) {
    const trust = trustFor(leader); if (!trust.enabled) return "";
    return `<div class="leader-trust-note"><div><strong>本组信任目标</strong><p>${esc(trust.goal)}</p><span>下次对话、恢复或压缩时传达；需要你动手的事进入“我的待办”。</span></div><button class="btn ghost" data-act="leader-trust" data-id="${esc(leader.session_id)}">设置</button></div>`;
  }
  function create(options) {
    const overlay = document.createElement("div"); overlay.className = "session-config-backdrop"; overlay.hidden = true; document.body.append(overlay);
    let active = null, draft = null, busy = false, error = "", report = null, returnTo = null;
    function render() {
      if (!active) return;
      overlay.innerHTML = `<section class="session-config-dialog leader-trust-dialog" role="dialog" aria-modal="true" aria-labelledby="leader-trust-title"><div class="session-config-head"><h2 id="leader-trust-title">${esc(active.name)} · 信任模式</h2><button class="icon-btn" data-lt-act="close" aria-label="关闭信任设置" ${busy ? "disabled" : ""}>×</button></div>${report === null ? `<form id="leader-trust-form"><p class="session-config-description">只为这个 leader 保存目标。保存后不会主动发消息或叫醒会话。</p><label class="leader-trust-toggle"><input name="enabled" type="checkbox" ${draft.enabled ? "checked" : ""} ${busy ? "disabled" : ""}>开启本组信任模式</label><label for="leader-trust-goal" class="session-config-label">目标和授权范围</label><textarea id="leader-trust-goal" name="goal" rows="6" maxlength="20000" placeholder="写清这个组要完成什么、可以自主处理到什么程度" ${draft.enabled ? "required" : ""} ${busy ? "disabled" : ""}>${esc(draft.goal)}</textarea><p class="session-config-hint">下次对话、恢复或压缩时，会顺便告知它当前设置。</p><p class="error-line" role="alert">${esc(error)}</p><div class="session-config-actions"><button type="button" class="btn ghost" data-lt-act="report" ${busy ? "disabled" : ""}>查看本组报告</button><a class="btn ghost" href="${esc(options.reportUrl(active.session_id))}" download>下载报告</a><span class="gap"></span><button type="button" class="btn ghost" data-lt-act="close" ${busy ? "disabled" : ""}>取消</button><button type="submit" class="btn primary" ${busy ? "disabled" : ""}>${busy ? "正在保存…" : "保存"}</button></div></form>` : `<p class="session-config-description">仅包含 ${esc(active.name)} 组的信任设置与推进记录。</p><div class="leader-trust-report markdown">${options.renderMarkdown(report, active.session_id)}</div><div class="session-config-actions"><button class="btn ghost" data-lt-act="back">返回设置</button><span class="gap"></span><a class="btn" href="${esc(options.reportUrl(active.session_id))}" download>下载本组报告</a></div>`}</section>`;
    }
    function close() { if (busy) return; overlay.hidden = true; active = null; if (returnTo?.isConnected) returnTo.focus({preventScroll:true}); }
    function open(id, showReport = false) {
      const leader = options.getLeader(id); if (!leader) { options.toast("这个 leader 已不在当前列表中。"); return; }
      active = leader; draft = {enabled:Boolean(trustFor(leader).enabled), goal:trustFor(leader).goal || ""};
      returnTo = document.activeElement; error = ""; report = null; busy = false; overlay.hidden = false; render(); overlay.querySelector("textarea").focus();
      if (showReport) overlay.querySelector('[data-lt-act="report"]').click();
    }
    overlay.addEventListener("click", async event => {
      if (event.target === overlay) { close(); return; }
      const button = event.target.closest("[data-lt-act]"); if (!button || button.disabled) return;
      if (button.dataset.ltAct === "close") close();
      else if (button.dataset.ltAct === "back") { report = null; render(); }
      else if (button.dataset.ltAct === "report" && !busy) {
        busy = true; error = ""; render();
        try { report = await options.readReport(active.session_id); }
        catch (caught) { error = caught.message || String(caught); }
        finally { busy = false; render(); }
      }
    });
    overlay.addEventListener("input", event => {
      if (!draft) return;
      if (event.target.name === "enabled") { draft.enabled = event.target.checked; overlay.querySelector("textarea").required = draft.enabled; }
      else if (event.target.name === "goal") draft.goal = event.target.value;
    });
    overlay.addEventListener("submit", async event => {
      if (event.target.id !== "leader-trust-form") return; event.preventDefault(); if (busy) return;
      let data; try { data = payload(active.session_id, draft.enabled, draft.goal); } catch (caught) { error = caught.message; render(); return; }
      busy = true; error = ""; render();
      try { await options.save(data); await options.onSaved(); busy = false; close(); options.toast("已保存，下次对话会告知它"); }
      catch (caught) { busy = false; error = caught.message || String(caught); render(); }
    });
    document.addEventListener("keydown", event => {
      if (overlay.hidden) return;
      if (event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); close(); }
      else if (event.key === "Tab") {
        const all = [...overlay.querySelectorAll('button:not([disabled]),input:not([disabled]),textarea:not([disabled]),a[href]')];
        const first = all[0], last = all[all.length - 1]; if (!first) return;
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    }, true);
    return {open, close, indicator, note};
  }
  window.CrewLeaderTrust = {create, payload, trustFor, indicator, note};
})();
