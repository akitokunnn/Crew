"use strict";
// Usage owns its filters, cached report, requests and rendering. The application
// supplies only workspace/account snapshots and shared rendering primitives.
(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[char]);
  const PERIODS = [["today", "今天"], ["week", "本周"], ["all", "全部"]];
  const TABS = [["tree", "按结构"], ["daily", "按天"], ["models", "按模型"]];
  const count = value => Number(value).toLocaleString("zh-CN", {maximumFractionDigits:1});
  const tokens = value => value === null ? "未知" : value >= 1000000 ? `${count(value / 1000000)}M` : value >= 1000 ? `${count(value / 1000)}K` : count(value);
  const money = value => value === null ? "未定价" : `$${Number(value).toFixed(2)}`;
  const crewPercent = value => `${value < 1 ? "<1" : Math.round(value)}%`;
  // Expired windows remain unknown until the next observation reports usage.
  const quotaText = limit => limit == null ? "未采集" : limit.used_percent === null ? (limit.expired ? "已重置" : "未采集") : `${count(limit.used_percent)}%`;

  function crewQuota(limit) {
    if (limit == null || limit.crew === undefined || limit.crew.points === null) return "";
    const crew = limit.crew, cost = `$${Math.round(crew.cost_usd).toLocaleString("zh-CN")}`;
    const title = `按每 1% ≈ $${crew.usd_per_point} 折算（社区实测 $${crew.usd_per_point_range[0]}～${crew.usd_per_point_range[1]}，对应 ${crewPercent(crew.low)}～${crewPercent(crew.high)}）。Crew = 进了组的 leader 和组员，含子 agent；本周 Crew 折合 ${cost}。`;
    return `<div class="quota-crew" title="${esc(title)}">其中 Crew ≈ ${esc(crewPercent(crew.points))}（折合 ${cost}）</div>`;
  }

  function create(options) {
    const ui = options.ui, icon = ui.icon, avatar = ui.avatar;
    let project = null, limits = null, scope = null, host = null;
    let period = "week", tab = "tree", report = null, loading = false, error = null;
    let sequence = 0, pending = null, destroyed = false;
    const collapsed = new Set();
    const quotas = () => [["Claude 每周", limits?.claude?.seven_day ?? null], ["Claude 5 小时", limits?.claude?.five_hour ?? null], ["Codex 每周", limits?.codex ?? null]];

    function summaryMarkup() {
      const title = quotas().map(([label, limit]) => `${label} ${quotaText(limit)}`).join("\n");
      return `<button class="quota quota-summary usage-summary" data-act="view" data-view="usage" title="${esc(title)}" aria-label="查看完整额度和用量"><span class="quota-summary-row"><span>Claude</span><span>周 ${quotaText(limits?.claude?.seven_day)} · 5h ${quotaText(limits?.claude?.five_hour)}</span></span><span class="quota-summary-row"><span>Codex</span><span>周 ${quotaText(limits?.codex)}</span></span></button>`;
    }

    function quotaCard(label, limit) {
      let body = '<div class="big unknown">未采集</div>';
      if (limit !== null) {
        const usage = limit.used_percent === null ? `<div class="big unknown">${quotaText(limit)}</div>` : `<div class="big">${count(limit.used_percent)}%<small>已用</small></div><div class="bar"><i style="width:${limit.used_percent}%;${limit.used_percent >= 80 ? "background:var(--warn)" : ""}"></i></div>`;
        body = `${usage}${crewQuota(limit)}<div class="rs">${esc(ui.date(limit.resets_at))} 重置 · ${esc(ui.time(limit.captured_at))} 采集${limit.expired ? " · 采集已过期" : ""}</div>`;
      }
      return `<div class="qcard"><div class="lbl"><span>${label}</span><span>账户</span></div>${body}</div>`;
    }

    const share = amount => `<div class="share">${count(amount)}%<span class="b"><i style="width:${Math.min(100, amount)}%"></i></span></div>`;
    const usageTokens = row => `${tokens(row.tokens)}${row.codex_runs > 0 ? `<div class="usage-codex-count">Codex ${tokens(row.codex_tokens)} · ${row.codex_runs} 次</div>` : ""}`;
    const treeNumbers = (row, amount) => `<td class="num">${usageTokens(row)}</td><td class="num">${money(row.cost)}</td><td class="num">${share(amount)}</td>`;

    function treeTable(totalTokens) {
      const rows = report.tree.map(row => {
        const key = row.leader_session_id === null ? "ungrouped" : row.leader_session_id, open = !collapsed.has(key);
        const group = `<tr class="grp"><td><button class="cell-name table-toggle" data-act="usage-fold" data-id="${esc(key)}" aria-expanded="${open}">${icon(open ? "down" : "right", "xs")}${avatar(row.name, 22)}${esc(row.name)}</button></td>${treeNumbers(row, row.share)}</tr>`;
        if (!open) return group;
        const selfShare = totalTokens === 0 ? 0 : row.self.tokens / totalTokens * 100;
        const self = row.leader_session_id === null ? "" : `<tr class="sub"><td><span class="cell-name">${avatar(row.name, 20, true)}${esc(row.name)}（leader 本身）</span></td>${treeNumbers(row.self, selfShare)}</tr>`;
        const members = row.members.map(item => `<tr class="sub"><td><span class="cell-name">${avatar(item.name, 20, true)}${esc(item.name)}</span></td>${treeNumbers(item, item.share)}</tr>`).join("");
        return group + self + members;
      }).join("");
      return `<table class="utable"><thead><tr><th>谁</th><th class="num">Claude Token</th><th class="num">Claude 折合</th><th class="num">Claude 占比</th></tr></thead><tbody>${rows}</tbody></table>`;
    }

    function tableMarkup(totalTokens) {
      if (tab === "tree") return treeTable(totalTokens);
      if (tab === "daily") return `<table class="utable"><thead><tr><th>日期</th><th>会话</th><th class="num">Token</th><th class="num">折合</th></tr></thead><tbody>${report.daily.map(row => `<tr><td>${esc(row.date)}</td><td>${esc(row.display_name)}</td><td class="num">${tokens(row.total_tokens)}</td><td class="num">${money(row.cost_usd)}</td></tr>`).join("")}</tbody></table>`;
      return `<table class="utable"><thead><tr><th>模型</th><th>来源</th><th class="num">输入</th><th class="num">输出</th><th class="num">合计</th></tr></thead><tbody>${report.models.map(row => `<tr><td>${esc(row.model)}</td><td>${esc(row.source)}</td><td class="num">${tokens(row.input_tokens)}</td><td class="num">${tokens(row.output_tokens)}</td><td class="num">${tokens(row.total_tokens)}</td></tr>`).join("")}</tbody></table>`;
    }

    function render() {
      if (destroyed || host === null) return;
      let table = `<p class="note" role="status">${error ? "用量没有读取成功。" : "正在读取用量…"}</p>`, total = "";
      if (report !== null) {
        const totalTokens = report.tree.reduce((sum, row) => sum + row.tokens, 0);
        const totalCost = report.tree.some(row => row.cost === null) ? null : report.tree.reduce((sum, row) => sum + row.cost, 0);
        const codexTokens = report.tree.reduce((sum, row) => sum + (row.codex_tokens || 0), 0);
        total = `Claude ${tokens(totalTokens)} token${codexTokens > 0 ? ` · Codex ${tokens(codexTokens)} token` : ""} · Claude 折合 ${money(totalCost)}`;
        table = tableMarkup(totalTokens);
      }
      const failure = error === null ? "" : `<div class="usage-error" role="alert"><p class="error-line">${esc(error)}</p><button class="btn" data-act="usage-retry" ${loading ? "disabled" : ""}>重试</button></div>`;
      const periodButtons = PERIODS.map(([key, name]) => `<button class="${period === key ? "on" : ""}" data-act="usage-period" data-period="${key}">${name}</button>`).join("");
      const tabButtons = TABS.map(([key, name]) => `<button class="${tab === key ? "on" : ""}" data-act="usage-tab" data-tab="${key}">${name}</button>`).join("");
      const exportPath = options.queryPath(`/api/usage/export?period=${period}&what=${tab === "daily" ? "daily" : "sessions"}`);
      options.replace(host, `<section class="usage-page"><div class="head big"><div><div class="title">用量</div><div class="sub">${esc(project?.display_name || project?.name || "")} · ${PERIODS.find(([key]) => key === period)[1]}</div></div><div class="right usage-toolbar"><div class="seg">${periodButtons}</div><button class="btn ghost" data-act="usage-refresh" ${loading ? "disabled" : ""}>${loading ? "读取中…" : "刷新"}</button><a class="btn" href="${esc(exportPath)}" download>${icon("download", "sm")}导出</a></div></div><div class="page"><div class="page-in"><div class="qcards">${quotas().map(([label, limit]) => quotaCard(label, limit)).join("")}</div><div class="usage-tabs"><div class="seg">${tabButtons}</div><span class="usage-total">${total}</span></div>${table}${failure}<div class="note">折合金额按 API 价换算，只用于比较；额度是账户级；界面字体 HarmonyOS Sans（华为，按其许可证随附）</div></div></div></section>`);
    }

    function load({force = false} = {}) {
      if (destroyed || scope === null) return Promise.resolve(null);
      if (loading) return pending;
      if (!force && report !== null) return Promise.resolve(report);
      const requested = ++sequence, requestedScope = scope, requestedPeriod = period;
      loading = true; error = null; render();
      const current = () => !destroyed && sequence === requested && scope === requestedScope && period === requestedPeriod;
      pending = (async () => {
        try {
          const result = await options.api(`/api/usage?period=${requestedPeriod}`);
          if (!current()) return null;
          report = result;
          return result;
        } catch (failure) {
          if (current()) error = failure.message;
          return null;
        } finally {
          if (current()) { loading = false; pending = null; render(); }
        }
      })();
      return pending;
    }

    function invalidate() { sequence++; report = null; loading = false; pending = null; error = null; }
    function ensureLoaded() { if (report === null && !loading && error === null) void load(); }
    function update(value) {
      if (destroyed) return;
      const nextScope = value.project?.name ?? null;
      if (scope !== nextScope) { invalidate(); collapsed.clear(); scope = nextScope; }
      project = value.project; limits = value.limits;
      render();
      if (host !== null) ensureLoaded();
    }
    function mount(node) { if (destroyed) return; host = node; render(); ensureLoaded(); }
    function unmount() { host = null; }
    function setPeriod(value) {
      if (destroyed || !PERIODS.some(([key]) => key === value) || period === value) return;
      period = value; invalidate(); render();
      if (host !== null) ensureLoaded();
    }
    function setTab(value) { if (!destroyed && TABS.some(([key]) => key === value)) { tab = value; render(); } }
    function toggle(id) { if (destroyed) return; if (collapsed.has(id)) collapsed.delete(id); else collapsed.add(id); render(); }
    const whenMounted = handler => context => { if (!destroyed && host !== null) return handler(context); };
    const unregister = options.actions?.register("usage", {
      "usage-period":whenMounted(({element}) => setPeriod(element.dataset.period)),
      "usage-tab":whenMounted(({element}) => setTab(element.dataset.tab)),
      "usage-fold":whenMounted(({element}) => toggle(element.dataset.id)),
      "usage-retry":whenMounted(() => load({force:true})),
      "usage-refresh":whenMounted(() => load({force:true}))
    });
    return {mount, update, unmount, summaryMarkup, setPeriod, setTab, toggle, load,
      snapshot:() => ({workspace:scope, period, tab, loading, error, hasData:report !== null, collapsed:[...collapsed], mounted:host !== null}),
      destroy() { if (destroyed) return; destroyed = true; host = null; invalidate(); collapsed.clear(); unregister?.(); }};
  }
  const exported = {create, quotaText};
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  else window.CrewUsage = exported;
})();
