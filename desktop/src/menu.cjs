'use strict';

function applicationMenuTemplate(platform, workspace, help) {
  const mac = platform === 'darwin';
  return [
    ...(mac ? [{ role:'appMenu' }] : []),
    { label:'工作区', submenu:workspace },
    mac ? { role:'editMenu' } : { label:'编辑', submenu:[{ role:'undo',label:'撤销' },{ role:'redo',label:'重做' },{ type:'separator' },{ role:'cut',label:'剪切' },{ role:'copy',label:'复制' },{ role:'paste',label:'粘贴' },{ role:'selectAll',label:'全选' }] },
    ...(mac ? [{ role:'windowMenu' }] : []),
    { label:'帮助', submenu:help }
  ];
}

function backAccelerator(platform) {
  return platform === 'darwin' ? 'Cmd+[' : 'Alt+Left';
}

function isBackShortcut(platform, input) {
  return platform === 'darwin'
    ? input.meta === true && !input.control && !input.alt && !input.shift && input.key === '['
    : input.alt === true && !input.control && !input.meta && input.key.toLowerCase() === 'arrowleft';
}

module.exports = { applicationMenuTemplate, backAccelerator, isBackShortcut };
