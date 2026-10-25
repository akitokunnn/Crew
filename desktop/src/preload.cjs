'use strict';
const { contextBridge, ipcRenderer } = require('electron');
let settings;
try { const arg=process.argv.find(value=>value.startsWith('--crew-desktop-config=')); settings=JSON.parse(decodeURIComponent(arg.slice('--crew-desktop-config='.length))); } catch(_){settings=null;}
const isHome=settings&&location.href===settings.home, isError=settings&&location.href===settings.error;
const trusted=process.isMainFrame&&(isHome||isError);
let readyResolve,backButton,titleNode,homePane,viewArea,tabsBar,recovery,lastBounds='';
const chromeReady=new Promise(resolve=>{readyResolve=resolve;});
function windowState(state){
 document.documentElement.classList.toggle('crew-desktop-maximized',Boolean(state.maximized));
 document.documentElement.classList.toggle('crew-desktop-inactive',!state.focused);
 if(backButton)backButton.disabled=!state.canGoBack;
}
function icon(paths){
 const element=document.createElementNS('http://www.w3.org/2000/svg','svg');element.setAttribute('viewBox','0 0 20 20');element.setAttribute('aria-hidden','true');
 for(const value of paths){const line=document.createElementNS('http://www.w3.org/2000/svg','path');line.setAttribute('d',value);element.append(line);}return element;
}
function reportError(error){if(!recovery)return;recovery.querySelector('p').textContent=error?.message||'这个操作暂时没有完成，请稍后重试。';recovery.hidden=false;}
async function invoke(command,value){try{return await ipcRenderer.invoke('desktop:chrome-action',command,value);}catch(error){reportError(error);}}
function button(label,paths,command,className=''){
 const element=document.createElement('button');element.type='button';element.className=className;element.setAttribute('aria-label',label);element.title=label;element.append(icon(paths));
 element.addEventListener('click',async()=>{if(command==='menu')element.setAttribute('aria-expanded','true');try{await invoke(command);}finally{if(command==='menu')element.setAttribute('aria-expanded','false');}});return element;
}
function layoutViews(){
 if(!isHome||!viewArea)return;const rect=viewArea.getBoundingClientRect();
 const bounds={x:Math.ceil(rect.x),y:Math.ceil(rect.y),width:Math.max(1,Math.floor(rect.width)),height:Math.max(1,Math.floor(rect.height))};
 const encoded=JSON.stringify(bounds);if(encoded!==lastBounds){lastBounds=encoded;ipcRenderer.send('desktop:tabs-layout',bounds);}
}
function tabsState(state){
 if(!tabsBar||!Array.isArray(state.tabs))return;
 const focused=document.hasFocus()&&document.activeElement?.dataset.tabId;tabsBar.replaceChildren();
 for(const tab of state.tabs){
  const item=document.createElement('div');item.className='crew-tab';item.dataset.active=String(state.activeId===tab.id);
  const select=document.createElement('button');select.type='button';select.dataset.tabId=tab.id;if(tab.project)select.dataset.project=tab.project;
  select.setAttribute('role','tab');select.setAttribute('aria-selected',String(state.activeId===tab.id));select.tabIndex=state.activeId===tab.id?0:-1;select.title=tab.name;
  select.textContent=(tab.loading?'◌ ':tab.error?'⚠ ':'')+tab.name;select.addEventListener('click',()=>{void invoke('tab-select',tab.id);});
  select.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const index=state.tabs.findIndex(candidate=>candidate.id===tab.id);const next=event.key==='Home'?0:event.key==='End'?state.tabs.length-1:(index+(event.key==='ArrowLeft'?-1:1)+state.tabs.length)%state.tabs.length;void invoke('tab-select',state.tabs[next].id);}});
  item.append(select);
  if(tab.project){const close=document.createElement('button');close.type='button';close.className='crew-tab-close';close.dataset.closeTabId=tab.id;close.setAttribute('aria-label','关闭 '+tab.name);close.title='关闭标签';close.append(icon(['m6 6 8 8','m14 6-8 8']));close.addEventListener('click',()=>{void invoke('tab-close',tab.id);});item.append(close);}
  tabsBar.append(item);
 }
 tabsBar.append(button('打开工作区标签 · Ctrl+T',['M10 4v12','M4 10h12'],'home','crew-tab-add'));
 const active=state.tabs.find(tab=>tab.id===state.activeId);homePane.hidden=state.activeId!=='home';viewArea.classList.toggle('crew-view-active',state.activeId!=='home');
 recovery.hidden=!active?.error;if(active?.error)recovery.querySelector('p').textContent='暂时无法打开这个工作区，请检查连接后重试。其他标签仍然保留。';
 titleNode.textContent=active?.project ? active.name : '工作区';
 const activeButton=tabsBar.querySelector('[data-tab-id="'+CSS.escape(state.activeId)+'"]');
 activeButton?.scrollIntoView({block:'nearest',inline:'nearest'});if(focused)activeButton?.focus();layoutViews();
}
async function mountChrome(){
 if(!trusted||document.getElementById('crew-desktop-titlebar')){readyResolve();return;}
 document.documentElement.classList.add('crew-desktop-shell');document.documentElement.dataset.crewDesktopPage=isHome?'home':'error';
 const content=document.createElement('div');content.id='crew-desktop-content';homePane=document.createElement('div');homePane.id='crew-desktop-home';while(document.body.firstChild)homePane.append(document.body.firstChild);
 const bar=document.createElement('header');bar.id='crew-desktop-titlebar';bar.setAttribute('aria-label','Crew 桌面窗口');const safe=document.createElement('div');safe.className='crew-chrome-safe';
 const brand=button('工作区首页 · Ctrl+T',['M14.8 5.2a6.3 6.3 0 1 0 0 9.6','M14.4 13.6h.1'],'home','crew-chrome-brand');const name=document.createElement('span');name.textContent='Crew';brand.append(name);safe.append(brand);
 backButton=button('返回上一页 · Alt+左方向键',['m11.7 4.6-5.4 5.4 5.4 5.4','M6.7 10h9'],'back');backButton.disabled=true;safe.append(backButton);
 const divider=document.createElement('span');divider.className='crew-chrome-divider';divider.setAttribute('aria-hidden','true');safe.append(divider);titleNode=document.createElement('span');titleNode.className='crew-chrome-title';titleNode.textContent=isHome?'工作区':'连接未完成';safe.append(titleNode);
 const spacer=document.createElement('span');spacer.className='crew-chrome-space';safe.append(spacer);safe.append(button('设置',['M4 5h12','M4 10h12','M4 15h12','M7 3v4','M13 8v4','M8 13v4'],'settings','crew-chrome-settings'));const admin=button('系统管理员',['M10 2.5 4 5v5c0 3 3 5.5 6 7 3-1.5 6-4 6-7V5l-6-2.5Z','m7 10 2 2 4-4'],'admin','crew-chrome-admin');const adminLabel=document.createElement('span');adminLabel.textContent='系统管理员';admin.append(adminLabel);safe.append(admin);const menu=button('应用菜单',['M4 10h.01','M10 10h.01','M16 10h.01'],'menu','crew-chrome-menu');menu.setAttribute('aria-haspopup','menu');menu.setAttribute('aria-expanded','false');safe.append(menu);bar.append(safe);
 tabsBar=document.createElement('nav');tabsBar.id='crew-desktop-tabs';tabsBar.setAttribute('aria-label','工作区标签');tabsBar.setAttribute('role','tablist');viewArea=document.createElement('div');viewArea.id='crew-workspace-view-area';
 recovery=document.createElement('section');recovery.id='crew-tab-recovery';recovery.hidden=true;recovery.setAttribute('role','alert');const heading=document.createElement('h2');heading.textContent='暂时无法打开工作区';const detail=document.createElement('p');const actions=document.createElement('div');
 const retry=document.createElement('button');retry.type='button';retry.textContent='重试';retry.addEventListener('click',()=>{void invoke('retry');});const home=document.createElement('button');home.type='button';home.textContent='返回工作区首页';home.addEventListener('click',()=>{void invoke('home');});actions.append(retry,home);recovery.append(heading,detail,actions);
 content.append(tabsBar,homePane,viewArea,recovery);document.body.append(bar,content);
 if(isError){document.getElementById('retry')?.addEventListener('click',()=>{void invoke('retry');});document.getElementById('home')?.addEventListener('click',()=>{void invoke('home');});}
 new ResizeObserver(layoutViews).observe(content);
 try{const state=await ipcRenderer.invoke('desktop:chrome-ready');windowState(state);if(state.tabs)tabsState(state.tabs);document.documentElement.dataset.crewDesktopChromeReady='true';layoutViews();}catch(error){reportError(error);}finally{readyResolve();}
}
if(trusted){ipcRenderer.on('desktop:window-state',(_event,state)=>windowState(state));ipcRenderer.on('desktop:tabs-state',(_event,state)=>tabsState(state));window.addEventListener('DOMContentLoaded',()=>{void mountChrome();},{once:true});}else readyResolve();
if(isHome&&process.isMainFrame)contextBridge.exposeInMainWorld('crewDesktop',Object.freeze({
 homeReady:()=>ipcRenderer.invoke('desktop:home-ready'),bootstrap:()=>ipcRenderer.invoke('desktop:bootstrap'),chooseExisting:()=>ipcRenderer.invoke('desktop:existing'),chooseParent:()=>ipcRenderer.invoke('desktop:parent'),createWorkspace:name=>ipcRenderer.invoke('desktop:create',name),openProject:name=>ipcRenderer.invoke('desktop:open',name),
 onAction:callback=>{if(typeof callback!=='function')throw new TypeError('需要工作区操作回调');const listener=(_event,message)=>{if(message&&['home','new','open','tools','admin','settings','workspace-settings'].includes(message.action))callback({action:message.action,...(typeof message.project==='string'?{project:message.project}:{})});};ipcRenderer.on('desktop:home-action',listener);return()=>ipcRenderer.removeListener('desktop:home-action',listener);},
 adminStatus:()=>ipcRenderer.invoke('desktop:admin-status'),adminCheck:()=>ipcRenderer.invoke('desktop:admin-check'),adminRestart:()=>ipcRenderer.invoke('desktop:admin-restart'),
 adminChatSnapshot:()=>ipcRenderer.invoke('desktop:admin-chat-snapshot'),adminChatSend:body=>ipcRenderer.invoke('desktop:admin-chat-send',body),adminChatInterrupt:()=>ipcRenderer.invoke('desktop:admin-chat-interrupt'),adminConfigGet:()=>ipcRenderer.invoke('desktop:admin-config-get'),adminConfigSave:body=>ipcRenderer.invoke('desktop:admin-config-save',body),
 toolsSnapshot:()=>ipcRenderer.invoke('desktop:tools-snapshot'),toolsCheck:()=>ipcRenderer.invoke('desktop:tools-check'),toolsUpdate:id=>ipcRenderer.invoke('desktop:tools-update',id),toolsCancel:id=>ipcRenderer.invoke('desktop:tools-cancel',id),toolsSettings:settings=>ipcRenderer.invoke('desktop:tools-settings',settings),
 settingsUI:()=>ipcRenderer.invoke('desktop:settings-ui'),settingsGet:()=>ipcRenderer.invoke('desktop:settings-get'),settingsSave:body=>ipcRenderer.invoke('desktop:settings-save',body),settingsConnection:()=>ipcRenderer.invoke('desktop:settings-connection'),settingsConnectionSave:body=>ipcRenderer.invoke('desktop:settings-connection-save',body),settingsConnectionAction:body=>ipcRenderer.invoke('desktop:settings-connection-action',body),settingsAway:away=>ipcRenderer.invoke('desktop:settings-away',away),
 launchSettingsGet:()=>ipcRenderer.invoke('desktop:launch-settings-get'),launchSettingsValidate:body=>ipcRenderer.invoke('desktop:launch-settings-validate',body),launchSettingsApply:body=>ipcRenderer.invoke('desktop:launch-settings-apply',body),launchSettingsPick:body=>ipcRenderer.invoke('desktop:launch-settings-pick',body),
 workspaceUI:()=>ipcRenderer.invoke('desktop:workspace-ui'),workspaceGet:name=>ipcRenderer.invoke('desktop:workspace-get',name),workspaceUpdate:body=>ipcRenderer.invoke('desktop:workspace-update',body),workspaceRemove:body=>ipcRenderer.invoke('desktop:workspace-remove',body),workspaceDirectory:current=>ipcRenderer.invoke('desktop:workspace-directory',current),workspaceMaintenance:(route,body)=>ipcRenderer.invoke('desktop:workspace-maintenance',{route,body}),
 onToolUpdates:callback=>{if(typeof callback!=='function')throw new TypeError('需要工具更新回调');const listener=(_event,state)=>callback(state);ipcRenderer.on('desktop:tools-state',listener);return()=>ipcRenderer.removeListener('desktop:tools-state',listener);},
 crewUpdateSnapshot:()=>ipcRenderer.invoke('desktop:crew-update-snapshot'),crewUpdateCheck:()=>ipcRenderer.invoke('desktop:crew-update-check'),crewUpdateApply:()=>ipcRenderer.invoke('desktop:crew-update-apply'),crewUpdateCancel:()=>ipcRenderer.invoke('desktop:crew-update-cancel'),crewUpdateOpen:kind=>ipcRenderer.invoke('desktop:crew-update-open',kind),
 onCrewUpdateState:callback=>{if(typeof callback!=='function')throw new TypeError('需要 Crew 更新回调');const listener=(_event,state)=>callback(state);ipcRenderer.on('desktop:crew-update-state',listener);return()=>ipcRenderer.removeListener('desktop:crew-update-state',listener);},
 ready:info=>chromeReady.then(()=>ipcRenderer.invoke('desktop:ready',info))
}));
