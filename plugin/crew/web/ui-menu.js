"use strict";
(() => {
  function create(options) {
    const doc = options.document || document, win = options.window || window, root = options.element;
    const keepOpen = new Set(options.selection?.keepOpen || []), restoreFocus = new Set(options.selection?.restoreFocus || []);
    let anchor = null, destroyed = false;
    const isOpen = () => !destroyed && root.classList.contains("show");
    const items = menu => menu ? [...menu.querySelectorAll("button:not(:disabled)")].filter(button => button.closest('[role="menu"]') === menu) : [];
    function place(menu, x, y) {
      menu.style.left = `${Math.max(8, Math.min(win.innerWidth - menu.offsetWidth - 8, x))}px`;
      menu.style.top = `${Math.max(8, Math.min(win.innerHeight - menu.offsetHeight - 8, y))}px`;
    }
    function closeChildren(menu) {
      for (const child of menu.querySelectorAll(".sub-menu")) {
        child.hidden = true; child.classList.remove("show"); child.previousElementSibling.setAttribute("aria-expanded", "false");
      }
    }
    function hide(restore = false) {
      closeChildren(root); root.classList.remove("show");
      if (restore && anchor?.isConnected) anchor.focus({preventScroll:true});
    }
    function show(element, html, point = null) {
      if (destroyed) return;
      hide(); anchor = element;
      const rect = element.getBoundingClientRect(); root.innerHTML = html; root.classList.add("show");
      place(root, point === null ? rect.right - root.offsetWidth : point.x, point === null ? rect.bottom + 6 : point.y);
      items(root)[0]?.focus({preventScroll:true});
    }
    function openSubmenu(button) {
      if (!isOpen() || button.disabled || !root.contains(button)) return;
      const menu = button.closest('[role="menu"]'), child = button.nextElementSibling;
      if (!menu || !child?.matches(".sub-menu")) return;
      closeChildren(menu);
      const rect = button.getBoundingClientRect(); child.hidden = false; child.classList.add("show"); button.setAttribute("aria-expanded", "true");
      place(child, rect.right + child.offsetWidth + 8 > win.innerWidth ? rect.left - child.offsetWidth : rect.right + 4, rect.top);
      items(child)[0]?.focus({preventScroll:true});
    }
    function pointerover(event) {
      const button = event.target.closest?.('[data-act="submenu"]');
      if (button && root.contains(button) && button.getAttribute("aria-expanded") !== "true") openSubmenu(button);
    }
    function outsideClick(event) {
      if (!root.contains(event.target) && !event.target.closest?.('[data-act$="-menu"]')) hide();
    }
    function handleClick(event, {restore} = {}) {
      const button = event.target.closest?.("[data-act]");
      if (!button || button.disabled || !root.contains(button) || button.dataset.act === "submenu" || keepOpen.has(button.dataset.act)) return false;
      hide(restore ?? restoreFocus.has(button.dataset.act)); return true;
    }
    function handleKey(event) {
      if (!isOpen() || event.defaultPrevented || event.isComposing || !["ArrowDown", "ArrowUp", "ArrowRight", "ArrowLeft", "Escape", "Home", "End", "Tab"].includes(event.key)) return false;
      event.preventDefault();
      const current = doc.activeElement, menu = root.contains(current) ? current.closest('[role="menu"]') : root;
      const enabled = items(menu), index = enabled.indexOf(current);
      if (event.key === "Tab") hide(true);
      else if (event.key === "ArrowRight" && current?.dataset.act === "submenu") openSubmenu(current);
      else if (event.key === "ArrowLeft" || event.key === "Escape") {
        if (menu === root) hide(true);
        else { const parent = menu.previousElementSibling; closeChildren(parent.closest('[role="menu"]')); parent.focus({preventScroll:true}); }
      } else if (enabled.length && ["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        const next = event.key === "Home" ? 0 : event.key === "End" ? enabled.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + enabled.length) % enabled.length;
        enabled[next].focus({preventScroll:true});
      }
      return true;
    }
    doc.addEventListener("pointerover", pointerover); doc.addEventListener("click", outsideClick);
    const unregister = options.actions?.register("ui-menu", {submenu:({element}) => openSubmenu(element)});
    const unregisterVisibility = options.visibility?.register("ui-menu", isOpen);
    return {show, hide, openSubmenu, handleClick, handleKey, isOpen, anchor:() => anchor,
      contains:node => root.contains(node), isCurrent:element => element === anchor && isOpen(),
      destroy() { if (destroyed) return; hide(); destroyed = true; anchor = null; unregister?.(); unregisterVisibility?.(); doc.removeEventListener("pointerover", pointerover); doc.removeEventListener("click", outsideClick); }};
  }
  const exported = {create};
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  else window.CrewUIMenu = exported;
})();
