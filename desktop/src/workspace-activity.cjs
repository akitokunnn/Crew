'use strict';
const count = value => Number.isSafeInteger(value) && value >= 0 ? value : 0;
const short = value => String(value ?? '').slice(0, 500);
function presentation(snapshot, stale = false, error = '') {
  if (!snapshot) return {tone:stale?'stale':'unknown',label:stale?'暂时无法读取项目动态':'正在读取项目动态',badge:0,unread:0,items:[],stale,error:short(error)};
  const counts = Object.fromEntries(['working','decisions','todos','approvals','waiting','acceptance','unread','errors'].map(key=>[key,count(snapshot.counts?.[key])]));
  const attention = counts.decisions + counts.todos + counts.approvals + counts.waiting;
  const tone = stale ? 'stale' : attention ? 'attention' : counts.errors ? 'error' : counts.acceptance ? 'delivery' : counts.working ? 'working' : counts.unread ? 'unread' : snapshot.unknown ? 'unknown' : 'idle';
  const label = stale ? '项目状态暂未更新' : attention ? `${attention} 项等你处理` : counts.errors ? `${counts.errors} 项执行异常` : counts.acceptance ? `${counts.acceptance} 个目标待验收` : counts.working ? `${counts.working} 个会话在推进` : counts.unread ? `${counts.unread} 条未读动态` : snapshot.unknown ? '部分运行状态未确认' : '当前没有运行中的会话';
  return {tone,label,badge:attention || counts.errors,unread:counts.unread,counts,stale,error:short(error),
    updatedAt:snapshot.updated_at,unknown:Boolean(snapshot.unknown),
    items:Array.isArray(snapshot.items)?snapshot.items.slice(0,5).map(item=>({kind:short(item.kind),title:short(item.title),detail:short(item.detail),name:short(item.name)})):[]};
}
module.exports = {presentation};
