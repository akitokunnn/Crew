'use strict';

const permissionLabels = {media:'摄像头和麦克风',notifications:'桌面通知','clipboard-read':'读取剪贴板',fullscreen:'全屏显示'};

function installPermissions(session, {homeContents, homePage, workspaceContents, workspaceOrigin, requestPermission}) {
  const decisions = new WeakMap();
  function trusted(contents, details) {
    if (!contents || contents.isDestroyed() || !details.isMainFrame) return false;
    const url = contents.getURL();
    if (details.requestingUrl !== url) return false;
    if (contents === homeContents) return url === homePage;
    if (!workspaceContents(contents)) return false;
    return new URL(url).origin === workspaceOrigin();
  }
  function allowed(contents, permission, details) {
    return trusted(contents, details) && (permission === 'clipboard-sanitized-write' || decisions.get(contents)?.has(contents.getURL()+'|'+permission) === true);
  }
  session.setPermissionCheckHandler((contents, permission, _origin, details) => allowed(contents, permission, details));
  session.setPermissionRequestHandler(async (contents, permission, callback, details) => {
    if (!trusted(contents, details)) return callback(false);
    if (allowed(contents, permission, details)) return callback(true);
    if (!Object.hasOwn(permissionLabels, permission) || !requestPermission) return callback(false);
    const url = contents.getURL();
    try {
      const granted = await requestPermission(permissionLabels[permission]);
      if (!granted || !trusted(contents, details) || contents.getURL() !== url) return callback(false);
      if (!decisions.has(contents)) decisions.set(contents, new Set());
      decisions.get(contents).add(url+'|'+permission);
      callback(true);
    } catch (error) { console.error('Crew permission request failed:', error); callback(false); }
  });
}

module.exports = {installPermissions};
