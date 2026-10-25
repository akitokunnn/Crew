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
    let notice = $('project-locations');
    if (!notice) { notice = document.createElement('section'); notice.id = 'project-locations'; notice.className = 'workspace-location-notice'; notice.setAttribute('role','status'); $('projects').before(notice); }
    const missing = state.projects.filter(item => item.directory_state && item.directory_state.status !== 'ready');
    notice.hidden = !missing.length;
    notice.textContent = missing.length ? `有 ${missing.length} 个工作区无法访问原目录。文件夹改名或移动了吗？点击对应工作区重新定位，原来的会话与历史会保留。` : '';
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
      if (project.directory_state && project.directory_state.status !== 'ready') { row.classList.add('project-missing'); root.textContent += project.directory_state.status === 'missing' ? ' · 找不到目录，点击重新定位' : ' · 无法访问：' + project.directory_state.error; }
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
async function openWorkspaceSettings(name, selection) {
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
  await workspaceSettings.open(name, selection);
}
async function openWorkspace(name, created = false) {
  await window.crewDesktop.openProject(name);
  if (created) resetCreate();
  $('create-panel').hidden = true;
  await refresh();
}
function relocationChoice(relocation) {
  return new Promise(resolve => {
    const dialog = document.createElement('dialog'); dialog.className = 'workspace-recovery'; dialog.setAttribute('aria-label','关联原来的工作区');
    const title = document.createElement('h2'); title.textContent = '这是原工作区的新位置吗？';
    const message = document.createElement('p'); message.textContent = '有工作区的原目录已找不到。关联后保留原来的 leader、成员、目标和聊天记录，不会新建重复项目。';
    const path = document.createElement('p'); path.className = 'recovery-path'; path.textContent = relocation.directory;
    const label = document.createElement('label'); label.textContent = '选择原工作区';
    const select = document.createElement('select'); select.setAttribute('aria-label','原工作区');
    const placeholder = document.createElement('option'); placeholder.value = ''; placeholder.textContent = '请选择要保留记录的原工作区'; placeholder.disabled = true; placeholder.selected = true; select.append(placeholder);
    for (const project of relocation.projects) { const option = document.createElement('option'); option.value = project.name; option.textContent = (project.display_name || project.name) + ' · ' + project.root; select.append(option); }
    label.append(select);
    const buttons = document.createElement('div'); buttons.className = 'recovery-actions';
    const finish = value => { dialog.close(); dialog.remove(); resolve(value); };
    for (const [text,kind] of [['取消','cancel'],['这是新工作区','new'],['关联原工作区','relink']]) {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'button' + (kind === 'relink' ? '' : ' secondary'); button.textContent = text; button.onclick = () => finish({kind,project:select.value});
      if (kind === 'relink') { button.disabled = true; select.addEventListener('change',()=>{button.disabled = !select.value;}); }
      buttons.append(button);
    }
    dialog.append(title,message,path,label,buttons); document.body.append(dialog); dialog.addEventListener('cancel',event=>{event.preventDefault();finish({kind:'cancel'});}); dialog.showModal(); select.focus();
  });
}
function openExisting() { return action(async () => {
  const project = await window.crewDesktop.chooseExisting();
  if (!project) return;
  if (!project.relocation) { await openWorkspace(project.name); return; }
  const choice = await relocationChoice(project.relocation);
  if (choice.kind === 'relink') await openWorkspaceSettings(choice.project, {root:project.relocation.directory});
  if (choice.kind === 'new') { const created = await window.crewDesktop.registerExisting(project.relocation.directory); await openWorkspace(created.name); }
}); }
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

window.addEventListener('focus',()=>{if(homeReady&&!busy&&!$('workspaces-page').hidden)void refresh().catch(failure);});
