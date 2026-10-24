'use strict';

const HEIGHT = 36;
const COLOR = '#f3f3f3';

function pageKind(value, home, origin, errorPage) {
  if (value === home) return 'home';
  if (errorPage && value === errorPage) return 'error';
  try {
    const url = new URL(value);
    if (url.protocol === 'http:' && url.origin === origin && url.pathname === '/' && !url.username && !url.password) return 'workspace';
  } catch (_) { /* A navigation outside the application never receives chrome. */ }
  return null;
}

function windowOptions(platform = process.platform) {
  return {
    titleBarStyle: 'hidden',
    ...(platform === 'darwin' ? {} : { titleBarOverlay: { color: COLOR, symbolColor: '#525252', height: HEIGHT } }),
    autoHideMenuBar: true,
    backgroundColor: COLOR,
    resizable: true, minimizable: true, maximizable: true, closable: true
  };
}

module.exports = { HEIGHT, COLOR, pageKind, windowOptions };
