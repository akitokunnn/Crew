'use strict';
// A small code-drawn Crew mark; no image service or runtime package required.
const fs = require('node:fs'); const path = require('node:path'); const zlib = require('node:zlib');
function crc(buffer) { let value = 0xffffffff; for (const byte of buffer) { value ^= byte; for (let bit = 0; bit < 8; bit++) value = (value >>> 1) ^ ((value & 1) ? 0xedb88320 : 0); } return (value ^ 0xffffffff) >>> 0; }
function chunk(type, bytes) { const name = Buffer.from(type), size = Buffer.alloc(4), checksum = Buffer.alloc(4); size.writeUInt32BE(bytes.length); checksum.writeUInt32BE(crc(Buffer.concat([name, bytes]))); return Buffer.concat([size, name, bytes, checksum]); }
const size = 64, pixels = Buffer.alloc((size * 4 + 1) * size);
for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) { const at = y * (size * 4 + 1) + 1 + x * 4; const r = Math.hypot(x - 31.5, y - 31.5); const c = r > 14 && r < 23 && !(x > 33 && Math.abs(y - 31.5) < 13); pixels.set(c ? [235, 242, 218, 255] : [45, 75, 56, 255], at); }
const header = Buffer.alloc(13); header.writeUInt32BE(size, 0); header.writeUInt32BE(size, 4); header[8] = 8; header[9] = 6;
const output = path.resolve(__dirname, '../assets/crew.png'); fs.mkdirSync(path.dirname(output), { recursive: true }); fs.writeFileSync(output, Buffer.concat([Buffer.from('89504e470d0a1a0a', 'hex'), chunk('IHDR', header), chunk('IDAT', zlib.deflateSync(pixels)), chunk('IEND', Buffer.alloc(0))]));
