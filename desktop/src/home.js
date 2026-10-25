'use strict';
const $ = id => document.getElementById(id);
let busy = false;
let homeReady = false;
let dispatching = false;
const homeActions = [];
let workspaceSettings = null, workspaceSettingsLoading = null, workspaceMaintenance = null;
function failure(error) { $('error').textContent = error.message || String(error); $('error').hidden = false; }
async function action(callback) {
  if (busy) return;
  busy = true; $('error').hidden = true;
  document.querySelectorAll('#workspaces-page button').forEach(button => button.disabled = true);
  try { return await callback(); }
  catch (error) { failure(error); }
  finally {
    busy = false; document.querySelectorAll('#workspaces-page button').forEach(button => button.disabled = false);
    void flushHomeActions();
  }
}
function icon(paths) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  node.setAttribute('class', 'icon'); node.setAttribute('viewBox', '0 0 24 24'); node.setAttribute('aria-hidden', 'true');
  for (const value of paths) { const path = document.createElementNS(node.namespaceURI, 'path'); path.setAttribute('d', value); node.append(path); }
  return node;
}
async function refresh() {
  $('projects').setAttribute('aria-busy', 'true');
  $('service').textContent = '正在连接…';
  try {
    const state = await window.crewDesktop.bootstrap();
    $('service').textContent = '已连接'; $('service').classList.remove('failed');
    $('service').title = state.service.owned ? '由桌面管理本机服务' : '已接续正在运行的本机服务';
    $('project-count').textContent = String(state.projects.length);
    $('projects').replaceChildren();
    if (!state.projects.length) {
      const empty = document.createElement('p'); empty.className = 'empty';
      const heading = document.createElement('strong'); heading.textContent = '还没有工作区';
      empty.append(heading, document.createTextNode('打开文件夹，或新建一个工作区。')); $('projects').append(empty);
    }
    for (const project of state.projects) {
      const row = document.createElement('div'); row.className = 'project-row';
      const button = document.createElement('button'); button.className = 'project'; button.type = 'button';
      const label = project.display_name || project.name;
      button.title = project.root; button.setAttribute('aria-label', '在标签页打开工作区 ' + label);
      const avatar = document.createElement('span'); avatar.className = 'project-avatar';
      avatar.append(icon(['M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z']));
      const info = document.createElement('span'); info.className = 'project-info';
      const name = document.createElement('strong'); name.textContent = label;
      const root = document.createElement('small'); root.textContent = project.root; info.append(name, root);
      const arrow = document.createElement('span'); arrow.className = 'arrow'; arrow.append(icon(['m9 5 7 7-7 7']));
      button.append(avatar, info, arrow);
      button.addEventListener('click', () => action(() => openWorkspace(project.name)));
      const settings = document.createElement('button'); settings.type = 'button'; settings.className = 'project-settings';
      settings.title = '工作区设置 · ' + label; settings.setAttribute('aria-label', settings.title); settings.dataset.projectSettings = project.name;
      settings.append(icon(['M4 6h16M4 12h16M4 18h16M8 3v6M16 9v6M10 15v6']));
      settings.addEventListener('click', () => { void openWorkspaceSettings(project.name).catch(failure); });
      row.append(button, settings); $('projects').append(row);
    }
    return state;
  } catch (error) {
    $('service').textContent = '连接失败'; $('service').classList.add('failed');
    throw error;
  } finally { $('projects').setAttribute('aria-busy', 'false'); }
}
async function openWorkspaceSettings(name) {
  if (!workspaceSettings) {
    if (!workspaceSettingsLoading) workspaceSettingsLoading = (async () => {
      await window.crewDesktop.workspaceUI();
      workspaceMaintenance = window.CrewMaintenance.create({host:$('workspace-settings-host'),
        api:(route, body) => window.crewDesktop.workspaceMaintenance(route, body), onCleaned:() => { void refresh().catch(failure); }});
      workspaceSettings = window.CrewWorkspaceSettings.create({host:$('workspace-settings-host'),
        read:key => window.crewDesktop.workspaceGet(key), update:body => window.crewDesktop.workspaceUpdate(body),
        remove:body => window.crewDesktop.workspaceRemove(body), pickDirectory:current => window.crewDesktop.workspaceDirectory(current),
        openCleanup:(key, label) => { void workspaceMaintenance.openCleanup(key, label); },
        onSaved:() => { void refresh().catch(failure); }, onRemoved:() => { void refresh().catch(failure); },
        onClose:key => document.querySelector('[data-project-settings="'+CSS.escape(key)+'"]')?.focus({preventScroll:true})});
    })();
    try { await workspaceSettingsLoading; } finally { workspaceSettingsLoading = null; }
  }
  await workspaceSettings.open(name);
}
async function openWorkspace(name, created = false) {
  await window.crewDesktop.openProject(name);
  if (created) resetCreate();
  $('create-panel').hidden = true;
  await refresh();
}
function openExisting() { return action(async () => { const project = await window.crewDesktop.chooseExisting(); if (project) await openWorkspace(project.name); }); }
function openCreate() { $('error').hidden = true; $('create-panel').hidden = false; $('name').focus(); }
function resetCreate() {
  $('create-form').reset(); $('location').textContent = '尚未选择文件夹'; $('location').removeAttribute('title');
}
async function flushHomeActions() {
  if (!homeReady || busy || dispatching) return;
  dispatching = true;
  try {
    while (homeActions.length && !busy) {
      const next = homeActions.shift();
      if (next === 'settings') { void window.CrewHomeSettings?.show(); continue; }
      window.CrewHomeSettings?.hide();
      if (next === 'admin') { window.CrewHomeHealth?.show(); continue; }
      window.CrewHomeHealth?.hide();
      if (next === 'tools') { window.CrewHomeTools?.show(); continue; }
      window.CrewHomeTools?.hide();
      if (next === 'new') { resetCreate(); openCreate(); }
      else if (next === 'open') await openExisting();
      else if (next === 'home') await action(refresh);
    }
  } finally { dispatching = false; }
}
if (typeof window.crewDesktop.onAction === 'function') window.crewDesktop.onAction(message => {
  if (message?.action === 'workspace-settings') { void openWorkspaceSettings(message.project).catch(failure); return; }
  if (!['new', 'open', 'home', 'tools', 'admin', 'settings'].includes(message?.action)) return;
  if (message.action === 'settings') { void window.CrewHomeSettings?.show(); return; }
  window.CrewHomeSettings?.hide();
  if (message.action === 'admin') { window.CrewHomeHealth?.show(); return; }
  homeActions.push(message.action); void flushHomeActions();
});
// The system administrator must remain reachable even while the workspace bootstrap is pending.
if (typeof window.crewDesktop.homeReady === 'function') void window.crewDesktop.homeReady().catch(failure);
$('existing').addEventListener('click', openExisting);
$('new').addEventListener('click', openCreate);
function closeCreate() { $('create-panel').hidden = true; $('new').focus(); }
$('cancel').addEventListener('click', closeCreate);
$('choose-parent').addEventListener('click', () => action(async () => { const directory = await window.crewDesktop.chooseParent(); if (directory) { $('location').textContent = directory; $('location').title = directory; } }));
$('create-form').addEventListener('submit', event => { event.preventDefault(); action(async () => { const project = await window.crewDesktop.createWorkspace($('name').value); await openWorkspace(project.name, true); }); });
$('refresh').addEventListener('click', () => action(refresh));
document.addEventListener('keydown', event => { if (event.key === 'Escape' && !$('create-panel').hidden && !busy) closeCreate(); });
async function finishStartup(state) {
  await window.crewDesktop.ready({ projects: state.projects.length, nodeAvailable: typeof require !== 'undefined',
    bridgeMethods: Object.keys(window.crewDesktop), title: document.title });
  if (['new', 'open', 'tools', 'admin', 'settings'].includes(state.pendingAction)) homeActions.unshift(state.pendingAction);
  homeReady = true;
  if (state.pendingAction === 'workspace-settings') await openWorkspaceSettings(state.pendingWorkspace);
  await flushHomeActions();
}
refresh().then(finishStartup, async error => {
  failure(error);
  // The persistent picker must still receive native menu actions after a failed first refresh.
  await finishStartup({ projects:[] });
}).catch(failure);
