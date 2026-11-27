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
  function postOptions({request_id = window.CrewBrowser.requestId(), ...body}) {
    return {
      method: "POST",
      headers: {"Content-Type":"application/json", "X-Crew":"1", "X-Crew-Request-Id":request_id},
      body: JSON.stringify(body)
    };
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
  function chatSendKey(event) {
    return event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229 && !matchMedia("(pointer: coarse)").matches;
  }
  function insideCodeFence(text) {
    let open;
    for (const [, fence, suffix] of text.matchAll(/^ {0,3}(`{3,}|~{3,})([^\n]*)/gm)) {
      if (open) {
        if (fence[0] === open[0] && fence.length >= open.length && !suffix.trim()) open = undefined;
      } else if (fence[0] === "~" || !suffix.includes("`")) {
        open = fence;
      }
    }
    return Boolean(open);
  }
  function enableChatLists(input) {
    input.enterKeyHint = "enter";
    input.addEventListener("beforeinput", event => {
      if (!event.cancelable || event.isComposing || !["insertLineBreak", "insertParagraph"].includes(event.inputType)) return;
      const {value, selectionStart: end, selectionEnd, maxLength} = input;
      if (end !== selectionEnd || (end < value.length && value[end] !== "\n")) return;
      const lineStart = value.slice(0, end).lastIndexOf("\n") + 1;
      const match = /^( {0,3})(?:(\d{1,9})([.)])|([-+*]))[ \t]+(.*)$/.exec(value.slice(lineStart, end));
      if (!match || insideCodeFence(value.slice(0, lineStart))) return;
      const [, indent, number, delimiter, bullet, content] = match;
      const next = number === undefined ? bullet : `${Number(number) + 1}${delimiter}`;
      const hasContent = Boolean(content.trim());
      const start = hasContent ? end : lineStart;
      // Keep a blank separator so text typed after the list becomes a new paragraph.
      const replacement = hasContent ? `\n${indent}${next} ` : lineStart === 0 ? "" : "\n";
      if (hasContent && number === "999999999") return;
      if (maxLength >= 0 && value.length - (end - start) + replacement.length > maxLength) return;
      event.preventDefault();
      input.setSelectionRange(start, end);
      // Use the browser edit history so Undo can remove the generated marker or restore an empty item.
      if (!document.execCommand(replacement ? "insertText" : "delete", false, replacement)) {
        input.setRangeText(replacement, start, end, "end");
        input.dispatchEvent(new Event("input", {bubbles:true}));
      }
    });
  }
  // Saves a same-origin file response (Content-Disposition names it); a Crew desktop client asks where to save.
  function download(url) {
    const link = document.createElement("a"); link.href = url; link.download = ""; link.hidden = true;
    document.body.append(link); link.click(); link.remove();
  }
  window.CrewBrowser = {requestId, postOptions, copyText, keyboardShortcut, chatSendKey, enableChatLists, download};
})();
