'use strict';
const { test } = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs/promises'), path = require('node:path'), os = require('node:os');
const { Appearance } = require('../src/appearance.cjs');
const chrome = require('../src/chrome.cjs');

async function directory(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'crew-appearance-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  return root;
}
const fakeTheme = () => ({ themeSource: 'unset', get shouldUseDarkColors() { return this.themeSource === 'dark'; } });

test('one desktop display mode drives Electron themeSource and survives restarts', async t => {
  const root = await directory(t), theme = fakeTheme();
  const first = new Appearance(root, { nativeTheme: theme });
  assert.deepEqual(await first.load(), { mode: 'system', dark: false, stored: false });
  assert.equal(theme.themeSource, 'system');
  assert.deepEqual(await first.set('dark'), { mode: 'dark', dark: true, stored: true });
  assert.equal(theme.themeSource, 'dark');
  const restarted = fakeTheme();
  assert.deepEqual(await new Appearance(root, { nativeTheme: restarted }).load(), { mode: 'dark', dark: true, stored: true });
  assert.equal(restarted.themeSource, 'dark');
  await assert.rejects(first.set('sepia'), /跟随系统、浅色或深色/);
  assert.equal(theme.themeSource, 'dark');
  assert.deepEqual((await fs.readdir(root)).sort(), ['appearance.json']);
});

test('a damaged record follows the system without being overwritten until the next choice', async t => {
  const root = await directory(t), theme = fakeTheme();
  await fs.writeFile(path.join(root, 'appearance.json'), '{broken');
  const errors = t.mock.method(console, 'error', () => {});
  assert.deepEqual(await new Appearance(root, { nativeTheme: theme }).load(), { mode: 'system', dark: false, stored: false });
  assert.equal(errors.mock.callCount(), 1);
  assert.equal(await fs.readFile(path.join(root, 'appearance.json'), 'utf8'), '{broken');
});

test('the window frame and Windows title bar overlay follow the display mode', () => {
  assert.equal(chrome.windowOptions('darwin', true).backgroundColor, chrome.DARK_COLOR);
  assert.equal(chrome.windowOptions('darwin').backgroundColor, chrome.COLOR);
  assert.deepEqual(chrome.windowOptions('win32', true).titleBarOverlay, { ...chrome.colors(true), height: chrome.HEIGHT });
  assert.deepEqual(chrome.windowOptions('win32', false).titleBarOverlay, { color: chrome.COLOR, symbolColor: '#5F5D56', height: chrome.HEIGHT });
});
