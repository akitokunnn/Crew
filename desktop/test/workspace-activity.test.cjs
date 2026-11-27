'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),{EventEmitter}=require('node:events');
const {presentation}=require('../src/workspace-activity.cjs');
const {ActivityPreview}=require('../src/activity-preview.cjs');
const snapshot=extra=>({counts:{working:0,working_leaders:0,decisions:0,todos:0,approvals:0,waiting:0,acceptance:0,attention:0,chat_unread:0,channel_unread:0,errors:0,...extra},items:[],updated_at:new Date().toISOString()});
test('the avatar ring and one right marker are independent; the right marker uses error, attention, unread, freshness order',()=>{
 for (const [counts, marker, working, markerCount] of [
  [{},null,false,0], [{working:1},null,true,0], [{chat_unread:1},'unread',false,0],
  [{working:1,attention:2,channel_unread:3},'attention',true,2],
  [{working:1,errors:4,attention:2,chat_unread:3},'error',true,4]
 ]) {
  const view=presentation(snapshot(counts));
  assert.deepEqual([view.marker,view.working,view.markerCount],[marker,working,markerCount]);
 }
 assert.equal(presentation(null).marker,'unknown');
 assert.equal(presentation({...snapshot(), unknown:true}).marker,'unknown');
 assert.equal(presentation(snapshot(),true).marker,'unknown');
 const stale=presentation(snapshot({working:2,chat_unread:1}),true,'Disconnected');
 assert.equal(stale.marker,'unread');assert.equal(stale.working,false);
 assert(!stale.rows.some(row=>row.label==='正在推进'));assert.match(stale.label,/状态暂未更新/);
 const old=snapshot({working:1});
 const time=Date.parse(old.updated_at);
 assert.equal(presentation(old,false,'',time+15000).working,true);
 assert.equal(presentation(old,false,'',time+15001).working,false);
});
test('the preview keeps four rows, names error reasons, and splits both unread sources',()=>{
 const value=snapshot({attention:2,approvals:1,decisions:1,working:3,working_leaders:2,errors:1,chat_unread:4,channel_unread:6});
 value.items=[{kind:'approval',title:'权限确认命令'},{kind:'decision',title:'选方案'},{kind:'error',name:'视觉',detail:'执行超时'}];
 value.war_rooms=[{title:'交付演练',status:'running'},{title:'接口评审',status:'awaiting_decision'}];
 const view=presentation(value);
 assert.deepEqual(view.rows.map(row=>row.label),['等你处理','执行异常','正在推进','未读']);
 assert.deepEqual(view.rows[0].detail,['拍板 1 · 权限确认 1 · 等你定 1','权限确认命令','选方案']);
 assert.deepEqual(view.rows[1].detail,['视觉 · 执行超时']);
 assert.deepEqual(view.rows[2].detail,['2 位 Leader 在推进','另有 1 个会话在推进','交付演练 · 作战室中','接口评审 · 等你定']);
 assert.deepEqual(view.rows[3].detail,['对话 4 条 · 交流区 6 条']);
 assert(!presentation(snapshot()).rows.some(row=>row.detail.some(line=>line.includes('作战室'))));
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
