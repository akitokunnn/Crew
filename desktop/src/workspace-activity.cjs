'use strict';
const STALE_MS = 15000;
const attentionKinds = {
  acceptance:['acceptance','待验收'], decisions:['decision','拍板'], todos:['todo','待动手'],
  approvals:['approval','权限确认'], waiting:['waiting','等你回复']
};

function presentation(snapshot, failed = false, error = '', now = Date.now()) {
  if (snapshot == null) {
    const labels = failed ? ['等你处理', '执行异常', '未读'] : ['等你处理', '执行异常', '正在推进', '未读'];
    return {marker:'unknown', markerCount:null, working:false,
      label:failed ? '工作区状态暂未更新' : '正在读取工作区动态',
      rows:labels.map(label => ({label, count:null, detail:[]})), stale:failed, error, updatedAt:undefined};
  }
  const {counts, items, war_rooms:rooms, unknown, updated_at:updatedAt} = snapshot;
  const stale = failed || now - Date.parse(updatedAt) > STALE_MS;
  const runningRooms = rooms.filter(room => room.status === 'running');
  const waitingRooms = rooms.filter(room => room.status === 'awaiting_decision');
  const attention = counts.attention + waitingRooms.length;
  const errors = counts.errors;
  const unread = counts.chat_unread + counts.channel_unread;
  const working = !stale && (counts.working > 0 || runningRooms.length > 0);
  const marker = errors ? 'error' : attention ? 'attention' : unread ? 'unread'
    : stale || unknown ? 'unknown' : null;
  const label = [
    errors && `${errors} 项执行异常`, attention && `${attention} 项等你处理`, unread && `${unread} 条未读`,
    working && '推进中', stale && '工作区状态暂未更新',
    !stale && unknown && '部分运行状态未确认'
  ].filter(Boolean).join('，');

  const waiting = Object.entries(attentionKinds).filter(([key]) => counts[key] > 0)
    .map(([key, [, name]]) => `${name} ${counts[key]}`);
  if (waitingRooms.length) waiting.push(`等你定 ${waitingRooms.length}`);
  const titles = [...waitingRooms.map(room => `${room.title} · 作战室等你定`),
    ...items.filter(item => Object.values(attentionKinds).some(([kind]) => kind === item.kind)).map(item => item.title)]
    .slice(0, 2);
  const failures = items.filter(item => item.kind === 'error').slice(0, 2)
    .map(item => [item.name, item.detail].filter(Boolean).join(' · '));
  if (errors > failures.length) failures.push(`另有 ${errors - failures.length} 项`);
  const progress = [];
  if (working && counts.working > 0) {
    progress.push(`${counts.working_leaders} 位 Leader 在推进`);
    const others = counts.working - counts.working_leaders;
    if (others > 0) progress.push(`另有 ${others} 个会话在推进`);
  }
  if (!stale) progress.push(...runningRooms.map(room => `${room.title} · 作战室中`));
  const rows = [
    {label:'等你处理', count:attention, detail:[waiting.join(' · '), ...titles].filter(Boolean)},
    {label:'执行异常', count:errors, detail:failures},
    ...(!stale ? [{label:'正在推进', count:unknown ? null : counts.working + runningRooms.length, detail:progress}] : []),
    {label:'未读', count:unread, detail:[`对话 ${counts.chat_unread} 条 · 交流区 ${counts.channel_unread} 条`]}
  ];
  return {marker, markerCount:errors || attention, working, label, rows, stale, error, updatedAt};
}
module.exports = {presentation};
