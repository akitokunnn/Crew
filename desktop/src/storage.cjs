'use strict';
// Desktop orchestration only. Data inspection/migration lives in the backend.
const core = require('./core.cjs');

async function resolveDiscovery(discovery, root, base, choose, progress=()=>{}, request=core.request) {
  if (discovery.status === 'history_missing') throw new Error(discovery.message);
  if (discovery.status === 'ready') {progress('正在读取并核对工作区历史…');try{return (await request(base, '/api/workspace-data/open', {root},120000)).project;}finally{progress('');}}
  if (discovery.status === 'new') return null;
  if (discovery.status === 'relocate_required') {
    const action = await choose({kind:'relocate', discovery});
    return action === 'relocate' ? (await request(base, '/api/workspace-data/open', {root,action},120000)).project : undefined;
  }
  const candidates = discovery.candidates || [];
  const empty = candidates.length === 1 && !Object.values(candidates[0].counts).some(Boolean);
  const selection = empty ? {action:'upgrade', candidate_id:candidates[0].id} : await choose({kind:'upgrade',discovery});
  if (!selection) return undefined;
  progress('正在升级并校验历史与产出文件，请稍候…');
  try{return (await request(base, '/api/workspace-data/open', {root,...selection},120000)).project;}finally{progress('');}
}

async function prepare(project, base, choose, progress, request=core.request) {
  if (project.read_only || project.name.startsWith('storage:')) return project;
  const discovery = await request(base, '/api/workspace-data/discover', {root:project.root});
  return resolveDiscovery(discovery, project.root, base, choose, progress, request);
}

async function openDirectory(root, base, choose, progress, request=core.request) {
  const discovery = await request(base, '/api/workspace-data/discover', {root});
  const result = await resolveDiscovery(discovery, root, base, choose, progress, request);
  if (discovery.status !== 'new') return result;
  const project = await core.existingWorkspace(root, base);
  if (project.relocation) return project;
  return prepare(project, base, choose, progress, request);
}

module.exports = {prepare, openDirectory, resolveDiscovery};
