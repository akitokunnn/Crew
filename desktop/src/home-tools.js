'use strict';
(() => {
  const $ = id => document.getElementById(id), bridge = window.crewDesktop;
  const panel = $('tools-page'), workspace = $('workspaces-page');
  let snapshot = null, request = null, setting = false, settingNote = '', globalError = '';
  const supported = typeof bridge?.toolsSnapshot === 'function';
  function text(value) { return typeof value === 'string' ? value : value?.message || ''; }
  function node(tag, className, value) {
    const element = document.createElement(tag); if (className) element.className = className;
    if (value !== undefined) element.textContent = value; return element;
  }
  function select(tools) {
    if (tools && !supported) return;
    panel.hidden = !tools; workspace.hidden = tools;
    for (const [id, active] of [['nav-workspaces', !tools], ['nav-tools', tools]]) {
      $(id).classList.toggle('active', active);
      if (active) $(id).setAttribute('aria-current', 'page'); else $(id).removeAttribute('aria-current');
    }
  }
  function phase(tool) {
    const labels = {checking:'检查中', queued:'等待空闲', updating:'正在更新'};
    if (labels[tool.phase]) return labels[tool.phase];
    if (tool.error) return tool.phase === 'error' ? '操作失败' : '检查失败';
    if (!tool.installed_version) return '未检测到';
    if (tool.update_available) return tool.can_update ? '有更新' : tool.can_queue ? '会话使用中' : '需手动更新';
    if (tool.phase === 'done') return '已更新';
    return tool.latest_version ? '已是最新' : '已安装';
  }
  function installation(tool) {
    const value = typeof tool.installation === 'string' ? tool.installation : tool.installation?.label || tool.installation?.kind || '';
    return {npm:'npm', native:'官方安装', unknown:'未识别', missing:'未安装'}[value] || value;
  }
  function render() {
    const busy = Boolean(snapshot?.checking || request === 'check');
    $('tools-check').disabled = busy || request !== null;
    $('tools-check').textContent = busy ? '正在检查…' : '检查更新';
    $('tools-auto').checked = Boolean(snapshot?.autoUpdate);
    $('tools-auto').disabled = !snapshot || setting;
    $('tools-setting-status').textContent = setting ? '正在保存…' : settingNote;
    $('tools-global-error').hidden = !globalError && !snapshot?.lastError;
    $('tools-global-error').textContent = globalError || text(snapshot?.lastError);
    let checked = '';
    if (snapshot?.checked_at) {
      const time = new Date(snapshot.checked_at);
      if (!Number.isNaN(time.valueOf())) checked = '上次检查：' + time.toLocaleString('zh-CN', {month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit'});
    }
    $('tools-check-status').textContent = busy ? '正在检查本机工具和最新版本…' : checked || (snapshot ? '还没有检查更新。' : '正在读取工具版本…');
    $('tools-list').replaceChildren();
    for (const tool of snapshot?.tools || []) {
      const card = node('article', 'tool-card'); card.dataset.toolId = tool.id;
      const header = node('div', 'tool-card-header'); header.append(node('h2', '', tool.name || tool.id));
      const badge = node('span', 'tool-phase', phase(tool)); badge.dataset.phase = ['checking', 'queued', 'updating'].includes(tool.phase) ? tool.phase : tool.error ? 'error' : 'idle'; header.append(badge);
      const versions = node('dl', 'tool-versions');
      for (const [label, value] of [['当前版本', tool.installed_version || '未检测到'], ['最新版本', tool.latest_version || '未获取']]) {
        const part = node('div'); part.append(node('dt', '', label), node('dd', '', value)); versions.append(part);
      }
      card.append(header, versions);
      const method = installation(tool); if (method) card.append(node('p', 'tool-installation', '安装方式：' + method));
      const footer = node('div', 'tool-card-footer'), messages = node('div', 'tool-messages'), actions = node('div', 'tool-card-actions');
      const distinct = new Set();
      for (const [value, error] of [[tool.message, false], [tool.blocked_reason, false], [tool.manual_update, false], [tool.error, true]]) {
        const message = text(value); if (!message || distinct.has(message)) continue; distinct.add(message);
        messages.append(node('p', 'tool-message' + (error ? ' error' : ''), message));
      }
      const update = node('button', 'button secondary', tool.phase === 'updating' ? '正在更新…' : tool.phase === 'queued' ? '已排队' : !tool.can_update && tool.can_queue ? '等待会话结束后更新' : '更新');
      update.type = 'button'; update.dataset.toolUpdate = tool.id;
      update.disabled = busy || request !== null || !(tool.can_update || tool.can_queue) || !tool.update_available || ['updating', 'queued', 'checking'].includes(tool.phase);
      if (tool.blocked_reason) update.title = text(tool.blocked_reason);
      update.addEventListener('click', () => { void operation(tool.id, () => bridge.toolsUpdate(tool.id)); }); actions.append(update);
      if (tool.phase === 'queued') {
        const cancel = node('button', 'button secondary', '取消等待'); cancel.type = 'button'; cancel.dataset.toolCancel = tool.id; cancel.disabled = request !== null;
        cancel.addEventListener('click', () => { void operation(tool.id, () => bridge.toolsCancel(tool.id)); }); actions.append(cancel);
      }
      footer.append(messages, actions); card.append(footer); $('tools-list').append(card);
    }
  }
  function consume(value) {
    if (!value || !Array.isArray(value.tools)) throw new Error('工具状态暂时不可用，请重新检查。');
    snapshot = value; render();
  }
  async function operation(id, callback) {
    if (request !== null) return;
    request = id; globalError = ''; render();
    try { consume(await callback()); }
    catch (error) { globalError = error.message || String(error); }
    finally { request = null; render(); }
  }
  async function read() { await operation('read', () => bridge.toolsSnapshot()); }
  function show() {
    select(true); if (!supported) return;
    $('tools-title').focus(); void read();
  }
  $('nav-workspaces').addEventListener('click', () => select(false));
  $('nav-tools').addEventListener('click', show);
  $('tools-check').addEventListener('click', () => { void operation('check', () => bridge.toolsCheck()); });
  $('tools-auto').addEventListener('change', async () => {
    if (setting) return;
    const enabled = $('tools-auto').checked; setting = true; settingNote = ''; globalError = ''; render();
    try { consume(await bridge.toolsSettings({autoUpdate:enabled})); settingNote = enabled ? '已开启' : '已关闭'; }
    catch (error) { globalError = error.message || String(error); }
    finally { setting = false; render(); }
  });
  window.CrewHomeTools = Object.freeze({show, hide:() => select(false)});
  if (supported) {
    $('nav-tools').hidden = false;
    if (typeof bridge.onToolUpdates === 'function') bridge.onToolUpdates(value => { try { consume(value); } catch (error) { globalError = error.message; render(); } });
    void read();
  }
})();
