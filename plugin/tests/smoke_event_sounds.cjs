/* Real Chrome/Web Audio + HTTP/SQLite. Only AI carriers are replaced. */
'use strict';
const {chromium} = require('playwright');
const {spawn} = require('node:child_process');
const fs = require('node:fs/promises'), path = require('node:path'), assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..'), out = path.join(root, 'Temp/ui-align/batch3/event-sounds');
const evidence = {checks: [], screenshots: [], consoleErrors: []};
let fixture, browser;
async function main() {
  await fs.mkdir(out, {recursive: true});
  const env = {...process.env, PYTHONIOENCODING: 'utf-8'};
  for (const name of ['CREW_SESSION_ID', 'CLAUDE_CODE_SESSION_ID', 'CREW_EXECUTION_ID', 'CREW_GOAL_ID']) delete env[name];
  fixture = spawn('python', ['-u', path.join(__dirname, 'sound_ui_fixture.py')], {cwd:path.join(root, 'plugin'), env, windowsHide:true, stdio:['pipe', 'pipe', 'pipe']});
  let buffer = '', info;
  const commands = [];
  fixture.stderr.on('data', chunk => process.stderr.write(chunk));
  fixture.stdout.on('data', chunk => {
    buffer += chunk;
    let end;
    while ((end = buffer.indexOf('\n')) >= 0) {
      const value = JSON.parse(buffer.slice(0, end)); buffer = buffer.slice(end + 1);
      if (value.url) info = value;
      if (value.done) commands.shift()?.(value.done);
    }
  });
  async function command(action, scope, extra = {}) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('Fixture timeout: ' + action)), 12000);
      commands.push(value => {clearTimeout(timer); resolve(value);});
      fixture.stdin.write(JSON.stringify({action, scope, ...extra}) + '\n');
    });
  }
  const deadline = Date.now() + 20000;
  while (!info && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 50));
  assert(info, 'fixture bootstrap'); evidence.fixture = info;
  browser = await chromium.launch({headless:true, channel:'chrome', args:['--mute-audio']});
  const context = await browser.newContext({viewport:{width:1440, height:1000}, colorScheme:'light'});
  await context.addInitScript(() => {
    window.__audioNotes = []; window.__audioPeak = 0;
    const NativeContext = window.AudioContext;
    window.AudioContext = class extends NativeContext {
      createOscillator() {
        const oscillator = super.createOscillator(), start = oscillator.start.bind(oscillator);
        const set = oscillator.frequency.setValueAtTime.bind(oscillator.frequency);
        let frequency;
        oscillator.frequency.setValueAtTime = (value, at) => {frequency = value; return set(value, at);};
        oscillator.start = at => {window.__audioNotes.push({frequency, state:this.state}); return start(at);};
        return oscillator;
      }
      createGain() {
        const gain = super.createGain(), connect = gain.connect.bind(gain);
        gain.connect = (...args) => {
          const result = connect(...args);
          if (args[0] === this.destination) {
            const analyser = this.createAnalyser(); analyser.fftSize = 256; connect(analyser);
            const samples = new Float32Array(256), until = performance.now() + 1200;
            const sample = () => {
              analyser.getFloatTimeDomainData(samples);
              window.__audioPeak = Math.max(window.__audioPeak, ...samples.map(Math.abs));
              if (performance.now() < until) requestAnimationFrame(sample);
            };
            requestAnimationFrame(sample);
          }
          return result;
        };
        return gain;
      }
    };
  });
  const pages = [];
  async function newPage(url) {
    const page = await context.newPage(); pages.push(page);
    page.on('pageerror', error => evidence.consoleErrors.push(error.stack || String(error)));
    page.on('console', message => {if (message.type() === 'error') evidence.consoleErrors.push(message.text());});
    await page.goto(url); return page;
  }
  const page = await newPage(info.url + '/?project=demo#leader=' + info.leader);
  const legacyContext = await browser.newContext({viewport:{width:1440, height:1000}});
  await legacyContext.addInitScript(() => {
    localStorage.setItem('crew.sound.muted', 'true'); localStorage.setItem('crew.sound.volume', '0.62');
  });
  const legacy = await legacyContext.newPage();
  await legacy.goto(info.url + '/?project=demo#leader=' + info.leader);
  await legacy.waitForFunction(() => window.CrewSound?.getSettings().leader === 'soft');
  await page.locator('#chat-head [data-act="tasks-toggle"]').waitFor();
  await page.locator('#sidebar [data-act="settings"]').click();
  await page.locator('[data-settings-category="sounds"]').click();
  await page.locator('[data-sound-kind="leader"]').waitFor();
  await page.evaluate(() => document.fonts.ready);
  function check(label, pass) {evidence.checks.push(label); assert(pass, label);}
  async function shot(name) {await page.screenshot({path:path.join(out, name + '.png'), fullPage:true, animations:'disabled'}); evidence.screenshots.push(name + '.png');}
  const picker = kind => page.locator(`[data-sound-kind="${kind}"]`);
  check('An existing browser retains its old mute and volume before the first save', await legacy.evaluate(() => CrewSound.getSettings().muted && CrewSound.getSettings().volume === .62));
  const unconfigured = await (await page.request.get(info.url + '/api/settings')).json();
  check('Reading legacy sound preferences does not silently save or migrate settings', unconfigured.sounds === null);
  await picker('leader').selectOption('knock');
  await picker('assistant').selectOption('bell');
  await picker('delivery').selectOption('rise');
  await page.locator('[data-sound-volume]').evaluate(node => {node.value = '37'; node.dispatchEvent(new Event('input', {bubbles:true}));});
  await page.locator('[data-settings-category="appearance"]').click();
  check('Sound controls moved out of appearance', await page.locator('#settings [data-act="sound-mute"]').count() === 0);
  await page.locator('[data-settings-category="sounds"]').click();
  check('Changing categories preserves the unsaved sound draft', await picker('leader').inputValue() === 'knock' && await page.locator('[data-sound-volume]').inputValue() === '37');
  for (const [index, kind] of ['leader', 'assistant', 'delivery'].entries()) {
    await page.locator(`[data-sound-preview="${kind}"]`).click();
    const count = [2, 4, 7][index];
    await page.waitForFunction(expected => window.__audioNotes.length === expected, count);
    await shot('preview-' + kind);
  }
  await page.waitForFunction(() => window.__audioPeak > .0001);
  evidence.preview = await page.evaluate(() => ({notes:__audioNotes, peak:__audioPeak}));
  check('All three previews render real Web Audio samples', evidence.preview.notes.every(item => item.state === 'running') && evidence.preview.peak > .0001);
  await page.locator('[data-sound-save]').click();
  await page.locator('[data-sound-status]').filter({hasText:'音效已保存'}).waitFor();
  const saved = await (await page.request.get(info.url + '/api/settings')).json();
  check('Save persists independent choices and volume to the real backend', saved.sounds.leader === 'knock' && saved.sounds.assistant === 'bell' && saved.sounds.delivery === 'rise' && saved.sounds.volume === .37);
  await legacy.waitForFunction(() => !CrewSound.getSettings().muted && CrewSound.getSettings().volume === .37);
  check('An explicit save synchronizes the previously muted browser with shared preferences', true);
  await legacyContext.close();
  const stale = await (await page.request.get(info.url + '/api/state?project=demo')).json();
  let releaseState, stateStarted;
  const started = new Promise(resolve => {stateStarted = resolve;});
  const gate = new Promise(resolve => {releaseState = resolve;});
  let hold = true;
  const staleRoute = async route => {
    if (!hold) {await route.continue(); return;}
    hold = false; stateStarted(); await gate;
    await route.fulfill({status:200, contentType:'application/json', body:JSON.stringify(stale)});
  };
  await page.route('**/api/state?project=demo', staleRoute);
  await started;
  await page.evaluate(revision => {
    window.__originalSound = CrewSound; window.__staleSeen = false;
    window.CrewSound = {...CrewSound, observe(value, now) {__originalSound.observe(value, now); if (value?.revision === revision) window.__staleSeen = true;}};
  }, stale.sounds.revision);
  await page.locator('[data-sound-volume]').evaluate(node => {node.value = '41'; node.dispatchEvent(new Event('input', {bubbles:true}));});
  await page.locator('[data-sound-save]').click();
  await page.locator('[data-sound-status]').filter({hasText:'音效已保存'}).waitFor();
  releaseState(); await page.waitForFunction(() => __staleSeen);
  check('A delayed old state response cannot revert newly saved sound settings', await page.evaluate(() => CrewSound.getSettings().volume) === .41);
  await page.evaluate(() => {window.CrewSound = __originalSound;});
  await page.unroute('**/api/state?project=demo', staleRoute);
  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({width, height:900});
    await picker('leader').scrollIntoViewIfNeeded(); await shot(width + '-settings');
    check(width + ': sound controls have no horizontal overflow', await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth) && await picker('leader').evaluate(node => node.getBoundingClientRect().right <= innerWidth));
    await page.locator('[data-sound-save]').scrollIntoViewIfNeeded(); await shot(width + '-save');
  }
  await page.setViewportSize({width:1440, height:1000});
  await page.evaluate(() => {window.__audioNotes = [];});
  await command('stream', 'leader');
  await page.waitForTimeout(4500);
  check('Streaming a leader reply does not play a completion cue', await page.evaluate(() => __audioNotes.length) === 0);
  await command('finish', 'leader');
  await page.waitForFunction(() => __audioNotes.filter(item => item.frequency === 330).length === 2);
  check('Claude general reply plays the selected leader cue only after completion', true);
  await page.waitForTimeout(4500);
  check('The next poll does not replay the same completed reply', await page.evaluate(() => __audioNotes.length) === 2);
  await page.evaluate(() => {window.__audioNotes = [];});
  await command('complete', 'codex');
  await page.waitForFunction(() => __audioNotes.filter(item => item.frequency === 330).length === 2);
  check('Codex replies use the same configured leader cue', true);
  await page.evaluate(() => {window.__audioNotes = [];});
  await command('complete', 'task');
  await page.waitForFunction(() => __audioNotes.filter(item => item.frequency === 330).length === 2);
  check('Task-scoped leader replies use the leader cue', true);
  await page.evaluate(() => {window.__audioNotes = [];});
  await command('complete', 'assistant');
  await page.waitForFunction(() => __audioNotes.some(item => item.frequency === 650));
  check('The assistant cue works while a project page is open', await page.evaluate(() => __audioNotes.length) === 2);
  const assistant = await newPage(info.url + '/assistant.html');
  await assistant.locator('#assistant-chat textarea').waitFor(); await assistant.locator('h1').click();
  await page.evaluate(() => {window.__audioNotes = [];});
  await command('complete', 'assistant');
  await page.waitForTimeout(8500);
  const combined = (await Promise.all(pages.map(tab => tab.evaluate(() => __audioNotes.length)))).reduce((a, b) => a + b, 0);
  check('Project and assistant tabs play a shared completion only once', combined === 2);
  for (const tab of pages) await tab.evaluate(() => {window.__audioNotes = [];});
  await command('stage'); await page.waitForTimeout(4500);
  check('A stage report is not a delivery sound', (await Promise.all(pages.map(tab => tab.evaluate(() => __audioNotes.length)))).every(count => count === 0));
  const delivery = await command('delivery'); evidence.delivery = delivery;
  await page.waitForTimeout(8500);
  const delivered = (await Promise.all(pages.map(tab => tab.evaluate(() => __audioNotes)))).flat();
  check('Final delivery plays its own three-note cue once across tabs', delivered.length === 3 && delivered.some(item => item.frequency === 620));
  await assistant.close(); pages.pop();
  await picker('leader').selectOption('off'); await page.locator('[data-sound-save]').click();
  await page.locator('[data-sound-status]').filter({hasText:'音效已保存'}).waitFor();
  await page.evaluate(() => {window.__audioNotes = [];});
  await command('complete', 'leader'); await page.waitForTimeout(4500);
  check('Disabling only leader sound silences later leader completions', await page.evaluate(() => __audioNotes.length) === 0);
  await page.locator('[data-sound-enabled]').uncheck();
  await page.locator('[data-sound-save]').click();
  await page.locator('[data-sound-status]').filter({hasText:'音效已保存'}).waitFor();
  await command('complete', 'assistant'); await page.waitForTimeout(4500);
  check('The master switch silences assistant completion as well', await page.evaluate(() => __audioNotes.length) === 0 && await page.locator('[data-sound-preview="assistant"]').isDisabled());
  await page.locator('[data-sound-enabled]').check();
  await page.locator('[data-sound-volume]').evaluate(node => {node.value = '0'; node.dispatchEvent(new Event('input', {bubbles:true}));});
  await page.locator('[data-sound-save]').click();
  await page.locator('[data-sound-status]').filter({hasText:'音效已保存'}).waitFor();
  await command('complete', 'assistant'); await page.waitForTimeout(4500);
  check('Zero volume remains silent and disables preview', await page.evaluate(() => __audioNotes.length) === 0 && await page.locator('[data-sound-preview="assistant"]').isDisabled());
  await page.locator('[data-sound-volume]').evaluate(node => {node.value = '41'; node.dispatchEvent(new Event('input', {bubbles:true}));});
  await page.locator('[data-sound-save]').click();
  await page.locator('[data-sound-status]').filter({hasText:'音效已保存'}).waitFor();
  await page.reload(); await page.locator('#chat-head [data-act="tasks-toggle"]').waitFor();
  await page.locator('#sidebar [data-act="settings"]').click(); await page.locator('[data-settings-category="sounds"]').click();
  await picker('leader').waitFor();
  check('Reload retains saved choices and does not replay old completions', await picker('leader').inputValue() === 'off' && await page.evaluate(() => __audioNotes.length) === 0);
  await page.emulateMedia({colorScheme:'dark'}); await shot('dark-settings');
  assert.deepEqual(evidence.consoleErrors, []);
}
main().catch(error => {evidence.error = error.stack || String(error); process.exitCode = 1;}).finally(async () => {
  await browser?.close();
  if (fixture) {
    fixture.stdin.end(JSON.stringify({action:'shutdown'}) + '\n');
    await new Promise(resolve => {fixture.once('exit', resolve); setTimeout(() => {fixture.kill(); resolve();}, 5000).unref();});
  }
  await fs.writeFile(path.join(out, 'evidence.json'), JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify({out, checks:evidence.checks.length, consoleErrors:evidence.consoleErrors, error:evidence.error}));
});
