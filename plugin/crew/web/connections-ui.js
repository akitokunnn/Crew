"use strict";
(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const names = {qq:"QQ", lark:"Lark / 飞书"};
  const labels = {unconfigured:"待配置", disabled:"已断开", disconnected:"已断开", connecting:"连接中", reconnecting:"重连中", unbound:"等待绑定", connected:"已连接", rejected:"发送被拒绝", error:"连接异常", stopped:"已停止"};
  function ready(channel) { return channel.enabled && channel.state === "connected" && channel.bound; }
  function statusLabel(channel) { return ready(channel) ? "已就绪" : labels[channel.state] || channel.state; }
  function guide(id, domain) {
    if (id === "qq") return `<ol class="cs-connection-guide"><li>在 <a href="https://q.qq.com/" target="_blank" rel="noopener noreferrer">QQ 开放平台 ↗</a> 创建机器人，启用单聊消息与事件接收。</li><li>在平台的开发设置中取得 <strong>AppID</strong> 和 <strong>AppSecret</strong>。测试阶段将自己的 QQ 号加入测试范围。</li><li>在下方保存并连接，然后用手机 QQ 私聊机器人完成绑定。打开会话中的<strong>允许主动发送</strong>，才能收到离开提醒。</li></ol>`;
    const site = domain === "lark" ? "https://open.larksuite.com" : "https://open.feishu.cn";
    return `<ol class="cs-connection-guide"><li>在 <a href="${site}/app" target="_blank" rel="noopener noreferrer">${domain === "lark" ? "Lark" : "飞书"} 开放平台 ↗</a> 创建<strong>企业自建应用</strong>，添加<strong>机器人</strong>能力，取得 App ID 和 App Secret。</li><li>在权限管理中开通应用权限：<code>im:message:send_as_bot</code>（发送消息）和 <code>im:message.p2p_msg:readonly</code>（接收私聊）。</li><li>先在下方<strong>保存并连接</strong>，再到平台“事件与回调”选择<strong>使用长连接接收事件</strong>，订阅 <code>im.message.receive_v1</code>。</li><li>创建并发布应用版本，将自己加入可用范围。打开机器人私聊，发送下方绑定命令。<a href="${site}/document/ukTMukTMukTM/uYDNxYjL2QTM24iN0EjN/event-subscription-configure-" target="_blank" rel="noopener noreferrer">查看官方长连接说明 ↗</a></li></ol><p class="cs-hint">无需公网地址。当前支持私聊文字、引用回复和通知；拍板可引用消息输入答案。</p>`;
  }
  function mount(host, options) {
    let active = true, snapshot = null, selected = "qq", busy = false, reading = false, timer = null, error = "", note = "", confirming = false, generation = 0;
    const drafts = new Map();
    let bindingKey = null;
    const channel = () => snapshot.channels.find(item => item.id === selected);
    function draftFor(item) {
      if (!drafts.has(item.id)) drafts.set(item.id, {app_id:item.app_id || "", app_secret:"", domain:item.domain || "feishu"});
      return drafts.get(item.id);
    }
    function setNote() {
      const node = host.querySelector("[data-connection-note]");
      if (node) { node.textContent = error || note; node.classList.toggle("cs-error", Boolean(error)); }
    }
    function schedule() {
      clearTimeout(timer);
      if (active) timer = setTimeout(() => { void read(false); }, 3000);
    }
    function renderState() {
      if (!active || !snapshot) return;
      for (const item of snapshot.channels) {
        const badge = host.querySelector(`[data-channel-badge="${item.id}"]`);
        if (badge) { badge.textContent = statusLabel(item); badge.classList.toggle("cs-ready", ready(item)); }
      }
      const item = channel(), connected = ["connected", "unbound"].includes(item.state) && item.enabled;
      const detail = ready(item) ? "机器人已连接并绑定你的账号。可发送测试通知确认手机收件。" : item.state === "unbound" || (connected && !item.bound) ? "连接已建立，等待你在机器人私聊中发送绑定命令。" : item.state === "connecting" || item.state === "reconnecting" ? "正在连接平台。连接成功后，继续完成事件订阅与私聊绑定。" : !item.enabled && item.secret_configured ? "已断开；凭证保留，点击重新连接即可恢复。" : !item.secret_configured ? "先按下面的步骤创建机器人，再填写凭证。" : "请按平台返回的原因检查配置，然后重新连接。";
      host.querySelector("[data-connection-state]").innerHTML = `<div class="cs-card-heading"><div><h2>${esc(names[selected])} · ${esc(statusLabel(item))}</h2><p>${esc(item.detail || detail)}</p></div><span class="cs-badge ${ready(item) ? "cs-ready" : ""}">${ready(item) ? "可以收发" : "接入进度"}</span></div>${item.error ? `<p class="cs-warning">${esc(item.error)}</p>` : ""}`;
      const step = !item.secret_configured ? 1 : !connected ? 2 : !item.bound ? 3 : 4;
      host.querySelector("[data-connection-steps]").innerHTML = ["创建机器人", "填写凭证", "私聊绑定", "开始使用"].map((title, index) => `<li class="${index + 1 < step ? "complete" : index + 1 === step ? "current" : ""}" ${index + 1 === step ? 'aria-current="step"' : ""}><span>${index + 1 < step ? "✓" : index + 1}</span>${title}</li>`).join("");
      const binding = host.querySelector("[data-connection-binding]");
      const nextBindingKey = JSON.stringify([selected, item.bound, item.binding_code, confirming]);
      if (bindingKey !== nextBindingKey) {
        binding.innerHTML = item.bound ? `<div class="cs-card-heading"><div><h3>你的账号已绑定</h3><p>私聊发送 <code>/projects</code> 查看项目，再用 <code>/project 项目名</code> 和 <code>/leader 名字</code> 选择对话。<code>/where</code> 可确认消息发给谁。</p></div><button type="button" class="cs-button" data-connection-action="reset-binding" ${busy ? "disabled" : ""}>重新绑定</button></div>${confirming ? '<p class="cs-warning">重新绑定后，当前账号将不能再控制此渠道。请在自己的机器人私聊中发送新绑定码。</p><div class="cs-connection-actions"><button type="button" class="cs-button" data-connection-action="cancel-binding">保留当前绑定</button><button type="button" class="cs-button" data-connection-action="confirm-binding">确认重新绑定</button></div>' : ""}` : item.binding_code ? `<h3>用你自己的账号私聊机器人</h3><p class="cs-hint">发送以下完整命令，收到“已绑定”回执后，本页会自动更新。请保管好绑定码。</p><div class="cs-binding-command"><input data-binding-command value="/bind ${esc(item.binding_code)}" readonly aria-label="绑定命令" spellcheck="false"><button type="button" class="cs-button" data-connection-action="copy-binding">复制命令</button></div><p class="cs-hint">若没有回执：${selected === "qq" ? "检查 QQ 测试范围、单聊事件接收和允许主动发送。" : "检查应用已发布、你在可用范围内、长连接与接收消息事件已保存。"}</p>` : '<h3>连接后绑定你的账号</h3><p class="cs-hint">保存并连接后，这里会显示绑定命令。用你自己的账号私聊机器人完成验证。</p>';
        bindingKey = nextBindingKey;
      }
      const away = snapshot.notification;
      const awayButton = host.querySelector("[data-away]");
      awayButton.textContent = away.manual_away ? "回到电脑前" : "设为离开";
      host.querySelector("[data-presence-label]").textContent = away.away ? "离开中" : "电脑前";
      for (const button of host.querySelectorAll("[data-connection-action], [data-away], [data-channel-select]")) {
        button.disabled = busy || (button.dataset.connectionAction === "test" && !ready(item)) || (["reconnect", "disconnect"].includes(button.dataset.connectionAction) && !item.secret_configured) || (button.dataset.connectionAction === "disconnect" && !item.enabled);
      }
      for (const field of host.querySelectorAll("[data-connection-field]")) field.disabled = busy;
      setNote();
    }
    function render() {
      bindingKey = null;
      const item = channel(), draft = draftFor(item);
      host.innerHTML = `<div class="cs-connections"><p class="cs-connection-intro">连接常用聊天工具，在手机上继续和 leader 对话、答复拍板、接收交付。QQ 与 Lark / 飞书可同时启用。</p><div class="cs-channel-picker" role="group" aria-label="选择消息渠道">${snapshot.channels.map(entry => `<button type="button" data-channel-select="${entry.id}" aria-pressed="${entry.id === selected}"><span class="cs-channel-logo" aria-hidden="true">${entry.id === "qq" ? "Q" : "L"}</span><span><strong>${esc(names[entry.id])}</strong><small>${entry.id === "qq" ? "通过 QQ 机器人私聊" : "通过企业自建机器人私聊"}</small></span><span class="cs-badge" data-channel-badge="${entry.id}"></span></button>`).join("")}</div><ol class="cs-connection-steps" aria-label="连接步骤" data-connection-steps></ol><section class="cs-card" data-connection-state aria-live="polite"></section><details class="cs-card cs-connection-preparation" ${item.secret_configured ? "" : "open"}><summary><h2>1. 准备机器人</h2><span>查看接入步骤</span></summary><div data-connection-guide>${guide(selected, draft.domain)}</div></details><section class="cs-card"><h2>2. 填写凭证并连接</h2><form data-connection-form><div class="cs-fields cs-connection-fields">${selected === "lark" ? `<label>平台<select data-connection-field="domain"><option value="feishu" ${draft.domain === "feishu" ? "selected" : ""}>飞书 · 中国版</option><option value="lark" ${draft.domain === "lark" ? "selected" : ""}>Lark · 国际版</option></select></label>` : ""}<label>App ID<input data-connection-field="app_id" value="${esc(draft.app_id)}" placeholder="${selected === "qq" ? "机器人 AppID" : "cli_…"}" required autocomplete="off" spellcheck="false"></label><label>App Secret<span class="cs-secret-input"><input type="password" data-connection-field="app_secret" value="${esc(draft.app_secret)}" placeholder="${item.secret_configured ? "已保存；留空保留现有 Secret" : "粘贴机器人 AppSecret"}" ${item.secret_configured ? "" : "required"} autocomplete="new-password" spellcheck="false"><button type="button" data-connection-action="toggle-secret" aria-label="显示 App Secret">显示</button></span></label></div><p class="cs-hint">凭证保存在本机 Crew 数据目录中，保存后立即生效。已保存的 Secret 不会回显。</p><div class="cs-connection-actions"><button type="submit" class="cs-button cs-primary" data-connection-action="save">保存并连接</button><button type="button" class="cs-button" data-connection-action="reconnect">重新连接</button><button type="button" class="cs-button" data-connection-action="disconnect">断开连接</button></div></form></section><section class="cs-card"><h2>3. 绑定与验证</h2><div class="cs-connection-binding" data-connection-binding></div><div class="cs-connection-actions"><button type="button" class="cs-button" data-connection-action="test">发送测试通知</button><button type="button" class="cs-button" data-connection-action="refresh">刷新状态</button></div><p class="cs-hint">“已连接”表示平台连接建立；绑定账号后可测试通知。在聊天工具中看到测试消息，才说明手机收件正常。</p></section><section class="cs-card"><div class="cs-card-heading"><div><h2>离开电脑后的提醒</h2><p>离开时，待拍板与最终交付会发到已连接并绑定的渠道。机器人私聊始终可用。</p></div><span class="cs-badge" data-presence-label></span></div><div class="cs-tool-row"><div><strong>我已离开</strong><p>手动切换离开状态；自动锁屏规则继续生效。</p></div><button type="button" class="cs-button" data-away></button></div>${snapshot.notification.error ? `<p class="cs-warning">${esc(snapshot.notification.error)}</p>` : ""}</section><p class="cs-status cs-connection-note" role="status" data-connection-note></p></div>`;
      renderState();
    }
    async function read(initial = false) {
      if (!active || reading || busy) { schedule(); return; }
      reading = true;
      const request = generation;
      try {
        const value = await options.read();
        if (!active || request !== generation) return;
        if (error.startsWith("读取连接状态失败：")) error = "";
        snapshot = value;
        if (initial || !host.querySelector("[data-connection-form]")) render(); else renderState();
      } catch (caught) {
        if (!active || request !== generation) return;
        error = "读取连接状态失败：" + caught.message;
        if (!snapshot) host.innerHTML = '<section class="cs-card"><h2>连接状态暂时无法读取</h2><p class="cs-warning" data-connection-note role="status"></p><button type="button" class="cs-button" data-connection-action="refresh">重新读取</button></section>';
        setNote();
      } finally { reading = false; schedule(); }
    }
    async function perform(action, payload) {
      if (busy) return;
      busy = true; generation++; error = ""; note = action === "configure" ? "正在保存并连接…" : "正在处理…"; renderState();
      try {
        const value = await (action === "configure" ? options.configure(payload) : action === "away" ? options.away(payload) : options.action({channel:selected, action}));
        if (!active) return;
        if (action === "away") snapshot.notification = value.notification || value;
        else snapshot = value;
        if (action === "configure") {
          const draft = drafts.get(selected); draft.app_secret = "";
          const input = host.querySelector('[data-connection-field="app_secret"]'); input.value = ""; input.type = "password"; input.required = false; input.placeholder = "已保存；留空保留现有 Secret";
          const toggle = host.querySelector('[data-connection-action="toggle-secret"]'); toggle.textContent = "显示"; toggle.setAttribute("aria-label", "显示 App Secret");
          host.querySelector(".cs-connection-preparation").open = false;
        }
        confirming = false;
        note = action === "test" ? "平台已接受测试通知，请在机器人私聊中确认收到。" : action === "disconnect" ? "连接已断开，凭证已保留。" : action === "reset-binding" ? "已生成新绑定命令，请在自己的机器人私聊中发送。" : action === "configure" ? "凭证已保存，正在连接。请继续完成平台配置与私聊绑定。" : action === "reconnect" ? "已重新发起连接。" : "离开状态已更新。";
      } catch (caught) { if (active) error = caught.message; }
      finally { busy = false; if (active) { renderState(); schedule(); if (action === "configure" && !error) host.querySelector("[data-connection-binding]").scrollIntoView({block:"center", behavior:"smooth"}); } }
    }
    function onInput(event) {
      const field = event.target.dataset.connectionField;
      if (!field || !snapshot) return;
      draftFor(channel())[field] = event.target.value;
      if (field === "domain") host.querySelector("[data-connection-guide]").innerHTML = guide(selected, event.target.value);
    }
    function onSubmit(event) {
      if (!event.target.matches("[data-connection-form]")) return;
      event.preventDefault(); if (busy) return;
      const item = channel(), draft = draftFor(item), body = {channel:selected, app_id:draft.app_id.trim(), enabled:true};
      if (!body.app_id || (!item.secret_configured && !draft.app_secret.trim())) { error = "请填写 App ID 和 App Secret。"; setNote(); return; }
      if (draft.app_secret.trim()) body.app_secret = draft.app_secret.trim();
      if (selected === "lark") body.domain = draft.domain;
      void perform("configure", body);
    }
    async function copyBinding() {
      const input = host.querySelector("[data-binding-command]"), command = input.value;
      try {
        if (!navigator.clipboard?.writeText) throw new Error("当前页面无法使用剪贴板");
        await navigator.clipboard.writeText(command);
        if (active && input.isConnected) { note = "绑定命令已复制，请粘贴到机器人私聊中。"; error = ""; setNote(); }
      } catch (caught) {
        if (active && input.isConnected) {
          input.focus(); input.select();
          error = "复制失败：" + caught.message + "。命令已选中，请按 Ctrl+C 复制，再粘贴到机器人私聊中。";
          setNote();
        }
      }
    }
    function onClick(event) {
      const button = event.target.closest("button"); if (!button || !host.contains(button) || button.disabled) return;
      if (button.dataset.channelSelect) { selected = button.dataset.channelSelect; confirming = false; error = ""; note = ""; render(); return; }
      const action = button.dataset.connectionAction;
      if (action === "refresh") { error = ""; note = ""; void read(!snapshot); }
      else if (action === "toggle-secret") { const input = host.querySelector('[data-connection-field="app_secret"]'); input.type = input.type === "password" ? "text" : "password"; button.textContent = input.type === "password" ? "显示" : "隐藏"; button.setAttribute("aria-label", button.textContent + " App Secret"); }
      else if (action === "reset-binding" || action === "cancel-binding") { confirming = action === "reset-binding"; renderState(); }
      else if (action === "confirm-binding") void perform("reset-binding");
      else if (action === "copy-binding") void copyBinding();
      else if (["reconnect", "disconnect", "test"].includes(action)) void perform(action);
      else if (button.hasAttribute("data-away")) void perform("away", !snapshot.notification.manual_away);
    }
    host.innerHTML = '<section class="cs-card"><h2>正在读取消息渠道…</h2></section>';
    host.addEventListener("input", onInput); host.addEventListener("change", onInput); host.addEventListener("submit", onSubmit); host.addEventListener("click", onClick);
    void read(true);
    return () => { active = false; clearTimeout(timer); drafts.clear(); host.removeEventListener("input", onInput); host.removeEventListener("change", onInput); host.removeEventListener("submit", onSubmit); host.removeEventListener("click", onClick); };
  }
  window.CrewConnections = {mount, ready, statusLabel, guide};
})();
