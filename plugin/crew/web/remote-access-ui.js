"use strict";
(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  function mount(host, {read, action}) {
    let snapshot = null, pair = null, busy = false, error = "", disposed = false, timer, lastMarkup = null;
    const when = value => new Date(value).toLocaleTimeString("zh-CN", {hour:"2-digit", minute:"2-digit"});
    function render() {
      if (disposed) return;
      if (pair && Date.parse(pair.expires_at) <= Date.now()) pair = null;
      const markup = `<section class="cs-card remote-access"><div class="cs-card-heading"><div><h2>局域网访问</h2><p>让同一网络中的手机或电脑，继续使用这里的 Crew。</p></div><span class="cs-badge">${snapshot?.enabled ? "已开启" : "未开启"}</span></div>${!snapshot ? '<p>正在读取…</p>' : snapshot.enabled ? `<p>在另一台设备打开下方地址，再输入这台电脑生成的配对码。</p><div class="remote-addresses">${snapshot.urls.map(url => `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a>`).join("") || '<p>暂未找到局域网地址，请检查这台电脑的网络。</p>'}</div>${pair ? `<div class="remote-pair" role="status"><span>配对码</span><strong>${esc(pair.code)}</strong><small>仅可使用一次 · ${esc(when(pair.expires_at))} 失效</small></div>` : snapshot.pair_pending ? '<p class="remote-hint">已有待使用的配对码。需要时可重新生成，旧码会失效。</p>' : ""}<div class="remote-actions"><button class="cs-button cs-primary" data-remote-action="pair" ${busy ? "disabled" : ""}>${pair || snapshot.pair_pending ? "重新生成配对码" : "生成配对码"}</button><button class="cs-button" data-remote-action="stop" ${busy ? "disabled" : ""}>关闭访问</button></div><h3>已连接设备</h3><div class="remote-devices">${snapshot.devices.map(device => `<div class="remote-device"><div><strong>${esc(device.name || "浏览器")}</strong><small>最近使用 ${esc(when(device.last_seen_at || device.created_at))}</small></div><button class="cs-button" data-remote-action="revoke" data-device-id="${esc(device.id)}" ${busy ? "disabled" : ""}>移除</button></div>`).join("") || '<p class="remote-hint">还没有配对的设备。</p>'}</div>${snapshot.devices.length ? `<button class="cs-button" data-remote-action="revoke_all" ${busy ? "disabled" : ""}>移除所有设备</button>` : ""}` : `<p>开启后可以查看聊天、任务和成果，也能发送消息、拍板和处理待办。文件和 AI 仍在这台电脑上运行。</p><button class="cs-button cs-primary" data-remote-action="start" ${busy ? "disabled" : ""}>开启局域网访问</button>`}<p class="remote-status ${error ? "cs-error" : ""}" role="status">${esc(error || (busy ? "正在处理…" : ""))}</p>${error && !snapshot ? '<button class="cs-button" data-remote-action="refresh">重新读取</button>' : ""}</section>`;
      if (markup !== lastMarkup) { host.innerHTML = markup; lastMarkup = markup; }
    }
    async function refresh() {
      try { snapshot = await read(); error = snapshot.last_error || ""; if (!snapshot.pair_pending) pair = null; }
      catch (caught) { error = caught.message; }
      render();
    }
    host.onclick = async event => {
      const button = event.target.closest("[data-remote-action]"); if (!button || busy) return;
      const kind = button.dataset.remoteAction;
      if (kind === "refresh") { await refresh(); return; }
      busy = true; error = ""; if (kind === "pair") pair = null; render();
      try {
        const result = await action({action:kind, ...(kind === "revoke" ? {device_id:button.dataset.deviceId} : {})});
        pair = kind === "pair" ? {code:result.code, expires_at:result.expires_at} : kind === "stop" ? null : pair;
        // Reading failure after success must not repeat the completed action.
        await refresh();
      } catch (caught) { error = caught.message; }
      finally { busy = false; render(); }
    };
    render(); void refresh(); timer = setInterval(() => { if (!busy) void refresh(); }, 5000);
    return () => { disposed = true; clearInterval(timer); host.onclick = null; pair = null; };
  }
  window.CrewRemoteAccess = {mount};
})();
