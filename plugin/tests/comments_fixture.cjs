"use strict";
const {create}=require("../crew/web/comments.js");
const escape=value=>String(value??"").replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
function portal(document){const events=new Map(),classes=new Set();return{dataset:{},innerHTML:"",events,
  classList:{add:name=>classes.add(name),remove:name=>classes.delete(name),contains:name=>classes.has(name)},
  querySelector:selector=>selector.startsWith("#")?document.getElementById(selector.slice(1)):null,querySelectorAll:()=>[],
  addEventListener(type,fn){events.set(type,fn);},removeEventListener(type){events.delete(type);}};}
function createComments(people,document,overrides={}) {
  return create({people,document,portal:portal(document),replace:(host,html)=>{host.innerHTML=html;},
    api:async()=>({comments:[]}),markdown:text=>`<p>${escape(text)}</p>`,notify(){},focus:{capture:()=>null,restore(){}},
    ui:{icon:name=>`<svg class="i ${name}"></svg>`,date:String,ago:()=>"刚刚"},...overrides});
}
module.exports={createComments,portal};
