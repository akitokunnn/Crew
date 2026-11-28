'use strict';
(() => {
  const $=id=>document.getElementById(id), bridge=window.crewDesktop, panel=$('settings-page'), root=$('native-settings-root');
  let ui=null, creating=null, visible=false, mode='system', fontScale=1, appearanceHost=null;
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
  const media=window.matchMedia('(prefers-color-scheme: dark)');
  // The desktop keeps one display mode; its themeSource drives this media query in every page and the chrome.
  function applyTheme(){document.documentElement.dataset.theme=media.matches?'dark':'light';}
  applyTheme();media.addEventListener('change',applyTheme);
  function adopt(state){if(!['system','light','dark'].includes(state?.mode))return;mode=state.mode;fontScale=state.fontScale;if(appearanceHost?.isConnected)appearance(appearanceHost);}
  void bridge.theme().then(async state=>{
    // First launch with the shared setting: keep the mode chosen earlier on this home page.
    let legacy=null;try{legacy=localStorage.getItem('crew.desktop.theme');}catch(_){ /* Storage may be unavailable. */ }
    if(!state.stored&&['light','dark'].includes(legacy))state=await bridge.setTheme(legacy);
    try{localStorage.removeItem('crew.desktop.theme');}catch(_){ /* Nothing left to migrate. */ }
    adopt(state);
  }).catch(error=>console.error('无法读取显示模式：',error));
  bridge.onTheme(adopt);
  const reason=error=>String(error?.message||error).replace(/^Error invoking remote method '[^']+':\s*(?:Error:\s*)?/,'');
  function appearance(host){
    appearanceHost=host;
    const percent=value=>Math.round(value*100)+'%';
    host.innerHTML=`<div class="hs-appearance"><section class="cs-card"><h2>显示模式</h2><p class="cs-hint">这台电脑上的 Crew 共用一个显示模式，首页、所有工作区和窗口边框同时生效。</p><div class="cs-theme-options">${[['system','跟随系统'],['light','浅色'],['dark','深色']].map(([id,title])=>`<button type="button" data-desktop-theme="${id}" aria-pressed="${mode===id}">${title}</button>`).join('')}</div><p role="status" data-theme-note></p></section>`+
      `<section class="cs-card"><h2>字号</h2><p class="cs-hint">工作区、助手和想法页面共用一个字号；首页保持标准大小。</p><div class="hs-font"><input type="range" min="0.9" max="1.4" step="0.01" value="${fontScale}" data-desktop-font aria-label="字号"><output data-font-value>${percent(fontScale)}</output><button type="button" class="cs-button" data-font-reset ${fontScale===1?'disabled':''}>恢复标准</button></div><p role="status" data-font-note></p></section>`+
      `<section class="cs-card"><h2>新手引导</h2><p class="cs-hint">认识 Crew、连接 AI 引擎，然后选一种方式开局。</p><button type="button" class="cs-button hs-guide" data-onboarding-open>重新打开新手引导</button></section></div>`;
    for(const button of host.querySelectorAll('[data-desktop-theme]'))button.onclick=async()=>{
      try{adopt(await bridge.setTheme(button.dataset.desktopTheme));}
      catch(error){host.querySelector('[data-theme-note]').textContent=reason(error);}
    };
    // Dragging only updates the readout; releasing saves the size for every Crew page.
    const slider=host.querySelector('[data-desktop-font]'),saveFont=async value=>{
      try{adopt(await bridge.setFontScale(value));}
      catch(error){host.querySelector('[data-font-note]').textContent=reason(error);}
    };
    slider.oninput=()=>{host.querySelector('[data-font-value]').textContent=percent(Number(slider.value));};
    slider.onchange=()=>{void saveFont(Number(slider.value));};
    host.querySelector('[data-font-reset]').onclick=()=>{void saveFont(1);};
    host.querySelector('[data-onboarding-open]').onclick=()=>{$('nav-workspaces').click();void window.CrewHomeOnboarding.open();};
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
  function remote(host){
    return window.CrewRemoteAccess.mount(host,{
      read:()=>bridge.remoteAccessRead(),
      action:body=>bridge.remoteAccessAction(body)
    });
  }
  function storage(host){
    return window.CrewStorage.mount(host,{
      read:()=>bridge.storageRead(),preview:body=>bridge.storagePreview(body),
      clean:body=>bridge.storageClean(body),restore:body=>bridge.storageRestore(body),
      emptyTrash:body=>bridge.storageEmptyTrash(body),savePolicy:body=>bridge.storagePolicy(body)
    });
  }
  // The settings and operations UIs are packaged assets injected once into this page.
  let assets=null;
  function loadUI(){if(!assets)assets=bridge.settingsUI().catch(error=>{assets=null;throw error;});return assets;}
  async function ensure(){
    if(ui)return ui;if(creating)return creating;
    // The operations chat and repair policy live on 系统管理员 · 运维; settings keep status and 连接与目录.
    // Connected to a LAN host, its LAN access card and tool updates stay on the host.
    const launch={get:()=>bridge.launchSettingsGet(),validate:body=>bridge.launchSettingsValidate(body),apply:body=>bridge.launchSettingsApply(body),pick:body=>bridge.launchSettingsPick(body),pair:body=>bridge.remoteBackendPair(body)};
    const operations={launch};
    creating=(async()=>{await loadUI();ui=window.CrewSettings.create({host:root,remote:bridge.remote,read:()=>bridge.settingsGet(),save:body=>bridge.settingsSave(body),operations,
      operationsPage:()=>window.CrewHomeHealth?.show(),panels:{
      persona:host=>CrewPersona.mountSettings(host,{save:body=>bridge.settingsSave(body),request:(op,body)=>{
        const action=op.slice('persona.'.length);
        return CrewPersona.isRead(op) ? bridge.personaGet({action,...body}) : bridge.personaSave({action,...body});
      }}),
      sounds:host=>window.CrewSoundSettings.mount(host,{read:()=>bridge.settingsGet(),save:body=>bridge.settingsSave(body)}),
      prompts:host=>window.CrewBehaviorPrompts.mount(host,{read:query=>bridge.promptsGet(query),save:body=>bridge.promptsSave(body)}),appearance,connections,storage,
      ...(bridge.remote?{}:{remote,tools:host=>window.CrewHomeTools?.mount(host)})}});return ui;})();
    try{return await creating;}finally{creating=null;}
  }
  async function show(category='ai',intent=null){
    window.CrewHomeTools?.hide();window.CrewHomeHealth?.hide();visible=true;panel.hidden=false;$('workspaces-page').hidden=true;
    for(const id of ['nav-workspaces','nav-tools','nav-admin','nav-settings']){const active=id==='nav-settings';$(id).classList.toggle('active',active);if(active)$(id).setAttribute('aria-current','page');else $(id).removeAttribute('aria-current');}
    try{const settings=await ensure();if(visible){settings.open(category,intent);if(!intent)root.querySelector('[data-settings-category="'+category+'"]')?.focus();}}
    catch(error){root.textContent='设置界面加载失败：'+error.message;const retry=document.createElement('button');retry.className='button secondary';retry.textContent='重试';retry.onclick=()=>{void show(category,intent);};root.append(retry);}
  }
  function hide(){visible=false;ui?.hide();panel.hidden=true;$('nav-settings').classList.remove('active');$('nav-settings').removeAttribute('aria-current');}
  $('nav-settings').addEventListener('click',()=>{void show();});
  $('nav-workspaces').addEventListener('click',hide);
  window.CrewHomeSettings={show,hide,loadUI};
})();
