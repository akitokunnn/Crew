'use strict';
(() => {
  const bridge = window.crewDesktop, root = document.getElementById('crew-update-card');
  if (!root || typeof bridge?.crewUpdateSnapshot !== 'function') return;
  root.hidden = false;
  let state = null, request = false;
  const $ = id => document.getElementById(id);
  const phases = { idle:'待检查', checking:'检查中', preparing:'准备更新', queued:'等待空闲', 'backing-up':'备份中',
    plugin:'同步插件', stopping:'停止旧后台', switching:'切换入口', starting:'连接新后台', done:'已更新', error:'未完成' };
  function render() {
    if (!state) return;
    $('crew-update-phase').textContent = phases[state.phase] || state.phase;
    $('crew-update-phase').dataset.phase = state.phase === 'error' ? 'error' : state.phase === 'idle' || state.phase === 'done' ? 'idle' : 'updating';
    $('crew-desktop-version').textContent = state.desktop_version || '未读取';
    $('crew-backend-version').textContent = state.backend?.version || '未读取';
    $('crew-target-version').textContent = state.target?.version || '未检查';
    $('crew-plugin-version').textContent = state.plugin?.state === 'not_installed' ? '未安装' : state.plugin?.registered_version || '未读取';
    const online=state.online;
    $('crew-release-version').textContent=state.online_checking?'检查中…':online?(online.release?.version||'尚无正式发布包'):state.online_error?'检查失败':'尚未检查';
    $('crew-main-version').textContent=state.online_checking?'检查中…':online?.main?.version|| (state.online_error?'检查失败':'尚未检查');
    $('crew-online-error').textContent=state.online_error?'线上检查失败：'+state.online_error:'';
    $('crew-online-error').hidden=!state.online_error;
    let onlineMessage=state.online_checking?'正在查询 GitHub 正式发布和主干版本…':'点击检查 Crew 查询线上版本。';
    if(online){
      const comparison=online.release_comparison;
      onlineMessage=comparison>0?'有新的正式发布，可打开发布页查看。':online.main_comparison<0?'当前桌面含未发布更改，版本高于线上主干。':online.main_comparison>0?'主干有更新的源码，尚未正式发布。':'当前桌面与线上主干版本一致。';
      if(!online.release)onlineMessage+=' 当前尚无正式发布包。';
      onlineMessage+=' 检查时间：'+new Date(online.checked_at).toLocaleString('zh-CN');
    }
    if(state.online_error)onlineMessage='本机检查结果仍可使用，请稍后重试线上查询。';
    $('crew-online-status').textContent=onlineMessage;

    $('crew-update-message').textContent = state.blocked_reason || state.message || '';
    $('crew-update-error').textContent = state.error || ''; $('crew-update-error').hidden = !state.error;
    $('crew-update-backup').textContent = state.backup?.path ? '备份：' + state.backup.path : '';
    const details = [['后台来源', state.backend?.plugin_root], ['命令入口', state.cli?.plugin_root],
      ['Claude 实际读取', state.plugin?.source], ['实际插件版本', state.plugin?.source_version]];
    $('crew-update-sources').replaceChildren();
    for (const [label, value] of details) {
      const line = document.createElement('p'); line.className = 'tool-message'; line.textContent = label + '：' + (value || '未检测到'); $('crew-update-sources').append(line);
    }
    const active = !['idle','queued','done','error'].includes(state.phase);
    $('crew-update-check').disabled = request || active;
    $('crew-update-apply').disabled = request || active || state.queued || !state.can_update;
    $('crew-update-apply').textContent = state.queued ? '已排队' : '统一后台与插件';
    $('crew-update-cancel').hidden = !state.queued; $('crew-update-cancel').disabled = request;
  }
  async function operation(action) {
    if (request) return;
    request = true; render();
    try { state = await action(); }
    catch (error) { state = { ...state, phase:'error', error:error.message || String(error) }; }
    finally { request = false; render(); }
  }
  function check(){void operation(() => bridge.crewUpdateCheck());}
  $('crew-update-check').addEventListener('click', check);
  $('tools-check').addEventListener('click', check);
  for(const kind of ['release','main'])$('crew-'+kind+'-open').addEventListener('click',()=>{
    void bridge.crewUpdateOpen(kind).catch(error=>{state={...state,online_error:error.message};render();});
  });
  $('crew-update-apply').addEventListener('click', () => { void operation(() => bridge.crewUpdateApply()); });
  $('crew-update-cancel').addEventListener('click', () => { void operation(() => bridge.crewUpdateCancel()); });
  bridge.onCrewUpdateState(value => { state = value; render(); });
  void operation(() => bridge.crewUpdateSnapshot());
})();
