"use strict";
// Features own their obstruction rules. Chat and notifications consume the same
// query instead of learning the DOM shape or private state of every new portal.
(() => {
  function create() {
    const blockers=new Map();
    return {
      register(owner,blocked){if(blockers.has(owner))throw Error(`UI visibility owner already registered: ${owner}`);const entry={blocked};blockers.set(owner,entry);return()=>{if(blockers.get(owner)===entry)blockers.delete(owner);};},
      blocked:()=>[...blockers.values()].some(entry=>entry.blocked()),
      owners:()=>[...blockers.keys()],destroy(){blockers.clear();}
    };
  }
  const exported={create};if(typeof module!=="undefined"&&module.exports)module.exports=exported;else window.CrewUIVisibility=exported;
})();
