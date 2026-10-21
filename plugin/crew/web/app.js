"use strict";
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));
const number = value => value == null ? "—" : Number(value).toLocaleString("zh-CN", {maximumFractionDigits: 2});
const money = value => value == null ? "未完整计价" : "$" + Number(value).toLocaleString("zh-CN", {minimumFractionDigits: 2, maximumFractionDigits: 2});
const dateTime = value => value ? new Date(value).toLocaleString("zh-CN") : "—";
const duration = value => {
  if (value == null) return "—";
  const minutes = Math.round(value / 60);
  return minutes < 60 ? `${minutes} 分` : `${Math.floor(minutes / 60)} 小时 ${String(minutes % 60).padStart(2, "0")} 分`;
};
const empty = () => '<p class="empty">暂无记录</p>';
const states = {working:"干活中", waiting:"等你", idle:"空闲", offline:"离线", closed:"已收"};
let state = null, project = null, activeTab = "board", usageData = null, usagePeriod = "week", stateRequest = 0, usageRequest = 0;
let sortKey = "total_tokens", sortDirection = -1, currentResource = null;
let requestedChat = new URLSearchParams(location.hash.slice(1)).get("chat");
const colors = new Map();
function color(id) {
  if (!colors.has(id)) {
    const index = colors.size;
    const variable = `--session-${index}`;
    document.documentElement.style.setProperty(variable, `hsl(${(index * 137.508) % 360} var(--chart-saturation) var(--chart-lightness))`);
    colors.set(id, `var(${variable})`);
  }
  return colors.get(id);
}
function ago(value) {
  if (!value) return "未知";
  const seconds = Math.max(0, (Date.now() - new Date(value).getTime()) / 1000);
  if (seconds < 60) return "刚刚";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  return `${Math.floor(seconds / 86400)} 天前`;
}
function showError(error) { $("error").textContent = error.message; $("error").hidden = false; }
function notice(text) { $("notice").textContent = text; $("notice").hidden = false; }
async function request(path, data) {
  const options = data === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json", "X-Crew":"1"}, body:JSON.stringify(data)};
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error);
  return result;
}
function projectQuery() { return project === null ? "" : "?project=" + encodeURIComponent(project); }
const post = (path, data) => request(path + projectQuery(), data);
function allSessions() { return state ? [...state.groups.flatMap(g => g.members), ...state.ungrouped] : []; }
function displayName(id) { const session = allSessions().find(s => s.session_id === id); return session ? session.crew_name || id.slice(0, 8) : id?.slice(0, 8) || "—"; }
function groupOptions(current = null, ungrouped = false) {
  return (ungrouped ? '<option value="">未分组</option>' : `<option value="" disabled ${current === null ? "selected" : ""}>挪到分组…</option>`) +
    state.groups.filter(g => !g.closed_at).map(g => `<option value="${esc(g.name)}" ${current === g.name ? "selected" : ""}>${esc(g.name)}</option>`).join("");
}
function sessionCard(s) {
  const name = s.crew_name || s.session_id.slice(0, 8);
  const path = (s.worktree || s.cwd || "—").replace(/\\/g, "/").split("/").slice(-3).join("/");
  const branch = s.branch && s.branch.length > 30 ? "…" + s.branch.slice(-29) : s.branch || "—";
  return `<article class="card ${s.role === "leader" ? "leader" : ""} ${s.status === "offline" ? "offline-card" : ""}">
    <div class="card-head"><h3>${s.role === "leader" ? "★ " : ""}${esc(name)}</h3><small>${s.role === "leader" ? "leader" : "成员"}</small><span class="pill ${esc(s.status)}">${states[s.status]}</span></div>
    <p class="pre">${esc(s.task || "尚未填写在做什么")}</p>${s.status_note ? `<p class="pre">${esc(s.status_note)}</p>` : ""}
    <small class="short-path" title="${esc(s.worktree || s.cwd)}">${esc(path)}</small><small class="short-path" title="${esc(s.branch)}">分支 ${esc(branch)} · ${ago(s.last_event_at)}</small>
    <p class="last-message" title="${esc(s.last_message)}">${esc(s.last_message || "暂无最后一句话")}</p>
    <button class="address" data-copy="${esc(s.cc_name || "")}" ${s.cc_name ? "" : "disabled"} title="点击复制 SendMessage 地址">SendMessage：${esc(s.cc_name || "尚未登记地址")}</button>
    ${s.open ? `<div class="card-actions"><button data-open="${esc(s.session_id)}" ${s.open.enabled ? "" : "disabled"} title="${esc(s.open.reason || "在 VS Code 打开这段对话")}">在 VS Code 打开 ↗</button></div>` : ""}
    ${s.web_chat ? `<div class="card-actions"><button class="primary" data-chat="${esc(s.session_id)}">打开网页对话 →</button></div>` : ""}
    ${s.role !== "leader" ? `<footer><label>挪组 <select data-move="${esc(name)}" aria-label="移动 ${esc(name)}">${groupOptions(s.group_name)}</select></label></footer>` : ""}</article>`;
}
function sessionGrid(sessions, group, expanded) {
  // Same rule as `crew status`: ended sessions pile up over weeks, so fold closed, unnamed and day-old offline ones.
  const folded = sessions.filter(s => s.status === "closed" || (s.status === "offline" && (!s.crew_name || !s.last_event_at || Date.now() - new Date(s.last_event_at) > 864e5)));
  const shown = sessions.filter(s => !folded.includes(s)).sort((a,b) => Number(b.role === "leader") - Number(a.role === "leader") || Number(a.status === "offline") - Number(b.status === "offline"));
  return `<div class="grid">${shown.map(sessionCard).join("") || (folded.length ? "" : empty())}</div>` +
    (folded.length ? `<details class="folded" data-group="${esc(group)}" ${expanded.has(group) ? "open" : ""}><summary>另有 ${folded.length} 个已结束的会话：已收、未命名，或离线超过一天</summary><div class="grid">${folded.map(sessionCard).join("")}</div></details>` : "");
}
function renderCandidates() {
  const candidates = state.candidates || [];
  $("candidates-panel").hidden = !candidates.length;
  $("candidate-count").textContent = candidates.length;
  $("candidates").innerHTML = candidates.map(s => `<article class="card candidate"><div class="card-head"><h3>${esc(s.title)}</h3><span class="pill ${esc(s.status)}">${states[s.status]}</span></div><small class="short-path" title="${esc(s.cwd)}">${esc(s.cwd)}</small><p class="muted">${esc(s.cc_name)}</p><button data-adopt="${esc(s.session_id)}">收进组</button></article>`).join("");
}
function table(rows, fields) {
  if (!rows.length) return empty();
  return `<table><thead><tr>${fields.map(([, label]) => `<th>${esc(label)}</th>`).join("")}</tr></thead><tbody>${rows.map(row => `<tr>${fields.map(([key,,format]) => `<td>${format ? format(row[key], row) : esc(row[key] ?? "—")}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}
function renderBoard() {
  $("project").innerHTML = state.projects.map(p => `<option value="${esc(p.name)}" ${p.name === project ? "selected" : ""}>${esc(p.name)}</option>`).join("");
  $("new-session").disabled = $("new-leader").disabled = !state.project;
  const waiting = allSessions().filter(s => s.status === "waiting");
  const open = state.decisions.filter(d => d.status === "open");
  $("waiting-count").textContent = `等你 ${waiting.length + open.length}`;
  $("agents-error").hidden = !state.agents_error;
  $("agents-error").textContent = state.agents_error ? state.agents_error + "；进程状态未知，以下状态按白板原样显示。" : "";
  $("freshness").textContent = state.agents_error ? "进程状态未知" : `进程状态 ${Math.floor(state.agents_age_seconds)} 秒前更新`;
  $("waiting").innerHTML = `<h2>等你 · ${waiting.length + open.length}</h2>` + (waiting.length + open.length ? `<ul>${waiting.map(s => `<li><strong>${esc(s.crew_name || s.session_id.slice(0,8))}</strong>：${esc(s.status_note || "等你处理")} <small>已等 ${ago(s.last_event_at) === "刚刚" ? "不足 1 分钟" : ago(s.last_event_at).replace(/前$/, "")}</small></li>`).join("")}${open.map(d => `<li><a href="#decision-${d.id}">${d.blocking ? "卡流程 · " : ""}${esc(d.question)}</a> · ${esc(displayName(d.session_id))}</li>`).join("")}</ul>` : "当前没有等待处理的事。");
  const expanded = new Set([...$("groups").querySelectorAll("details[open]")].map(d => d.dataset.group));
  $("groups").innerHTML = state.groups.filter(g => !g.closed_at).map(g => `<section><h2>${esc(g.name)}</h2>${sessionGrid(g.members, g.name, expanded)}</section>`).join("") + `<section><h2>未分组</h2>${sessionGrid(state.ungrouped, "", expanded)}</section>`;
  renderCandidates();
  const resources = state.resources.map(r => ({...r, lease:state.leases.find(l => l.resource === r.name)}));
  $("leases").innerHTML = table(resources, [["name","资源"],["lease","借用人", l => esc(l ? l.holder_name || displayName(l.holder_session) : "空闲")],["lease","用途",l => esc(l?.purpose || "—")],["lease","借出时间", l => esc(dateTime(l?.taken_at))],["lease","预计归还", l => esc(dateTime(l?.until_at))],["lease","操作",(l,r) => l ? `<button data-give="${esc(r.name)}">强制归还</button>` : "—"]]);
  $("claims").innerHTML = table(state.claims, [["pattern","路径"],["crew_name","归属人", (v,r) => esc(v || displayName(r.session_id))],["note","说明"],["created_at","声明时间", v => esc(dateTime(v))]]);
  const focused = document.activeElement;
  // Keep a reply being edited intact across the three-second board refresh.
  if (!focused?.closest("#decisions")) {
    const drafts = new Map([...$("decisions").querySelectorAll("form")].map(form => [form.dataset.answer, form.elements.text.value]));
    $("decisions").innerHTML = state.decisions.map(d => `<article class="card" id="decision-${d.id}"><div class="card-head"><h3>#${d.id} ${esc(d.question)}</h3><span class="pill ${d.status === "open" ? "waiting" : "idle"}">${d.status === "open" ? "未答" : "已答"}</span></div><p>提问人：${esc(displayName(d.session_id))} · ${ago(d.created_at)}</p><p>是否卡流程：${d.blocking ? "是" : "否"}</p>${d.options.length ? `<ul>${d.options.map(o => `<li>${esc(o)}</li>`).join("")}</ul>` : ""}<p>推荐：${esc(d.recommendation || "无")}</p>${d.status === "open" ? `<form class="answer-form" data-answer="${d.id}"><label>答复<textarea name="text" required rows="2">${esc(drafts.get(String(d.id)) || "")}</textarea></label><button>答复</button><p class="form-error" role="alert"></p></form>` : `<p class="pre">${esc(d.answer)}</p><small>${esc(d.answered_by)} · ${dateTime(d.answered_at)}</small>`}</article>`).join("") || empty();
  }
  $("handoffs").innerHTML = state.handoffs.map(h => `<article class="entry"><small>${esc(h.from_name || displayName(h.from_session))} → ${esc(h.to_target)} · ${ago(h.created_at)}</small><p class="pre">${esc(h.body)}</p></article>`).join("") || empty();
  $("reports").innerHTML = state.reports.map(r => `<article class="entry"><small>${esc(r.crew_name || displayName(r.session_id))} · ${ago(r.created_at)}</small><p class="pre">${esc(r.body)}</p></article>`).join("") || empty();
  $("events").innerHTML = state.events.map(e => `<div class="entry"><small>${dateTime(e.ts)} · ${esc(displayName(e.session_id))}</small> <strong>${esc(e.kind)}</strong><details><summary>详情</summary><pre class="pre">${esc(JSON.stringify(e.detail, null, 2))}</pre></details></div>`).join("") || empty();
  window.crewChat?.update();
}
async function loadState() {
  const sequence = ++stateRequest;
  try {
    const data = await request("/api/state" + projectQuery());
    if (sequence !== stateRequest) return;
    state = data; project = data.project?.name ?? null;
    $("error").hidden = true;
    renderBoard();
  } catch (error) { if (sequence === stateRequest) showError(error); }
}
function renderSessions() {
  const columns = [["display_name","会话"],["project","项目"],["group_name","分组"],["input_tokens","输入"],["cache_write_tokens","缓存写入"],["cache_read_tokens","缓存读取"],["output_tokens","输出"],["total_tokens","总 token"],["cost_usd","金额 USD"],["unpriced_tokens","未计价 token"],["prompts","提示数"],["work_seconds","干活时长"],["compactions","压缩"],["cache_hit_rate","缓存命中"],["subagent_tokens","子 agent token"],["codex_runs","Codex 次数"],["codex_tokens","Codex token"]];
  const rows = [...usageData.sessions].sort((a,b) => {
    const x = a[sortKey], y = b[sortKey];
    if (x == null || y == null) return x == null ? y == null ? 0 : 1 : -1;
    return sortDirection * (typeof x === "number" ? x - y : String(x).localeCompare(String(y), "zh-CN"));
  });
  $("sessions-table").innerHTML = rows.length ? `<table><thead><tr>${columns.map(([key,label]) => `<th><button data-sort="${key}">${label}${sortKey === key ? sortDirection === 1 ? " ↑" : " ↓" : ""}</button></th>`).join("")}</tr></thead><tbody>${rows.map(r => `<tr>${columns.map(([key]) => {
    let value = key === "cost_usd" ? money(r[key]) : key === "work_seconds" ? duration(r[key]) : key === "cache_hit_rate" ? number(r[key] * 100) + "%" : typeof r[key] === "number" ? number(r[key]) : r[key] ?? "—";
    return `<td class="${typeof r[key] === "number" ? "num" : ""}">${key === "display_name" ? `<i class="swatch" style="--series-color:${color(r.session_id)}"></i>` : ""}${esc(value)}</td>`;
  }).join("")}</tr>`).join("")}</tbody></table>` : empty();
}
function renderChart(rows) {
  if (!rows.length) { $("daily-chart").innerHTML = empty(); $("legend").innerHTML = ""; return; }
  const days = [...new Set(rows.map(r => r.date))].sort();
  const sessions = [...new Set(rows.map(r => r.session_id))];
  const totals = new Map(days.map(day => [day, rows.filter(r => r.date === day).reduce((sum,r) => sum + r.total_tokens, 0)]));
  const max = Math.max(...totals.values(), 1), width = Math.max(760, days.length * 62 + 110), height = 300;
  const left = 92, top = 20, plotHeight = 235, step = (width - left - 20) / days.length;
  let svg = `<svg class="chart" viewBox="0 0 ${width} ${height}" style="min-width:${width}px" role="img" aria-label="每天各会话的 Claude token 堆叠柱状图">`;
  for (let tick = 0; tick <= 4; tick++) {
    const y = top + plotHeight - tick / 4 * plotHeight;
    svg += `<line x1="${left}" y1="${y}" x2="${width-20}" y2="${y}"/><text x="${left-10}" y="${y+4}" text-anchor="end">${number(Math.round(max*tick/4))}</text>`;
  }
  days.forEach((day,index) => {
    let bottom = top + plotHeight;
    for (const row of rows.filter(r => r.date === day)) {
      const h = row.total_tokens / max * plotHeight;
      bottom -= h;
      svg += `<rect x="${left + index*step + step*.15}" y="${bottom}" width="${step*.7}" height="${h}" style="--series-color:${color(row.session_id)}"><title>${esc(day)} · ${esc(row.display_name)}：${number(row.total_tokens)} token</title></rect>`;
    }
    svg += `<text x="${left + (index+.5)*step}" y="${top+plotHeight+24}" text-anchor="middle">${esc(day.slice(5))}</text>`;
  });
  $("daily-chart").innerHTML = svg + "</svg>";
  $("legend").innerHTML = sessions.map(id => `<span><i class="swatch" style="--series-color:${color(id)}"></i>${esc(rows.find(r => r.session_id === id).display_name)}</span>`).join("");
}
function renderUsage() {
  const summary = usageData.summary, limits = summary.rate_limits;
  $("metrics").innerHTML = `<article class="metric"><span>Claude token</span><strong>${number(summary.claude.total_tokens)}</strong><small>${number(summary.claude.requests)} 次请求</small></article><article class="metric"><span>按 API 价折算</span><strong>${money(summary.claude.cost_usd)}</strong><small>已计价 ${money(summary.claude.priced_cost_usd)} · 未计价 ${number(summary.claude.unpriced_tokens)} token</small></article><article class="metric"><span>Codex token</span><strong>${number(summary.codex.total_tokens)}</strong><small>${number(summary.codex.runs)} 次运行</small></article><article class="metric"><span>Codex 周额度${limits?.window_minutes !== 10080 && limits ? `（快照窗口 ${number(limits.window_minutes)} 分钟）` : ""}</span><strong>${limits?.used_percent == null ? "暂无快照" : number(limits.used_percent) + "%"}</strong>${limits?.used_percent != null ? `<progress max="100" value="${limits.used_percent}" aria-label="Codex 额度已用百分比"></progress>` : ""}<small>重置 ${dateTime(limits?.resets_at)}</small></article>`;
  renderSessions(); renderChart(usageData.daily);
  $("codex-table").innerHTML = table(usageData.codex, [["task_name","任务"],["display_name","派活会话"],["started_at","开始",v => esc(dateTime(v))],["duration_seconds","用时",duration],["total_tokens","token",number],["exit_code","退出码"],["model","模型"],["effort","强度"]]);
  $("agents-table").innerHTML = table(usageData.agents, [["agent_type","类型"],["requests","请求数",number],["total_tokens","token",number],["cost_usd","金额",money],["unpriced_tokens","未计价 token",number]]);
  $("models-table").innerHTML = table(usageData.models, [["source","来源"],["model","模型"],["input_tokens","输入",number],["output_tokens","输出",number],["total_tokens","token",number],["cost_usd","金额",money]]);
  $("exports").innerHTML = [["sessions","会话"],["daily","按天"],["codex","Codex"],["requests","请求明细"]].map(([key,label]) => `<a href="/api/usage/export?what=${key}&period=${encodeURIComponent(usagePeriod)}" download="crew-${key}.csv">${label} CSV</a>`).join("");
}
async function loadUsage() {
  const sequence = ++usageRequest;
  $("usage-loading").hidden = false;
  try {
    const data = await request("/api/usage?period=" + encodeURIComponent(usagePeriod));
    if (sequence !== usageRequest) return;
    usageData = data; renderUsage(); $("error").hidden = true;
  } catch (error) { if (sequence === usageRequest) showError(error); }
  finally { if (sequence === usageRequest) $("usage-loading").hidden = true; }
}
function setTab(tab) {
  if (tab === activeTab) return;
  activeTab = tab;
  $("board-view").hidden = tab !== "board"; $("usage-view").hidden = tab !== "usage";
  $("chat-view").hidden = tab !== "chat";
  $("board-tab").setAttribute("aria-pressed", String(tab === "board")); $("usage-tab").setAttribute("aria-pressed", String(tab === "usage"));
  $("chat-tab").setAttribute("aria-pressed", String(tab === "chat"));
  const chatId = window.crewChat?.selectedId() || requestedChat;
  history.replaceState(null, "", tab === "usage" ? "#usage" : tab === "chat" ? "#chat" + (chatId ? "=" + encodeURIComponent(chatId) : "") : location.pathname + location.search);
  document.body.classList.toggle("chat-active", tab === "chat");
  window.scrollTo(0, 0);
  window.crewChat?.visibility(tab === "chat");
  if (tab === "usage") loadUsage(); else loadState();
}
$("board-tab").onclick = () => setTab("board"); $("usage-tab").onclick = () => setTab("usage");
$("chat-tab").onclick = () => setTab("chat");
$("project").onchange = () => { project = $("project").value; window.crewChat?.reset(); loadState(); };
$("new-session").onclick = () => { const form = $("spawn-form"); form.reset(); form.querySelector(".form-error").textContent = ""; form.elements.group.innerHTML = groupOptions(null, true); $("spawn-dialog").showModal(); };
$("new-leader").onclick = () => { const form = $("leader-form"); form.reset(); form.querySelector(".form-error").textContent = ""; form.elements.members.innerHTML = allSessions().filter(s => s.role !== "leader" && s.crew_name).map(s => `<option value="${esc(s.crew_name)}">${esc(s.crew_name)} · ${esc(s.group_name || "未分组")}</option>`).join(""); $("leader-candidates").innerHTML = (state.candidates || []).length ? '<p class="muted">也可以选尚未登记的会话，并给它起个名字。</p>' + state.candidates.map(s => `<div class="candidate-choice"><label><input type="checkbox" value="${esc(s.session_id)}">${esc(s.title)}</label><input data-candidate-name="${esc(s.session_id)}" aria-label="${esc(s.title)} 的组员名字" placeholder="组员名字" value="${esc(s.title)}"></div>`).join("") : ""; $("leader-dialog").showModal(); };
document.addEventListener("click", async event => {
  const close = event.target.closest("[data-close]"); if (close) close.closest("dialog").close();
  const copy = event.target.closest("[data-copy]"); if (copy) { try { await navigator.clipboard.writeText(copy.dataset.copy); notice("已复制 SendMessage 地址"); } catch (error) { showError(error); } }
  const give = event.target.closest("[data-give]"); if (give) { currentResource = give.dataset.give; $("give-form").reset(); $("give-form").querySelector(".form-error").textContent = ""; $("give-resource").textContent = currentResource; $("give-dialog").showModal(); }
  const sort = event.target.closest("[data-sort]"); if (sort) { sortDirection = sortKey === sort.dataset.sort ? -sortDirection : -1; sortKey = sort.dataset.sort; renderSessions(); }
  const adopt = event.target.closest("[data-adopt]"); if (adopt) { const s = state.candidates.find(s => s.session_id === adopt.dataset.adopt), form = $("adopt-form"); form.reset(); form.querySelector(".form-error").textContent = ""; form.elements.session_id.value = s.session_id; form.elements.name.value = s.title; form.elements.group.innerHTML = groupOptions(null, true); $("adopt-title").textContent = s.title; $("adopt-dialog").showModal(); }
  const open = event.target.closest("[data-open]"); if (open) { open.disabled = true; try { const result = await post("/api/open", {session_id:open.dataset.open}), s = allSessions().find(s => s.session_id === result.session_id), form = $("open-form"); form.reset(); form.elements.session_id.value = result.session_id; form.querySelector(".form-error").textContent = ""; $("open-name").textContent = s?.crew_name || result.session_id.slice(0,8); $("open-cwd").textContent = result.cwd; $("open-action").textContent = result.location === "web" ? "继续时会结束这段空闲的网页会话，并在 VS Code 续上同一段对话。" : result.location === "terminal" ? "继续时会关闭这段空闲的终端会话，并在 VS Code 续上同一段对话。" : "继续时会在 VS Code 打开同一段对话。"; $("open-dialog").showModal(); } catch (error) { showError(error); } finally { open.disabled = false; } }
});
document.addEventListener("change", async event => {
  const select = event.target.closest("[data-move]");
  if (select && select.value) { select.disabled = true; try { await post("/api/roster/move", {name:select.dataset.move, to:select.value}); await loadState(); } catch (error) { await loadState(); showError(error); } finally { select.disabled = false; } }
});
document.addEventListener("submit", async event => {
  const form = event.target;
  if (!form.matches("#spawn-form, #leader-form, #give-form, #adopt-form, #open-form, .answer-form")) return;
  event.preventDefault();
  const button = form.querySelector('button[type="submit"], button:not([type])'), error = form.querySelector(".form-error");
  button.disabled = true; error.textContent = "";
  try {
    if (form.id === "spawn-form") { const result = await post("/api/spawn", {name:form.elements.name.value, task:form.elements.task.value, group:form.elements.group.value || null, role:"member", new_worktree:form.elements.new_worktree.value || null}); window.crewChat?.select(result.session_id); }
    else if (form.id === "leader-form") { const result = await post("/api/leader", {name:form.elements.name.value, members:[...form.elements.members.selectedOptions].map(o => o.value), candidate_members:[...$("leader-candidates").querySelectorAll('input[type="checkbox"]:checked')].map(input => ({session_id:input.value, name:[...$("leader-candidates").querySelectorAll("[data-candidate-name]")].find(n => n.dataset.candidateName === input.value).value})), task:form.elements.task.value || null}); window.crewChat?.select(result.session_id); }
    else if (form.id === "adopt-form") await post("/api/roster/adopt", {session_id:form.elements.session_id.value, name:form.elements.name.value, group:form.elements.group.value || null});
    else if (form.id === "open-form") await post("/api/open/continue", {session_id:form.elements.session_id.value, confirm:true});
    else if (form.id === "give-form") await post("/api/lease/give", {resource:currentResource, reason:form.elements.reason.value});
    else await post("/api/answer", {id:Number(form.dataset.answer), text:form.elements.text.value});
    form.closest("dialog")?.close();
    if (form.matches(".answer-form")) document.activeElement?.blur();
    notice("操作完成"); await loadState();
  } catch (failure) { error.textContent = failure.message; }
  finally { button.disabled = false; }
});
$("period").onchange = () => { $("dates").hidden = $("period").value !== "custom"; if ($("period").value !== "custom") { usagePeriod = $("period").value; loadUsage(); } };
$("dates").onsubmit = event => { event.preventDefault(); usagePeriod = $("date-from").value + ".." + $("date-to").value; loadUsage(); };
document.addEventListener("visibilitychange", () => { if (!document.hidden && activeTab !== "usage") loadState(); });
setInterval(() => { if (!document.hidden && activeTab !== "usage") loadState(); }, 3000);
loadState();
if (location.hash === "#usage") setTab("usage");
if (location.hash.startsWith("#chat")) setTab("chat");
