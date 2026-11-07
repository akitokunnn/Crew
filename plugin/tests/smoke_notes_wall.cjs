'use strict';
// Real Chromium (Electron) walk-through of the idea wall against the in-memory
// contract API in notes_fake_api.cjs. No Crew HOME, CLI or model is used.
// Run: desktop/node_modules/.bin/electron plugin/tests/smoke_notes_wall.cjs [output-dir]
const {app, BrowserWindow, nativeTheme} = require('electron');
const fs = require('node:fs/promises'), path = require('node:path'), http = require('node:http'), os = require('node:os');
const {createNotesApi} = require('./notes_fake_api.cjs');

const root = path.resolve(__dirname, '../..'), web = path.join(root, 'plugin/crew/web');
const output = path.resolve(process.argv.find(arg => arg.startsWith('--out='))?.slice(6) || path.join(root, 'Temp/notes-wall'));
const evidence = {errors: [], expectedHttpErrors: [], checks: [], screenshots: []};
const api = createNotesApi();
const types = {'.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.ico': 'image/x-icon'};
const project = {name: 'crew', display_name: 'Crew', root: 'C:/fixture/crew', theme: 'green'};
const workspaceState = {project, projects: [project], themes: [{id: 'green', name: '青绿', light: {'--accent': '#3E7C74', '--on-accent': '#F7FBFA', '--accent-text': '#2E6760'}, dark: {'--accent': '#83B9A8', '--on-accent': '#182A24', '--accent-text': '#A8D6C7'}}],
  runtimes: [], ai: {default: {runtime: 'codex', model: null, effort: null}, digest: null, git: null, operations: null}, git_assistant: null, leaders: [], members: [], goals: [], decisions: [], todos: [], attention: [],
  candidates: {running: [], others: []}, limits: {claude: {seven_day: null, five_hour: null}, codex: null}, channel: {unread: 0, latest_id: 0, digesting: false, digest_error: null, digest_id: null},
  notification: {manual_away: false, away: false, error: null, channels: []}, vscode_error: null};

async function serve(request, response) {
  const url = new URL(request.url, 'http://127.0.0.1');
  let body;
  if (request.method === 'POST') { let raw = ''; for await (const part of request) raw += part; body = JSON.parse(raw || '{}'); }
  if (!url.pathname.startsWith('/api/')) {
    const file = path.resolve(web, '.' + (url.pathname === '/' ? '/index.html' : url.pathname));
    if (!file.startsWith(web + path.sep)) { response.writeHead(403); response.end(); return; }
    try { const content = await fs.readFile(file); response.writeHead(200, {'Content-Type': types[path.extname(file)] || 'application/octet-stream'}); response.end(content); }
    catch (_) { response.writeHead(404); response.end(); }
    return;
  }
  if (url.pathname === '/api/chat/events') { response.writeHead(200, {'Content-Type': 'text/event-stream', 'X-Crew-Execution-Id': '', 'X-Crew-Latest-Seq': '1'}); response.write(': connected\n\n'); return; }
  if (url.pathname === '/api/state') { response.writeHead(200, {'Content-Type': 'application/json'}); response.end(JSON.stringify(workspaceState)); return; }
  if (url.pathname === '/api/runtimes') { response.writeHead(200, {'Content-Type': 'application/json'}); response.end(JSON.stringify({runtimes: []})); return; }
  let result;
  try { result = api.handle(request.method, url, body); }
  catch (error) { result = {status: error.status || 500, body: {error: error.message}}; }
  response.writeHead(result.status, {'Content-Type': 'application/json'}); response.end(JSON.stringify(result.body));
}

