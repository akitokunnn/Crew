'use strict';
// The Crew card on 「工具与更新」: checks GitHub's formal release, downloads and
// verifies it, then restarts into the new version (or shows the package for a
// manual replacement where Crew cannot replace itself).
(() => {
  const bridge = window.crewDesktop, root = document.getElementById('crew-self-update-card');
  if (!root || typeof bridge?.selfUpdateSnapshot !== 'function') return;
  root.hidden = false;
  const $ = id => document.getElementById(id);
  const phases = { idle:'待检查', checking:'检查中', latest:'已是最新', available:'有新版本', downloading:'下载中', verifying:'校验中',
    ready:'可以安装', installing:'安装中', error:'未完成', unsupported:'不可用' };
  const busy = new Set(['checking', 'downloading', 'verifying', 'installing']);
  let state = null, request = false;
  const megabytes = bytes => (bytes / 1048576).toFixed(bytes >= 104857600 ? 0 : 1) + ' MB';
  function actionLabel() {
    if (state.phase === 'available' && state.can_download) return '下载更新';
    if (state.phase === 'downloading') return '取消下载';
    if (state.phase !== 'ready') return '';
    if (state.restart) return '重启并更新';
    return state.kind === 'mac-manual' ? '打开 dmg' : '在文件夹中显示';
  }
  function render() {
    if (!state) return;
    const phase = $('crew-self-phase');
    phase.textContent = phases[state.phase] || state.phase;
    phase.dataset.phase = state.phase === 'error' ? 'error' : busy.has(state.phase) ? 'updating' : 'idle';
    $('crew-self-current').textContent = state.current || '未读取';
    $('crew-self-latest').textContent = state.phase === 'checking' ? '检查中…' : state.latest?.version || (state.checked_at ? '尚无正式发布' : '尚未检查');
    const progress = state.phase === 'downloading' && state.progress;
    $('crew-self-download').hidden = !progress;
    if (progress) {
      $('crew-self-progress').max = progress.total || 1; $('crew-self-progress').value = progress.received;
      $('crew-self-progress-text').textContent = megabytes(progress.received) + ' / ' + megabytes(progress.total);
    }
    const notes = state.phase !== 'latest' && state.latest?.notes?.trim();
    $('crew-self-notes').hidden = !notes; $('crew-self-notes-body').textContent = notes || '';
    $('crew-self-updated').hidden = !state.updated;
    $('crew-self-updated').textContent = state.updated ? '已从 ' + state.updated.from + ' 更新到 ' + state.updated.to + '。' : '';
    $('crew-self-message').textContent = state.updated && state.message.startsWith('已从 ') ? '' : state.message || '';
    const blocked = $('crew-self-blocked');
    blocked.replaceChildren(...(state.blocked || []).map(reason => { const item = document.createElement('li'); item.textContent = reason; return item; }));
    blocked.hidden = !state.blocked?.length;
    const manual = state.phase === 'ready' && !state.restart && state.file;
    $('crew-self-file').hidden = !manual; $('crew-self-file').textContent = manual ? '安装包：' + state.file : '';
    $('crew-self-error').hidden = !state.error; $('crew-self-error').textContent = state.error || '';
    $('crew-self-custom').hidden = !state.custom_backend;
    const label = actionLabel(), action = $('crew-self-action');
    action.hidden = !label; action.textContent = label;
    action.disabled = request && state.phase !== 'downloading' || state.phase === 'installing';
    action.classList.toggle('secondary', state.phase === 'downloading');
    $('crew-self-check').disabled = request || busy.has(state.phase) || state.phase === 'unsupported';
    $('crew-self-release').hidden = !state.latest?.url || state.phase === 'latest';
    $('crew-self-auto').checked = Boolean(state.auto_download);
    $('crew-self-auto').disabled = state.phase === 'unsupported';
  }
  async function operation(action) {
    if (request) return;
    request = true; render();
    try { state = await action(); }
    catch (error) { state = { ...state, error: error.message || String(error) }; }
    finally { request = false; render(); }
  }
  function check() { void operation(() => bridge.selfUpdateCheck()); }
  $('crew-self-check').addEventListener('click', check);
  $('tools-check').addEventListener('click', check);
  $('crew-self-action').addEventListener('click', () => {
    // Cancelling must work while the download request is still pending.
    if (state.phase === 'downloading') { void bridge.selfUpdateCancel().then(value => { state = value; render(); }); return; }
    if (state.phase === 'available') void operation(() => bridge.selfUpdateDownload());
    else if (state.phase === 'ready') void operation(() => bridge.selfUpdateInstall());
  });
  $('crew-self-release').addEventListener('click', () => {
    void bridge.selfUpdateOpen().catch(error => { state = { ...state, error: error.message }; render(); });
  });
  $('crew-self-auto').addEventListener('change', event => {
    const value = event.target.checked;
    void operation(() => bridge.selfUpdateSettings({ auto_download: value }));
  });
  bridge.onSelfUpdateState(value => { state = value; render(); });
  void operation(() => bridge.selfUpdateSnapshot());
})();
