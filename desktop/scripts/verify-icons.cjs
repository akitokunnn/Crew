'use strict';
// Read-only verification of actual PE resources. Never launches the executable.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { parseIco } = require('./icon.cjs');
const digest = value => crypto.createHash('sha256').update(value).digest('hex');

function peResources(bytes) {
  if (bytes.length < 64 || bytes.toString('ascii', 0, 2) !== 'MZ') throw new Error('Not a Windows PE executable');
  const pe = bytes.readUInt32LE(0x3c);
  if (pe + 24 > bytes.length || bytes.toString('ascii', pe, pe + 4) !== 'PE\0\0') throw new Error('Invalid PE header');
  const count = bytes.readUInt16LE(pe + 6), optionalSize = bytes.readUInt16LE(pe + 20), optional = pe + 24;
  const magic = bytes.readUInt16LE(optional), directory = optional + (magic === 0x20b ? 112 : magic === 0x10b ? 96 : -1);
  if (directory < optional || directory + 24 > optional + optionalSize) throw new Error('Invalid PE optional header');
  const resourceRva = bytes.readUInt32LE(directory + 16), sections = [];
  for (let index = 0; index < count; index++) {
    const at = optional + optionalSize + index * 40;
    sections.push({ rva: bytes.readUInt32LE(at + 12), rawSize: bytes.readUInt32LE(at + 16), offset: bytes.readUInt32LE(at + 20) });
  }
  function fileOffset(rva, length = 1) {
    const section = sections.find(item => rva >= item.rva && rva + length <= item.rva + item.rawSize);
    if (!section || section.offset + rva - section.rva + length > bytes.length) throw new Error('PE resource points outside a section');
    return section.offset + rva - section.rva;
  }
  if (!resourceRva) throw new Error('Executable has no resource directory');
  const root = fileOffset(resourceRva), entries = [], seen = new Set();
  function visit(relative, keys = []) {
    if (keys.length > 3 || seen.has(relative)) throw new Error('Invalid PE resource tree');
    seen.add(relative);
    const at = root + relative, size = bytes.readUInt16LE(at + 12) + bytes.readUInt16LE(at + 14);
    for (let index = 0; index < size; index++) {
      const item = at + 16 + index * 8, name = bytes.readUInt32LE(item), value = bytes.readUInt32LE(item + 4);
      let id = name;
      if (name & 0x80000000) {
        const start = root + (name & 0x7fffffff), length = bytes.readUInt16LE(start);
        id = bytes.toString('utf16le', start + 2, start + 2 + length * 2);
      }
      if (value & 0x80000000) visit(value & 0x7fffffff, [...keys, id]);
      else {
        const dataAt = root + value, rva = bytes.readUInt32LE(dataAt), length = bytes.readUInt32LE(dataAt + 4);
        const start = fileOffset(rva, length);
        entries.push({ type: keys[0], id: keys[1], lang: id, data: bytes.subarray(start, start + length) });
      }
    }
  }
  visit(0);
  return entries;
}

function verifyExecutable(executable, icon = path.resolve(__dirname, '../assets/crew.ico')) {
  const expected = parseIco(fs.readFileSync(icon)), resources = peResources(fs.readFileSync(executable));
  const groups = resources.filter(item => item.type === 14).map(group => {
    const count = group.data.readUInt16LE(4), frames = [];
    for (let index = 0; index < count; index++) {
      const at = 6 + index * 14, id = group.data.readUInt16LE(at + 12);
      const image = resources.find(item => item.type === 3 && item.id === id && item.lang === group.lang);
      if (!image) throw new Error('PE group icon references a missing RT_ICON');
      frames.push({ width: group.data[at] || 256, height: group.data[at + 1] || 256, sha256: digest(image.data) });
    }
    const custom = expected.every(frame => frames.some(item => item.width === frame.width && item.height === frame.height &&
      item.sha256 === digest(frame.data)));
    return { id: group.id, lang: group.lang, custom, frames };
  });
  if (!groups.some(group => group.custom)) throw new Error('Executable does not embed the complete custom Crew icon; default or missing application icon');
  return { executable: path.resolve(executable), icon: path.resolve(icon), verified: true,
    expectedSizes: expected.map(item => item.width), groups };
}

if (require.main === module) {
  if (process.argv.length < 3) throw new Error('Usage: node scripts/verify-icons.cjs <executable> [...]');
  try { for (const executable of process.argv.slice(2)) console.log(JSON.stringify(verifyExecutable(executable), null, 2)); }
  catch (error) { console.error(error.message); process.exitCode = 1; }
}
module.exports = { peResources, verifyExecutable };