app.setPath('userData', path.join(os.tmpdir(), 'crew-notes-wall-smoke-' + process.pid));
app.whenReady().then(async () => {
  let server, win;
  try {
    await fs.mkdir(output, {recursive: true});
    server = http.createServer((request, response) => { serve(request, response).catch(error => { response.writeHead(500); response.end(String(error)); }); });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = `http://127.0.0.1:${server.address().port}`;
    nativeTheme.themeSource = 'light';
    win = new BrowserWindow({width: 1440, height: 900, show: false, webPreferences: {sandbox: true, contextIsolation: true, backgroundThrottling: false, offscreen: true}});
    const wc = win.webContents;
    wc.on('console-message', event => {
      if (event.level !== 'error') return;
      const expected = evidence.expectedHttpErrors.find(item => !item.seen && event.message.includes(`status of ${item.status}`));
      if (expected) expected.seen = true; else evidence.errors.push(event.message);
    });
    wc.on('render-process-gone', (_event, detail) => evidence.errors.push('render gone ' + JSON.stringify(detail)));
    const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
    const js = source => wc.executeJavaScript(source, true);
    const q = value => JSON.stringify(value);
    async function wait(source, label = source) {
      const end = Date.now() + 10000; while (Date.now() < end) { if (await js(source)) return; await pause(40); }
      await shot('failure'); throw Error(`Timed out: ${label}\nPage: ${await js('location.href+" | "+document.body.innerText.slice(0,1500)')}`);
    }
    async function check(label, source) { await wait(source, label); evidence.checks.push(label); }
    const click = selector => js(`(()=>{const n=document.querySelector(${q(selector)});if(!n)throw Error('missing '+${q(selector)});n.click();return true;})()`);
    const clickText = (selector, text) => js(`(()=>{const n=[...document.querySelectorAll(${q(selector)})].find(n=>n.textContent.trim().includes(${q(text)}));if(!n)throw Error('missing '+${q(selector + ' ' + text)});n.click();return true;})()`);
    async function key(keyCode, modifiers = []) {
      wc.sendInputEvent({type: 'keyDown', keyCode, modifiers});
      if (keyCode.length === 1 || keyCode === 'Return') wc.sendInputEvent({type: 'char', keyCode: keyCode === 'Return' ? String.fromCharCode(13) : keyCode, modifiers});
      wc.sendInputEvent({type: 'keyUp', keyCode, modifiers}); await pause(120);
    }
    async function type(text) { wc.insertText(text); await pause(120); }
    async function shot(name) {
      await js('document.fonts.ready.then(()=>true)'); await pause(300);
      wc.debugger.attach('1.3');
      try { const result = await wc.debugger.sendCommand('Page.captureScreenshot', {format: 'png', fromSurface: true}); const file = path.join(output, name + '.png'); await fs.writeFile(file, Buffer.from(result.data, 'base64')); evidence.screenshots.push(file); }
      finally { wc.debugger.detach(); }
    }
    async function size(width, height) { win.setContentSize(width, height); await pause(400); }
    async function open(pathname) { await win.loadURL(origin + pathname); }
    // Chromium may log a deliberately provoked HTTP failure as a console error; only those are excused.
    const expectHttp = (status, why) => evidence.expectedHttpErrors.push({status, why, seen: false});
    const noteCard = id => `#nw-wall .nw-card[data-id=${q(id)}]`;
    const lastSend = () => api.sent.at(-1);

    // 1. Wall layout: pinned, needs review, time bands, side rail.
    await open('/notes.html');
    await check('cards flow into several masonry columns', `new Set([...document.querySelectorAll('#nw-wall .nw-card')].map(n=>Math.round(n.getBoundingClientRect().left))).size>=3`);
    await check('wall renders pinned, needs-review and time sections in order', `(()=>{const b=[...document.querySelectorAll('#nw-wall .nw-band')].map(n=>n.firstChild.textContent).join('|');return b.startsWith('置顶|需要回顾|今天|')&&b.endsWith('|更早')})()`);
    await check('stale ideas are marked and the review button counts them', `document.querySelectorAll('#nw-wall .nw-card.stale').length===3&&document.querySelector('#nw-review .nw-badge').textContent==='3'`);
    await check('side rail lists tags with counts and the archive views', `(()=>{const t=document.querySelector('#nw-side').textContent;return t.includes('产品')&&t.includes('需要回顾')&&t.includes('已转任务')&&t.includes('已归档')&&document.querySelector('#nw-side [data-act=view][data-value=promoted] .nw-side-count').textContent==='1'})()`);
    await check('assistant-recorded idea shows its source and a near-duplicate pair is flagged', `document.querySelector(${q(noteCard('wall'))}).textContent.includes('助手代记')&&document.querySelector(${q(noteCard('git-cards') + ' .nw-dup')})&&document.querySelector(${q(noteCard('git-cards-2') + ' .nw-dup')})`);
    await check('long body is clamped to six lines with an expand control', `(()=>{const b=document.querySelector(${q(noteCard('long') + ' .nw-body')});return b.scrollHeight>b.clientHeight&&!b.nextElementSibling.hidden})()`);
    await shot('01-想法墙-浅色-宽屏');

    // 2. Quick capture with # completion; current filter tags ride along later.
    await js(`document.querySelector('#nw-input').focus()`);
    await type('Git 页要能看到 PR 状态 #体');
    await check('# opens tag completion with matching tags', `!document.querySelector('#nw-suggest').hidden&&document.querySelector('#nw-suggest').textContent.includes('体验')`);
    await shot('02-快速输入-标签补全');
    await key('Return');
    await check('Enter on a completion inserts the tag instead of submitting', `document.querySelector('#nw-input').value==='Git 页要能看到 PR 状态 #体验 '`);
    await type('@Cr');
    await check('@ opens workspace completion', `document.querySelector('#nw-suggest').textContent.includes('Crew')`);
    await key('Return');
    await check('choosing a workspace shows it as a chip and removes the token', `document.querySelector('#nw-capture-extra').textContent.includes('工作区：Crew')&&!document.querySelector('#nw-input').value.includes('@')`);
    await key('Return', ['shift']);
    await type('第二行写细节');
    await check('Shift+Enter inserts a newline', `document.querySelector('#nw-input').value.includes('\\n第二行写细节')`);
    await key('Return');
    await check('Enter records the idea with tags and workspace through capture', `(()=>{const c=[...document.querySelectorAll('#nw-wall .nw-card')].find(n=>n.textContent.includes('Git 页要能看到 PR 状态'));return c&&c.textContent.includes('体验')&&c.textContent.includes('Crew')&&document.querySelector('#nw-input').value===''})()`);
    const captured = api.calls.filter(call => call.name === '/api/personal/notes' && call.method === 'POST').at(-1).body;
    if (captured.action !== 'capture' || captured.data.project !== 'crew' || captured.data.tags.join() !== '体验' || captured.data.text !== 'Git 页要能看到 PR 状态\n第二行写细节' || !captured.request_id) throw Error('capture body ' + JSON.stringify(captured));
    evidence.checks.push('capture body is {action:"capture", data:{text,tags,project}, request_id}');
    await shot('03-记下一条');

    // 3. Tag panel (T) on the focused card.
    await js(`document.querySelector(${q(noteCard('handoff'))}).focus()`);
    await key('T');
    await check('T opens the tag panel for the focused card', `document.querySelector('dialog.nw-tag-panel')?.open&&document.querySelector('dialog.nw-tag-panel h2').textContent.includes('组员会话断了自动交接')`);
    await type('产');
    await check('tag panel filters by fuzzy search', `[...document.querySelectorAll('#nw-tag-options [role=option]')].map(n=>n.textContent).join()==='产品,新建标签「产」'`);
    await shot('04-标签面板');
    await key('Space');
    await check('Space toggles the tag on the card through update', `document.querySelector(${q(noteCard('handoff'))}).textContent.includes('产品')`);
    await key('Escape');
    await wait(`!document.querySelector('dialog.nw-tag-panel')`);

    // 4. Filters: tag chip, all/any, URL hash survives reload, new ideas carry filter tags.
    await click(`${noteCard('wall')} .nw-tag[data-tag="产品"]`);
    await click('#nw-side [data-act=filter-tag][data-tag="AI"]');
    await check('two tags filter with all-of by default', `location.hash.includes('tags=')&&document.querySelectorAll('#nw-wall .nw-card').length===1`);
    await click('#nw-filters [data-act=mode][data-value=any]');
    await check('any-of widens the wall and is written to the hash', `location.hash.includes('mode=any')&&document.querySelectorAll('#nw-wall .nw-card').length>3`);
    await wc.reload(); await wait(`document.querySelectorAll('#nw-wall .nw-card').length>3`);
    await check('filters survive a reload from the URL hash', `document.querySelector('#nw-filters [data-act=mode][data-value=any]').getAttribute('aria-checked')==='true'&&document.querySelectorAll('#nw-filters .nw-tag-on').length===2`);
    await check('capture shows the filter tags it will add', `document.querySelector('#nw-capture-extra').textContent.includes('#产品')`);
    await shot('05-筛选-任一-刷新保留');
    await click('#nw-filters [data-act=clear-filters]');
    await wait(`location.hash===''`);
    await js(`(()=>{const s=document.querySelector('#nw-filters [data-act=scope]');s.value='global';s.dispatchEvent(new Event('change',{bubbles:true}));return true})()`);
    await check('workspace filter narrows to global ideas', `location.hash==='#global=1'&&[...document.querySelectorAll('#nw-wall .nw-meta')].every(n=>n.textContent.startsWith('全局'))`);
    await click('#nw-filters [data-act=group][data-value=stage]');
    await check('stage grouping shows new and discussed sections', `[...document.querySelectorAll('#nw-wall .nw-band')].map(n=>n.firstChild.textContent).join('|')==='新想法|讨论过'`);
    await shot('06-按阶段分组');
    await click('#nw-filters [data-act=clear-filters]'); await click('#nw-filters [data-act=group][data-value=time]');
    await wait(`location.hash===''`);

    // 5. ⋯ menu: pin.
    await click(`${noteCard('handoff')} [data-act=menu]`);
    await check('the ⋯ menu lists edit, pin, tags, merge, snooze, archive, original and summaries', `[...document.querySelectorAll('.nw-menu [role=menuitem]')].map(n=>n.textContent).join()==='编辑,置顶,打标签,合并,暂缓…,归档,最初记录,讨论结论'`);
    await shot('07-更多菜单');
    await clickText('.nw-menu [role=menuitem]', '置顶');
    await check('pinning moves the card into the pinned section', `document.querySelector('#nw-wall .nw-section').querySelector(${q(noteCard('handoff').replace('#nw-wall ', ''))})`);

    // 6. Merge from the duplicate hint, then undo from the archive.
    await click(`${noteCard('git-cards')} .nw-dup`);
    await check('duplicate hint opens merge with both cards included', `document.querySelectorAll('dialog.nw-merge [data-include]:checked').length===2&&document.querySelector('dialog.nw-merge [type=submit]').textContent==='把 1 条并入主卡'`);
    await shot('08-合并-可能重复');
    await click('dialog.nw-merge [type=submit]');
    await check('merge archives the duplicate and unions tags on the primary', `!document.querySelector(${q(noteCard('git-cards-2'))})&&document.querySelector(${q(noteCard('git-cards'))}).textContent.includes('合并了 1 条')&&!document.querySelector('.nw-dup')`);
    await click('#nw-side [data-act=view][data-value=archived]');
    await check('archived view shows the merged card with undo', `document.querySelector(${q(noteCard('git-cards-2'))})?.textContent.includes('已并入')&&document.querySelector(${q(noteCard('git-cards-2') + ' [data-act=unmerge]')})`);
    await shot('09-已归档-撤销合并');
    await click(`${noteCard('git-cards-2')} [data-act=unmerge]`);
    await check('undo merge returns the card to the wall', `!document.querySelector(${q(noteCard('git-cards-2'))})`);
    await click('#nw-side [data-act=view][data-value=open]');
    await wait(`document.querySelector(${q(noteCard('git-cards-2'))})`);

    // 7. Review mode (R): progress, K / H / A, arrows.
    await js(`document.activeElement.blur()`);
    await key('R');
    await check('R opens review over the needs-review queue with progress', `document.querySelector('dialog.nw-review')?.open&&document.querySelector('.nw-review-progress').textContent.includes('需要回顾 · 第 1 / 3 张')`);
    await shot('10-回顾模式');
    await key('K');
    await check('K keeps the idea and moves on', `document.querySelector('.nw-review-progress').textContent.includes('已处理 1')`);
    await key('H');
    await check('H snoozes for seven days', `document.querySelector('.nw-review-progress').textContent.includes('已处理 2')`);
    await key('Left');
    await check('← goes back to a handled card', `document.querySelector('.nw-review-done')`);
    await key('Right'); await key('A');
    await check('A archives and the round finishes', `document.querySelector('dialog.nw-review h2').textContent==='回顾完了'`);
    await shot('11-回顾完成');
    await key('Escape');
    await check('after review nothing needs review', `!document.querySelector('#nw-review .nw-badge')&&!document.querySelector('#nw-wall .nw-card.stale')`);
    const snoozed = api.notes().find(note => note.snoozed_until), kept = api.notes().find(note => note.reviewed_at);
    if (!snoozed || Math.round((Date.parse(snoozed.snoozed_until) - Date.now()) / 86400000) !== 7 || !kept) throw Error('review writes ' + JSON.stringify({snoozed, kept}));
    evidence.checks.push('review wrote keep (reviewed_at) and a 7-day snooze through the contract');

    // 8. Tag management: recolour, then rename into an existing name -> confirm merge.
    await click('#nw-side [data-act=manage-tags]');
    await check('tag manager lists tags', `document.querySelectorAll('dialog.nw-manage .nw-manage-list li').length>=5`);
    await js(`document.querySelector('dialog.nw-manage li[data-tag="通知"] summary').click();true`);
    await click('dialog.nw-manage li[data-tag="通知"] [data-color="c4"]');
    await check('recolour applies a palette key', `document.querySelector('dialog.nw-manage li[data-tag="通知"] summary').classList.contains('tag-c4')`);
    expectHttp(409, 'rename into an existing tag asks for a merge');
    await js(`(()=>{const i=document.querySelector('dialog.nw-manage li[data-tag="体验"] .nw-rename');i.focus();i.value='产品';return true})()`);
    await key('Return');
    const affected = api.notes().filter(note => note.tags.includes('体验')).length;
    await check('renaming onto an existing tag asks to merge with the affected count', `document.querySelector('dialog.nw-confirm')?.textContent.includes('合并 体验 → 产品（影响 ${affected} 条）')`);
    await shot('12-标签改名撞名-确认合并');
    await click('dialog.nw-confirm [data-ok]');
    await check('merge removes the old tag and keeps its ideas under the target', `!document.querySelector('dialog.nw-manage li[data-tag="体验"]')&&document.querySelector('dialog.nw-manage .nw-form-status').textContent==='合并标签完成。'`);
    await js(`document.querySelector('dialog.nw-manage li[data-tag="AI"] [data-tag-act=pin]').click();true`);
    await wait(`document.querySelector('dialog.nw-manage li[data-tag="AI"] [data-tag-act=pin]').getAttribute('aria-pressed')==='true'`);
    await shot('13-管理标签');
    await key('Escape');
    if (api.tags().some(tag => tag.name === '体验') || api.notes().filter(note => note.tags.includes('产品')).length < 5) throw Error('tag merge not applied');
    evidence.checks.push('rename 409 merge_required -> tags merge {from:["体验"], into:"产品"}');

    // 9. Multi-select and bulk tag.
    await click('#nw-select-mode');
    await js(`for(const id of ['long','stale-search']){const c=document.querySelector('#nw-wall .nw-card[data-id="'+id+'"] .nw-check');if(c)c.click();}true`);
    await check('bulk bar counts the selection', `!document.querySelector('#nw-bulk').hidden&&document.querySelector('#nw-bulk b').textContent==='2'`);
    await js(`document.querySelector('#nw-bulk [data-act=bulk-tags]').click();true`);
    await type('通');
    await key('Space');
    await check('bulk tag_add reaches every selected card', `document.querySelector(${q(noteCard('long'))}).textContent.includes('通知')`);
    await key('Escape');
    await shot('14-多选批量');
    await click('#nw-bulk [data-act=bulk-clear]');

    // 10. Promote: the card leaves the wall and appears under 已转任务.
    await click(`${noteCard('long')} [data-act=promote]`);
    await js(`(()=>{const s=document.querySelector('dialog.nw-promote select');s.value='crew';return true})()`);
    await shot('15-转任务');
    await click('dialog.nw-promote [type=submit]');
    await check('a promoted idea disappears from the wall', `!document.querySelector(${q(noteCard('long'))})&&document.querySelector('#nw-toast').textContent.includes('已转任务')`);
    const start = api.calls.filter(call => call.name === '/api/personal/notes/start').at(-1).body;
    if (start.note_id !== 'long' || start.project !== 'crew' || !start.request_id) throw Error('start body ' + JSON.stringify(start));
    await click('#nw-side [data-act=view][data-value=promoted]');
    await check('promoted view shows the linked leader', `document.querySelector(${q(noteCard('long'))})?.querySelector('a[href*="leader=leader-long"]')`);
    await shot('16-已转任务');
    await click('#nw-side [data-act=view][data-value=open]');

    // 11. Error state shows the real reason and recovers on retry.
    api.failOnce('/api/personal/notes', 500, 'fixture：数据库暂时被占用');
    expectHttp(500, 'injected read failure');
    await wc.reload();
    await check('a failed read shows the server reason with retry', `document.querySelector('#nw-error').textContent.includes('fixture：数据库暂时被占用')&&!document.querySelector('#nw-error').hidden`);
    await shot('17-读取失败');
    await click('#nw-error button');
    await check('retry recovers the wall', `document.querySelector('#nw-error').hidden&&document.querySelectorAll('#nw-wall .nw-card').length>3`);

    // 12. Empty state for a filter without matches.
    await click('#nw-side [data-act=view][data-value=stale]');
    await check('empty needs-review view explains itself', `document.querySelector('#nw-wall .nw-empty h2').textContent.includes('没有需要回顾的想法')`);
    await shot('18-空状态');
    await click('#nw-side [data-act=view][data-value=open]');

    // 13. 去聊聊 -> assistant with the idea chip, prefilled, not sent until the user sends.
    await click(`${noteCard('wall')} [data-act=chat]`);
    await wait(`location.pathname==='/assistant.html'`);
    await check('assistant attaches the idea chip and prefills the opening line', `!document.querySelector('.chat-note-chip').hidden&&document.querySelector('.chat-note-chip').textContent.includes('想法：想法墙')&&document.querySelector('#assistant-chat textarea').value==='帮我把这个想法想清楚'`);
    if (api.sent.length) throw Error('opening the assistant sent a message');
    evidence.checks.push('opening 去聊聊 sent nothing');
    await shot('19-去聊聊-助手页想法标签');
    await click('#assistant-chat .send');
    await wait(`document.querySelector('.chat-note-chip').hidden`);
    if (lastSend().note_id !== 'wall' || lastSend().text !== '帮我把这个想法想清楚' || JSON.stringify(lastSend()).includes('卡片能维护')) throw Error('send body ' + JSON.stringify(lastSend()));
    await check('the hash is cleared after the idea was sent', `location.hash===''`);
    evidence.checks.push('send carried note_id once and no idea text');

    // 14. 随手记 on the assistant page.
    await click('#assistant-notes');
    await check('随手记 shows one box, the three latest ideas and the wall link', `document.querySelector('.jot-dialog')?.open&&document.querySelectorAll('.jot-dialog .pn-item').length===3&&document.querySelector('.jot-dialog a[href="/notes.html"]')`);
    await js(`document.querySelector('.jot-dialog [name=text]').focus()`);
    await type('随手记下的一条');
    await key('Return');
    await check('随手记 Enter records and lists it first', `document.querySelector('.jot-dialog .pn-item').textContent.includes('随手记下的一条')`);
    await shot('20-随手记');
    await key('Escape');

    // 15. Workspace rail entry goes to the wall.
    await open('/?project=crew');
    await check('workspace rail 想法 links to the idea wall', `document.querySelector('#sidebar a.rail-button[href="/notes.html"]')?.getAttribute('aria-label')==='想法墙'&&!document.querySelector('[data-act="personal-notes"]')`);

    // 16. Dark theme and narrow width.
    nativeTheme.themeSource = 'dark'; await open('/notes.html');
    await wait(`document.documentElement.dataset.theme==='dark'&&document.querySelectorAll('#nw-wall .nw-card').length>3`);
    await check('dark tag chips meet 4.5:1 contrast', `(()=>{const lum=c=>{const v=c.match(/\\d+/g).slice(0,3).map(x=>{x/=255;return x<=.03928?x/12.92:((x+.055)/1.055)**2.4});return .2126*v[0]+.7152*v[1]+.0722*v[2]};return [...document.querySelectorAll('#nw-wall .nw-tag')].every(n=>{const s=getComputedStyle(n),a=lum(s.color),b=lum(s.backgroundColor);return (Math.max(a,b)+.05)/(Math.min(a,b)+.05)>=4.5})})()`);
    await shot('21-暗色-想法墙');
    await click('#nw-side [data-act=manage-tags]'); await js(`document.querySelector('dialog.nw-manage li summary').click();true`);
    await shot('22-暗色-管理标签色板');
    await key('Escape'); await key('Escape');
    nativeTheme.themeSource = 'light';
    await open('/notes.html');
    await check('light tag chips meet 4.5:1 contrast', `(()=>{const lum=c=>{const v=c.match(/\\d+/g).slice(0,3).map(x=>{x/=255;return x<=.03928?x/12.92:((x+.055)/1.055)**2.4});return .2126*v[0]+.7152*v[1]+.0722*v[2]};const all=[...document.querySelectorAll('#nw-wall .nw-tag')];return all.length>0&&all.every(n=>{const s=getComputedStyle(n),a=lum(s.color),b=lum(s.backgroundColor);return (Math.max(a,b)+.05)/(Math.min(a,b)+.05)>=4.5})})()`);
    await size(400, 860);
    await check('narrow layout: one column, no side rail, no horizontal scroll', `getComputedStyle(document.querySelector('#nw-side')).display==='none'&&document.documentElement.scrollWidth<=innerWidth&&document.querySelector('#nw-main').scrollWidth<=document.querySelector('#nw-main').clientWidth&&getComputedStyle(document.querySelector('[data-act=manage-tags].nw-narrow-only')).display!=='none'`);
    await shot('23-窄屏-想法墙');
    await js(`document.activeElement.blur()`); await key('R');
    await wait(`document.querySelector('dialog.nw-review')?.open`);
    await shot('24-窄屏-回顾');
    await key('Escape');
    nativeTheme.themeSource = 'dark'; await pause(400);
    await shot('25-窄屏-暗色');
    nativeTheme.themeSource = 'light';
    await size(1440, 900);

    evidence.ok = evidence.errors.length === 0;
  } catch (error) { evidence.ok = false; evidence.error = error.stack; }
  finally {
    await fs.writeFile(path.join(output, 'evidence.json'), JSON.stringify(evidence, null, 2));
    console.log(JSON.stringify({ok: evidence.ok, checks: evidence.checks.length, errors: evidence.errors, error: evidence.error, output}, null, 2));
    server?.close();
    app.exit(evidence.ok ? 0 : 1);
  }
});
