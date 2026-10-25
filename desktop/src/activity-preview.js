'use strict';
const $=id=>document.getElementById(id);
window.crewActivity.onState(tab=>{
  const activity=tab.activity||{};
  $('name').textContent=tab.name;$('mark').textContent=Array.from(tab.name)[0]?.toUpperCase()||'C';
  $('signal').dataset.tone=activity.tone||'unknown';$('summary').textContent=activity.error||activity.label||'正在读取项目动态';
  $('items').replaceChildren();
  for(const item of activity.items||[]){
    const row=document.createElement('div');row.className='item';row.dataset.kind=item.kind;
    const icon=document.createElement('span');icon.className='item-icon';icon.textContent=({decision:'?',todo:'↗',approval:'◇',working:'·',delivery:'✓',reply:'↩',error:'!'})[item.kind]||'·';
    const text=document.createElement('div');text.className='item-text';const title=document.createElement('p');title.className='item-title';title.textContent=item.title;
    const meta=document.createElement('p');meta.className='item-meta';meta.textContent=[item.name,item.detail].filter(Boolean).join(' · ');text.append(title,meta);row.append(icon,text);$('items').append(row);
  }
  $('updated').textContent=activity.stale?'显示上次记录，正在重新连接':activity.updatedAt?'刚刚更新 · 仅展示已有记录':'正在获取状态';
  $('open').onclick=()=>window.crewActivity.open(tab.id);
});
document.addEventListener('mouseenter',()=>window.crewActivity.hover(true));
document.addEventListener('mouseleave',()=>window.crewActivity.hover(false));
document.addEventListener('keydown',event=>{if(event.key==='Escape')window.crewActivity.hover(false);});
