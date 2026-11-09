"use strict";
// One reading pane inside the task area (task report or file preview). On wide screens the conversation
// stays on the left and the pane takes the rest; narrow screens put the pane on top and the conversation
// below; "full" hides the conversation. Owners render their own head and body; this module owns the layout,
// the divider, the conversation toggle, the reading reference offered to the composer and quoting.
(() => {
  const CHAT_MIN = 320, READER_MIN = 440, CHAT_DEFAULT = 400, LEAVE_MS = 260, STEP = 24;
  const narrow = () => window.matchMedia("(max-width:760px)").matches;
  const reduced = () => window.matchMedia("(prefers-reduced-motion:reduce)").matches;
  const main = () => document.getElementById("main");
  const saved = {
    get(key) { try { return localStorage.getItem(key); } catch { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch { /* Per-viewer convenience only. */ } }
  };
  const covered = new Set();
  let current = null, hooks = null, observer = null, quote = null, quoteText = "", frame = 0;

  const chatHost = () => main()?.querySelector(":scope > #chat-host") || null;
  const composer = () => Boolean(chatHost()?.querySelector("textarea"));
  // A leader column that is still sliding shut is counted as already free, so the layout does not flip mid-way.
  function available() {
    const panel = document.getElementById("leader-panel");
    const closing = panel && panel.dataset.open === "false" ? panel.getBoundingClientRect().width : 0;
    return main().clientWidth + closing;
  }
  function chatWidth(total) {
    const wanted = Number(saved.get("crew.readerChatWidth")) || CHAT_DEFAULT;
    return Math.round(Math.max(CHAT_MIN, Math.min(wanted, total - READER_MIN)));
  }
  function mode() {
    if (!current.chat || !chatHost()) return "full";
    return !narrow() && available() >= CHAT_MIN + READER_MIN ? "split" : "stack";
  }
  function apply() {
    const host = main(); if (!current || !host) return;
    const next = mode();
    host.dataset.reader = next;
    host.dataset.readerChat = chatHost() ? "available" : "none";
    host.style.setProperty("--reader-chat", `${chatWidth(available())}px`);
    host.style.setProperty("--reader-top", `${Math.round(host.clientHeight * .5)}px`);
    cover(); sync();
    if (next === "full") hideQuote();
  }
  // Only the pane, and the conversation while it is shown, take focus or input; the rest of the task area waits.
  function cover() {
    const host = main(), chat = host.dataset.reader !== "full";
    for (const node of covered) if (!node.isConnected) covered.delete(node);
    for (const node of host.children) {
      const keep = node === current.pane || chat && (node.id === "chat-host" || node.id === "chat-head");
      if (!keep && !node.inert && !node.classList.contains("reader-leaving")) { node.inert = true; covered.add(node); }
      else if (keep && covered.has(node)) { node.inert = false; covered.delete(node); }
    }
  }
  function uncover() { for (const node of covered) node.inert = false; covered.clear(); }
  function sync() {
    if (!current) return;
    const open = main().dataset.reader !== "full";
    for (const button of current.pane.querySelectorAll("[data-reader-act=chat]")) {
      button.setAttribute("aria-expanded", String(open));
      button.title = open ? "收起旁边的对话，全屏阅读" : "在旁边打开对话，边看边聊";
    }
    const divider = current.pane.querySelector(":scope > .reader-divider");
    if (divider) divider.setAttribute("aria-valuenow", String(parseInt(main().style.getPropertyValue("--reader-chat"), 10) || CHAT_DEFAULT));
  }
  function release(entry) {
    hideQuote(); uncover();
    window.CrewChat.setReading(null);
    entry.onClose?.();
  }
  // Owners keep their element; the module adopts it. Opening while another pane is open swaps the content
  // in place, keeping the layout, so the conversation does not jump.
  function open(pane, {label, reading = null, onClose = null} = {}) {
    const host = main(); if (!host) return;
    const previous = current;
    if (previous) { current = null; release(previous); previous.pane.remove(); }
    pane.classList.add("reader-pane");
    pane.insertAdjacentHTML("afterbegin", '<div class="reader-divider" role="separator" aria-orientation="vertical" aria-label="拖动调整对话宽度" aria-valuemin="320" tabindex="0" title="拖动调整宽度，双击恢复"></div>');
    if (label) pane.setAttribute("aria-label", label);
    current = {pane, onClose, chat:previous ? previous.chat : narrow() ? false : saved.get("crew.readerChat") !== "0", leaders:previous?.leaders || false};
    host.append(pane);
    if (!previous) { hooks.onActive(true); current.leaders = !narrow() && hooks.collapseLeaders(); }
    apply();
    window.CrewChat.setReading(reading);
    observer = observer || new ResizeObserver(() => { if (current) { apply(); hideQuote(); } });
    observer.observe(host);
    return pane;
  }
  // animate: the user's own close ("返回任务", Esc, ×). Navigation replaces the task area, so it removes at once.
  function close(pane = null, {animate = false} = {}) {
    if (!current || pane && current.pane !== pane) return false;
    const entry = current, host = main(); current = null;
    const leaving = animate && !reduced() && entry.pane.isConnected;
    // The leaving pane stays where it is and slides out, instead of jumping to full width under the layout reset.
    if (leaving) {
      const box = entry.pane.getBoundingClientRect(), frameBox = host.getBoundingClientRect();
      entry.pane.style.setProperty("left", `${box.left - frameBox.left}px`, "important");
      entry.pane.style.setProperty("bottom", `${frameBox.bottom - box.bottom}px`, "important");
    }
    release(entry);
    observer.disconnect();
    if (host) { delete host.dataset.reader; delete host.dataset.readerChat; }
    if (entry.leaders) hooks.restoreLeaders();
    hooks.onActive(false);
    if (leaving) {
      entry.pane.inert = true; entry.pane.classList.add("reader-leaving");
      setTimeout(() => entry.pane.remove(), LEAVE_MS);
    } else entry.pane.remove();
    return true;
  }
  function toggleChat(show = !current?.chat) {
    if (!current || !chatHost()) return;
    current.chat = show;
    if (!narrow()) saved.set("crew.readerChat", show ? "1" : "0");
    apply();
    if (show) chatHost().querySelector("textarea")?.focus({preventScroll:true});
  }

  // Divider: drag, arrow keys, double click to reset. The pane's iframe would swallow the drag, so it is
  // made transparent to pointers while resizing.
  function resizeTo(width, store = false) {
    const host = main(), value = Math.round(Math.max(CHAT_MIN, Math.min(width, available() - READER_MIN)));
    host.style.setProperty("--reader-chat", `${value}px`);
    if (store) saved.set("crew.readerChatWidth", String(value));
    sync();
  }
  document.addEventListener("pointerdown", event => {
    const divider = event.target.closest?.(".reader-divider");
    if (!divider || !current || main().dataset.reader !== "split" || event.button !== 0) return;
    event.preventDefault();
    const host = main(), left = host.getBoundingClientRect().left;
    host.classList.add("reader-resizing"); divider.setPointerCapture(event.pointerId);
    const move = moved => resizeTo(moved.clientX - left);
    const end = () => {
      divider.removeEventListener("pointermove", move); divider.removeEventListener("pointerup", end); divider.removeEventListener("pointercancel", end);
      host.classList.remove("reader-resizing");
      saved.set("crew.readerChatWidth", String(parseInt(host.style.getPropertyValue("--reader-chat"), 10)));
    };
    divider.addEventListener("pointermove", move); divider.addEventListener("pointerup", end); divider.addEventListener("pointercancel", end);
  });
  document.addEventListener("dblclick", event => { if (event.target.closest?.(".reader-divider") && current) resizeTo(CHAT_DEFAULT, true); });
  document.addEventListener("keydown", event => {
    const divider = event.target.closest?.(".reader-divider"); if (!divider || !current) return;
    const now = parseInt(main().style.getPropertyValue("--reader-chat"), 10) || CHAT_DEFAULT;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); resizeTo(now + (event.key === "ArrowLeft" ? -STEP : STEP), true); }
    else if (event.key === "Home") { event.preventDefault(); resizeTo(CHAT_MIN, true); }
    else if (event.key === "End") { event.preventDefault(); resizeTo(available() - READER_MIN, true); }
  });
  document.addEventListener("click", event => {
    const button = event.target.closest?.("[data-reader-act]"); if (!button || !current || !current.pane.contains(button)) return;
    if (button.dataset.readerAct === "chat") toggleChat();
  });

  // Quoting: a selection inside the pane (DOM text, or a report iframe that posts its selection) offers one
  // button that copies the passage into the composer as a quote. Nothing is sent until the user sends.
  function hideQuote() { quoteText = ""; quote?.classList.remove("show"); }
  function showQuote(text, rect) {
    if (!current || !text || !composer() || main().dataset.reader === "full" && narrow()) { hideQuote(); return; }
    if (!quote) {
      quote = document.createElement("button"); quote.type = "button"; quote.className = "reader-quote";
      quote.innerHTML = '<svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 7H5v6h4l-2 4m12-10h-4v6h4l-2 4"/></svg><span>引用到对话</span>';
      quote.addEventListener("pointerdown", event => event.preventDefault());
      quote.addEventListener("click", () => {
        const text = quoteText; hideQuote(); if (!text || !current) return;
        if (!current.chat) toggleChat(true);
        window.CrewChat.quote(text);
        if (!current.pane.querySelector("iframe")) window.getSelection()?.removeAllRanges();
      });
    }
    if (quote.parentNode !== current.pane) current.pane.append(quote);
    const box = current.pane.getBoundingClientRect();
    const x = Math.max(8, Math.min(rect.left + rect.width / 2 - box.left - 52, box.width - 120));
    const above = rect.top - box.top - 40;
    quote.style.left = `${Math.round(x)}px`;
    quote.style.top = `${Math.round(above > 56 ? above : rect.bottom - box.top + 8)}px`;
    quoteText = text.slice(0, 4000);
    quote.classList.add("show");
  }
  document.addEventListener("selectionchange", () => {
    if (!current || frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0; if (!current) return;
      const selection = window.getSelection(), area = current.pane.querySelector(".reader-quotable");
      if (!area || !selection || selection.isCollapsed || !area.contains(selection.anchorNode) || !area.contains(selection.focusNode)) {
        if (!current.pane.querySelector("iframe")) hideQuote();
        return;
      }
      showQuote(String(selection).trim(), selection.getRangeAt(0).getBoundingClientRect());
    });
  });
  window.addEventListener("message", event => {
    const data = event.data;
    if (!current || !data || data.crewReader !== "selection") return;
    const frameNode = [...current.pane.querySelectorAll("iframe")].find(node => node.contentWindow === event.source);
    if (!frameNode) return;
    const rect = data.rect, outer = frameNode.getBoundingClientRect();
    if (typeof data.text !== "string" || !data.text.trim() || !rect || ![rect.left, rect.top, rect.right, rect.bottom].every(Number.isFinite)) { hideQuote(); return; }
    showQuote(data.text.trim(), {left:outer.left + rect.left, top:outer.top + rect.top, bottom:outer.top + rect.bottom, width:rect.right - rect.left});
  });
  // Runs inside the report iframes, which have no access to Crew: posts the selection to the parent, relays a
  // nested report body's selection with its offset, and passes itself into that body before it loads.
  function selectionProbe() {
    let frame = 0;
    const up = message => parent.postMessage(message, "*");
    const post = () => {
      frame = 0;
      const selection = getSelection(), text = selection && !selection.isCollapsed ? String(selection).trim() : "";
      const box = text && selection.rangeCount ? selection.getRangeAt(0).getBoundingClientRect() : null;
      up({crewReader:"selection", text:text.slice(0, 4000), rect:box && {left:box.left, top:box.top, right:box.right, bottom:box.bottom}});
    };
    const queue = () => { if (!frame) frame = requestAnimationFrame(post); };
    document.addEventListener("selectionchange", queue);
    addEventListener("scroll", queue, true);
    addEventListener("message", event => {
      const data = event.data, node = [...document.querySelectorAll("iframe")].find(item => item.contentWindow === event.source);
      if (!node || !data || data.crewReader !== "selection") return;
      const offset = node.getBoundingClientRect(), rect = data.rect;
      up({crewReader:"selection", text:data.text, rect:rect && {left:rect.left + offset.left, top:rect.top + offset.top, right:rect.right + offset.left, bottom:rect.bottom + offset.top}});
    });
    const tag = "<scr" + "ipt>" + document.currentScript.textContent + "</scr" + "ipt>";
    document.addEventListener("DOMContentLoaded", () => {
      for (const node of document.querySelectorAll("iframe[srcdoc]")) node.srcdoc = node.srcdoc.replace(/^(\s*<!doctype html>)?/i, head => head + tag);
    });
  }
  const selectionScript = "<script>(" + selectionProbe + ")();</script>";

  const chatButton = () => '<button type="button" class="btn ghost reader-chat-toggle" data-reader-act="chat" aria-expanded="true"><svg class="i sm" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/></svg><span class="reader-chat-hide">收起对话</span><span class="reader-chat-show">显示对话</span></button>';
  window.CrewReader = {
    configure(next) { hooks = next; },
    open, close, toggleChat, chatButton, selectionScript,
    pane: () => current?.pane || null,
    active: () => current !== null,
    canHost: () => Boolean(chatHost()),
    cover: () => { if (current) cover(); },
    refresh: () => { if (current) apply(); },
    escape() { if (!current) return false; close(null, {animate:true}); return true; }
  };
})();
