'use strict';
const $ = id => document.getElementById(id);
// macOS keeps Crew in the menu bar (status item) rather than a Windows tray.
if (window.crewDesktop.platform === 'darwin') { $('home-shortcut').textContent = '⌘T'; $('home-footer').textContent = '关闭窗口后，Crew 会继续在菜单栏运行。'; }
let busy = false;
let homeReady = false;
let dispatching = false;
const homeActions = [];
let workspaceSettings = null, workspaceSettingsLoading = null, workspaceMaintenance = null;
// Connected to a LAN host: folders are the host's, chosen in the page's host folder picker.
const remote = window.crewDesktop.remote === true;
let connection = {mode:remote ? 'remote' : 'local'}, remoteParent = null, bannerNote = '', bannerPending = false, repairing = false;
function message(error) { return String(error?.message || error).replace(/^Error invoking remote method '[^']+':\s*(?:Error:\s*)?/, ''); }
function failure(error) {
  // The LAN banner already explains a lost connection or authorization; other errors still show here.
  if (remote && connection.error && message(error) === connection.error) { $('error').hidden = true; return; }
  $('error').textContent = message(error); $('error').hidden = false;
}
function hostFolder(title, confirm, start = '') { return window.CrewHostFolders.pick({title, confirm, start, host:connection.name || ''}); }
window.crewDesktop.onStorageProgress?.(message=>{const status=$('storage-progress');if(status){status.textContent=message;status.hidden=!message;}});
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
    $('service').textContent = remote ? '已连接 · ' + (connection.name || '局域网主机') : '已连接'; $('service').classList.remove('failed');
    $('service').title = remote ? '已连接到局域网主机 ' + state.service.url : state.service.owned ? '由桌面管理本机服务' : '已接续正在运行的本机服务';
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
      if(project.read_only){settings.disabled=true;settings.title='导入的历史可查看和导出；执行需要重新绑定本机环境。';}
      const exportButton=document.createElement('button');exportButton.type='button';exportButton.className='project-action';exportButton.textContent='导出';exportButton.title='导出完整工作区';exportButton.setAttribute('aria-label','导出工作区 '+label);
      exportButton.addEventListener('click',()=>action(()=>window.crewDesktop.storageExport(project.name)));
      row.append(button, exportButton);
      if(project.workspace_id&&!project.read_only){const history=document.createElement('button');history.type='button';history.className='project-action';history.textContent='历史';history.title='查看完整保存历史';history.onclick=()=>action(()=>openWorkspace('storage:'+project.workspace_id));row.append(history);}
      if(project.can_activate){const resume=document.createElement('button');resume.type='button';resume.className='project-action';resume.textContent='接续';resume.title=remote?'绑定主机上的代码目录后在 Crew 接续':'绑定本机目录后在Crew接续';resume.onclick=()=>action(async()=>{
        if(remote){const root=await hostFolder('选择工作区副本的代码目录','绑定副本并接续');if(!root)return;await window.crewDesktop.storageActivate(project.workspace_id,root);}
        else await window.crewDesktop.storageActivate(project.workspace_id);
        await refresh();});row.append(resume);}
      if(!project.read_only)row.append(settings);$('projects').append(row);
    }
    return state;
  } catch (error) {
    $('service').textContent = '连接失败'; $('service').classList.add('failed');
    // Never leave the list on "reading": the reason is shown above it and on the system administrator page.
    const empty = document.createElement('p'); empty.className = 'empty';
    const heading = document.createElement('strong'); heading.textContent = '后台未连接，暂时读不到工作区';
    empty.append(heading, document.createTextNode('原因见上方提示；处理后点击「刷新」，或到「系统管理员」查看后台状态。'));
    $('projects').replaceChildren(empty);
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
        remove:body => window.crewDesktop.workspaceRemove(body),
        pickDirectory:current => remote ? hostFolder('选择工作目录', '使用此目录', current) : window.crewDesktop.workspaceDirectory(current),
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
async function chooseExisting() {
  if (!remote) return window.crewDesktop.chooseExisting();
  const directory = await hostFolder('打开主机上的文件夹', '打开此文件夹');
  return directory ? window.crewDesktop.openExistingDirectory(directory) : null;
}
function openExisting() { return action(async () => {
  const project = await chooseExisting();
  if (!project) return;
  if (!project.relocation) { await openWorkspace(project.name); return; }
  const choice = await relocationChoice(project.relocation);
  if (choice.kind === 'relink') await openWorkspaceSettings(choice.project, {root:project.relocation.directory});
  if (choice.kind === 'new') { const created = await window.crewDesktop.registerExisting(project.relocation.directory); await openWorkspace(created.name); }
}); }
// Importing a package reads a file on the host's own disk, so it stays on the host.
$('import-workspace').hidden = remote;
if (!remote) $('import-workspace').addEventListener('click',()=>action(async()=>{await window.crewDesktop.storageImport();await refresh();}));
function openCreate() { $('error').hidden = true; $('create-panel').hidden = false; $('name').focus(); }
function resetCreate() {
  $('create-form').reset(); remoteParent = null; $('location').textContent = '尚未选择文件夹'; $('location').removeAttribute('title');
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
$('choose-parent').addEventListener('click', () => action(async () => {
  const directory = remote ? await hostFolder('选择新工作区的保存位置', '保存到这里', remoteParent || '') : await window.crewDesktop.chooseParent();
  if (directory) { if (remote) remoteParent = directory; $('location').textContent = directory; $('location').title = directory; }
}));
$('create-form').addEventListener('submit', event => { event.preventDefault(); action(async () => {
  const project = remote ? await window.crewDesktop.createWorkspace($('name').value, remoteParent) : await window.crewDesktop.createWorkspace($('name').value);
  await openWorkspace(project.name, true);
}); });
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
function bannerButton(text, kind, onClick) {
  const button = document.createElement('button'); button.type = 'button'; button.className = 'button ' + kind; button.textContent = text;
  button.disabled = bannerPending; button.addEventListener('click', onClick); return button;
}
async function bannerAction(note, work) {
  if (bannerPending) return;
  bannerPending = true; bannerNote = note; renderBanner();
  try { await work(); }
  catch (error) { bannerNote = message(error); bannerPending = false; renderBanner(true); }
}
// Reconnect with the saved device's new token, or return to this computer's backend; both reopen Crew.
async function reopen(settings) {
  const result = await window.crewDesktop.launchSettingsApply({settings, home_action:'existing'});
  if (result.restarting !== true) throw new Error('连接设置没有变化，Crew 未重新打开。');
}
async function reconnect() { await reopen((await window.crewDesktop.launchSettingsGet()).settings); }
function switchLocal() {
  return bannerAction('正在切回本机后台，Crew 将重新打开…', async () => {
    const launch = await window.crewDesktop.launchSettingsGet();
    await reopen({...launch.settings, mode:'local', remote_backend:''});
  });
}
async function openRepair() {
  if (bannerPending) return;
  try { repairing = {device_name:(await window.crewDesktop.launchSettingsGet()).device_name}; bannerNote = ''; }
  catch (error) { bannerNote = message(error); }
  renderBanner(); $('remote-banner').querySelector('input[name="code"]')?.focus();
}
function repairForm() {
  const form = document.createElement('form'); form.className = 'remote-repair'; form.setAttribute('aria-label', '重新配对');
  const field = (label, name, attributes) => {
    const wrapper = document.createElement('label'), input = document.createElement('input'); wrapper.textContent = label;
    input.name = name; input.required = true; input.autocomplete = 'off'; Object.assign(input, attributes); wrapper.append(input); return wrapper;
  };
  form.append(field('主机上的新配对码', 'code', {inputMode:'numeric', maxLength:8, placeholder:'8 位数字'}), field('本机设备名', 'device', {maxLength:80, value:repairing.device_name}));
  const actions = document.createElement('div'); actions.className = 'remote-repair-actions';
  const submit = document.createElement('button'); submit.type = 'submit'; submit.className = 'button primary'; submit.textContent = bannerPending ? '正在配对…' : '重新配对并打开'; submit.disabled = bannerPending;
  actions.append(bannerButton('取消', 'secondary', () => { repairing = false; bannerNote = ''; renderBanner(); }), submit);
  form.append(actions);
  form.elements.device.addEventListener('input', () => { repairing.device_name = form.elements.device.value; });
  form.addEventListener('submit', event => {
    event.preventDefault();
    const code = form.elements.code.value.trim(), device_name = form.elements.device.value.trim();
    void bannerAction('已提交配对，正在连接主机…', async () => {
      await window.crewDesktop.remoteBackendPair({url:connection.url, code, device_name});
      bannerNote = '已重新配对，Crew 正在重新打开…'; renderBanner();
      await reconnect();
    });
  });
  for (const input of form.querySelectorAll('input')) input.disabled = bannerPending;
  return form;
}
function renderBanner(failed = false) {
  const banner = $('remote-banner');
  if (!remote) { banner.hidden = true; return; }
  const name = connection.name || '局域网主机', expired = connection.auth_expired, unreachable = !expired && Boolean(connection.error);
  const mismatch = !expired && !unreachable && connection.host_version && connection.local_version && connection.host_version !== connection.local_version;
  banner.replaceChildren(); banner.hidden = !(expired || unreachable || mismatch);
  if (banner.hidden) return;
  banner.dataset.tone = expired ? 'warning' : unreachable ? 'error' : 'info';
  banner.setAttribute('role', expired || unreachable ? 'alert' : 'status');
  const text = document.createElement('div'); text.className = 'remote-banner-text';
  const title = document.createElement('strong'), detail = document.createElement('p');
  if (expired) { title.textContent = '授权已失效，请在主机上重新生成配对码'; detail.textContent = `在 ${name} 上打开“设置 → 局域网访问”生成新的配对码，然后在这里重新配对；也可以切回本机后台。`; }
  else if (unreachable) { title.textContent = `暂时无法连接 ${name}`; detail.textContent = connection.error + '。主机恢复后会自动重新连接，也可以切回本机后台。'; }
  else { title.textContent = `已连接到 ${name}（版本 ${connection.host_version}），本机版本 ${connection.local_version}，部分功能可能不一致`; detail.hidden = true; }
  text.append(title, detail);
  if (bannerNote) { const note = document.createElement('p'); note.className = 'remote-banner-note' + (failed ? ' is-error' : ''); note.setAttribute('role', failed ? 'alert' : 'status'); note.textContent = bannerNote; text.append(note); }
  banner.append(text);
  if (expired && repairing) { banner.append(repairForm()); return; }
  if (expired || unreachable) {
    const actions = document.createElement('div'); actions.className = 'remote-banner-actions';
    if (expired && connection.url) actions.append(bannerButton('重新配对', 'primary', () => { void openRepair(); }));
    actions.append(bannerButton('切回本机', 'secondary', () => { void switchLocal(); }));
    banner.append(actions);
  }
}
function applyConnection(state) {
  const expiredNow = state.auth_expired && !connection.auth_expired, recovered = connection.error && !state.error;
  connection = state; renderBanner();
  if (expiredNow) {
    // Leave settings or administrator pages so the expired authorization is seen at once.
    $('nav-workspaces').click(); $('error').hidden = true;
    $('service').textContent = '授权已失效'; $('service').classList.add('failed');
  }
  if (recovered && homeReady) void action(refresh);
}
if (remote) window.crewDesktop.onConnection(applyConnection);
(remote ? window.crewDesktop.connection().then(applyConnection) : Promise.resolve()).then(refresh).then(finishStartup, async error => {
  failure(error);
  // The persistent picker must still receive native menu actions after a failed first refresh.
  await finishStartup({ projects:[] });
}).catch(failure);

window.addEventListener('focus',()=>{if(homeReady&&!busy&&!$('workspaces-page').hidden)void refresh().catch(failure);});
