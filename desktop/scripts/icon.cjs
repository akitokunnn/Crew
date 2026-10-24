'use strict';
// Deterministic, dependency-free rasterization of Crew's existing SVG mark.
// The open ring AND separate point come from plugin/crew/web/logo.svg. No font,
// Electron placeholder or generated bitmap is used as the application identity.
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const crypto = require('node:crypto');

const SIZES = Object.freeze([16, 20, 24, 32, 40, 48, 64, 128, 256, 512, 1024]);
const ICO_SIZES = Object.freeze([16, 24, 32, 48, 64, 128, 256]);
const MARK_PATH = 'M14.26 8.54 A4.90 4.90 0 1 0 14.26 15.46';
const COLORS = Object.freeze({ tile: [41, 49, 46], ring: [246, 245, 241], point: [138, 198, 184] });
const SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 24 24">
  <title>Crew</title>
  <desc>Crew's original open-ring and separate crew-point mark, adapted for desktop.</desc>
  <rect x="0.5" y="0.5" width="23" height="23" rx="6" fill="#29312E"/>
  <path d="${MARK_PATH}" fill="none" stroke="#F6F5F1" stroke-width="2.8" stroke-linecap="round"/>
  <circle cx="17.4" cy="16.4" r="1.75" fill="#8AC6B8"/>
