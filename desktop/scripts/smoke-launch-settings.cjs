'use strict';
// Real Python supervisor/service + control helpers, all in a private HOME.
// Native model CLIs are absent from the child PATH, and no AI endpoint is used.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const {spawn, execFile} = require('node:child_process');
const {promisify} = require('node:util');
const core = require('../src/core.cjs');
const {LaunchSettings, readSync} = require('../src/launch-settings.cjs');
const {CrewUpdates} = require('../src/crew-updates.cjs');
const {OperationsClient} = require('../src/operations.cjs');

const seed = String.raw`
import json,sys,shutil
from pathlib import Path
from contextlib import closing
from crew import board,chat,goals,runtimes,store
home,project=map(Path,sys.argv[1:3])
assert not shutil.which('claude') and not shutil.which('codex'), 'Native model CLI must be unavailable'
project.mkdir(parents=True,exist_ok=True)
with closing(store.connect(home)) as conn:
    board.init_project(conn,'lifecycle-fixture',project)
    board.join(conn,'fixture-leader','lifecycle-fixture','fixture leader',project,None,None,role='leader')
    board.join(conn,'fixture-member','lifecycle-fixture','fixture member',project,None,None,group_name='fixture leader')
    for sid in ('fixture-leader','fixture-member'):
        runtimes.set(conn,sid,'codex',None)
    goal=goals.create(conn,'lifecycle-fixture','fixture-leader','Keep delivery across HOME migration',
                      'Preserve the original requirement, identities and discussion.', ['History and task remain available'])
for sid in ('fixture-leader','fixture-member'):
    journal=chat.Journal(home/'chat'/(sid+'.jsonl'))
    journal.emit('message',id=sid+'-question',role='user',text='Keep this existing discussion.')
    journal.emit('message',id=sid+'-answer',role='assistant',text='Existing reply retained without waking AI.')
attachment=home/'chat'/'files'/'fixture-member'/'fixture.txt'
attachment.parent.mkdir(parents=True,exist_ok=True)
attachment.write_text('existing attachment',encoding='utf-8')
# A retained portable runtime from an older HOME must not override the next
# explicit launch. The live supervisor already owns its in-memory source.
deployment=home/'desktop'/'admin'/'deployment.json'
deployment.parent.mkdir(parents=True,exist_ok=True)
deployment.write_text(json.dumps({'source':str(home/'desktop'/'backend'/'retired-flat-runtime'),
                                  'staged':None}),encoding='utf-8')
print(json.dumps({'goal_id':goal['id'],'sessions':['fixture-leader','fixture-member'],
                  'native_cli_available':False}))
`;

const inspectHome = String.raw`
import json,sys,shutil
from contextlib import closing
from pathlib import Path
from crew import store
home=Path(sys.argv[1])
with closing(store.connect(home)) as conn:
    sessions=[dict(row) for row in conn.execute('SELECT session_id,crew_name,role,group_name,cwd,status FROM sessions ORDER BY session_id')]
    goals=[dict(row) for row in conn.execute('SELECT id,title,original_request,status,manual_pause FROM goals ORDER BY id')]
    native=[dict(row) for row in conn.execute('SELECT session_id,runtime,native_id FROM session_runtimes ORDER BY session_id')]
    executions=conn.execute('SELECT COUNT(*) FROM goal_executions').fetchone()[0]
    execution_statuses=[dict(row) for row in conn.execute('SELECT id,status FROM goal_executions')]
    stop_gates=[row[0] for row in conn.execute('SELECT session_id FROM session_stop_gates')]
    pending_notices=conn.execute("SELECT COUNT(*) FROM leader_notices WHERE text LIKE 'offline notice%' AND sent_at IS NULL").fetchone()[0]
    notice_errors=conn.execute("SELECT COUNT(*) FROM events WHERE kind='leader:notice_error'").fetchone()[0]
    integrity=conn.execute('PRAGMA integrity_check').fetchone()[0]
print(json.dumps({'sessions':sessions,'goals':goals,'native':native,'executions':executions,
                  'execution_statuses':execution_statuses,'stop_gates':stop_gates,'pending_notices':pending_notices,
                  'notice_errors':notice_errors,'integrity':integrity,
                  'native_cli_available':bool(shutil.which('claude') or shutil.which('codex'))}))
`;

