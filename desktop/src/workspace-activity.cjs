'use strict';
const STALE_MS = 15000;
const attentionKinds = {
  acceptance:['acceptance','待验收'], decisions:['decision','拍板'], todos:['todo','待动手'],
  approvals:['approval','权限确认'], waiting:['waiting','等你回复']
};

function presentation(snapshot, failed = false, error = '', now = Date.now()) {
  const stale = failed || Boolean(snapshot && now - Date.parse(snapshot.updated_at) > STALE_MS);
  const counts = snapshot?.counts;
  // Optional room activity from the workspace snapshot. The task counts
  // already describe ordinary sessions, so room states contribute separately.
  const rooms = snapshot?.war_rooms ?? [];
  const runningRooms = rooms.filter(room => room.status === 'running');
  const waitingRooms = rooms.filter(room => room.status === 'awaiting_decision');
  const attention = (counts?.attention ?? 0) + waitingRooms.length;
  const errors = counts?.errors ?? 0;
  const unread = counts ? counts.chat_unread + counts.channel_unread : 0;
  const working = !stale && ((counts?.working ?? 0) > 0 || runningRooms.length > 0);
  const marker = errors ? 'error' : attention ? 'attention' : unread ? 'unread'
    : stale || !snapshot || snapshot.unknown ? 'unknown' : null;
  const label = [
    errors && `${errors} 项执行异常`, attention && `${attention} 项等你处理`, unread && `${unread} 条未读`,
    working && '推进中', stale && '工作区状态暂未更新',
    !stale && (!snapshot ? '正在读取工作区动态' : snapshot.unknown && '部分运行状态未确认')
  ].filter(Boolean).join('，');

  const items = snapshot?.items ?? [];
  const waiting = counts ? Object.entries(attentionKinds).filter(([key]) => counts[key] > 0)
    .map(([key, [, name]]) => `${name} ${counts[key]}`) : [];
  if (waitingRooms.length) waiting.push(`等你定 ${waitingRooms.length}`);
  const titles = items.filter(item => Object.values(attentionKinds).some(([kind]) => kind === item.kind))
    .slice(0, 2).map(item => item.title);
  const failures = items.filter(item => item.kind === 'error').slice(0, 2)
    .map(item => [item.name, item.detail].filter(Boolean).join(' · '));
  if (errors > failures.length) failures.push(`另有 ${errors - failures.length} 项`);
  const progress = [];
  if (working && counts.working > 0) {
    progress.push(`${counts.working_leaders} 位 Leader 在推进`);
    const others = counts.working - counts.working_leaders;
    if (others > 0) progress.push(`另有 ${others} 个会话在推进`);
  }
  if (!stale) {
    progress.push(...runningRooms.map(room => `${room.title} · 作战室中`));
    progress.push(...waitingRooms.map(room => `${room.title} · 等你定`));
  }
  const rows = [
    {label:'等你处理', count:counts ? attention : null, detail:[waiting.join(' · '), ...titles].filter(Boolean)},
    {label:'执行异常', count:counts ? errors : null, detail:failures},
    ...(!stale ? [{label:'正在推进', count:counts && !snapshot.unknown ? counts.working + runningRooms.length : null, detail:progress}] : []),
    {label:'未读', count:counts ? unread : null, detail:counts ? [`对话 ${counts.chat_unread} 条 · 交流区 ${counts.channel_unread} 条`] : []}
  ];
  return {marker, markerCount:errors || attention, working, label, rows, stale, error, updatedAt:snapshot?.updated_at};
}
module.exports = {presentation};
