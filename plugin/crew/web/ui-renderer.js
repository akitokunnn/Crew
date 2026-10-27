"use strict";
// Shared DOM replacement owns IME deferral, form values, focus and scroll. Each
// feature queues its own rendering root; composition completion does not call
// application-specific render functions or drop a portal's pending update.
(() => {
  function create(options={}) {
    const document=options.document||globalThis.document,set=options.setTimeout||setTimeout,clear=options.clearTimeout||clearTimeout;
    let composingInput=null,timer=null,destroyed=false;const deferred=new Map();
    function replace(node, html) {
    if (destroyed) return;
    if (node.dataset.render === html) { deferred.delete(node); return; }
    // Recreating a textarea commits/discards the native IME session even if its
    // value and caret are restored. Keep the composing DOM node connected.
    if (composingInput !== null && node.contains(composingInput)) { deferred.set(node, html); return; }
    deferred.delete(node);
    const focused = node.contains(document.activeElement) ? document.activeElement : null;
    const focusKey = focused?.dataset.preserve;
    const focusData = focused === null ? null : {...focused.dataset};
    const focusForm = focused?.form;
    const selection = focused && "selectionStart" in focused ? [focused.selectionStart, focused.selectionEnd] : null;
    const values = new Map(Array.from(node.querySelectorAll("[data-preserve]")).filter(item => "value" in item).map(item => [item.dataset.preserve, item.value]));
    const scroll = Array.from(node.querySelectorAll(".page,.card-body,.tree,.tray-in,.lead-grid,.comments-list,.group-goal-list,.sb-leaders")).map(item => [item.className, item.scrollTop, item.scrollLeft]);
    node.innerHTML = html; node.dataset.render = html;
    for (const item of node.querySelectorAll("[data-preserve]")) if (values.has(item.dataset.preserve) && "value" in item) item.value = values.get(item.dataset.preserve);
    for (const [name, top, left] of scroll) { const item = Array.from(node.querySelectorAll(".page,.card-body,.tree,.tray-in,.lead-grid,.comments-list,.group-goal-list,.sb-leaders")).find(item => item.className === name); if (item) { item.scrollTop = top; item.scrollLeft = left; } }
    if (focusKey !== undefined) { const item = Array.from(node.querySelectorAll("[data-preserve]")).find(item => item.dataset.preserve === focusKey); if (item) { item.focus({preventScroll:true}); if (selection) item.setSelectionRange(...selection); } }
    else if (focused !== null) {
      const item = Array.from(node.querySelectorAll(focused.tagName)).find(item => item.id === focused.id && item.getAttribute("name") === focused.getAttribute("name") && item.getAttribute("type") === focused.getAttribute("type") && Object.entries(focusData).every(([key, value]) => item.dataset[key] === value) && (focusForm === null || focusForm === undefined || item.form !== null && item.form.id === focusForm.id && Object.entries(focusForm.dataset).every(([key, value]) => item.form.dataset[key] === value)));
      if (item) item.focus({preventScroll:true});
    }
  }

    function start(event){clear(timer);composingInput=event.target;}
    function end(){clear(timer);timer=set(()=>{composingInput=null;const queue=[...deferred];deferred.clear();for(const [node,html]of queue)if(node.isConnected!==false)replace(node,html);},0);}
    document.addEventListener("compositionstart",start);document.addEventListener("compositionend",end);
    return {replace,pending:()=>deferred.size,destroy(){destroyed=true;clear(timer);deferred.clear();document.removeEventListener("compositionstart",start);document.removeEventListener("compositionend",end);}};
  }
  const exported={create};if(typeof module!=="undefined"&&module.exports)module.exports=exported;else window.CrewDOMRenderer=exported;
})();
