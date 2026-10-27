"use strict";
// The real attention feature with explicit presentation/I/O ports for tests of
// another module. No application source rewriting or private state injection.
const {create}=require("../crew/web/attention.js");
const escape=value=>String(value??"").replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
function createAttention(state,document,overrides={}) {
  return create({state,document,api:async()=>{throw Error("Unexpected attention request");},refresh:async()=>{},notify(){},changed(){},navigate(){},
    rich:{inline:escape,markdown:text=>`<p>${escape(text)}</p>`},
    ui:{icon:name=>`<svg class="i ${name}"></svg>`,avatar:name=>`<span class="av">${escape(name)}</span>`,priority:()=>"",date:String,ago:()=>"刚刚",empty:text=>`<div class="empty">${escape(text)}</div>`},...overrides});
}
module.exports={createAttention};
