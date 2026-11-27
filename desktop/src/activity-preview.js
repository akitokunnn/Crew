'use strict';
const $ = id => document.getElementById(id);
window.crewActivity.onState(tab => {
  const {activity} = tab;
  $('name').textContent = tab.name;
  $('mark').textContent = Array.from(tab.name)[0]?.toUpperCase() || 'C';
  $('summary').textContent = activity?.error || (activity?.stale ? '显示上次记录，状态暂未更新' : '');
  $('summary').hidden = !$('summary').textContent;
  $('items').replaceChildren();
  for (const {label, count, detail} of activity?.rows ?? []) {
    const row = document.createElement('div');
    row.className = 'activity-row';
    const title = document.createElement('dt');
    title.textContent = count === null ? label : `${label}（${count}）`;
    const description = document.createElement('dd');
    if (detail.length === 0) description.textContent = count === null ? '状态未确认' : '—';
    for (const line of detail) {
      const text = document.createElement('span');
      text.textContent = line;
      description.append(text);
    }
    row.append(title, description);
    $('items').append(row);
  }
  $('updated').textContent = activity?.stale ? '显示上次记录' :
    tab.readOnly ? '已保存的只读历史' : activity?.updatedAt ? '刚刚更新' : '正在读取动态';
  $('open').onclick = () => window.crewActivity.open(tab.id);
});
document.addEventListener('mouseenter', () => window.crewActivity.hover(true));
document.addEventListener('mouseleave', () => window.crewActivity.hover(false));
document.addEventListener('keydown', event => { if (event.key === 'Escape') window.crewActivity.hover(false); });
