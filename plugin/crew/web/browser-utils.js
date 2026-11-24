"use strict";
(() => {
  let sequence = 0;
  function requestId() {
    const crypto = globalThis.crypto;
    if (crypto?.randomUUID) return crypto.randomUUID();
    if (crypto?.getRandomValues) {
      const bytes = crypto.getRandomValues(new Uint8Array(16));
      bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
      const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
      return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
    }
    // Operation identifiers are for deduplication, never authentication.
    return `web-${Date.now().toString(36)}-${++sequence}-${Math.random().toString(36).slice(2)}`;
  }
  async function copyText(text) {
    if (globalThis.navigator?.clipboard?.writeText) {
      try { await navigator.clipboard.writeText(text); return; } catch (_) { /* Try the user-gesture copy path below. */ }
    }
    const previous = document.activeElement, selection = document.getSelection();
    const ranges = selection ? Array.from({length:selection.rangeCount}, (_, index) => selection.getRangeAt(index).cloneRange()) : [];
    const input = document.createElement("textarea"); input.value = text; input.readOnly = true;
    input.style.cssText = "position:fixed;left:-9999px;top:0";
    document.body.append(input);
    try {
      input.select(); input.setSelectionRange(0, text.length);
      if (!document.execCommand("copy")) throw Error("浏览器未允许自动复制");
    } finally {
      input.remove(); previous?.focus({preventScroll:true});
      if (selection) { selection.removeAllRanges(); for (const range of ranges) selection.addRange(range); }
    }
  }
  function keyboardShortcut(key, {shift = false, platform = globalThis.navigator?.userAgentData?.platform || globalThis.navigator?.platform} = {}) {
    return /^Mac/i.test(platform) ? `${shift ? "⇧" : ""}⌘${key}` : `Ctrl+${shift ? "Shift+" : ""}${key}`;
  }
  // Saves a same-origin file response (Content-Disposition names it); a Crew desktop client asks where to save.
  function download(url) {
    const link = document.createElement("a"); link.href = url; link.download = ""; link.hidden = true;
    document.body.append(link); link.click(); link.remove();
  }
  window.CrewBrowser = {requestId, copyText, keyboardShortcut, download};
})();
