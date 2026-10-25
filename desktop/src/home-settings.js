'use strict';
(() => {
  const $=id=>document.getElementById(id), bridge=window.crewDesktop, panel=$('settings-page'), root=$('native-settings-root');
  let ui=null, creating=null, visible=false, mode='system';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
  try { const saved=localStorage.getItem('crew.desktop.theme'); if(['system','light','dark'].includes(saved))mode=saved; } catch(_) { /* The default follows the OS when storage is unavailable. */ }
  const media=window.matchMedia('(prefers-color-scheme: dark)');
  function applyTheme(){document.documentElement.dataset.theme=mode==='system'?(media.matches?'dark':'light'):mode;}
  applyTheme();media.addEventListener('change',applyTheme);
  function appearance(host){
    host.innerHTML=`<section class="cs-card"><h2>显示模式</h2><p class="cs-hint">保存在这台电脑上，立即应用于桌面首页。</p><div class="cs-theme-options">${[['system','跟随系统'],['light','浅色'],['dark','深色']].map(([id,title])=>`<button type="button" data-desktop-theme="${id}" aria-pressed="${mode===id}">${title}</button>`).join('')}</div><p role="status" data-theme-note></p></section>`;
    for(const button of host.querySelectorAll('[data-desktop-theme]'))button.onclick=()=>{
      mode=button.dataset.desktopTheme;applyTheme();
      try { localStorage.setItem('crew.desktop.theme',mode);appearance(host); }
      catch(error){host.querySelector('[data-theme-note]').textContent=error.message;}
    };
  }
  function connections(host){
    return window.CrewConnections.mount(host,{
      read:()=>bridge.settingsConnection(),
      configure:body=>bridge.settingsConnectionSave(body),
      action:body=>bridge.settingsConnectionAction(body),
      preferences:body=>bridge.settingsConnectionPreferences(body),
      away:value=>bridge.settingsAway(value)
    });
  }
  function backend(host){
    return window.CrewHomeHealth?.mount(host);
  }
  function storage(host){
    return window.CrewStorage.mount(host,{
      read:()=>bridge.storageRead(),preview:body=>bridge.storagePreview(body),
      clean:body=>bridge.storageClean(body),restore:body=>bridge.storageRestore(body),
      emptyTrash:body=>bridge.storageEmptyTrash(body),savePolicy:body=>bridge.storagePolicy(body)
    });
  }
  async function ensure(){
    if(ui)return ui;if(creating)return creating;
    creating=(async()=>{await bridge.settingsUI();ui=window.CrewSettings.create({host:root,read:()=>bridge.settingsGet(),save:body=>bridge.settingsSave(body),operations:{snapshot:()=>bridge.adminChatSnapshot(),send:body=>bridge.adminChatSend(body),interrupt:()=>bridge.adminChatInterrupt(),configGet:()=>bridge.adminConfigGet(),configSave:body=>bridge.adminConfigSave(body),launch:{get:()=>bridge.launchSettingsGet(),validate:body=>bridge.launchSettingsValidate(body),apply:body=>bridge.launchSettingsApply(body),pick:body=>bridge.launchSettingsPick(body)}},panels:{appearance,connections,storage,tools:host=>window.CrewHomeTools?.mount(host),backend}});return ui;})();
    try{return await creating;}finally{creating=null;}
  }
  async function show(category='ai'){
    window.CrewHomeTools?.hide();window.CrewHomeHealth?.hide();visible=true;panel.hidden=false;$('workspaces-page').hidden=true;
    for(const id of ['nav-workspaces','nav-tools','nav-admin','nav-settings']){const active=id==='nav-settings';$(id).classList.toggle('active',active);if(active)$(id).setAttribute('aria-current','page');else $(id).removeAttribute('aria-current');}
    try{const settings=await ensure();if(visible){settings.open(category);root.querySelector('[data-settings-category="'+category+'"]')?.focus();}}
    catch(error){root.textContent='设置界面加载失败：'+error.message;const retry=document.createElement('button');retry.className='button secondary';retry.textContent='重试';retry.onclick=()=>{void show(category);};root.append(retry);}
  }
  function hide(){visible=false;ui?.hide();panel.hidden=true;$('nav-settings').classList.remove('active');$('nav-settings').removeAttribute('aria-current');}
  $('nav-settings').addEventListener('click',()=>{void show();});
  $('nav-workspaces').addEventListener('click',hide);
  window.CrewHomeSettings={show,hide};
})();
