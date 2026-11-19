'use strict';
const path = require('node:path');

function bundlePaths(desktop, platform = process.platform, application) {
  const mac = platform === 'darwin';
  const root = application ? path.resolve(application) : path.join(desktop, 'dist', mac ? 'mac-arm64/Crew.app' : 'win-unpacked');
  return {
    application: root,
    executable: path.join(root, mac ? 'Contents/MacOS/Crew' : 'Crew.exe'),
    resources: path.join(root, mac ? 'Contents/Resources' : 'resources'),
  };
}

module.exports = { bundlePaths };