const seedOffline = String.raw`
import json,sys
from pathlib import Path
from contextlib import closing
from crew import board,chat,goals,runtimes,store
home,project=map(Path,sys.argv[1:3])
with closing(store.connect(home)) as conn:
    goal=goals.create(conn,'lifecycle-fixture','fixture-leader','Interrupted offline goal',
                      'Do not resume automatically after choosing another HOME.', ['Remain manually paused'])
    goals.transition(conn,goal['id'],'running')
    execution=goals.create_execution(conn,goal['id'],'fixture-leader',runtime='codex')
    goals.set_execution_status(conn,execution['id'],'running')
    board.join(conn,'notice-only-leader','lifecycle-fixture','notice only',project,None,None,role='leader')
    runtimes.set(conn,'notice-only-leader','codex',None)
    for sid in ('fixture-leader','notice-only-leader'):
        conn.execute("INSERT INTO leader_notices(project,session_id,source,text,created_at) VALUES('lifecycle-fixture',?,'crew',?,'fixture')",
                     (sid,'offline notice for '+sid))
journal=chat.Journal(home/'chat'/'notice-only-leader.jsonl')
journal.emit('message',id='notice-history',role='user',text='Existing conversation; await explicit user input.')
print(json.dumps({'goal_id':goal['id'],'execution_id':execution['id']}))
`;

