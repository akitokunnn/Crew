'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const icon = require('../scripts/icon.cjs');
const { peResources } = require('../scripts/verify-icons.cjs');
const assets = path.resolve(__dirname, '../assets');

test('PNG sizes and seven Windows ICO frames contain the same custom images', () => {
  const frames = icon.parseIco(fs.readFileSync(path.join(assets, 'crew.ico')));
  assert.deepEqual(frames.map(frame => frame.width), [16, 24, 32, 48, 64, 128, 256]);
  for (const size of icon.SIZES) {
    const png = fs.readFileSync(path.join(assets, `crew-${size}.png`));
    assert.equal(png.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
    assert.equal(png.readUInt32BE(16), size); assert.equal(png.readUInt32BE(20), size);
    const frame = frames.find(item => item.width === size);
    if (frame) { assert.equal(frame.height, size); assert.equal(frame.bitCount, 32); assert.deepEqual(frame.data, png); }
  }
  assert.deepEqual(fs.readFileSync(path.join(assets, 'crew.png')), fs.readFileSync(path.join(assets, 'crew-256.png')));
  assert.deepEqual(fs.readFileSync(path.join(assets, 'crew-tray.png')), fs.readFileSync(path.join(assets, 'crew-32.png')));
});

test('icon manifest verifies actual files and malformed ICO / PE input is rejected', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(assets, 'crew-icons.json')));
  for (const [name, expected] of Object.entries(manifest.files)) {
    assert.equal(crypto.createHash('sha256').update(fs.readFileSync(path.join(assets, name))).digest('hex'), expected);
  }
  assert.throws(() => icon.parseIco(Buffer.alloc(6)), /ICO/);
  assert.throws(() => peResources(Buffer.alloc(64)), /PE/);
});
