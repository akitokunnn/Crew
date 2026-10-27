"use strict";
(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[char]);
  function create(options) {
    const doc = options.document || document, overlay = options.portal || doc.createElement("div");
    overlay.className = "backdrop command-palette-overlay";
    if (!options.portal) doc.body.append(overlay);
    let opened = false, destroyed = false, returnTo = null, selected = 0, term = "", items = [];
    function update() {
      if (!opened || destroyed) return;
      items = options.commands().filter(item => item.key.toLowerCase().includes(term.trim().toLowerCase()));
      selected = Math.max(0, Math.min(items.length - 1, selected));
      let group = "";
      overlay.querySelector("#palette-results").innerHTML = items.length === 0 ? '<p class="note" style="padding:12px">没有找到匹配项</p>' : items.map((item, index) => {
        const title = group === item.group ? "" : `<div class="grp">${esc(item.group)}</div>`; group = item.group;
        return `${title}<button class="it ${index === selected ? "on" : ""}" data-act="palette-go" data-index="${index}">${item.html}</button>`;
      }).join("");
    }
    function open() {
      if (destroyed) return;
      options.beforeOpen?.();
      if (!opened) returnTo = options.focus.capture();
      opened = true; selected = 0; term = "";
      overlay.innerHTML = `<div class="palette" role="dialog" aria-modal="true" aria-labelledby="palette-title"><span class="sr-only" id="palette-title">快速查找</span>
        <div class="in">${options.icon("search")}<input id="palette-query" placeholder="输入名字，回车打开" aria-label="搜索 leader、下属或操作"><span class="kbd">Esc</span></div><div class="res" id="palette-results"></div></div>`;
      overlay.classList.add("show"); update(); overlay.querySelector("#palette-query").focus();
    }
    function close() {
      if (!opened) return false;
      opened = false; items = []; overlay.classList.remove("show"); options.focus.restore(returnTo); returnTo = null; return true;
    }
    function choose(index) {
      if (!opened || destroyed || !Number.isInteger(index)) return;
      const item = items[index]; if (!item) return;
      close(); item.go();
    }
    function input(event) {
      if (!opened || event.target.id !== "palette-query") return;
      term = event.target.value; selected = 0; update();
    }
    function keydown(event) {
      if (!opened || destroyed || event.defaultPrevented || event.isComposing) return;
      if (event.key === "Escape") { event.preventDefault(); close(); return; }
      if (event.target.id === "palette-query" && ["ArrowDown", "ArrowUp"].includes(event.key)) {
        event.preventDefault(); selected += event.key === "ArrowDown" ? 1 : -1; update(); overlay.querySelector("#palette-results").querySelector(".on")?.scrollIntoView({block:"nearest"});
      } else if (event.target.id === "palette-query" && event.key === "Enter" && items[selected]) { event.preventDefault(); choose(selected); }
      else if (event.key === "Tab") {
        const enabled = [...overlay.querySelectorAll('button:not([disabled]),input:not([disabled])')], first = enabled[0], last = enabled.at(-1);
        if (event.shiftKey && doc.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && doc.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    }
    function click(event) { if (event.target === overlay) close(); }
    overlay.addEventListener("input", input); overlay.addEventListener("click", click); doc.addEventListener("keydown", keydown);
    const unregister = options.actions?.register("command-palette", {palette:open, "palette-go":({element}) => choose(Number(element.dataset.index))});
    const unregisterVisibility = options.visibility?.register("command-palette", () => opened);
    return {open, close, update, isOpen:() => opened,
      destroy() { if (destroyed) return; close(); destroyed = true; unregister?.(); unregisterVisibility?.(); overlay.removeEventListener("input", input); overlay.removeEventListener("click", click); doc.removeEventListener("keydown", keydown); if (!options.portal) overlay.remove(); }};
  }
  const exported = {create};
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  else window.CrewCommandPalette = exported;
})();