(async () => {
  const desktop = path.resolve(__dirname, '..'), plugin = path.resolve(desktop, '../plugin');
  const output = path.join(desktop, 'smoke-output', 'launch-lifecycle');
  await fs.mkdir(output, {recursive:true});
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'crew-launch-lifecycle-'));
  const source = path.join(temp, 'source HOME'), target = path.join(temp, 'new HOME');
  const preferences = path.join(temp, 'preferences', 'launch-settings.json');
  const project = path.join(temp, 'business folder without git');
  const interpreter = JSON.parse((await promisify(execFile)(process.env.CREW_SMOKE_PYTHON || 'python',
    ['-c', 'import json,sys; print(json.dumps(sys.executable))'], {windowsHide:true, encoding:'utf8'})).stdout);
  const env = {...process.env};
  for (const key of Object.keys(env)) if (/^CREW_|^CLAUDE|^CODEX_/i.test(key)) delete env[key];
  // Keep Python and OS utilities; exclude the user's CLI installation folders.
  env.PATH = [path.dirname(interpreter), ...(process.platform === 'win32' ? [path.join(process.env.SystemRoot || 'C:\\Windows', 'System32')] : ['/usr/bin','/bin'])].join(path.delimiter);
  env.PYTHONPATH = plugin;
  env.PYTHONIOENCODING = 'utf-8';
  env.PYTHONUNBUFFERED = '1';
  function controlledExec(exe, args, options, callback) {
    return execFile(exe, args, {...options, env:{...options.env, ...env}}, callback);
  }
  const execute = (exe, args, options) => new Promise((resolve, reject) => {
    controlledExec(exe, args, options, (error, stdout, stderr) => error ? reject(Object.assign(error, {stdout, stderr})) : resolve({stdout, stderr}));
  });
  function controlledSpawn(exe, args, options) {
    return spawn(exe, args, {...options, env:{...options.env, ...env}});
  }
  const python = async (script, ...args) => JSON.parse((await execute(interpreter, ['-c', script, ...args],
    {cwd:plugin, windowsHide:true, encoding:'utf8', timeout:30000, maxBuffer:1024 * 1024})).stdout);
  const evidence = {ok:false, source, target, preferences, temporary_root:temp, checks:[], errors:[], actual_helpers:true};
  const check = (label, passed, detail) => { assert.ok(passed, label); evidence.checks.push({label, passed:true, ...(detail === undefined ? {} : {detail})}); };
  let first, second, third, updates;
  const configure = value => Object.assign(value, {bundledBackendDir:plugin, helperBackendDir:plugin,
    backendDir:value.backendDir || plugin, desktopVersion:require('../package.json').version, startupTimeoutMs:20000});
  try {
    const safe = await python('import json,shutil; print(json.dumps(not shutil.which("claude") and not shutil.which("codex")))');
    check('child PATH cannot discover native model CLIs', safe);
    const initial = configure(core.options(['--home', source, '--launch-settings', preferences, '--isolated-sources']));
    check('initial configuration uses only temporary HOME and preferences', initial.home === source && initial.launchSettingsPath === preferences && initial.isolated);
    first = new core.Backend(initial, {spawn:controlledSpawn});
    await first.start();
    check('first real isolated service is owned', first.owned && first.child.pid > 0);
    const seeded = await python(seed, source, project);
    check('fixture contains two identities and a queued goal without native AI', seeded.sessions.length === 2 && !seeded.native_cli_available);
    const historyBefore = await fs.readFile(path.join(source, 'chat', 'fixture-leader.jsonl'), 'utf8');
    const operations = new OperationsClient(initial, controlledExec);
    let requestedRelaunch = false;
    updates = new CrewUpdates(initial, first, {run:execute, spawnProcess:controlledSpawn,
      checkLatest:async () => { throw Error('Network version checks are not part of this isolated smoke'); }});
    const launch = new LaunchSettings(initial, {backend:first, updates, operations, bundledBackend:plugin, run:execute,
      onApplied:async saved => { requestedRelaunch = true; check('application callback sees saved target HOME', saved.home === target && readSync(preferences).home === target); }});
    const body = {settings:{mode:'local', home:target, source:'bundled', backend_directory:plugin,
      service_url:'', port:0, python:interpreter}, home_action:'copy'};
    const preview = await launch.validate(body);
    check('real HTTP preview lists leader and member', preview.impact.sessions.length === 2 && preview.impact.goals_count === 1);
    check('real Python HOME inspection permits a new directory', preview.inspection.can_copy && preview.impact.copy_required);
    check('preview leaves source service alive and creates no preferences', first.child.exitCode === null && await fs.stat(preferences).then(() => false, error => error.code === 'ENOENT'));
    const applied = await launch.apply(body);
    check('real prepare, stop, OS-exit check, copy and save complete', applied.ok && applied.restarting && requestedRelaunch && first.child.exitCode !== null);
    check('source service registry is removed after actual exit', await fs.stat(path.join(source, 'web.json')).then(() => false, error => error.code === 'ENOENT'));
    const archived = await fs.readdir(path.join(target, 'desktop', 'admin', 'deployment-history'));
    const originalDeployment = await fs.readFile(path.join(source, 'desktop', 'admin', 'deployment.json'), 'utf8');
    check('old portable deployment is archived byte-for-byte before applying selected source', archived.length === 1 &&
      await fs.readFile(path.join(target, 'desktop', 'admin', 'deployment-history', archived[0]), 'utf8') === originalDeployment &&
      await fs.stat(path.join(target, 'desktop', 'admin', 'deployment.json')).then(() => false, error => error.code === 'ENOENT'));
    const oldData = await python(inspectHome, source), copiedData = await python(inspectHome, target);
    check('source HOME remains as a readable backup', oldData.sessions.length === 2 && oldData.goals.length === 1 && oldData.integrity === 'ok');
    check('copy preserves identities and has no native bindings/executions', JSON.stringify(oldData.sessions) === JSON.stringify(copiedData.sessions) && copiedData.native.every(item => item.native_id === null) && copiedData.executions === 0);
    check('all goals are paused before copy with original request preserved', copiedData.goals.every(item => item.status === 'paused' && item.manual_pause === 1) && copiedData.goals[0].original_request === oldData.goals[0].original_request);
    check('history and attachments survive at the new HOME', await fs.readFile(path.join(target, 'chat', 'fixture-leader.jsonl'), 'utf8') === await fs.readFile(path.join(source, 'chat', 'fixture-leader.jsonl'), 'utf8') && (await fs.readFile(path.join(target, 'chat', 'files', 'fixture-member', 'fixture.txt'), 'utf8')) === 'existing attachment');
    check('original discussion remains in source backup', (await fs.readFile(path.join(source, 'chat', 'fixture-leader.jsonl'), 'utf8')).includes('Keep this existing discussion.') && historyBefore.includes('Keep this existing discussion.'));
    const reloaded = configure(core.options(['--launch-settings', preferences, '--isolated-sources']));
    check('relaunch arguments reload persisted HOME and Python', reloaded.home === target && reloaded.python === interpreter && reloaded.isolated && !reloaded.serviceUrl);
    second = new core.Backend(reloaded, {spawn:controlledSpawn});
    await second.start();
    const health = await core.request(second.url, '/api/admin/health');
    check('second real owned service runs from the new HOME and current checkout', second.owned && health.home === target && path.resolve(health.plugin_root) === plugin);
    const state = await core.request(second.url, '/api/state?project=lifecycle-fixture');
    check('new service exposes the same leader and member', state.leaders.some(item => item.session_id === 'fixture-leader') && state.members.some(item => item.session_id === 'fixture-member'));
    const finalData = await python(inspectHome, target);
    check('restart keeps goals paused and never creates an AI execution', finalData.goals[0].status === 'paused' && finalData.executions === 0 && !finalData.native_cli_available && finalData.native.every(item => item.native_id === null));
    const plan = await core.request(second.url, '/api/admin/connection/plan');
    check('new service reports zero model sessions running', plan.running_count === 0 && !plan.operations_chat_busy && !plan.repair_active);
    const offlineHome = path.join(temp, 'another pre-existing HOME');
    await python(seed, offlineHome, project);
    const offline = await python(seedOffline, offlineHome, project);
    const beforeOffline = await python(inspectHome, offlineHome);
    check('another existing offline HOME contains queued and running work and pending notices',
      beforeOffline.goals.some(item => item.status === 'queued') && beforeOffline.goals.some(item => item.status === 'running') && beforeOffline.pending_notices === 2);
    const offlineOperations = new OperationsClient(reloaded, controlledExec);
    updates = new CrewUpdates(reloaded, second, {run:execute, spawnProcess:controlledSpawn,
      checkLatest:async () => { throw Error('No online checks during HOME switching'); }});
    const selectExisting = new LaunchSettings(reloaded, {backend:second, updates, operations:offlineOperations,
      bundledBackend:plugin, run:execute, onApplied:async saved => { check('offline target is saved before requested relaunch', saved.home === offlineHome && readSync(preferences).home === offlineHome); }});
    const selected = await selectExisting.apply({settings:{...body.settings, home:offlineHome}, home_action:'existing'});
    check('choosing existing offline data really stops the previous owned service', selected.ok && second.child.exitCode !== null);
    const preparedOffline = await python(inspectHome, offlineHome);
    check('offline queued and interrupted goals are manually paused before startup',
      preparedOffline.goals.every(item => item.status === 'paused' && item.manual_pause === 1) &&
      preparedOffline.execution_statuses.find(item => item.id === offline.execution_id)?.status === 'paused');
    check('pending notices retain history and all adopted sessions receive stop gates',
      preparedOffline.pending_notices === 2 && ['fixture-leader','fixture-member','notice-only-leader'].every(id => preparedOffline.stop_gates.includes(id)));
    const thirdConfig = configure(core.options(['--launch-settings', preferences, '--isolated-sources']));
    third = new core.Backend(thirdConfig, {spawn:controlledSpawn});
    await third.start();
    await new Promise(resolve => setTimeout(resolve, 2300)); // Observe at least one real notice-delivery cadence.
    const afterOffline = await python(inspectHome, offlineHome);
    const offlinePlan = await core.request(third.url, '/api/admin/connection/plan');
    check('new service keeps offline goals and orphan execution paused without waking any model',
      third.owned && afterOffline.goals.every(item => item.status === 'paused' && item.manual_pause === 1) &&
      afterOffline.execution_statuses.every(item => item.status === 'paused') && offlinePlan.running_count === 0);
    check('real notice loop leaves both pending notices unsent and records no native wake error',
      afterOffline.pending_notices === 2 && afterOffline.notice_errors === 0 && !afterOffline.native_cli_available);
    evidence.offline_home = offlineHome;
    evidence.seeded = seeded;
    evidence.first_service = {url:first.url, exited:first.child.exitCode !== null};
    evidence.second_service = {url:second.url, home:health.home, plugin_root:health.plugin_root, version:health.version};
    evidence.ok = true;
  } catch (error) {
    evidence.errors.push(error.stack || String(error));
    process.exitCode = 1;
  } finally {
    updates?.cancel();
    for (const backend of [third, second, first]) if (backend) {
      try { await backend.stop(); }
      catch (error) { evidence.errors.push('Owned service cleanup: ' + error.message); evidence.ok = false; process.exitCode = 1; }
    }
    evidence.owned_services_exited = [first, second, third].filter(Boolean).every(item => !item.child || item.child.exitCode !== null);
    if (!evidence.owned_services_exited) { evidence.ok = false; process.exitCode = 1; }
    await fs.writeFile(path.join(output, 'evidence.json'), JSON.stringify(evidence, null, 2) + '\n');
    console.log(JSON.stringify({ok:evidence.ok, checks:evidence.checks.length, evidence:path.join(output, 'evidence.json'), owned_services_exited:evidence.owned_services_exited}));
    if (evidence.errors.length) console.error(evidence.errors.join('\n'));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
