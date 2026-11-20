'use strict';
const path = require('node:path');

function samePath(left, right, platform = process.platform) {
  if (typeof left !== 'string' || typeof right !== 'string') return false;
  const a = path.resolve(left), b = path.resolve(right);
  return platform === 'win32' || platform === 'darwin' ? a.toLowerCase() === b.toLowerCase() : a === b;
}

module.exports = { samePath };
