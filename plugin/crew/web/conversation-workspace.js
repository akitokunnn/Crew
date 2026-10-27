"use strict";
// Owns auxiliary workspace state and geometry. It has no knowledge of goals,
// people, carriers, HTTP, chat rendering or their mutable application state.
(() => {
  const sections = new Set(["attention", "tree", "tasks"]);
  const triggers = {attention:"attention", tree:"right-open", tasks:"task-panel"};

  function createState() {
    let context = {active:false, scope:null, listing:false, task:false, materialsOpen:false, reader:false};
    let opened = {attention:false, tree:false, tasks:false}, lastAuxiliary = "attention";
    const active = () => context.active && !context.listing;
    const assertSection = section => { if (!sections.has(section)) throw Error(`Unknown workspace section: ${section}`); };
    function update(next) {
      const changedScope = Object.hasOwn(next, "scope") && next.scope !== context.scope;
      context = {...context, ...next};
      if (changedScope || context.listing || !context.active) opened = {attention:false, tree:false, tasks:false};
      return snapshot();
    }
    function setOpen(section, value) {
      assertSection(section);
      opened = {...opened, [section]:Boolean(value)};
      if (section !== "tasks" && value) lastAuxiliary = section;
      return snapshot();
    }
    function snapshot() {
      const visible = Object.fromEntries([...sections].map(section => [section, active() && opened[section]]));
      return {...context, active:active(), opened:{...opened}, visible, lastAuxiliary,
        auxiliaryOpen:visible.attention || visible.tree,
        blocked:!active() || context.reader || context.materialsOpen || visible.tasks};
    }
    return {update, setOpen, snapshot,
      toggle(section) { assertSection(section); return setOpen(section, !opened[section]); },
      closeAuxiliary() { opened = {...opened, attention:false, tree:false}; return snapshot(); },
      escape() { const current = snapshot(); return current.reader || current.materialsOpen ? null : current.visible.tasks ? "tasks" : current.visible.attention ? "attention" : current.visible.tree ? "tree" : null; }};
  }

  function geometry({viewportWidth, viewportHeight, anchorBottom, mainWidth, treeOpen}) {
    const width = Math.min(332, viewportWidth - 24);
    const top = Math.max(12, Math.min(anchorBottom + 10, viewportHeight - 230));
    const height = Math.max(180, viewportHeight - top - 14);
    const reserve = mainWidth - width - 36 >= 430;
    return {width, top, height, overlap:!reserve, sideSpace:reserve ? width + 28 : 0,
      attentionMaxHeight:treeOpen ? Math.max(90, Math.min(320, height * .46)) : Math.max(90, height - 66)};
  }

  function create(options = {}) {
    const doc = options.document || document, win = options.window || window, model = createState();
    const node = id => doc.getElementById(id);
    const panel = node("chat-attention"), slot = node("attention-slot"), tree = node("right-panel");
    const attention = node("side-attention-section"), tasks = node("side-task-section"), main = node("main");
    let destroyed = false;
    const state = () => model.snapshot();
    function focus(section) { node("chat-head")?.querySelector(`[data-act="${triggers[section]}"]`)?.focus({preventScroll:true}); }
    function layout() {
      if (destroyed || !state().auxiliaryOpen) return;
      const host = node("goal-head") || node("chat-head");
      if (!host) return;
      const box = geometry({viewportWidth:win.innerWidth, viewportHeight:win.innerHeight,
        anchorBottom:host.getBoundingClientRect().bottom, mainWidth:main.clientWidth, treeOpen:state().visible.tree});
      panel.style.width = `${box.width}px`; panel.style.top = `${box.top}px`; panel.style.height = `${box.height}px`;
      panel.style.setProperty("--attention-max-height", `${box.attentionMaxHeight}px`);
      slot.style.height = state().visible.attention ? `${attention.getBoundingClientRect().height + 12}px` : "0px";
      main.dataset.sideOverlap = String(box.overlap);
      main.style.setProperty("--chat-side-space", `${box.sideSpace}px`);
    }
    function render() {
      if (destroyed) return;
      const current = state();
      panel.hidden = !current.auxiliaryOpen; panel.inert = panel.hidden || current.reader;
      tree.hidden = !current.visible.tree; tree.inert = tree.hidden || current.reader;
      tasks.hidden = !current.visible.tasks; tasks.inert = tasks.hidden || current.reader;
      slot.dataset.open = String(current.visible.attention); slot.inert = !current.visible.attention || current.reader;
      slot.setAttribute("aria-hidden", String(!current.visible.attention));
      for (const section of sections) {
        const expanded = current.visible[section] || section === "tasks" && current.active && current.materialsOpen;
        node("chat-head")?.querySelector(`[data-act="${triggers[section]}"]`)?.setAttribute("aria-expanded", String(expanded));
      }
      if (current.auxiliaryOpen) layout();
      else { slot.style.height = "0px"; main.style.setProperty("--chat-side-space", "0px"); main.dataset.sideOverlap = "false"; }
    }
    function close(section, restoreFocus = false) { model.setOpen(section, false); render(); if (restoreFocus) focus(section); }
    function open(section) { model.setOpen(section, true); render(); }
    const observer = new win.ResizeObserver(layout);
    observer.observe(attention); observer.observe(main);
    win.addEventListener("resize", layout);
    render();
    const controller = {
      update(snapshot) { model.update(snapshot); render(); },
      snapshot:state, layout, open, close,
      expanded(section) { const current = state(); return current.visible[section] || section === "tasks" && current.active && current.materialsOpen; },
      auxiliaryOpen:() => state().auxiliaryOpen,
      blocksReading:() => state().blocked || state().auxiliaryOpen && main.dataset.sideOverlap === "true",
      setReader(active) { model.update({reader:Boolean(active)}); render(); },
      closeAuxiliary(restoreFocus = false) { const last = state().lastAuxiliary; model.closeAuxiliary(); render(); if (restoreFocus) focus(last); },
      toggle(section) {
        if (section === "tasks") {
          if (!state().task) { options.onTaskList?.(); return; }
          if (state().materialsOpen) { options.closeMaterials?.(); model.update({materialsOpen:false}); }
        }
        model.toggle(section); render();
        if (section === "tasks" && state().visible.tasks) tasks.querySelector("button")?.focus({preventScroll:true});
      },
      escape() { const section = model.escape(); if (!section) return false; close(section, true); return true; },
      destroy() { destroyed = true; observer.disconnect(); win.removeEventListener("resize", layout); unregister?.(); unregisterVisibility?.(); }
    };
    const unregister = options.actions?.register("conversation-workspace", {
      attention:() => controller.toggle("attention"), "task-panel":() => controller.toggle("tasks"),
      "right-open":() => controller.toggle("tree"), "right-close":() => controller.close("tree",true),
      "side-section-close":({element}) => controller.close(element.dataset.section,true)
    });
    const unregisterVisibility=options.visibility?.register("conversation-workspace",controller.blocksReading);
    return controller;
  }
  const exported = {createState, geometry, create};
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  else window.CrewConversationWorkspace = exported;
})();
