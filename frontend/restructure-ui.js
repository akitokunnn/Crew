"use strict";
// Restructure preview: a plan of structural changes shown as before / after, with what it does to leaders and tasks
// and the editable handoff drafts for merged, retired or split leaders. A standalone panel: the architecture board
// opens it from a leader's menu, and the assistant's confirmation card will open it with the plan it composed
// (docs/ASSISTANT_CAMPAIGN_DESIGN.md §7). The before / after outline is shared with the board's version history.
const CrewRestructure = (() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[c]);
  const join = path => path.join(" › ");
  const MAX_HANDOFF = 16000, NOTE_MAX = 200;
  const ACTION = {cancel:"作废", transfer:"改派"};
  const TYPE = {"part.create":"新建 part", "part.rename":"part 改名", "part.move":"改挂 part", "part.merge":"合并 part", "part.delete":"删除 part",
    "leader.assign":"调整 leader 位置", "leader.lead":"设置负责人", "leader.merge":"合并 leader", "leader.retire":"退役 leader",
    "leader.split":"拆分 leader", "goal.cancel":"作废任务", "goal.transfer":"改派任务"};

  // ---- before / after outline --------------------------------------------------------------------------------------
  // One structure as an indented tree, each part and leader marked against the other side.
  function outline(state, other, side, planned = new Map()) {
    const otherParts = new Map(other.parts.map(part => [part.id, part]));
    const otherPlaces = new Map(other.leaders.map(item => [item.session_id, item]));
    const children = new Map();
    for (const part of state.parts) { const key = part.parent_id ?? ""; if (!children.has(key)) children.set(key, []); children.get(key).push(part); }
    const members = new Map();
    for (const item of state.leaders) { if (!members.has(item.domain_id)) members.set(item.domain_id, []); members.get(item.domain_id).push(item); }
    for (const list of members.values()) list.sort((a, b) => (b.lead - a.lead) || a.name.localeCompare(b.name, "zh"));
    const row = (part, level) => {
      const peer = otherParts.get(part.id);
      let status = "", tag = "";
      if (!peer) { status = side === "after" ? "new" : "removed"; tag = side === "after" ? "新建" : "删除"; }
      else if (peer.name !== part.name) { status = "renamed"; tag = side === "after" ? `原名 ${peer.name}` : `改名为 ${peer.name}`; }
      else if (peer.parent_id !== part.parent_id) { status = "moved"; tag = "改挂"; }
      const chips = (members.get(part.id) || []).map(item => {
        const there = otherPlaces.get(item.session_id);
        const moved = !there || there.domain_id !== item.domain_id;
        const cls = moved ? (side === "after" ? " in" : " out") : "";
        const note = moved ? (side === "after" ? " · 移入" : " · 移出") : item.lead ? " · 负责" : "";
        return `<span class="rs-leader${cls}${item.lead ? " lead" : ""}">${esc(item.name)}${note}</span>`;
      }).concat((planned.get(part.id) || []).map(name => `<span class="rs-leader planned">${esc(name)} · 待启动</span>`)).join("");
      return `<div class="rs-part ${status}" style="--level:${level}">${tag ? `<span class="rs-status">${esc(tag)}</span>` : ""}<span class="rs-name">${esc(part.name)}</span>${chips ? `<span class="rs-leaders">${chips}</span>` : ""}</div>`;
    };
    const walk = (key, level) => (children.get(key) || []).map(part => row(part, level) + walk(part.id, level + 1)).join("");
    return walk("", 0) || '<p class="rs-quiet">没有 part。</p>';
  }
  function compare(before, after, {beforeTitle = "改前", afterTitle = "改后", planned = new Map()} = {}) {
    return `<div class="rs-compare" data-showing="after"><div class="rs-seg" role="radiogroup" aria-label="对照"><button type="button" role="radio" aria-checked="true" data-rs-side="after">${esc(afterTitle)}</button><button type="button" role="radio" aria-checked="false" data-rs-side="before">${esc(beforeTitle)}</button></div>`
      + `<div class="rs-grid"><section class="rs-col before"><h3>${esc(beforeTitle)}</h3>${outline(before, after, "before")}</section><section class="rs-col after"><h3>${esc(afterTitle)}</h3>${outline(after, before, "after", planned)}</section></div></div>`;
  }
  // Phones show one side at a time.
  document.addEventListener("click", event => {
    const button = event.target.closest("[data-rs-side]"); if (!button) return;
    const box = button.closest(".rs-compare"); box.dataset.showing = button.dataset.rsSide;
    for (const item of box.querySelectorAll("[data-rs-side]")) item.setAttribute("aria-checked", String(item === button));
  });
  // What a structural difference amounts to, e.g. 新建 2 · 删除 1 · leader 变动 3.
  function diffSummary(diff) {
    const p = diff.parts, l = diff.leaders, leaders = l.placed.length + l.moved.length + l.unplaced.length;
    return [["新建", p.added.length], ["删除", p.removed.length], ["改名", p.renamed.length], ["改挂", p.moved.length], ["leader 变动", leaders], ["负责人变化", l.lead_on.length + l.lead_off.length]]
      .filter(([, n]) => n).map(([label, n]) => `${label} ${n}`).join(" · ");
  }

  // ---- the plan dialog ---------------------------------------------------------------------------------------------
  function create(options) {
    const overlay = document.createElement("div"); overlay.className = "session-config-backdrop rs-backdrop"; overlay.hidden = true; document.body.append(overlay);
    let project = null, changes = [], note = "", plan = null, drafts = new Map(), busy = false, error = "", status = "", returnTo = null, token = 0;
    const leaders = () => options.leaders().filter(item => item.status !== "closed");

    async function refresh(message = "") {
      const mine = ++token; busy = true; render();
      try {
        const next = await options.api("/api/op/domains.restructure.preview", {project, changes});
        if (mine !== token) return;
        plan = next; error = ""; status = message;
        const kept = new Map();
        for (const draft of plan.handoffs || []) {
          const key = `${draft.index}:${draft.from.session_id}:${draft.to.session_id || draft.to.ref}`, old = drafts.get(key);
          kept.set(key, old?.edited ? old : {text:draft.text, wake:false, edited:false});
        }
        drafts = kept;
      } catch (caught) { if (mine !== token) return; plan = null; error = caught.message || String(caught); }
      busy = false; render();
    }
    function draftKey(draft) { return `${draft.index}:${draft.from.session_id}:${draft.to.session_id || draft.to.ref}`; }
    function targetSelect(change, index) {
      if (change.type !== "leader.merge" && change.type !== "leader.retire") return "";
      const key = change.type === "leader.merge" ? "into" : "successor", current = change[key];
      const people = leaders().filter(item => item.session_id !== change.leader);
      const label = change.type === "leader.merge" ? "并入" : "接手人";
      return `<label class="rs-target">${label}<select data-rs-target="${index}" ${busy ? "disabled" : ""}>${change.type === "leader.retire" ? `<option value="" ${current === null ? "selected" : ""}>无接手人</option>` : ""}${people.map(item => `<option value="${esc(item.session_id)}" ${item.session_id === current ? "selected" : ""}>${esc(item.name)}</option>`).join("")}</select></label>`;
    }
    function changeList() {
      const items = plan ? plan.changes : changes.map((change, index) => ({index, label:"", error:null}));
      return `<ol class="rs-changes">${items.map(item => `<li class="${item.error ? "bad" : ""}"><div class="rs-change-line"><span class="rs-change-text">${esc(item.label || TYPE[changes[item.index].type] || `不认识的变更：${changes[item.index].type}`)}</span>${targetSelect(changes[item.index], item.index)}${changes.length > 1 ? `<button type="button" class="btn sm ghost" data-rs-act="remove" data-index="${item.index}" ${busy ? "disabled" : ""}>移除</button>` : ""}</div>${item.error ? `<p class="rs-change-error" role="alert">${esc(item.error)}</p>` : ""}</li>`).join("")}</ol>`;
    }
    function summaryLine() {
      const s = plan.summary;
      const parts = [["新建 part", s.parts_created], ["删除 part", s.parts_removed], ["改名", s.parts_renamed], ["改挂", s.parts_moved], ["leader 变动", s.leaders_moved], ["合并", s.merged], ["退役", s.retired], ["作废任务", s.goals_cancelled], ["改派任务", s.goals_transferred], ["待启动", s.to_start]];
      return parts.filter(([, n]) => n).map(([label, n]) => `<b>${label} ${n}</b>`).join(" · ") || "没有结构变化";
    }
    function impact() {
      const blocks = [];
      if (plan.leaders.length) blocks.push(`<section class="rs-block"><h3>受影响的 leader</h3><ul>${plan.leaders.map(item => `<li><b>${esc(item.name)}</b>：${esc(item.effects.join("；"))}</li>`).join("")}</ul></section>`);
      if (plan.goals.length) blocks.push(`<section class="rs-block"><h3>任务</h3><ul>${plan.goals.map(item => `<li><span class="rs-tag ${item.action}">${ACTION[item.action]}</span><b>${esc(item.title)}</b>（${esc(item.leader.name)}负责）${item.to ? ` → ${esc(item.to.name)}` : ""}${item.implicit ? '<span class="rs-tag soft">随合并或退役</span>' : ""}${item.wakes ? `<span class="rs-tag warn">会唤醒 ${esc(item.to.name)} 开工</span>` : ""}</li>`).join("")}</ul></section>`);
      if (plan.archived.length) blocks.push(`<section class="rs-block"><h3>将归档（原会话保留，不删除）</h3><p>${plan.archived.map(item => `${esc(item.name)}${item.role === "member" ? "（组员）" : ""}`).join("、")}</p></section>`);
      if (plan.to_start.length) blocks.push(`<section class="rs-block"><h3>预计要启动的新 leader</h3><ul>${plan.to_start.map(item => `<li><b>${esc(item.name)}</b> · ${esc(item.runtime === "codex" ? "Codex" : "Claude Code")}${item.model ? ` · ${esc(item.model)}` : ""}${item.effort ? ` · ${esc(item.effort)}` : ""} · 负责「${esc(item.part.name)}」 · 从 ${esc(item.split_from.name)} 拆出</li>`).join("")}</ul><p class="rs-note">执行时只建好它负责的 part，不会启动；确认费用后再新建这位 leader。</p></section>`);
      return blocks.join("");
    }
    function handoffEditor() {
      if (!plan.handoffs.length) return "";
      return `<section class="rs-block rs-handoffs"><h3>交接稿</h3>${plan.handoffs.map(draft => {
        const key = draftKey(draft), value = drafts.get(key), id = `rs-handoff-${draft.index}`;
        const head = draft.delivers ? `${esc(draft.from.name)} → ${esc(draft.to.name)}` : `${esc(draft.from.name)} → 新 leader ${esc(draft.to.name)}（建好后作为开工说明）`;
        return `<div class="rs-handoff"><label class="session-config-label" for="${id}">${head}<span class="rs-chars" data-rs-chars="${esc(key)}">${Array.from(value.text).length} 个字符${value.edited ? " · 已编辑" : ""}</span></label><textarea id="${id}" data-rs-draft="${esc(key)}" rows="7" maxlength="${MAX_HANDOFF}" ${busy ? "disabled" : ""}>${esc(value.text)}</textarea>${draft.truncated ? '<p class="session-config-hint">草稿按长度截取了最近的聊天，请检查任务和关键进度是否完整。</p>' : ""}${draft.delivers ? `<label class="rs-wake"><input type="checkbox" data-rs-wake="${esc(key)}" ${value.wake ? "checked" : ""} ${busy ? "disabled" : ""}>投递后立即唤醒 ${esc(draft.to.name)} 读交接（会开一轮模型）；不勾则等它下次对话时带上</label>` : ""}</div>`;
      }).join("")}</section>`;
    }
    function costLine() {
      const wakes = [...plan.handoffs.filter(draft => draft.delivers && drafts.get(draftKey(draft)).wake).map(draft => `${draft.to.name}（交接稿）`),
        ...plan.goals.filter(item => item.wakes).map(item => `${item.to.name}（任务「${item.title}」）`)];
      return wakes.length ? `执行后会唤醒：${wakes.join("、")}，可能产生用量；其余改动不启动模型。` : "执行不会启动任何模型。";
    }
    function render() {
      if (overlay.hidden) return;
      const focus = document.activeElement && overlay.contains(document.activeElement) ? (document.activeElement.dataset.rsDraft || document.activeElement.id) : null;
      const ready = plan && plan.ok && !plan.nothing && !busy && note.trim();
      const body = !plan ? (error ? "" : '<p class="rs-loading" role="status">正在计算改动…</p>')
        : `${plan.ok ? `<p class="rs-sum">${summaryLine()}</p>` + compare(plan.before, plan.after, {beforeTitle:"改前 · 现在的架构图", planned:new Map(plan.to_start.reduce((map, item) => map.set(item.part.id, [...(map.get(item.part.id) || []), item.name]), new Map()))}) + impact() + handoffEditor() + `<p class="runtime-switch-cost">${esc(costLine())}</p>` : `<p class="rs-errors" role="alert">方案还有 ${plan.errors.length} 处问题，修改或移除标红的条目后再执行。</p>`}`;
      overlay.innerHTML = `<section class="session-config-dialog rs-dialog" role="dialog" aria-modal="true" aria-labelledby="rs-title"><div class="session-config-head"><h2 id="rs-title">架构重整预览</h2><button class="icon-btn" data-rs-act="close" aria-label="关闭架构重整预览" ${busy ? "disabled" : ""}>×</button></div>`
        + `<p class="session-config-description">预览只读，不改任何东西；确认后在一个事务里执行，并存成一个架构图版本，可以在版本历史里回滚。被合并或退役的 leader 归档保留，不删除。</p>`
        + `<label class="rs-note-field">说明<span>存进版本历史</span><input id="rs-note" value="${esc(note)}" maxlength="${NOTE_MAX}" placeholder="例如：合并前后端" ${busy ? "disabled" : ""}></label>`
        + `<h3 class="rs-heading">变更 ${changes.length} 条</h3>${changeList()}${body}`
        + `<p class="error-line" role="alert">${esc(error)}</p>${status ? `<p class="rs-status-line" role="status">${esc(status)}</p>` : ""}`
        + `<div class="session-config-actions"><button class="btn ghost" data-rs-act="close" ${busy ? "disabled" : ""}>取消</button><span class="gap"></span><button class="btn" data-rs-act="refresh" ${busy ? "disabled" : ""}>重新预览</button><button class="btn primary" data-rs-act="confirm" ${ready ? "" : "disabled"} title="${!note.trim() ? "先填写说明" : plan?.nothing ? "方案没有任何改动" : ""}">${busy ? "正在处理…" : "确认执行"}</button></div></section>`;
      if (focus) (overlay.querySelector(`[data-rs-draft="${CSS.escape(focus)}"]`) || overlay.querySelector(`#${CSS.escape(focus)}`))?.focus({preventScroll:true});
    }
    function open(request) {
      project = request.project; changes = request.changes.map(change => ({...change})); note = request.note || ""; plan = null; drafts = new Map(); error = ""; status = ""; busy = false;
      returnTo = document.activeElement; overlay.hidden = false; render(); overlay.querySelector("#rs-note").focus({preventScroll:true}); void refresh();
    }
    function close() { if (busy) return; token++; overlay.hidden = true; overlay.innerHTML = ""; if (returnTo?.isConnected) returnTo.focus({preventScroll:true}); }
    async function confirm() {
      busy = true; error = ""; render();
      try {
        const handoffs = plan.handoffs.map(draft => { const value = drafts.get(draftKey(draft)); return {index:draft.index, text:value.text, wake:draft.delivers && value.wake}; });
        const empty = handoffs.find(item => !item.text.trim()); if (empty) throw Error("交接稿不能为空。");
        const result = await options.api("/api/op/domains.restructure.apply", {project, changes, note:note.trim(), board_version:plan.board_version, revision:plan.revision, handoffs});
        busy = false; close();
        options.toast(`已执行重整，存为版本 ${result.version}${result.to_start.length ? `；${result.to_start.length} 位新 leader 待确认费用后启动` : ""}${result.starts_turn ? `；已唤醒 ${[...new Set(result.wakes.map(item => item.name))].join("、")}` : ""}。`);
        await options.onApplied(result);
      } catch (caught) {
        busy = false;
        if (caught.status === 409) { await refresh("架构图、leader 或任务刚变过，已重新预览，请再核对一次。"); return; }
        error = caught.message || String(caught); render();
      }
    }
    overlay.addEventListener("click", event => {
      if (event.target === overlay) { close(); return; }
      const button = event.target.closest("[data-rs-act]"); if (!button || button.disabled || busy) return;
      const act = button.dataset.rsAct;
      if (act === "close") close();
      else if (act === "refresh") void refresh();
      else if (act === "remove") { changes.splice(Number(button.dataset.index), 1); void refresh(); }
      else if (act === "confirm") void confirm();
    });
    overlay.addEventListener("input", event => {
      if (event.target.id === "rs-note") { note = event.target.value; const button = overlay.querySelector('[data-rs-act="confirm"]'); button.disabled = !(plan && plan.ok && !plan.nothing && note.trim()); return; }
      const key = event.target.dataset.rsDraft; if (!key) return;
      const value = drafts.get(key); value.text = event.target.value; value.edited = true;
      overlay.querySelector(`[data-rs-chars="${CSS.escape(key)}"]`).textContent = `${Array.from(value.text).length} 个字符 · 已编辑`;
    });
    overlay.addEventListener("change", event => {
      const wake = event.target.dataset.rsWake;
      if (wake) { drafts.get(wake).wake = event.target.checked; const cost = overlay.querySelector(".runtime-switch-cost"); if (cost) cost.textContent = costLine(); return; }
      const index = event.target.dataset.rsTarget; if (index === undefined) return;
      const change = changes[Number(index)];
      change[change.type === "leader.merge" ? "into" : "successor"] = event.target.value || null;
      void refresh();
    });
    document.addEventListener("keydown", event => {
      if (overlay.hidden) return;
      if (event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); close(); }
      else if (event.key === "Tab") {
        const all = [...overlay.querySelectorAll('button:not([disabled]),select:not([disabled]),textarea:not([disabled]),input:not([disabled])')], first = all[0], last = all.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    }, true);
    return {open, close, isOpen:() => !overlay.hidden};
  }
  return {create, outline, compare, diffSummary};
})();
window.CrewRestructure = CrewRestructure;