</svg>
`;

function crc(buffer) {
  let value = 0xffffffff;
  for (const byte of buffer) {
    value ^= byte;
    for (let bit = 0; bit < 8; bit++) value = (value >>> 1) ^ ((value & 1) ? 0xedb88320 : 0);
  }
  return (value ^ 0xffffffff) >>> 0;
}
function chunk(type, bytes) {
  const name = Buffer.from(type), length = Buffer.alloc(4), checksum = Buffer.alloc(4);
  length.writeUInt32BE(bytes.length);
  checksum.writeUInt32BE(crc(Buffer.concat([name, bytes])));
  return Buffer.concat([length, name, bytes, checksum]);
}
function png(width, height, rgba) {
  const header = Buffer.alloc(13), rows = Buffer.alloc((width * 4 + 1) * height);
  header.writeUInt32BE(width, 0); header.writeUInt32BE(height, 4); header[8] = 8; header[9] = 6;
  for (let y = 0; y < height; y++) rgba.copy(rows, y * (width * 4 + 1) + 1, y * width * 4, (y + 1) * width * 4);
  return Buffer.concat([Buffer.from('89504e470d0a1a0a', 'hex'), chunk('IHDR', header),
    chunk('IDAT', zlib.deflateSync(rows, { level: 9 })), chunk('IEND', Buffer.alloc(0))]);
}

// Exact circular arc geometry of the original SVG, including rounded line caps.
const radius = 4.9, halfGap = 3.46, centerX = 14.26 - Math.sqrt(radius * radius - halfGap * halfGap);
function sample(x, y) {
  const dx = Math.max(Math.abs(x - 12) - 5.5, 0), dy = Math.max(Math.abs(y - 12) - 5.5, 0);
  if (dx * dx + dy * dy > 36) return null;
  if ((x - 17.4) ** 2 + (y - 16.4) ** 2 <= 1.75 ** 2) return COLORS.point;
  const cx = x - centerX, cy = y - 12;
  const onArc = (cx <= 0 || Math.abs(cy) * Math.sqrt(radius * radius - halfGap * halfGap) >= cx * halfGap) &&
    Math.abs(Math.hypot(cx, cy) - radius) <= 1.4;
  const onCap = (x - 14.26) ** 2 + Math.min((y - 8.54) ** 2, (y - 15.46) ** 2) <= 1.4 ** 2;
  return onArc || onCap ? COLORS.ring : COLORS.tile;
}
function raster(size) {
  if (!Number.isInteger(size) || size < 1 || size > 2048) throw new Error('Icon size must be between 1 and 2048');
  const rgba = Buffer.alloc(size * size * 4), grid = size <= 64 ? 8 : 3, samples = grid * grid;
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    let red = 0, green = 0, blue = 0, count = 0;
    for (let sy = 0; sy < grid; sy++) for (let sx = 0; sx < grid; sx++) {
      const color = sample((x + (sx + 0.5) / grid) * 24 / size, (y + (sy + 0.5) / grid) * 24 / size);
      if (color) { red += color[0]; green += color[1]; blue += color[2]; count++; }
    }
    if (count) {
      const at = (y * size + x) * 4;
      rgba[at] = Math.round(red / count); rgba[at + 1] = Math.round(green / count);
      rgba[at + 2] = Math.round(blue / count); rgba[at + 3] = Math.round(255 * count / samples);
    }
  }
  return rgba;
}
function renderPng(size) { return png(size, size, raster(size)); }
function makeIco(images) {
  const header = Buffer.alloc(6), directories = [], payloads = [];
  header.writeUInt16LE(1, 2); header.writeUInt16LE(images.length, 4);
  let offset = 6 + images.length * 16;
  for (const { size, data } of images) {
    if (size > 256 || size < 1) throw new Error('ICO frame dimensions must be at most 256');
    const entry = Buffer.alloc(16);
    entry[0] = size === 256 ? 0 : size; entry[1] = entry[0];
    entry.writeUInt16LE(1, 4); entry.writeUInt16LE(32, 6);
    entry.writeUInt32LE(data.length, 8); entry.writeUInt32LE(offset, 12);
    directories.push(entry); payloads.push(data); offset += data.length;
  }
  return Buffer.concat([header, ...directories, ...payloads]);
}
function parseIco(bytes) {
  if (bytes.length < 6 || bytes.readUInt16LE(0) !== 0 || bytes.readUInt16LE(2) !== 1) throw new Error('Invalid ICO header');
  const count = bytes.readUInt16LE(4), frames = [];
  if (!count || bytes.length < 6 + count * 16) throw new Error('Invalid ICO directory');
  for (let index = 0; index < count; index++) {
    const at = 6 + index * 16, length = bytes.readUInt32LE(at + 8), offset = bytes.readUInt32LE(at + 12);
    if (offset < 6 + count * 16 || offset + length > bytes.length) throw new Error('ICO frame exceeds file');
    frames.push({ width: bytes[at] || 256, height: bytes[at + 1] || 256,
      bitCount: bytes.readUInt16LE(at + 6), data: bytes.subarray(offset, offset + length) });
  }
  return frames;
}
function writeAssets(directory = path.resolve(__dirname, '../assets')) {
  fs.mkdirSync(directory, { recursive: true });
  fs.writeFileSync(path.join(directory, 'crew.svg'), SVG);
  const images = new Map(SIZES.map(size => [size, renderPng(size)]));
  for (const [size, data] of images) fs.writeFileSync(path.join(directory, `crew-${size}.png`), data);
  fs.writeFileSync(path.join(directory, 'crew.png'), images.get(256));
  fs.writeFileSync(path.join(directory, 'crew-tray.png'), images.get(32));
  const ico = makeIco(ICO_SIZES.map(size => ({ size, data: images.get(size) })));
  fs.writeFileSync(path.join(directory, 'crew.ico'), ico);
  const manifest = { source: 'plugin/crew/web/logo.svg', mark: 'open ring plus separate point',
    palette: { tile: '#29312E', ring: '#F6F5F1', point: '#8AC6B8' }, icoSizes: ICO_SIZES,
    files: Object.fromEntries(['crew.svg', 'crew.png', 'crew-tray.png', 'crew.ico', ...SIZES.map(size => `crew-${size}.png`)].map(name =>
      [name, crypto.createHash('sha256').update(fs.readFileSync(path.join(directory, name))).digest('hex')])) };
  fs.writeFileSync(path.join(directory, 'crew-icons.json'), JSON.stringify(manifest, null, 2) + '\n');
  return manifest;
}

if (require.main === module) {
  if (process.argv.length > 3) throw new Error('Usage: node scripts/icon.cjs [output-directory]');
  const manifest = writeAssets(process.argv[2]);
  console.log(`Crew icon: ${SIZES.length} PNG sizes; Windows ICO ${manifest.icoSizes.join('/')}; original Crew vector mark`);
}
module.exports = { SIZES, ICO_SIZES, MARK_PATH, SVG, COLORS, raster, renderPng, makeIco, parseIco, writeAssets, png };
