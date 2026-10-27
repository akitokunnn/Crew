"use strict";
const {create}=require("../crew/web/people-ui.js");
const esc=value=>String(value??"").replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
function node(){
  const classes=new Set();return{innerHTML:"",dataset:{},textContent:"",disabled:false,isConnected:true,
    classList:{add(...names){names.forEach(name=>classes.add(name));},remove(...names){names.forEach(name=>classes.delete(name));},contains:name=>classes.has(name)},
    focus(){},select(){},querySelectorAll:()=>[],getClientRects:()=>[{}]};
}
function fixture(people=()=>({leaders:[],members:[]}),overrides={}){
  const handlers=new Map(),nodes=new Map(),calls=[],notices=[];let restores=0,refreshes=0,changes=0;
  for(const id of ["card","card-back","backdrop","session-new-name","rename-error"])nodes.set(id,node());
  const submit=node(),input=nodes.get("session-new-name"),form={querySelectorAll:()=>[input,submit],querySelector:()=>submit};
  const card=nodes.get("card"),overlay=nodes.get("backdrop");
  card.querySelector=()=>node();overlay.querySelector=selector=>selector==="#session-rename-form"?form:selector.startsWith("#")?nodes.get(selector.slice(1)):node();
  const portal={innerHTML:"",querySelector:selector=>nodes.get(selector.slice(1))};
  const document={activeElement:null,addEventListener(type,handler){if(!handlers.has(type))handlers.set(type,new Set());handlers.get(type).add(handler);},removeEventListener(type,handler){handlers.get(type)?.delete(handler);}};
  let menuOpen=false,menuAnchor=null;
  const menu={show(anchor,html){menuAnchor=anchor;menuOpen=true;menu.html=html;},hide(){menuOpen=false;},contains:()=>false,anchor:()=>menuAnchor,isOpen:()=>menuOpen};
  const ports={document,portal,menu,person:id=>people().leaders.concat(people().members).find(item=>item.session_id===id),leaders:()=>people().leaders,
    members:id=>people().members.filter(item=>item.leader_session_id===id),inTask:()=>false,runtimeId:item=>item?.runtime||"claude",ordering:{sortable:item=>people().leaders.filter(person=>person.priority===item.priority)},
    modelMenu:()=>"",relatedGoals:()=>[],api:async(path,body)=>{calls.push({path,body});return{before:{fixture:true}};},
    refresh:async()=>refreshes++,notify:(...args)=>notices.push(args),compact:async()=>{},replace:(target,html)=>{target.innerHTML=html;},changed:()=>changes++,
    focus:{capture:()=>"opener",restore:()=>restores++},
    ui:{icon:name=>`<svg class="i ${name}"></svg>`,avatar:name=>`<span>${esc(name)}</span>`,status:()=>"",context:()=>"",model:()=>"",activity:()=>"",inline:esc,task:()=>"",date:String,time:String,runtime:()=>""},...overrides};
  const module=create(ports);
  return{module,ports,nodes,portal,document,handlers,menu,calls,notices,restores:()=>restores,refreshes:()=>refreshes,changes:()=>changes};
}
const person=(id,extra={})=>({session_id:id,name:id,runtime:"claude",priority:null,where:"offline",status:"idle",activity:null,scope:null,task:null,done_when:null,progress:[],codex:[],codex_older:0,leases:[],claims:[],branch:null,...extra});
module.exports={fixture,person,node};
