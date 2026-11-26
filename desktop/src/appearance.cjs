'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');

// One display mode and one font size for the whole desktop app: the home page, every
// workspace, settings, assistant, notes and the window chrome. Electron's themeSource
// drives prefers-color-scheme in every page, so pages only follow the media query; the
// font size reaches pages through the same desktop bridge. Browsers and phones reaching
// the backend directly keep their own choice.
const MODES = ['system', 'light', 'dark'];
// Same range as the workspace settings slider (frontend/app.js FONT_MIN / FONT_MAX).
const FONT_MIN = 0.9, FONT_MAX = 1.4;
const validFont = value => typeof value === 'number' && Number.isFinite(value) && value >= FONT_MIN && value <= FONT_MAX;

class Appearance {
  constructor(directory, { nativeTheme }) {
    this.file = path.join(directory, 'appearance.json');
    this.nativeTheme = nativeTheme;
    this.mode = 'system';
    this.stored = false;
    // Standard size until someone picks one; fontStored lets a page carry over its older per-page choice once.
    this.fontScale = 1;
    this.fontStored = false;
  }

  async load() {
    try {
      const value = JSON.parse(await fs.readFile(this.file, 'utf8'));
      // Only the choices someone made are written, so a record may hold just the font size.
      const record = value !== null && typeof value === 'object' && !Array.isArray(value);
      const fontOnly = record && !('mode' in value) && 'fontScale' in value;
      if (MODES.includes(value?.mode)) { this.mode = value.mode; this.stored = true; }
      else if (!fontOnly) console.error('Crew 显示模式记录无效，暂按跟随系统显示：' + this.file);
      if (record && 'fontScale' in value) {
        if (validFont(value.fontScale)) { this.fontScale = value.fontScale; this.fontStored = true; }
        else console.error('Crew 字号记录无效，暂按标准字号显示：' + this.file);
      }
    } catch (error) {
      // A damaged record is kept for inspection; the next explicit choice replaces it.
      if (error.code !== 'ENOENT') console.error('无法读取 Crew 显示模式，暂按跟随系统显示：' + error.message);
    }
    this.nativeTheme.themeSource = this.mode;
    return this.state();
  }

  state() {
    return { mode: this.mode, dark: this.nativeTheme.shouldUseDarkColors, stored: this.stored, fontScale: this.fontScale, fontStored: this.fontStored };
  }

  async write(record) {
    await fs.mkdir(path.dirname(this.file), { recursive: true });
    const temporary = this.file + '.' + crypto.randomUUID() + '.tmp';
    try {
      await fs.writeFile(temporary, JSON.stringify(record) + '\n', 'utf8');
      await fs.rename(temporary, this.file);
    } finally { await fs.rm(temporary, { force: true }); }
  }

  record() {
    return { ...(this.stored ? { mode: this.mode } : {}), ...(this.fontStored ? { fontScale: this.fontScale } : {}) };
  }

  async set(mode) {
    if (!MODES.includes(mode)) throw new Error('显示模式只能是跟随系统、浅色或深色。');
    await this.write({ ...this.record(), mode });
    this.mode = mode; this.stored = true;
    this.nativeTheme.themeSource = mode;
    return this.state();
  }

  async setFontScale(value) {
    if (!validFont(value)) throw new Error('字号只能在 90% 到 140% 之间。');
    const scale = Math.round(value * 100) / 100;
    await this.write({ ...this.record(), fontScale: scale });
    this.fontScale = scale; this.fontStored = true;
    return this.state();
  }
}

module.exports = { Appearance, MODES, FONT_MIN, FONT_MAX };
