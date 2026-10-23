'use strict';
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('crewDesktop', Object.freeze({
  bootstrap: () => ipcRenderer.invoke('desktop:bootstrap'),
  chooseExisting: () => ipcRenderer.invoke('desktop:existing'),
  chooseParent: () => ipcRenderer.invoke('desktop:parent'),
  createWorkspace: name => ipcRenderer.invoke('desktop:create', name),
  openProject: name => ipcRenderer.invoke('desktop:open', name),
  ready: info => ipcRenderer.invoke('desktop:ready', info)
}));
