const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

test('Crew keeps one public product name across windows and desktop packaging', () => {
  const desktop = require('../package.json');
  const product = require('../src/product.cjs');
  const core = require('../src/core.cjs');
  assert.equal(core.options([]).instanceName, 'Crew');
  assert.equal(product.name, 'Crew');
  assert.equal(desktop.build.productName, 'Crew');
  assert.equal(desktop.build.nsis.shortcutName, 'Crew');
  assert.equal(desktop.build.nsis.uninstallDisplayName, 'Crew');
  for (const target of [desktop.build.win, desktop.build.portable, desktop.build.nsis, desktop.build.mac, desktop.build.dmg]) {
    assert.match(target.artifactName, /^Crew-\$\{version\}-/);
  }
  assert.equal(desktop.build.appId, product.appId);
  assert.equal(product.appId, 'local.crew.desktop');
  assert.equal(product.homeDirectory, '.crew');
  assert.equal(product.settingsDirectory, 'Crew');
});

test('Mac packaging uses arm64 DMG and ZIP with ad-hoc signing and Crew artwork', () => {
  const { scripts, build } = require('../package.json');
  assert.equal(scripts['prebuild:mac'], 'npm run check:version');
  assert.equal(scripts['build:mac'], 'node scripts/icon.cjs && electron-builder --mac dmg zip --arm64');
  assert.equal(build.mac.artifactName, 'Crew-${version}-mac-${arch}.${ext}');
  assert.equal(build.dmg.artifactName, 'Crew-${version}-mac-${arch}.dmg');
  assert.deepEqual(build.mac.target, [{target:'dmg',arch:['arm64']},{target:'zip',arch:['arm64']}]);
  assert.equal(build.mac.icon, 'assets/crew-1024.png');
  assert.equal(build.mac.category, 'public.app-category.developer-tools');
  assert.equal(build.mac.identity, '-');
  assert.equal(build.mac.hardenedRuntime, false);
  assert.equal(build.mac.gatekeeperAssess, false);
  assert.equal(build.mac.notarize, false);
});

test('Claude plugin metadata follows the desktop release version', () => {
  const desktop = require('../package.json');
  const pluginPath = path.resolve(__dirname, '../../plugin/.claude-plugin/plugin.json');
  const plugin = JSON.parse(fs.readFileSync(pluginPath, 'utf8'));
  assert.equal(plugin.version, desktop.version,
    'plugin/.claude-plugin/plugin.json must match desktop/package.json');
});
