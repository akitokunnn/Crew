'use strict';
const { contextBridge, ipcRenderer } = require('electron');

// Workspace pages (this computer's backend or a LAN host) only read and change the
// desktop-wide display mode and font size through this bridge; nothing else crosses it.
// Pages show the reason itself, not Electron's "Error invoking remote method '<channel>': Error: " prefix.
const invoke = (channel, ...args) => ipcRenderer.invoke(channel, ...args).catch(error => {
  throw new Error(String(error?.message || error).replace(/^Error invoking remote method '[^']+':\s*(?:Error:\s*)?/, ''));
});
contextBridge.exposeInMainWorld('crewDesktopTheme', Object.freeze({
  get: () => invoke('desktop:theme'),
  set: mode => invoke('desktop:theme-set', mode),
  setFontScale: value => invoke('desktop:font-scale-set', value),
  onChange: callback => {
    if (typeof callback !== 'function') throw new TypeError('需要显示模式回调');
    const listener = (_event, state) => callback(state);
    ipcRenderer.on('desktop:theme', listener);
    return () => ipcRenderer.removeListener('desktop:theme', listener);
  }
}));
