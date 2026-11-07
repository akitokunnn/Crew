'use strict';
// Small DOM surface for the Git shell's four independent rendering regions.
module.exports=function gitHost({scroll={scrollTop:0},handlers=new Map()}={}){
  let markup='';const parts=new Map();
  return {get innerHTML(){return markup.replace(/(<div class="(?:head big )?(git-head|git-assistant-summary|git-page-in|git-operation-error)"[^>]*>)(<\/div>)/g,(_all,start,name,end)=>start+(parts.get('.'+name)?.innerHTML||'')+end);},
    set innerHTML(value){markup=value;parts.clear();},
    querySelector(selector){
      if(selector==='.git-page')return markup.includes('class="git-page"')?scroll:null;
      if(['.git-head','.git-assistant-summary','.git-page-in','.git-operation-error','#git-chat-host'].includes(selector)){
        if(!parts.has(selector))parts.set(selector,{innerHTML:'',addEventListener(){}});
        return parts.get(selector);
      }
      return null;
    },querySelectorAll:()=>[],addEventListener:(name,fn)=>handlers.set(name,fn),removeEventListener:name=>handlers.delete(name)};
};
