"use strict";
(() => {
  const mark = '<path d="M14.26 8.54 A4.90 4.90 0 1 0 14.26 15.46" fill="none" stroke="CURRENT" stroke-width="3" stroke-linecap="round"/><circle cx="17.4" cy="16.4" r="1.75" fill="CURRENT"/>';
  const logo = (accent, foreground) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect width="24" height="24" rx="6.5" fill="${accent}"/>${mark.replaceAll("CURRENT", foreground)}</svg>`;
  const get = (themes, id) => themes.find(item => item.id === id);
  function setMode(mode) {
    if (mode === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = mode;
  }
  function apply(themes, id, mode) {
    const selected = id === null ? themes[0] : get(themes, id);
    const dark = mode === "dark" || mode === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches;
    const palette = dark ? selected.dark : selected.light;
    document.documentElement.dataset.projectTheme = selected.id;
    for (const [token, value] of Object.entries(palette)) document.documentElement.style.setProperty(token, value);
    document.getElementById("project-favicon").href = "data:image/svg+xml," + encodeURIComponent(logo(palette["--accent"], palette["--on-accent"]));
  }
  function projectStyle(themes, id) {
    const selected = get(themes, id);
    return `--project-accent-light:${selected.light["--accent"]};--project-accent-dark:${selected.dark["--accent"]};--project-text-light:${selected.light["--accent-text"]};--project-text-dark:${selected.dark["--accent-text"]}`;
  }
  window.CrewTheme = {apply, setMode, get, projectStyle, logo:() => logo("currentColor", "var(--on-accent)")};
})();
