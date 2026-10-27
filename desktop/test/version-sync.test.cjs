const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

test('Claude plugin metadata follows the desktop release version', () => {
  const desktop = require('../package.json');
  const pluginPath = path.resolve(__dirname, '../../plugin/.claude-plugin/plugin.json');
  const plugin = JSON.parse(fs.readFileSync(pluginPath, 'utf8'));
  assert.equal(plugin.version, desktop.version,
    'plugin/.claude-plugin/plugin.json must match desktop/package.json');
});
