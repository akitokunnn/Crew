'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');

// One display mode for the whole desktop app: the home page, every workspace,
// settings, assistant, notes and the window chrome. Electron's themeSource
// drives prefers-color-scheme in every page, so pages only follow the media
// query. Browsers and phones reaching the backend directly keep their own choice.
const MODES = ['system', 'light', 'dark'];

class Appearance {
  constructor(directory, { nativeTheme }) {
    this.file = path.join(directory, 'appearance.json');
    this.nativeTheme = nativeTheme;
    this.mode = 'system';
    this.stored = false;
  }

  async load() {
    try {
      const value = JSON.parse(await fs.readFile(this.file, 'utf8'));
      if (MODES.includes(value?.mode)) { this.mode = value.mode; this.stored = true; }
      else console.error('Crew 显示模式记录无效，暂按跟随系统显示：' + this.file);
    } catch (error) {
      // A damaged record is kept for inspection; the next explicit choice replaces it.
      if (error.code !== 'ENOENT') console.error('无法读取 Crew 显示模式，暂按跟随系统显示：' + error.message);
    }
    this.nativeTheme.themeSource = this.mode;
    return this.state();
  }

  state() {
    return { mode: this.mode, dark: this.nativeTheme.shouldUseDarkColors, stored: this.stored };
  }

  async set(mode) {
    if (!MODES.includes(mode)) throw new Error('显示模式只能是跟随系统、浅色或深色。');
    await fs.mkdir(path.dirname(this.file), { recursive: true });
    const temporary = this.file + '.' + crypto.randomUUID() + '.tmp';
    try {
      await fs.writeFile(temporary, JSON.stringify({ mode }) + '\n', 'utf8');
      await fs.rename(temporary, this.file);
    } finally { await fs.rm(temporary, { force: true }); }
    this.mode = mode; this.stored = true;
    this.nativeTheme.themeSource = mode;
    return this.state();
  }
}

module.exports = { Appearance, MODES };
