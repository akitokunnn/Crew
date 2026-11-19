'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { inflateSync } = require('node:zlib');
const icon = require('../scripts/icon.cjs');
const { peResources } = require('../scripts/verify-icons.cjs');
const assets = path.resolve(__dirname, '../assets');

test('desktop vector retains the actual Crew open-ring and separate-point brand', () => {
  const web = fs.readFileSync(path.resolve(__dirname, '../../plugin/crew/web/logo.svg'), 'utf8');
  const svg = fs.readFileSync(path.join(assets, 'crew.svg'), 'utf8');
  assert.ok(web.includes(icon.MARK_PATH));
  assert.ok(svg.includes(icon.MARK_PATH));
  assert.match(svg, /<circle cx="17\.4" cy="16\.4" r="1\.75"/);
  assert.doesNotMatch(svg, /<text\b|font-family|Electron|data:image/);
});

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

test('16px taskbar image has transparent rounded corners, a bright ring, and a distinct teal point', () => {
  const pixels = icon.raster(16);
  const at = (x, y) => [...pixels.subarray((y * 16 + x) * 4, (y * 16 + x + 1) * 4)];
  assert.equal(at(0, 0)[3], 0);
  assert.deepEqual(at(8, 8), [...icon.COLORS.tile, 255]);
  assert.ok(at(4, 8)[0] > 200, 'open ring must survive small-size rasterization');
  const point = at(11, 10);
  assert.ok(point[1] > point[0] + 20 && point[1] > point[2], 'crew point must remain a separate teal mark');
});

test('both Mac tray template PNGs contain a black ring and point on transparent pixels', () => {
  for (const [file,size] of [['crew-trayTemplate.png',16],['crew-trayTemplate@2x.png',32]]) {
    const png = fs.readFileSync(path.join(assets,file));
    assert.equal(png.readUInt32BE(16),size);
    assert.equal(png.readUInt32BE(20),size);
    const data = [];
    for (let offset = 8; offset < png.length;) {
      const count = png.readUInt32BE(offset), kind = png.toString('ascii',offset+4,offset+8);
      if (kind === 'IDAT') data.push(png.subarray(offset+8,offset+8+count));
      if (kind === 'pHYs') assert.equal(png.readUInt32BE(offset+8),Math.round(size * 72 / 16 / 0.0254));
      offset += 12+count;
    }
    const rows = inflateSync(Buffer.concat(data)), stride = 1+size*4;
    assert.equal(rows.length,stride*size);
    const alpha = (x,y) => rows[y*stride+4+x*4];
    for (let y=0;y<size;y++) {
      assert.equal(rows[y*stride],0);
      for (let x=0;x<size;x++) assert.deepEqual([...rows.subarray(y*stride+1+x*4,y*stride+4+x*4)],[0,0,0]);
    }
    assert.equal(alpha(0,0),0);
    assert.equal(alpha(size/2,size/2),0);
    assert.ok(alpha(size/4,size/2)>128,'ring is visible');
    assert.ok(alpha(size===16?11:23,size===16?10:21)>128,'separate point is visible');
  }
});

test('icon manifest verifies actual files and malformed ICO / PE input is rejected', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(assets, 'crew-icons.json')));
  for (const [name, expected] of Object.entries(manifest.files)) {
    assert.equal(crypto.createHash('sha256').update(fs.readFileSync(path.join(assets, name))).digest('hex'), expected);
  }
  assert.throws(() => icon.parseIco(Buffer.alloc(6)), /ICO/);
  assert.throws(() => peResources(Buffer.alloc(64)), /PE/);
});
