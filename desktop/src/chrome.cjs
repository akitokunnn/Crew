'use strict';

const HEIGHT = 36;
const COLOR = '#f3f3f3';
// The frame around the content follows the desktop display mode; it must match chrome.css.
const DARK_COLOR = '#171716';
const colors = dark => dark ? { color: DARK_COLOR, symbolColor: '#b9b6ad' } : { color: COLOR, symbolColor: '#525252' };

function pageKind(value, home, origin, errorPage) {
  if (value === home) return 'home';
  if (errorPage && value === errorPage) return 'error';
  try {
    const url = new URL(value);
    if (url.protocol === 'http:' && url.origin === origin && url.pathname === '/' && !url.username && !url.password) return 'workspace';
  } catch (_) { /* A navigation outside the application never receives chrome. */ }
  return null;
}

function windowOptions(platform = process.platform, dark = false) {
  const { color, symbolColor } = colors(dark);
  return {
    titleBarStyle: 'hidden',
    // The 12 pt macOS buttons get equal (36 - 12) / 2 pt top and bottom margins.
    ...(platform === 'darwin' ? { trafficLightPosition: { x:12, y:(HEIGHT - 12) / 2 } }
      : { titleBarOverlay: { color, symbolColor, height: HEIGHT }, autoHideMenuBar:true }),
    backgroundColor: color,
    resizable: true, minimizable: true, maximizable: true, closable: true
  };
}

module.exports = { HEIGHT, COLOR, DARK_COLOR, colors, pageKind, windowOptions };
