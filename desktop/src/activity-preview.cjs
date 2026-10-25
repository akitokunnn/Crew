'use strict';
const path=require('node:path');
// A native overlay is needed: workspace WebContentsViews cover ordinary DOM
// popovers in the shell. This view has no workspace navigation or AI access.
class ActivityPreview {
  constructor(window,{createView,getTab,onSelect,setTimer=setTimeout,clearTimer=clearTimeout}) {
    Object.assign(this,{window,createView,getTab,onSelect,setTimer,clearTimer});
    this.view=null;this.id=null;this.ready=false;this.timer=null;this.epoch=0;this.disposed=false;
  }
  async show(id,anchor) {
    if(this.disposed||!this.getTab(id)||!anchor||!['x','y','width','height'].every(key=>Number.isFinite(anchor[key])))return;
    this.cancelHide();const epoch=++this.epoch;this.id=id;
    if(!this.view){
      this.view=this.createView();this.view.setVisible(false);
      this.view.setBackgroundColor?.('#00000000');
      this.window.contentView.addChildView(this.view);
      this.view.webContents.setWindowOpenHandler(()=>({action:'deny'}));
      this.view.webContents.on('will-navigate',event=>event.preventDefault());
      this.loading=this.view.webContents.loadFile(path.join(__dirname,'activity-preview.html'));
    }
    try{await this.loading;}catch(_){this.hide();return;}
    if(this.disposed||epoch!==this.epoch||!this.getTab(id))return;
    const bounds=this.window.getContentBounds(),width=Math.min(380,bounds.width-16);
    const x=Math.max(8,Math.min(Math.floor(anchor.x),bounds.width-width-8));
    this.view.setBounds({x,y:Math.max(0,Math.floor(anchor.y+anchor.height-1)),width,height:Math.min(450,Math.max(180,bounds.height-anchor.y-anchor.height-8))});
    // Reinsert above any project opened since the overlay was created.
    this.window.contentView.removeChildView(this.view);this.window.contentView.addChildView(this.view);
    this.view.setVisible(true);this.refresh();
  }
  refresh(){if(this.id&&this.ready&&!this.disposed){const tab=this.getTab(this.id);if(tab)this.view.webContents.send('desktop:activity-preview-state',tab);else this.hide();}}
  owns(event){return Boolean(this.view&&event.sender===this.view.webContents&&event.senderFrame===this.view.webContents.mainFrame);}
  markReady(event){if(this.owns(event)){this.ready=true;this.refresh();}}
  cancelHide(){if(this.timer!==null)this.clearTimer(this.timer);this.timer=null;}
  scheduleHide(){this.cancelHide();this.timer=this.setTimer(()=>this.hide(),180);}
  hide(){this.cancelHide();++this.epoch;this.id=null;this.view?.setVisible(false);}
  open(event,id){if(this.owns(event)&&id===this.id&&this.getTab(id)){this.hide();this.onSelect(id);}}
  dispose(){if(this.disposed)return;this.hide();this.disposed=true;if(this.view){this.window.contentView.removeChildView(this.view);this.view.webContents.close();}}
}
module.exports={ActivityPreview};
