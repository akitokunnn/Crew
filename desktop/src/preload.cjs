'use strict';
const { contextBridge, ipcRenderer } = require('electron');
// Electron prefixes errors thrown in the main process with "Error invoking remote method '<channel>': Error: ";
// every page shows the reason itself, so all bridge calls strip it once here.
const bridgeInvoke=(channel,...args)=>ipcRenderer.invoke(channel,...args).catch(error=>{throw new Error(String(error?.message||error).replace(/^Error invoking remote method '[^']+':\s*(?:Error:\s*)?/,''));});
const platform=process.platform, mac=platform==='darwin';
const homeShortcut=mac?'⌘T':'Ctrl+T', backShortcut=mac?'⌘[':'Alt+左方向键';
let settings;
try { const arg=process.argv.find(value=>value.startsWith('--crew-desktop-config=')); settings=JSON.parse(decodeURIComponent(arg.slice('--crew-desktop-config='.length))); } catch(_){settings=null;}
const isHome=settings&&location.href===settings.home, isError=settings&&location.href===settings.error;
const trusted=process.isMainFrame&&(isHome||isError);
let readyResolve,backButton,titleNode,homePane,viewArea,tabsBar,recovery,lastBounds='';
let tabPreviewTimer=null,lastActiveTab=null;
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
async function invoke(command,value){try{return await bridgeInvoke('desktop:chrome-action',command,value);}catch(error){reportError(error);}}
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
 const focused=document.hasFocus()&&document.activeElement?.dataset.tabId,scroll=tabsBar.scrollLeft;tabsBar.replaceChildren();
 for(const tab of state.tabs){
  const item=document.createElement('div');item.className='crew-tab';item.dataset.active=String(state.activeId===tab.id);
  const select=document.createElement('button');select.type='button';select.dataset.tabId=tab.id;if(tab.project)select.dataset.project=tab.project;
  select.setAttribute('role','tab');select.setAttribute('aria-selected',String(state.activeId===tab.id));select.tabIndex=state.activeId===tab.id?0:-1;
  select.setAttribute('aria-label',tab.name+(tab.activity?'，'+tab.activity.label+(tab.activity.unread?'，有未读动态':''):''));
  const mark=document.createElement('span');mark.className='crew-tab-mark';mark.textContent=tab.project?Array.from(tab.name)[0]?.toUpperCase():'▦';
  const name=document.createElement('span');name.className='crew-tab-name';name.textContent=tab.name;select.append(mark,name);
  if(tab.project){
   const signal=document.createElement('span');signal.className='crew-tab-signal';signal.setAttribute('aria-hidden','true');
   signal.dataset.tone=tab.loading?'loading':tab.error?'error':tab.activity?.stale?'stale':tab.activity?.counts?.working?'working':tab.activity?.tone||'unknown';select.append(signal);
   if(tab.activity?.badge){const badge=document.createElement('span');badge.className='crew-tab-badge';badge.dataset.tone=tab.activity.tone;badge.textContent=tab.activity.badge>99?'99+':String(tab.activity.badge);badge.setAttribute('aria-hidden','true');select.append(badge);}
   if(tab.activity?.unread){const unread=document.createElement('span');unread.className='crew-tab-unread';unread.setAttribute('aria-hidden','true');mark.append(unread);}
   const preview=()=>{clearTimeout(tabPreviewTimer);tabPreviewTimer=setTimeout(()=>{if(!select.isConnected)return;const rect=select.getBoundingClientRect();void invoke('tab-preview',{id:tab.id,anchor:{x:rect.x,y:rect.y,width:rect.width,height:rect.height}});},280);};
   select.addEventListener('mouseenter',preview);select.addEventListener('focus',preview);
   select.addEventListener('mouseleave',()=>{clearTimeout(tabPreviewTimer);void invoke('tab-preview-hide');});
   select.addEventListener('blur',()=>{clearTimeout(tabPreviewTimer);void invoke('tab-preview-hide');});
  }
  select.addEventListener('click',()=>{clearTimeout(tabPreviewTimer);void invoke('tab-select',tab.id);});
  select.addEventListener('keydown',event=>{if(event.key==='Escape'){clearTimeout(tabPreviewTimer);void invoke('tab-preview-hide');}if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const index=state.tabs.findIndex(candidate=>candidate.id===tab.id);const next=event.key==='Home'?0:event.key==='End'?state.tabs.length-1:(index+(event.key==='ArrowLeft'?-1:1)+state.tabs.length)%state.tabs.length;void invoke('tab-select',state.tabs[next].id);}});
  item.append(select);
  if(tab.id!=='home'){const close=document.createElement('button');close.type='button';close.className='crew-tab-close';close.dataset.closeTabId=tab.id;close.setAttribute('aria-label','关闭 '+tab.name);close.title='关闭标签';close.append(icon(['m6 6 8 8','m14 6-8 8']));close.addEventListener('click',()=>{void invoke('tab-close',tab.id);});item.append(close);}
  tabsBar.append(item);
 }
 tabsBar.append(button('打开工作区标签 · '+homeShortcut,['M10 4v12','M4 10h12'],'home','crew-tab-add'));
 const active=state.tabs.find(tab=>tab.id===state.activeId);homePane.hidden=state.activeId!=='home'&&state.transition?.from!=='home';viewArea.classList.toggle('crew-view-active',state.activeId!=='home');
 recovery.hidden=!active?.error;if(active?.error)recovery.querySelector('p').textContent='暂时无法打开这个工作区，请检查连接后重试。其他标签仍然保留。';
 titleNode.textContent=active?.name || '工作区';
 const activeButton=tabsBar.querySelector('[data-tab-id="'+CSS.escape(state.activeId)+'"]');
 if(lastActiveTab!==state.activeId)activeButton?.scrollIntoView({block:'nearest',inline:'nearest'});else tabsBar.scrollLeft=scroll;
 lastActiveTab=state.activeId;if(focused)tabsBar.querySelector('[data-tab-id="'+CSS.escape(focused)+'"]')?.focus({preventScroll:true});layoutViews();
}
async function mountChrome(){
 if(!trusted||document.getElementById('crew-desktop-titlebar')){readyResolve();return;}
 document.documentElement.classList.add('crew-desktop-shell','crew-platform-'+platform);document.documentElement.dataset.crewDesktopPage=isHome?'home':'error';
 const content=document.createElement('div');content.id='crew-desktop-content';homePane=document.createElement('div');homePane.id='crew-desktop-home';while(document.body.firstChild)homePane.append(document.body.firstChild);
 const bar=document.createElement('header');bar.id='crew-desktop-titlebar';bar.setAttribute('aria-label','Crew 桌面窗口');const safe=document.createElement('div');safe.className='crew-chrome-safe';
 const brand=button('工作区首页 · '+homeShortcut,['M14.8 5.2a6.3 6.3 0 1 0 0 9.6','M14.4 13.6h.1'],'home','crew-chrome-brand');const name=document.createElement('span');name.textContent=settings.instanceName;brand.append(name);safe.append(brand);
 backButton=button('返回上一页 · '+backShortcut,['m11.7 4.6-5.4 5.4 5.4 5.4','M6.7 10h9'],'back');backButton.disabled=true;safe.append(backButton);
 const divider=document.createElement('span');divider.className='crew-chrome-divider';divider.setAttribute('aria-hidden','true');safe.append(divider);titleNode=document.createElement('span');titleNode.className='crew-chrome-title';titleNode.textContent=isHome?'工作区':'连接未完成';safe.append(titleNode);
 const spacer=document.createElement('span');spacer.className='crew-chrome-space';safe.append(spacer);safe.append(button('全局助手',['M4 4h12v9H9l-5 4V4','M7 8h6'],'assistant','crew-chrome-assistant'));safe.append(button('设置',['M4 5h12','M4 10h12','M4 15h12','M7 3v4','M13 8v4','M8 13v4'],'settings','crew-chrome-settings'));const admin=button('系统管理员',['M10 2.5 4 5v5c0 3 3 5.5 6 7 3-1.5 6-4 6-7V5l-6-2.5Z','m7 10 2 2 4-4'],'admin','crew-chrome-admin');const adminLabel=document.createElement('span');adminLabel.textContent='系统管理员';admin.append(adminLabel);safe.append(admin);const menu=button('应用菜单',['M4 10h.01','M10 10h.01','M16 10h.01'],'menu','crew-chrome-menu');menu.setAttribute('aria-haspopup','menu');menu.setAttribute('aria-expanded','false');safe.append(menu);bar.append(safe);
 tabsBar=document.createElement('nav');tabsBar.id='crew-desktop-tabs';tabsBar.setAttribute('aria-label','工作区标签');tabsBar.setAttribute('role','tablist');viewArea=document.createElement('div');viewArea.id='crew-workspace-view-area';
 recovery=document.createElement('section');recovery.id='crew-tab-recovery';recovery.hidden=true;recovery.setAttribute('role','alert');const heading=document.createElement('h2');heading.textContent='暂时无法打开工作区';const detail=document.createElement('p');const actions=document.createElement('div');
 const retry=document.createElement('button');retry.type='button';retry.textContent='重试';retry.addEventListener('click',()=>{void invoke('retry');});const home=document.createElement('button');home.type='button';home.textContent='返回工作区首页';home.addEventListener('click',()=>{void invoke('home');});actions.append(retry,home);recovery.append(heading,detail,actions);
 content.append(tabsBar,homePane,viewArea,recovery);document.body.append(bar,content);
 if(isError){document.getElementById('retry')?.addEventListener('click',()=>{void invoke('retry');});document.getElementById('home')?.addEventListener('click',()=>{void invoke('home');});}
 new ResizeObserver(layoutViews).observe(content);
 try{const state=await bridgeInvoke('desktop:chrome-ready');windowState(state);if(state.tabs)tabsState(state.tabs);document.documentElement.dataset.crewDesktopChromeReady='true';layoutViews();}catch(error){reportError(error);}finally{readyResolve();}
}
if(trusted){ipcRenderer.on('desktop:window-state',(_event,state)=>windowState(state));ipcRenderer.on('desktop:tabs-state',(_event,state)=>tabsState(state));window.addEventListener('DOMContentLoaded',()=>{void mountChrome();},{once:true});}else readyResolve();
// Connected to a LAN host, the home page gets the host folder picker instead of native
// folder dialogs, and never sees this computer's backend, tool or update controls.
const remote=settings?.remote===true;
const local=methods=>remote?{}:methods, lan=methods=>remote?methods:{};
if(isHome&&process.isMainFrame)contextBridge.exposeInMainWorld('crewDesktop',Object.freeze({
 platform,remote,connection:()=>bridgeInvoke('desktop:connection'),
 onConnection:callback=>{if(typeof callback!=='function')throw new TypeError('需要连接状态回调');const listener=(_event,state)=>callback(state);ipcRenderer.on('desktop:connection',listener);return()=>ipcRenderer.removeListener('desktop:connection',listener);},
 homeReady:()=>bridgeInvoke('desktop:home-ready'),bootstrap:()=>bridgeInvoke('desktop:bootstrap'),registerExisting:directory=>bridgeInvoke('desktop:existing-new',directory),openProject:name=>bridgeInvoke('desktop:open',name),
 ...local({chooseExisting:()=>bridgeInvoke('desktop:existing'),chooseParent:()=>bridgeInvoke('desktop:parent'),createWorkspace:name=>bridgeInvoke('desktop:create',name),storageActivate:workspaceId=>bridgeInvoke('desktop:storage-activate',workspaceId),storageImport:()=>bridgeInvoke('desktop:storage-import')}),
 ...lan({hostFolders:directory=>bridgeInvoke('desktop:host-folders',directory),openExistingDirectory:directory=>bridgeInvoke('desktop:existing-directory',directory),createWorkspace:(name,parent)=>bridgeInvoke('desktop:create',name,parent),storageActivate:(workspaceId,root)=>bridgeInvoke('desktop:storage-activate',workspaceId,root)}),
 storageExport:name=>bridgeInvoke('desktop:storage-export',name),
 onStorageProgress:callback=>{if(typeof callback!=='function')throw new TypeError('需要进度回调');const listener=(_event,message)=>{if(typeof message==='string')callback(message);};ipcRenderer.on('desktop:storage-progress',listener);return()=>ipcRenderer.removeListener('desktop:storage-progress',listener);},
 onAction:callback=>{if(typeof callback!=='function')throw new TypeError('需要工作区操作回调');const listener=(_event,message)=>{if(message&&['home','new','open','tools','admin','settings','workspace-settings'].includes(message.action))callback({action:message.action,...(typeof message.project==='string'?{project:message.project}:{})});};ipcRenderer.on('desktop:home-action',listener);return()=>ipcRenderer.removeListener('desktop:home-action',listener);},
 adminStatus:()=>bridgeInvoke('desktop:admin-status'),
 ...local({adminCheck:()=>bridgeInvoke('desktop:admin-check'),adminRestart:()=>bridgeInvoke('desktop:admin-restart'),
  adminChatSnapshot:()=>bridgeInvoke('desktop:admin-chat-snapshot'),adminChatSend:body=>bridgeInvoke('desktop:admin-chat-send',body),adminChatInterrupt:()=>bridgeInvoke('desktop:admin-chat-interrupt'),adminConfigGet:()=>bridgeInvoke('desktop:admin-config-get'),adminConfigSave:body=>bridgeInvoke('desktop:admin-config-save',body),
  toolsSnapshot:()=>bridgeInvoke('desktop:tools-snapshot'),toolsCheck:()=>bridgeInvoke('desktop:tools-check'),toolsUpdate:id=>bridgeInvoke('desktop:tools-update',id),toolsCancel:id=>bridgeInvoke('desktop:tools-cancel',id),toolsSettings:settings=>bridgeInvoke('desktop:tools-settings',settings),
  remoteAccessRead:()=>bridgeInvoke('desktop:remote-access-read'),remoteAccessAction:body=>bridgeInvoke('desktop:remote-access-action',body)}),
 promptsGet:query=>bridgeInvoke('desktop:prompts-get',query),promptsSave:body=>bridgeInvoke('desktop:prompts-save',body),
 settingsUI:()=>bridgeInvoke('desktop:settings-ui'),settingsGet:()=>bridgeInvoke('desktop:settings-get'),settingsSave:body=>bridgeInvoke('desktop:settings-save',body),settingsConnection:()=>bridgeInvoke('desktop:settings-connection'),settingsConnectionSave:body=>bridgeInvoke('desktop:settings-connection-save',body),settingsConnectionAction:body=>bridgeInvoke('desktop:settings-connection-action',body),settingsConnectionPreferences:body=>bridgeInvoke('desktop:settings-connection-preferences',body),settingsAway:away=>bridgeInvoke('desktop:settings-away',away),
 launchSettingsGet:()=>bridgeInvoke('desktop:launch-settings-get'),launchSettingsValidate:body=>bridgeInvoke('desktop:launch-settings-validate',body),launchSettingsApply:body=>bridgeInvoke('desktop:launch-settings-apply',body),launchSettingsPick:body=>bridgeInvoke('desktop:launch-settings-pick',body),remoteBackendPair:body=>bridgeInvoke('desktop:remote-backend-pair',body),
 storageRead:()=>bridgeInvoke('desktop:storage-read'),storagePreview:body=>bridgeInvoke('desktop:storage-preview',body),storageClean:body=>bridgeInvoke('desktop:storage-clean',body),storageRestore:body=>bridgeInvoke('desktop:storage-restore',body),storageEmptyTrash:body=>bridgeInvoke('desktop:storage-empty-trash',body),storagePolicy:body=>bridgeInvoke('desktop:storage-policy',body),
 workspaceUI:()=>bridgeInvoke('desktop:workspace-ui'),workspaceGet:name=>bridgeInvoke('desktop:workspace-get',name),workspaceUpdate:body=>bridgeInvoke('desktop:workspace-update',body),workspaceRemove:body=>bridgeInvoke('desktop:workspace-remove',body),...local({workspaceDirectory:current=>bridgeInvoke('desktop:workspace-directory',current)}),workspaceMaintenance:(route,body)=>bridgeInvoke('desktop:workspace-maintenance',{route,body}),
 ...local({onToolUpdates:callback=>{if(typeof callback!=='function')throw new TypeError('需要工具更新回调');const listener=(_event,state)=>callback(state);ipcRenderer.on('desktop:tools-state',listener);return()=>ipcRenderer.removeListener('desktop:tools-state',listener);}}),
 ...(mac?{}:local({
  crewUpdateSnapshot:()=>bridgeInvoke('desktop:crew-update-snapshot'),crewUpdateCheck:()=>bridgeInvoke('desktop:crew-update-check'),crewUpdateApply:()=>bridgeInvoke('desktop:crew-update-apply'),crewUpdateCancel:()=>bridgeInvoke('desktop:crew-update-cancel'),crewUpdateOpen:kind=>bridgeInvoke('desktop:crew-update-open',kind),
  onCrewUpdateState:callback=>{if(typeof callback!=='function')throw new TypeError('需要 Crew 更新回调');const listener=(_event,state)=>callback(state);ipcRenderer.on('desktop:crew-update-state',listener);return()=>ipcRenderer.removeListener('desktop:crew-update-state',listener);}})),
 ready:info=>chromeReady.then(()=>bridgeInvoke('desktop:ready',info))
}));
