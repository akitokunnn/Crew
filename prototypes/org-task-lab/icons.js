(function (root) {
  "use strict";

  // 24 网格、1.6 线宽、圆头。状态图标用形状区分，颜色只作辅助。
  const ring = '<circle cx="12" cy="12" r="8.25"/>';
  const paths = {
    chevron: '<path d="m9.5 6.5 5.5 5.5-5.5 5.5"/>',
    panel: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M14.5 4.5v15"/>',
    graph: '<rect x="3.5" y="9.5" width="5" height="5" rx="1.3"/><rect x="15.5" y="3.5" width="5" height="5" rx="1.3"/><rect x="15.5" y="15.5" width="5" height="5" rx="1.3"/><path d="M8.5 12h2.5a2 2 0 0 0 2-2V8a2 2 0 0 1 2-2h.5M11 12h0a2 2 0 0 1 2 2v2a2 2 0 0 0 2 2h.5"/>',
    close: '<path d="m6.5 6.5 11 11m0-11-11 11"/>',
    chat: '<path d="M5 5.5h14A1.5 1.5 0 0 1 20.5 7v8.5A1.5 1.5 0 0 1 19 17h-8.5L6 20.5V17H5a1.5 1.5 0 0 1-1.5-1.5V7A1.5 1.5 0 0 1 5 5.5z"/>',
    person: '<circle cx="12" cy="8.5" r="3.75"/><path d="M5 19.5c.8-3.4 3.6-5.25 7-5.25s6.2 1.85 7 5.25"/>',
    pencil: '<path d="M5 19h3.6l9.8-9.8a1.9 1.9 0 0 0 0-2.7l-.9-.9a1.9 1.9 0 0 0-2.7 0L5 15.4z"/><path d="m13.6 6.8 3.6 3.6"/>',
    layers: '<path d="m12 4 8 4.5-8 4.5-8-4.5z"/><path d="m4 13 8 4.5 8-4.5"/>',
    send: '<path d="M12 18.5v-13M6.5 11 12 5.5l5.5 5.5"/>',
    menu: '<path d="M4.5 7h15M4.5 12h15M4.5 17h15"/>',
    home: '<path d="M4.5 10.5 12 4.5l7.5 6V19a1 1 0 0 1-1 1h-4v-5.5h-5V20h-4a1 1 0 0 1-1-1z"/>',
    search: '<circle cx="11" cy="11" r="6.25"/><path d="m15.6 15.6 4 4"/>',
    spark: '<path d="M12 3.8c.6 3.9 2.3 5.6 6.2 6.2-3.9.6-5.6 2.3-6.2 6.2-.6-3.9-2.3-5.6-6.2-6.2 3.9-.6 5.6-2.3 6.2-6.2z"/><path d="M18.5 15.5c.3 1.6.9 2.2 2.5 2.5-1.6.3-2.2.9-2.5 2.5-.3-1.6-.9-2.2-2.5-2.5 1.6-.3 2.2-.9 2.5-2.5z"/>',
    gear: '<circle cx="12" cy="12" r="2.75"/><path d="M12 3.8v2.1M12 18.1v2.1M20.2 12h-2.1M5.9 12H3.8M17.8 6.2l-1.5 1.5M7.7 16.3l-1.5 1.5M17.8 17.8l-1.5-1.5M7.7 7.7 6.2 6.2"/>',
    file: '<path d="M6.5 3.5h7l4 4v12a1 1 0 0 1-1 1h-10a1 1 0 0 1-1-1v-15a1 1 0 0 1 1-1z"/><path d="M13.5 3.5v4h4M9 12.5h6M9 16h4"/>',
    back: '<path d="M19 12H5.5M11 6.5 5.5 12l5.5 5.5"/>',
    play: '<path d="M8 5.8v12.4a.6.6 0 0 0 .9.5l9.8-6.2a.6.6 0 0 0 0-1L8.9 5.3a.6.6 0 0 0-.9.5z"/>',
    more: '<circle cx="6" cy="12" r="1.1" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.1" fill="currentColor" stroke="none"/><circle cx="18" cy="12" r="1.1" fill="currentColor" stroke="none"/>',
    list: '<path d="M9 7h10.5M9 12h10.5M9 17h10.5"/><circle cx="5" cy="7" r=".9" fill="currentColor" stroke="none"/><circle cx="5" cy="12" r=".9" fill="currentColor" stroke="none"/><circle cx="5" cy="17" r=".9" fill="currentColor" stroke="none"/>',
    "st-run": `${ring}<circle cx="12" cy="12" r="3.6" fill="currentColor" stroke="none"/>`,
    "st-queue": `${ring}<path d="M12 8v4.2l2.6 1.6"/>`,
    "st-wait": `${ring}<path d="M12 7.8v5"/><circle cx="12" cy="16.1" r="1" fill="currentColor" stroke="none"/>`,
    "st-pause": `${ring}<path d="M10 9.2v5.6M14 9.2v5.6"/>`,
    "st-idle": `${ring}<path d="M9 12h6"/>`,
    "st-plan": '<circle cx="12" cy="12" r="8.25" stroke-dasharray="2.7 2.7"/>',
    "st-done": `${ring}<path d="m8.6 12.3 2.4 2.4 4.6-4.9"/>`,
    "st-fail": `${ring}<path d="m9.5 9.5 5 5m0-5-5 5"/>`,
    "st-off": `${ring}<path d="m6.6 17.4 10.8-10.8"/>`
  };

  function icon(name, className = "") {
    return `<svg class="ic${className ? ` ${className}` : ""}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${paths[name] || ""}</svg>`;
  }

  root.CrewLightIcons = { icon, names: Object.keys(paths) };
})(globalThis);
