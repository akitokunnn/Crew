const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

test('Crew keeps one public product name across windows and Windows packaging', () => {
  const desktop = require('../package.json');
  const product = require('../src/product.cjs');
  const core = require('../src/core.cjs');
  assert.equal(core.options([]).instanceName, 'Crew');
  assert.equal(product.name, 'Crew');
  assert.equal(desktop.build.productName, 'Crew');
  assert.equal(desktop.build.nsis.shortcutName, 'Crew');
  assert.equal(desktop.build.nsis.uninstallDisplayName, 'Crew');
  for (const target of [desktop.build.win, desktop.build.portable, desktop.build.nsis]) {
    assert.match(target.artifactName, /^Crew-\$\{version\}-/);
  }
  // Existing installs and histories must survive the visible rename.
  assert.equal(desktop.build.appId, product.appId);
  assert.equal(product.homeDirectory, '.crew-v2');
  assert.equal(product.settingsDirectory, 'Crew V2');
});

test('Claude plugin metadata follows the desktop release version', () => {
  const desktop = require('../package.json');
  const pluginPath = path.resolve(__dirname, '../../plugin/.claude-plugin/plugin.json');
  const plugin = JSON.parse(fs.readFileSync(pluginPath, 'utf8'));
  assert.equal(plugin.version, desktop.version,
    'plugin/.claude-plugin/plugin.json must match desktop/package.json');
});
