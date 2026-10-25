'use strict';
const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('crewActivity',Object.freeze({
  onState(callback){ipcRenderer.on('desktop:activity-preview-state',(_event,state)=>callback(state));ipcRenderer.send('desktop:activity-preview-ready');},
  open:id=>ipcRenderer.send('desktop:activity-preview-open',id),
  hover:inside=>ipcRenderer.send('desktop:activity-preview-hover',Boolean(inside))
}));
