"use strict";
(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[c]);
  const isRead = op => ["persona.get", "persona.history", "persona.list"].includes(op);
  const webRequest = api => (op, body) => isRead(op)
    ? api("/api/op/" + op + "?" + new URLSearchParams(body)) : api("/api/op/" + op, body);
  const reasons = {
    persona_disabled:"AI 性格已关闭，打开总开关后就能成长。",
    persona_nothing_new:"还没有新对话可整理，多聊几句会自动长。",
    persona_busy:"正在整理最近的对话，请稍等。",
    persona_frozen:"性格已经定型，取消定型后就能继续成长。"
  };
  function errorCode(error) {
    const payload = error.body ?? error.details;
    return payload?.code ?? payload?.error_code;
  }
  function growthReason(message) {
    if (message === "没有找到已接收的新私聊") return reasons.persona_nothing_new;
    if (message === "被用户修改覆盖，已放弃") return "整理时你修改了性格，已保留你的版本。";
    return reasons[message] || message;
  }
  const reason = error => reasons[errorCode(error)] ||
    growthReason(error.message.replace(/^Error invoking remote method '[^']+':\s*(?:Error:\s*)?/, ""));
  const authors = {grow:"AI 成长", edit:"你改过", seed:"选了起点", reset:"已重置", restore:"恢复"};
  const time = value => new Date(value).toLocaleString("zh-CN", {year:"numeric",month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"});
  const btn = (action, label, extra = "") => `<button type="button" class="ps-btn" data-ps="${action}" ${extra}>${label}</button>`;
  const markdown = content => window.CrewReportMarkdown.render(content);

  function mount(host, {session_id, request}) {
    let person, active = true, busy = false, poll, observer, note = "", refreshFailed = false;
    let editing = false, draft = "", editVersion, conflict = false, expanded = false;
    let custom = false, seedText = "", pendingSeed = null, resetting = false;
    let historyOpen = false, rows = [], nextBefore = null, shownVersion = null;
    const call = (action, body = {}) => request("persona." + action, {session_id, ...body});
    const panel = document.createElement("div");
    panel.className = "persona-panel";
    host.replaceChildren(panel);

    function seedMarkup() {
      const choices = [...person.presets, {key:"blank",name:"空白",summary:"从空白开始，边聊边长。"},
        {key:"custom",name:"自己写",summary:"写下你喜欢的相处方式。"}];
      return `<section><h3>起点</h3><div class="ps-seeds">${choices.map(({key,name,summary}) =>
        `<button type="button" class="ps-seed" data-ps="seed" data-key="${esc(key)}" aria-pressed="${key === (pendingSeed?.body.seed ?? (custom ? "custom" : person.seed))}"><strong>${esc(name)}</strong><span>${esc(summary)}</span></button>`).join("")}</div>
        ${custom ? `<label class="ps-field">起点设定<textarea data-field="seed" maxlength="1000" rows="3" placeholder="比如：先说结论，再解释原因。">${esc(seedText)}</textarea></label>${btn("custom-save","保存起点")}` : ""}
        ${pendingSeed ? `<div class="ps-confirm">换成「${esc(pendingSeed.name)}」会替换现在的性格，旧内容会留在成长记录里，可以找回。<div class="ps-actions">${btn("confirm-seed","确认")}${btn("cancel-seed","取消")}</div></div>` : ""}</section>`;
    }
    function historyMarkup() {
      return `<section class="ps-records">${btn("history",`<span aria-hidden="true">${historyOpen ? "▾" : "▸"}</span> 成长记录`,`aria-expanded="${historyOpen}"`)}
        ${historyOpen ? rows.map(row => `<div class="ps-record"><button class="ps-record-title" type="button" data-ps="version" data-version="${row.version}" aria-expanded="${shownVersion === row.version}">
          <span><strong>${authors[row.action]}</strong> · ${time(row.created_at)}</span><small>容量 ${Math.round(100 * row.tokens_estimate / person.limit)}%</small>
          <span class="ps-record-summary">${esc(row.summary || row.content.split("\n").find(line=>line.trim()) || "空白")}</span></button>
          ${shownVersion === row.version ? `<div class="ps-reading">${markdown(row.content) || "空白"}</div>${btn("restore","用回这一版",`data-version="${row.version}"`)}` : ""}</div>`).join("") +
          (!rows.length ? '<p>还没有成长记录。</p>' : "") + (nextBefore !== null ? btn("more","更早的记录") : "") : ""}</section>`;
    }
    function render() {
      if (!active) return;
      observer?.disconnect();
      if (!person) {
        panel.innerHTML = note ? `<p role="alert">${esc(note)}</p>${btn("reload","重试")}` : '<p role="status">正在读取性格…</p>';
        return;
      }
      const noMaterial = person.growth_error === "persona_nothing_new";
      const lock = person.growing ? "disabled" : "";
      panel.innerHTML = `${!person.enabled ? '<div class="ps-notice">AI 性格已关闭：现在统一用默认口吻。下面的内容会保留，打开后恢复使用。</div>' : ""}
        <section class="ps-growth"><div class="ps-row"><h3>最近成长</h3>${person.last_growth_at ? `<time>${time(person.last_growth_at)}</time>` : ""}</div>
          ${person.growing ? '<p role="status"><i class="ps-spin" aria-hidden="true"></i>正在整理最近的对话…</p>' : ""}
          ${person.last_growth_summary ? `<p>${esc(person.last_growth_summary)}</p>` : person.growing ? "" : "<p>还没有成长记录。</p>"}</section>
        ${person.growth_error && !noMaterial ? `<div class="ps-notice ps-row"><span>上次整理没成功：${esc(growthReason(person.growth_error))}</span>${btn("grow","重试",lock)}</div>` : noMaterial && !person.growing && note !== reasons.persona_nothing_new ? `<p class="ps-quiet">${reasons.persona_nothing_new}</p>` : ""}
        <div role="status" class="${note === reasons.persona_nothing_new ? "ps-quiet" : "ps-feedback"}" ${note ? "" : "hidden"}>${esc(note)} ${refreshFailed ? btn("reload","重新读取") : ""}</div>
        <fieldset class="ps-controls" ${busy ? "disabled" : ""}>
        <section><div class="ps-row"><h3>当前性格</h3>${editing ? "" : `<div class="ps-actions">${btn("edit","编辑",lock)}${btn("reset","重置…",lock)}</div>`}</div>
        ${editing ? `${conflict ? `<details open><summary>刚更新的性格</summary><div class="ps-reading">${markdown(person.content) || "空白"}</div></details>` : ""}
          <label class="ps-field">性格草稿<textarea data-field="content" rows="7">${esc(draft)}</textarea></label><div class="ps-actions">${btn("save","保存修改",lock)}${btn("cancel-edit","取消")}</div>`
          : person.content ? `<div class="ps-reading ps-current" data-expanded="${expanded}">${markdown(person.content)}</div>${btn("expand",expanded ? "收起" : "展开全部",`aria-expanded="${expanded}" ${expanded ? "" : "hidden"}`)}`
          : '<p class="ps-empty">再聊几句就会长出性格，也可以先选个起点。</p>'}
        <div class="ps-capacity" data-warning="${person.percent >= 80}"><progress max="100" value="${person.percent}" aria-label="容量已用"></progress>已用 ${person.percent}%（上限约 2 万 token，估算）</div>
        ${resetting ? `<div class="ps-confirm">重置前的内容会留在成长记录里，可以找回。<div class="ps-actions">${person.seed && person.seed !== "blank" ? btn("to-seed","回到起点",lock) : ""}${btn("clear","清空",lock)}${btn("cancel-reset","取消")}</div></div>` : ""}</section>
        ${seedMarkup()}
        <div class="ps-row ps-freeze"><label><input type="checkbox" data-field="freeze" ${person.frozen ? "checked" : ""}>定型（不再自己改写）</label>${btn("grow","现在整理一次",lock)}</div>
        ${historyMarkup()}</fieldset>`;
      const reader = panel.querySelector(".ps-current");
      if (reader && !expanded) {
        const measure = () => {
          const clipped = reader.scrollHeight > reader.clientHeight + 1;
          reader.classList.toggle("ps-clipped",clipped);
          panel.querySelector('[data-ps="expand"]').hidden = !clipped;
        };
        measure();
        observer = new ResizeObserver(measure);
        observer.observe(reader);
      }
    }
    async function history(before) {
      const page = await call("history",before === undefined ? {limit:10} : {before,limit:10});
      rows = before === undefined ? page.history : [...rows, ...page.history];
      nextBefore = page.next_before;
    }
    function schedule() {
      clearTimeout(poll);
      if (active && person?.growing && !refreshFailed) poll = setTimeout(checkGrowth, 1800);
    }
    async function checkGrowth() {
      const previous = person;
      try {
        const latest = await call("get");
        if (!active || busy || previous !== person) return;
        if (!latest.growing || latest.version !== person.version) {
          await run(async () => {
            person = latest;
            if (latest.enabled && !latest.frozen && !latest.growing && !latest.growth_error && latest.version === previous.version) {
              note = reasons.persona_nothing_new;
            }
            if (historyOpen && latest.version !== previous.version) await history();
          });
        } else schedule();
      } catch (error) {
        if (active && !busy && previous === person) { refreshFailed = true; note = reason(error); render(); }
      }
    }
    async function refresh() { person = await call("get"); refreshFailed = false; }
    async function run(work) {
      if (busy || !active) return;
      clearTimeout(poll);
      busy = true; note = "";
      const controls = panel.querySelector(".ps-controls");
      if (controls) controls.disabled = true;
      try { await work(); }
      catch (error) { note = reason(error); }
      finally { busy = false; render(); schedule(); }
    }
    async function write(action, body) {
      try { person = await call(action,body); }
      catch (error) {
        // Electron IPC preserves the server message but strips custom error properties.
        if (errorCode(error) !== "conflict" && !error.message.endsWith("性格已更新，请刷新后再试")) throw error;
        await refresh();
        if (editing) {
          conflict = true; editVersion = person.version;
          note = "刚才性格有更新，你的草稿还在，核对后再保存。";
        } else {
          pendingSeed = null; resetting = false;
          note = "刚才性格有更新，请核对后再操作。";
        }
        return;
      }
      editing = false; conflict = false; resetting = false; custom = false; pendingSeed = null;
      if (historyOpen) await history();
    }
    async function chooseSeed(key, name) {
      const body = {seed:key, expected_version:person.version};
      if (key === "custom") {
        if (!seedText.trim()) { note = "先写几句想要的起点。"; return; }
        body.seed_text = seedText;
      }
      if (person.content) pendingSeed = {name, body};
      else await write("seed", body);
    }
    async function act(button) {
      const action = button.dataset.ps;
      switch (action) {
        case "reload": await refresh(); break;
        case "edit": editing = true; draft = person.content; editVersion = person.version; resetting = false; break;
        case "cancel-edit": editing = false; conflict = false; break;
        case "save": await write("save",{content:draft,expected_version:editVersion}); break;
        case "expand": expanded = !expanded; break;
        case "reset": resetting = true; break;
        case "cancel-reset": resetting = false; break;
        case "to-seed": case "clear": await write("reset",{mode:action === "clear" ? "clear" : "seed",expected_version:person.version}); break;
        case "seed": {
          pendingSeed = null; custom = button.dataset.key === "custom";
          if (custom) seedText = person.seed_text ?? "";
          else await chooseSeed(button.dataset.key, button.querySelector("strong").textContent);
          break;
        }
        case "custom-save": await chooseSeed("custom","自己写"); break;
        case "confirm-seed": await write("seed",pendingSeed.body); break;
        case "cancel-seed": pendingSeed = null; break;
        case "grow": {
          const version = person.version;
          await call("grow"); await refresh();
          if (person.growing) note = "已开始整理最近的对话。";
          else if (person.enabled && !person.frozen && !person.growth_error && person.version === version) note = reasons.persona_nothing_new;
          break;
        }
        case "history": if (!historyOpen) await history(); historyOpen = !historyOpen; break;
        case "more": await history(nextBefore); break;
        case "version": shownVersion = shownVersion === Number(button.dataset.version) ? null : Number(button.dataset.version); break;
        case "restore": await write("restore",{version:Number(button.dataset.version),expected_version:person.version}); break;
      }
    }
    panel.addEventListener("click", async event => {
      const button = event.target.closest("[data-ps]");
      if (!button || button.disabled || busy) return;
      await run(() => act(button));
      if (!active || panel.contains(document.activeElement)) return;
      const field = {edit:"content",seed:custom ? "seed" : null}[button.dataset.ps];
      const target = field ? panel.querySelector(`[data-field="${field}"]`) : panel.querySelector(`[data-ps="${button.dataset.ps}"]${button.dataset.key ? `[data-key="${button.dataset.key}"]` : ""}`);
      target?.focus({preventScroll:true});
    });
    panel.addEventListener("input", ({target}) => {
      if (target.dataset.field === "content") draft = target.value;
      if (target.dataset.field === "seed") { seedText = target.value; if (pendingSeed) { pendingSeed = null; panel.querySelector(".ps-seeds").parentElement.querySelector(".ps-confirm").remove(); } }
    });
    panel.addEventListener("change", ({target}) => {
      if (target.dataset.field === "freeze") void run(async () => { person = await call("freeze",{frozen:target.checked}); });
    });
    render(); void run(refresh);
    return () => { active = false; clearTimeout(poll); observer?.disconnect(); panel.remove(); };
  }

  function open(options) {
    const dialog = document.createElement("dialog");
    dialog.className = "persona-dialog"; dialog.setAttribute("aria-label", options.name + "的性格");
    dialog.innerHTML = `<header class="ps-header"><div><h2>性格</h2><span>${esc(options.name)}</span></div><button type="button" class="ps-close cs-close" aria-label="关闭性格"><svg class="cs-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6"/></svg></button></header><div class="ps-scroll"></div>`;
    document.body.append(dialog); dialog.showModal();
    const cleanup = mount(dialog.querySelector(".ps-scroll"),options);
    dialog.querySelector(".ps-close").onclick = () => dialog.close();
    dialog.addEventListener("close", () => { cleanup(); dialog.remove(); options.onClose?.(); },{once:true});
    return dialog;
  }

  function mountSettings(host,{request,save}) {
    let active = true, list;
    const section = document.createElement("div");
    section.className = "ps-settings"; host.replaceChildren(section);
    async function load() {
      try {
        list = await request("persona.list",{});
        if (!active) return;
        render();
      } catch (error) {
        if (active) { section.innerHTML = `<p role="alert">${esc(reason(error))}</p>${btn("retry","重试")}`; }
      }
    }
    function render() {
      const groups = new Map();
      for (const item of list.items) {
        if (!groups.has(item.project)) groups.set(item.project,[]);
        groups.get(item.project).push(item);
      }
      section.innerHTML = `<section class="cs-card"><label class="ps-master"><input type="checkbox" ${list.enabled ? "checked" : ""}>允许 AI 慢慢有自己的性格</label>
        <p>${list.enabled ? "开启后，每位 Leader 和工作区助手会慢慢形成自己的说话方式。性格只存在这台电脑上，聊天时会带给正在用的 AI。" : "已暂停，内容保留。重新打开后接着用。"}</p><p role="status"></p></section>
        ${Array.from(groups,([project,items])=>`<section class="cs-card"><h3>工作区 ${esc(project)}</h3>${items.map(item=>`<button type="button" class="ps-person" data-session="${esc(item.session_id)}"><span><strong>${esc(item.name)}</strong><small>${item.subject === "leader" ? "Leader" : "工作区助手"}</small></span><span>${item.frozen ? "<small>已定型</small>" : ""}${item.growth_error ? '<small class="ps-issue">成长失败</small>' : ""}<small>${item.percent ? `容量 ${item.percent}%` : "还没长"}</small></span></button>`).join("")}</section>`).join("") || '<p class="cs-hint">还没有 Leader 或工作区助手。</p>'}`;
    }
    section.addEventListener("change",async ({target})=>{
      const enabled = target.checked;
      target.disabled = true;
      try {
        const saved = await save({persona_enabled:enabled});
        if (saved.persona_enabled !== enabled) throw Error("性格开关未保存，请重试。");
        list.enabled = enabled;
        if (active) render();
      } catch (error) {
        target.checked = list.enabled; target.disabled = false;
        section.querySelector('[role="status"]').textContent = reason(error);
      }
    });
    section.addEventListener("click",event=>{
      const button = event.target.closest("button");
      if (button?.dataset.ps === "retry") void load();
      if (button?.dataset.session) {
        const item = list.items.find(entry=>entry.session_id === button.dataset.session);
        open({session_id:item.session_id,name:item.name,request,onClose:()=>{if (active) void load();}});
      }
    });
    section.textContent = "正在读取性格…"; void load();
    return () => {active = false;section.remove();};
  }
  window.CrewPersona = {open,mount,mountSettings,webRequest,isRead};
})();
