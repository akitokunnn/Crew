"use strict";
(() => {
  const data = {
    projectName: "舱体交互",
    leader: {name:"MiPS 微姿态实装", shortName:"MiPS", description:"把 MiPS 流式语音微姿态模型实装到舱体 NPU，替代 Type5。负责拆解任务、协调成员、核对效果与最终交付。", runtime:"Codex", model:"原生默认", effort:"默认"},
    tasks: [
      {id:"mips", title:"MiPS 语音微姿态在舱体 NPU 上实装（替代 Type5）", shortTitle:"MiPS 微姿态实装", status:"queued", summary:"将流式语音驱动的微姿态接入舱体，先核对模型输入输出与 NPU 约束，再完成替换与回归。", acceptance:["对齐模型输入输出与帧率","完成 NPU 推理与姿态映射","保留原链路回退，提供延迟和效果对比"]},
      {id:"latency", title:"语音驱动链路的延迟基线与性能优化", shortTitle:"语音延迟优化", status:"running", summary:"定位端到端延迟，统一测量方式，减少首帧等待。", acceptance:["记录首帧与稳态延迟","用同一音频集比较优化前后结果"]},
      {id:"replay", title:"实机回放与效果验收", shortTitle:"实机回放验收", status:"waiting", summary:"对照舱体动作范围与语音表现，确认实机验收方案。", acceptance:["覆盖不同语速和静音片段","确认验收样本与通过标准"]}
    ],
    longTaskTitle:"MiPS 流式语音微姿态模型在舱体 NPU 上完整实装，替代 Type5 并完成长音频、静音片段、多语速、低功耗与异常恢复的实机回归验收"
  };
  const paths = {
    menu:'M4 6h16M4 12h16M4 18h16', chevron:'m8 10 4 4 4-4', more:'M5 12h.01M12 12h.01M19 12h.01', info:'M12 11v6M12 7h.01',
    send:'m5 12-2-9 18 9-18 9 2-9Zm0 0h16', close:'m6 6 12 12M6 18 18 6', plus:'M12 5v14M5 12h14', check:'m5 12 4 4L19 6',
    grid:'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z', chat:'M21 11a8 8 0 0 1-8 8H6l-4 3V11a9 9 0 0 1 19 0Z',
    file:'M13 3H5v18h14V9Zm0 0v6h6M8 13h8M8 17h6', 'arrow-left':'m10 5-7 7 7 7M3 12h18', 'arrow-right':'m14 5 7 7-7 7M3 12h18', search:'M10.5 3a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15Zm5.5 13 5 5'
  };
  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${name === "info" ? '<circle cx="12" cy="12" r="9"/>' : ""}<path d="${paths[name] || paths.file}"/></svg>`;
  const labels = {queued:"待规划",running:"推进中",waiting:"等你拍板",delivered:"已交付",idle:"总会话"};
  const statusLabel = value => labels[value] || value;
  const key = "crew.header-lab.v1";
  let saved;
  try { saved = JSON.parse(localStorage.getItem(key) || "{}"); } catch { saved = {}; }
  if (!saved || typeof saved !== "object" || Array.isArray(saved)) saved = {};
  const params = new URLSearchParams(location.search);
  let variant = params.get("view") || "switcher";
  if (!["switcher","sidebar","document","tabs","legacy"].includes(variant)) variant = "switcher";
  const defaults = () => ({scope:"mips", statuses:Object.fromEntries(data.tasks.map(t=>[t.id,t.status])), modes:{}, drafts:{}, messages:{}, scroll:{}, longTitle:false, note:""});
  const profiles = saved.profiles && typeof saved.profiles === "object" ? saved.profiles : {};
  let profile = Object.assign(defaults(), profiles[variant]);
  for (const name of ["statuses","modes","drafts","messages","scroll"]) if (!profile[name] || typeof profile[name] !== "object") profile[name] = defaults()[name];
  if (!["general",...data.tasks.map(t=>t.id)].includes(profile.scope)) profile.scope = "mips";
  let mobileNav = false, overlay = null, opener = null, toastTimer, resizeObserver;
  const root = document.getElementById("demo-root");
  const overlays = document.getElementById("overlay-root");
  const toolbar = document.getElementById("lab-toolbar");
  const footer = document.querySelector(".prototype-footer");
  const title = () => profile.scope === "general" ? "总会话" : profile.longTitle && profile.scope === "mips" ? data.longTaskTitle : data.tasks.find(t=>t.id===profile.scope).title;
  const task = () => data.tasks.find(t=>t.id===profile.scope);
  const status = () => profile.scope === "general" ? "idle" : profile.statuses[profile.scope];
  const save = () => {
    profiles[variant] = profile;
    try {
      const latest = JSON.parse(localStorage.getItem(key) || "{}");
      Object.assign(profiles, latest?.profiles, {[variant]:profile});
      localStorage.setItem(key, JSON.stringify({profiles,theme:document.documentElement.dataset.theme || "light"}));
    }
    catch { toast("浏览器未允许保存，本次试用仍可继续。"); }
  };
  const toast = message => {
    const element = document.getElementById("toast");
    element.textContent = message; element.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(()=>{element.hidden=true;},3200);
  };
  document.documentElement.dataset.theme = saved.theme === "dark" ? "dark" : "light";
  toolbar.hidden = params.get("focus") === "1";
  function conversation() {
    const general = profile.scope === "general", current = status();
    const custom = profile.messages[profile.scope] || [];
    let content = "";
    if (current === "waiting") content += `<section class="decision-request" aria-label="需要你拍板"><div><strong>需要你拍板：先保效果，还是先降延迟？</strong><p>这项决定只影响当前任务，其他任务继续推进。</p></div><button class="button" data-action="decision">查看并拍板${icon("arrow-right")}</button></section>`;
    if (general || current === "queued") content += `<div class="empty-conversation"><div class="empty-icon">${icon(general ? "chat" : "file")}</div><h2>${general ? "和 MiPS 讨论整体方向" : variant === "document" ? "从这项任务开始聊" : "准备好后，再开始推进"}</h2><p>${general ? "这里是总会话，可以讨论整体安排、补充背景，或调整多个任务的优先级。每项任务有自己的独立对话。" : "先聊需求、确认边界，或者直接开始。任务对话与总会话分别保留，切换不会启动任务。"}</p><div class="suggestion-list">${(general ? ["梳理下一步优先级","查看并行任务"] : ["先梳理实现路径","检查验收标准"]).map((text,i)=>`<button data-action="suggest" data-value="${esc(text)}">${esc(text)}</button>`).join("")}${!general && variant !== "document" && variant !== "tabs" ? '<button data-action="start">开始推进 →</button>' : ""}</div></div>`;
    else content += `<div class="conversation-date">今天 · 示例对话</div><article class="message assistant"><span class="message-label">M · MiPS / leader · 演示内容</span><p>${current === "delivered" ? "本轮实现已交付。模型映射、流式推理与实机回放已整理为下面这份示例结果，接下来请你检查效果与验收标准。" : current === "waiting" ? "模型映射已完成。实机测试中，低延迟与动作细节之间存在取舍，需要你确认优先级，随后我会继续安排回归。" : "已将任务拆成模型适配、NPU 推理和实机回放三步。当前先核对输入输出，后续进展会继续留在这个任务的对话里。"}</p>${current === "delivered" ? '<button class="button" data-action="delivery">查看示例交付</button>' : ""}</article>`;
    content += custom.map(message=>`<article class="message ${message.role === "user" ? "user" : "assistant"}">${message.role === "assistant" ? '<span class="message-label">本地原型反馈 · 未调用 AI</span>' : ""}<p>${esc(message.text)}</p></article>`).join("");
    const mode = profile.modes[profile.scope] === "auto" ? "自主推进" : "由我拍板";
    return `<section class="conversation" aria-label="${esc(title())}的对话"><div class="conversation-scroll"><div class="conversation-content"><div data-conversation-content>${content}<div class="mock-note"><i></i>界面示例，所有进展均为本地模拟</div></div></div></div><div class="composer-wrap"><div class="composer-inner">${!general && current === "queued" && variant !== "document" ? '<p class="composer-hint">点击“开始推进”或发送消息后，才开始这项任务。 <button class="inline-link" data-action="scope" data-scope="general">先去总会话聊</button></p>' : ""}<form class="composer" id="message-form"><textarea id="message-input" aria-label="${general ? "总会话消息" : "任务消息"}" placeholder="${general ? "和 leader 聊聊整体安排…" : "发消息给这个任务…"}" rows="2">${esc(profile.drafts[profile.scope] || "")}</textarea><div class="composer-bar"><button class="composer-context inline-link" type="button" data-action="mode" aria-label="切换决策方式：${mode}">${icon("chat")}<span>${esc(general ? "总会话" : task().shortTitle)} · ${mode}</span>${icon("chevron")}</button><button class="composer-send" type="submit" data-action="send" aria-label="发送消息">${icon("send")}</button></div></form><div class="composer-helper"><span>${esc(data.leader.runtime)} · 原生默认模型</span><span><kbd>Enter</kbd> 发送 · <kbd>Shift Enter</kbd> 换行</span></div></div></div></section>`;
  }
  function legacy(ctx) {
    return `<div class="legacy-layout"><aside class="legacy-sidebar"><strong>舱体交互</strong><p>原版层级对照<br>按截图结构重建</p><button class="button" data-action="task-list">任务清单</button></aside><main class="legacy-main"><div data-main-header><div class="legacy-identity"><span class="avatar">M</span><div><strong>${esc(data.leader.name)}</strong> <span class="scope-status">本目标尚未启动</span><p>${esc(data.leader.description)}</p></div></div><div class="legacy-tabs"><button class="button" data-action="scope" data-scope="general">总会话</button><button class="button" data-action="task-list">任务清单 ${data.tasks.length}</button><button class="icon-button" data-action="details" aria-label="详情">${icon("info")}</button></div>${profile.scope === "general" ? "" : `<div class="legacy-task-title">${ctx.statusHtml}<h1>${esc(title())}</h1></div><div class="legacy-start-note">开始推进会交给 leader 执行；在此发送消息也会开始本任务。先讨论可回到总会话。</div>`}</div><div class="conversation-slot">${ctx.conversation}</div></main></div>`;
  }
  function render(options = {}) {
    const previous = root.querySelector(".conversation-scroll");
    if (options.rememberScroll !== false && previous) profile.scroll[profile.scope] = previous.scrollTop;
    const state = {scope:profile.scope,status:status(),mode:profile.modes[profile.scope] || "human",longTitle:profile.longTitle,mobileNav,details:overlay === "details"};
    const currentData = {...data,tasks:data.tasks.map(item=>({...item,status:profile.statuses[item.id]}))};
    const context = {state,data:currentData,esc,icon,conversation:conversation(),task:task(),currentTitle:title(),statusLabel,statusHtml:profile.scope === "general" ? "" : `<span class="scope-status" data-status="${esc(status())}">${esc(statusLabel(status()))}</span>`};
    root.innerHTML = variant === "legacy" ? legacy(context) : window.HeaderLabVariants[variant].render(context);
    const scroll = root.querySelector(".conversation-scroll");
    if (scroll) scroll.scrollTop = options.bottom ? scroll.scrollHeight : profile.scroll[profile.scope] || 0;
    const rail = root.querySelector(".tabs-track"), selected = rail?.querySelector('[aria-selected="true"]');
    if (selected) rail.scrollLeft = Math.max(0, selected.offsetLeft - rail.offsetLeft - 8);
    document.getElementById("lab-variant").value = variant;
    document.getElementById("lab-status").disabled = profile.scope === "general";
    document.getElementById("lab-status").value = profile.scope === "general" ? "queued" : status();
    document.getElementById("lab-long-title").checked = Boolean(profile.longTitle);
    document.getElementById("lab-theme").textContent = document.documentElement.dataset.theme === "dark" ? "浅色" : "深色";
    document.title = `Crew · ${variant === "legacy" ? "原版对照" : window.HeaderLabVariants[variant].name}`;
    const header = root.querySelector("[data-main-header]");
    resizeObserver?.disconnect();
    resizeObserver = new ResizeObserver(()=>{document.getElementById("lab-height").textContent = `内容顶部 ${Math.round(header.getBoundingClientRect().height)}px`;});
    resizeObserver.observe(header);
    setInert();
    if (mobileNav) {
      const navigation = nav();
      navigation?.setAttribute("tabindex","-1");
      navigation?.focus({preventScroll:true});
    }
    save();
  }
  const nav = () => root.querySelector('[data-variant-nav],.sidebar-pane,.tabs-project-sidebar');
  const visible = element => element && element.getClientRects().length && getComputedStyle(element).visibility !== "hidden";
  const focusables = parent => [...parent.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled),textarea:not(:disabled),select:not(:disabled),[tabindex="0"]')].filter(visible);
  function setInert() {
    root.inert = Boolean(overlay); toolbar.inert = Boolean(overlay || mobileNav); footer.inert = Boolean(overlay || mobileNav);
    for (const element of root.querySelectorAll("main")) element.inert = Boolean(mobileNav);
  }
  function rememberOpener(element) {
    opener = element ? {action:element.dataset.action,scope:element.dataset.scope,id:element.id,label:element.getAttribute("aria-label"),inHeader:Boolean(element.closest("[data-main-header]"))} : null;
  }
  function restoreFocus() {
    let element = opener?.id ? document.getElementById(opener.id) : null;
    if (!element && opener?.action) element = [...root.querySelectorAll(`[data-action="${opener.action}"]`)].find(item=>(!opener.scope || item.dataset.scope === opener.scope) && item.getAttribute("aria-label") === opener.label && Boolean(item.closest("[data-main-header]")) === opener.inHeader && visible(item));
    (element || root.querySelector("[data-main-header] button"))?.focus({preventScroll:true});
  }
  function closeOverlay(restore = true) {
    overlay = null; overlays.innerHTML = ""; setInert();
    if (restore) restoreFocus();
  }
  const panelHeader = (heading,description="") => `<header><div><h2 id="panel-title">${heading}</h2>${description ? `<p>${description}</p>` : ""}</div><button class="icon-button" data-action="close-overlay" aria-label="关闭">${icon("close")}</button></header>`;
  const centerPanel = (heading,body,description="") => `<section class="lab-panel center-panel" role="dialog" aria-modal="true" aria-labelledby="panel-title">${panelHeader(heading,description)}<div class="panel-body">${body}</div></section>`;
  function pickerRows(search="") {
    const items = [{id:"general",title:"总会话",shortTitle:"讨论整体安排",status:"idle"},...data.tasks];
    const filtered = items.filter(item=>(item.title+item.shortTitle).toLowerCase().includes(search.toLowerCase()));
    return filtered.length ? filtered.map(item=>`<button class="scope-row" data-action="scope" data-scope="${item.id}" aria-current="${profile.scope === item.id}" title="${esc(item.title)}">${icon(item.id === "general" ? "chat" : "file")}<span class="scope-copy"><strong>${esc(item.id === "mips" && profile.longTitle ? data.longTaskTitle : item.title)}</strong><small>${item.id === "general" ? "与各任务独立" : esc(statusLabel(profile.statuses[item.id]))}</small></span>${profile.scope === item.id ? icon("check") : ""}</button>`).join("") : '<div class="scope-empty">没有匹配的任务，试试“语音”或“回放”。</div>';
  }
  function openOverlay(type,element) {
    rememberOpener(element || document.activeElement);
    mobileNav = false;
    overlay = type;
    render();
    let html;
    if (type === "picker") html = `<section class="lab-panel scope-picker" role="dialog" aria-modal="true" aria-labelledby="panel-title">${panelHeader("切换会话","切换只改变当前位置，不会启动任务。商量整体方向请选总会话。")}<label class="search-field">${icon("search")}<input id="scope-search" placeholder="查找总会话或任务…" aria-label="查找会话" autocomplete="off"></label><div class="scope-list">${pickerRows()}</div></section>`;
    else if (type === "details") html = `<section class="lab-panel details-panel" data-panel="details" role="dialog" aria-modal="true" aria-labelledby="panel-title">${panelHeader("资料与设置","长说明按需展开，对话空间留给正在做的事。")}<div class="panel-body"><section class="detail-section"><h3>${task() ? "当前任务" : "当前会话"}</h3><h4 class="detail-title" data-detail-title>${esc(title())}</h4>${task() ? `<p>${esc(task().summary)}</p>` : '<p>这里讨论整体方向，不会把消息自动送入某一项任务。</p>'}</section>${task() ? `<section class="detail-section"><h3>验收标准</h3><ul>${task().acceptance.map(item=>`<li>${esc(item)}</li>`).join("")}</ul></section>` : ""}<section class="detail-section"><h3>Leader</h3><p><strong>${esc(data.leader.name)}</strong></p><p>${esc(data.leader.description)}</p></section><dl class="detail-fields"><dt>执行状态</dt><dd>${esc(statusLabel(status()))}</dd><dt>决策方式</dt><dd>${profile.modes[profile.scope] === "auto" ? "自主推进" : "由我拍板"} <button class="inline-link" data-action="mode">修改</button></dd><dt>AI 载体</dt><dd>${esc(data.leader.runtime)} · 原生默认模型</dd><dt>范围</dt><dd>仅本地示例，不连接 AI</dd></dl></div></section>`;
    else if (type === "mode") html = centerPanel("这项会话如何推进？",`<div class="choice-list"><button data-action="set-mode" data-value="human">${profile.modes[profile.scope] !== "auto" ? "✓ " : ""}由我拍板<small>遇到关键取舍时，在任务对话里等我决定。</small></button><button data-action="set-mode" data-value="auto">${profile.modes[profile.scope] === "auto" ? "✓ " : ""}自主推进<small>leader 自行取舍，结果回到当前对话。</small></button></div>`,"设置只影响当前会话，保存不会启动任务。");
    else if (type === "decision") html = centerPanel("确认这次取舍",`<p class="panel-intro">NPU 预算有限，先确定本轮验收优先级。</p><div class="choice-list"><button data-action="resolve-decision" data-value="优先动作效果">优先动作效果<small>保留完整微姿态细节，下一轮再优化延迟。</small></button><button data-action="resolve-decision" data-value="优先降低延迟">优先降低延迟<small>先缩短响应等待，允许适度简化细节。</small></button></div>`,"原型操作只会更新当前示例任务。");
    else if (type === "note") html = centerPanel("记下这版的感受",`<label for="lab-notes" class="notes-label">哪里顺手？哪里还挡着你？</label><textarea class="notes-input" id="lab-notes" placeholder="例如：切任务很快，但手机上标题太短…">${esc(profile.note)}</textarea><div class="panel-actions"><button class="button" data-action="export-notes">导出四版笔记</button><button class="button primary" data-action="close-overlay">保存并关闭</button></div>`,"笔记自动保存在本机浏览器，四版分别记录。");
    else if (type === "more") html = centerPanel("会话操作",`<div class="choice-list"><button data-action="scope" data-scope="general">回到总会话<small>讨论整体安排，与当前任务分别保存草稿。</small></button><button data-action="task-list">切换任务</button><button data-action="details">资料与设置</button><button data-action="mode">修改决策方式</button><button data-action="reset">重置这一版示例<small>清除本版的草稿、对话和状态，保留试用笔记。</small></button></div>`);
    else if (type === "delivery") html = centerPanel("本轮交付 · 示例",`<div class="detail-section"><h3>${esc(title())}</h3><p>示例交付包含：模型输入输出约定、NPU 推理适配、三组实机回放结果。</p><ul><li>验收建议：先比较动作连续性，再核对延迟</li><li>相关材料始终在对应任务里打开</li></ul><p>这里演示交付入口，不代表真实实现或性能数据。</p></div>`);
    else if (type === "assistant") html = centerPanel("全局助手",`<p class="panel-intro">助手入口固定在项目侧栏底部。正式产品里它打开独立页面，这里用浮层标明去向，方便继续比较顶部。</p><div class="choice-list"><button data-action="note">记录这个入口是否合适</button></div>`,"这是导航示意，不连接真实助手。");
    else if (type === "preferences") html = centerPanel("偏好设置 · 示例",`<div class="choice-list"><button data-action="theme">切换深浅色<small>四版共享同一外观设置。</small></button><button data-action="note">记录试用感受</button></div>`);
    else html = centerPanel("我的待办 · 示例",`<p class="panel-intro">实机回放验收正在等你拍板。</p><div class="choice-list"><button data-action="scope" data-scope="replay">进入实机回放任务</button></div>`);
    overlays.innerHTML = `<button class="overlay-backdrop" data-action="close-overlay" aria-label="关闭浮层"></button>${html}`;
    setInert();
    (overlays.querySelector("#scope-search,#lab-notes") || focusables(overlays.querySelector(".lab-panel"))[0])?.focus({preventScroll:true});
  }
  function switchScope(scope) {
    if (!["general",...data.tasks.map(t=>t.id)].includes(scope)) return;
    const scroll = root.querySelector(".conversation-scroll");
    if (scroll) profile.scroll[profile.scope] = scroll.scrollTop;
    profile.scope = scope; mobileNav = false; closeOverlay(false); render({rememberScroll:false});
    root.querySelector("#message-input")?.focus({preventScroll:true});
  }
  function addMessage(text,role="user") {
    (profile.messages[profile.scope] ||= []).push({role,text});
  }
  function send() {
    const text = (root.querySelector("#message-input")?.value || "").trim();
    if (!text) { root.querySelector("#message-input")?.focus(); return; }
    addMessage(text); addMessage("已保存在当前示例会话。这个原型不会调用 AI，你可以切换其他会话，再回来查看草稿与记录。","assistant");
    profile.drafts[profile.scope] = "";
    if (status() === "queued") profile.statuses[profile.scope] = "running";
    render({bottom:true}); root.querySelector("#message-input")?.focus();
  }
  document.addEventListener("click",event=>{
    const element = event.target.closest("[data-action]");
    if (!element) return;
    const action = element.dataset.action;
    if (action === "send") return;
    event.preventDefault();
    if (action === "scope") switchScope(element.dataset.scope);
    else if (action === "task-list" || action === "scope-switch") openOverlay("picker",element);
    else if (["details","more","mode","note","decision","delivery","assistant","preferences","todo"].includes(action)) openOverlay(action,element);
    else if (action === "close-overlay") closeOverlay();
    else if (action === "mobile-nav") {
      const closing = mobileNav; if (!closing) rememberOpener(element);
      mobileNav = !mobileNav; render(); if (closing) restoreFocus();
    }
    else if (action === "start") {
      if (profile.scope === "general") return;
      profile.statuses[profile.scope] = "running"; closeOverlay(false); render(); toast("已模拟开始当前任务，其他任务状态保持原样。");
    }
    else if (action === "set-mode") { profile.modes[profile.scope]=element.dataset.value; closeOverlay(false); render(); restoreFocus(); toast("决策方式已保存；任务不会因此启动。"); }
    else if (action === "resolve-decision") { addMessage(`已拍板：${element.dataset.value}`); profile.statuses[profile.scope]="running"; closeOverlay(false); render({bottom:true}); }
    else if (action === "suggest") { const input=root.querySelector("#message-input"); input.value=element.dataset.value; profile.drafts[profile.scope]=input.value; save(); input.focus(); }
    else if (action === "toggle-tools") { toolbar.hidden=!toolbar.hidden; }
    else if (action === "theme") { toggleTheme(); closeOverlay(); }
    else if (action === "reset") { const note=profile.note; profile=defaults(); profile.note=note; mobileNav=false; closeOverlay(false); render({rememberScroll:false}); toast("这一版已恢复初始示例。"); }
    else if (action === "export-notes") {
      save(); const text=["Crew 顶部原型试用笔记",...Object.entries(profiles).map(([id,value])=>`\n${id} · ${window.HeaderLabVariants[id]?.name || "原版对照"}\n${value.note || "未记录"}`)].join("\n");
      const url=URL.createObjectURL(new Blob([text],{type:"text/plain;charset=utf-8"})); const link=document.createElement("a"); link.href=url; link.download="Crew-顶部原型试用笔记.txt"; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
    }
  });
  document.addEventListener("submit",event=>{if(event.target.id === "message-form"){event.preventDefault();send();}});
  document.addEventListener("input",event=>{
    if (event.target.id === "message-input") {profile.drafts[profile.scope]=event.target.value;save();}
    else if (event.target.id === "scope-search") overlays.querySelector(".scope-list").innerHTML=pickerRows(event.target.value);
    else if (event.target.id === "lab-notes") {profile.note=event.target.value;save();}
  });
  document.addEventListener("keydown",event=>{
    if (event.isComposing) return;
    if (event.key === "Escape") {if(overlay){event.preventDefault();closeOverlay();}else if(mobileNav){event.preventDefault();mobileNav=false;render();restoreFocus();}}
    if (event.key === "Enter" && !event.shiftKey && event.target.id === "message-input") {event.preventDefault();send();}
    if (event.key === "Tab" && (overlay || mobileNav)) {
      const container=overlay ? overlays.querySelector(".lab-panel") : nav(); const items=focusables(container);
      if (!items.length) return;
      const index=items.indexOf(document.activeElement);
      if(event.shiftKey && index<=0){event.preventDefault();items.at(-1).focus();}
      else if(!event.shiftKey && (index===-1 || index===items.length-1)){event.preventDefault();items[0].focus();}
    }
    if (event.target.getAttribute("role") === "tab" && ["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) {
      const tabs=[...root.querySelectorAll('[role="tab"]')], index=tabs.indexOf(event.target); event.preventDefault();
      const next=event.key === "Home" ? tabs[0] : event.key === "End" ? tabs.at(-1) : tabs[(index+(event.key === "ArrowRight" ? 1 : tabs.length-1))%tabs.length];
      switchScope(next.dataset.scope); root.querySelector(`#${next.id}`)?.focus({preventScroll:true});
    }
  });
  function toggleTheme(){document.documentElement.dataset.theme=document.documentElement.dataset.theme === "dark" ? "light" : "dark";render();}
  document.getElementById("lab-theme").addEventListener("click",toggleTheme);
  document.getElementById("lab-variant").addEventListener("change",event=>{save();const next=new URL(location.href);next.searchParams.set("view",event.target.value);location.href=next.href;});
  document.getElementById("lab-status").addEventListener("change",event=>{profile.statuses[profile.scope]=event.target.value;render();});
  document.getElementById("lab-long-title").addEventListener("change",event=>{profile.longTitle=event.target.checked;render();});
  window.addEventListener("resize",()=>{if(innerWidth>(variant === "switcher" ? 620 : 760) && mobileNav){mobileNav=false;render();}});
  render({rememberScroll:false});
})();
