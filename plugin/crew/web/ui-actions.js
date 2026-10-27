"use strict";
// Feature-owned UI commands. Registration rejects overlap rather than depending
// on document-listener order; unclaimed actions remain the composition root's.
(() => {
  function create({onError} = {}) {
    const routes = new Map(), owners = new Set();
    function register(owner, handlers) {
      if (!owner || owners.has(owner)) throw Error(`UI action owner already registered: ${owner}`);
      for (const [action, handler] of Object.entries(handlers)) {
        if (!action || typeof handler !== "function") throw Error(`Invalid UI action in ${owner}`);
        if (routes.has(action)) throw Error(`UI action already owned by ${routes.get(action).owner}: ${action}`);
      }
      owners.add(owner);
      for (const [action, handler] of Object.entries(handlers)) routes.set(action, {owner,handler});
      let active = true;
      return () => { if (!active) return; active = false; for (const [action, entry] of routes) if (entry.owner === owner) routes.delete(action); owners.delete(owner); };
    }
    function dispatch(action, context = {}) {
      const entry = routes.get(action);
      if (!entry) return false;
      const report = error => {
        // The request is not retried or reversed. Its departed UI owner must
        // not present a late error in a remounted feature or another view.
        if (routes.get(action) !== entry) return;
        if (onError) onError(error);
        else throw error;
      };
      try {
        Promise.resolve(entry.handler(context)).catch(error => {
          if (onError) report(error);
          else if (routes.get(action) === entry) queueMicrotask(() => report(error));
        });
      } catch (error) { report(error); }
      return true;
    }
    return {register, dispatch,
      handle(event) { const element = event.target.closest?.("[data-act]"); return element && !element.disabled ? dispatch(element.dataset.act, {element,event}) : false; },
      owners:() => [...owners],
      destroy() { routes.clear(); owners.clear(); }};
  }
  const exported = {create};
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  else window.CrewUIActions = exported;
})();
