'use strict';
const $ = id => document.getElementById(id);
let busy = false;
function failure(error) { $('error').textContent = error.message || String(error); $('error').hidden = false; }
async function action(callback) { if (busy) return; busy = true; $('error').hidden = true; document.querySelectorAll('button').forEach(button => button.disabled = true); try { return await callback(); } catch (error) { failure(error); } finally { busy = false; document.querySelectorAll('button').forEach(button => button.disabled = false); } }
async function refresh() {
  const state = await window.crewDesktop.bootstrap();
  $('service').textContent = '● 已连接 · ' + (state.service.owned ? '桌面托管' : '接续现有服务');
  $('projects').replaceChildren();
  if (!state.projects.length) { const empty = document.createElement('p'); empty.className = 'empty'; empty.textContent = '还没有工作区。打开一个项目，开始协作。'; $('projects').append(empty); }
  for (const project of state.projects) {
    const button = document.createElement('button'); button.className = 'project';
    const avatar = document.createElement('span'); avatar.className = 'project-avatar'; avatar.textContent = project.name.slice(0, 1).toUpperCase();
    const info = document.createElement('span'); info.className = 'project-info'; const name = document.createElement('strong'); name.textContent = project.name; const root = document.createElement('small'); root.textContent = project.root; info.append(name, root);
    const arrow = document.createElement('span'); arrow.className = 'arrow'; arrow.textContent = '→'; button.append(avatar, info, arrow);
    button.addEventListener('click', () => action(() => window.crewDesktop.openProject(project.name))); $('projects').append(button);
  }
  return state;
}
$('existing').addEventListener('click', () => action(async () => { const project = await window.crewDesktop.chooseExisting(); if (project) await window.crewDesktop.openProject(project.name); }));
$('new').addEventListener('click', () => { $('create-panel').hidden = false; $('name').focus(); });
$('cancel').addEventListener('click', () => { $('create-panel').hidden = true; });
$('choose-parent').addEventListener('click', () => action(async () => { const directory = await window.crewDesktop.chooseParent(); if (directory) $('location').textContent = directory; }));
$('create').addEventListener('click', () => action(async () => { const project = await window.crewDesktop.createWorkspace($('name').value); await window.crewDesktop.openProject(project.name); }));
$('refresh').addEventListener('click', () => action(refresh));
refresh().then(state => window.crewDesktop.ready({ projects: state.projects.length, nodeAvailable: typeof require !== 'undefined',
  bridgeMethods: Object.keys(window.crewDesktop), title: document.title })).catch(failure);
