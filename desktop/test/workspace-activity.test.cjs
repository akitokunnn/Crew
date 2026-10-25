'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),{EventEmitter}=require('node:events');
const {presentation}=require('../src/workspace-activity.cjs');
const {ActivityPreview}=require('../src/activity-preview.cjs');
const snapshot=extra=>({counts:{working:3,decisions:0,todos:0,approvals:0,waiting:0,acceptance:0,unread:1,errors:0,...extra},items:[],updated_at:'2026-10-26T00:00:00Z'});
test('tab indicators separate work, human action, delivery and unread without claiming old reports are new work',()=>{
 assert.equal(presentation(snapshot()).tone,'working');assert.equal(presentation(snapshot()).badge,0);
 assert.equal(presentation(snapshot({decisions:2})).badge,2);assert.equal(presentation(snapshot({decisions:2})).tone,'attention');
 assert.equal(presentation(snapshot({acceptance:1})).tone,'delivery');
 assert.equal(presentation(snapshot({errors:1})).tone,'error');
 assert.equal(presentation(snapshot(),true,'Disconnected').tone,'stale');
 assert.equal(presentation(null,true,'Disconnected').label,'暂时无法读取项目动态');
});
function fixture(){
 const wc=new EventEmitter();wc.mainFrame={};wc.messages=[];wc.send=(...v)=>wc.messages.push(v);wc.close=()=>wc.closed=true;wc.setWindowOpenHandler=handler=>wc.popup=handler;wc.loadFile=async()=>{};
 const view={webContents:wc,visible:false,setVisible(v){this.visible=v;},setBounds(v){this.bounds=v;}};
 const children=[],tabs=new Map([['one',{id:'one',name:'MIB',activity:presentation(snapshot())}]]),selected=[];
 let timer=null;
 const preview=new ActivityPreview({getContentBounds:()=>({width:900,height:650}),contentView:{addChildView:v=>children.push(v),removeChildView:v=>children.splice(children.indexOf(v),1)}},
  {createView:()=>view,getTab:id=>tabs.get(id),onSelect:id=>selected.push(id),setTimer:fn=>(timer=fn,1),clearTimer:()=>timer=null});
 return {preview,view,wc,tabs,selected,children,event:{sender:wc,senderFrame:wc.mainFrame},fire:()=>timer?.()};
}
test('native preview is above workspace views, scoped to its own main frame, and never navigates the workspace',async()=>{
 const f=fixture();await f.preview.show('one',{x:850,y:40,width:180,height:30});f.preview.markReady(f.event);
 assert.equal(f.view.visible,true);assert(f.view.bounds.x+f.view.bounds.width<=900);assert.equal(f.wc.messages[0][1].name,'MIB');
 assert.deepEqual(f.wc.popup(),{action:'deny'});
 f.preview.open({sender:f.wc,senderFrame:{}},'one');assert.equal(f.selected.length,0);
 f.preview.open(f.event,'one');assert.deepEqual(f.selected,['one']);assert.equal(f.view.visible,false);
 f.preview.dispose();assert.equal(f.children.length,0);assert(f.wc.closed);
});
test('crossing from the tab into its preview keeps it open and a removed project cannot survive refresh',async()=>{
 const f=fixture();await f.preview.show('one',{x:100,y:40,width:180,height:30});
 f.preview.scheduleHide();f.preview.cancelHide();f.fire();assert(f.view.visible);
 f.tabs.clear();f.preview.markReady(f.event);assert.equal(f.view.visible,false);
 f.preview.dispose();
});
