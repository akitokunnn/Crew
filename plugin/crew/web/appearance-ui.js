"use strict";
(() => {
  const MODES = ["system", "light", "dark"], FONT_MIN = .9, FONT_MAX = 1.4;
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[char]);
  const pct = value => `${Math.round(value * 100)}%`;
  function create(options) {
    const doc = options.document || document, win = options.window || window, host = options.host;
    let mode = "system", fontScale = 1, themes = null, project = null, destroyed = false, saving = false;
    const storage = () => options.storage ?? win.localStorage;
    function report(error) { options.notify(error.message); }
    function persist(key, value) { try { storage().setItem(key, String(value)); } catch (error) { report(error); } }
    try { const stored = storage().getItem("crew.theme"); if (MODES.includes(stored)) mode = stored; } catch (error) { report(error); }
    try { const stored = Number(storage().getItem("crew.fontScale")); if (Number.isFinite(stored) && stored >= FONT_MIN && stored <= FONT_MAX) fontScale = stored; } catch (error) { report(error); }
    function apply() {
      if (destroyed) return;
      options.theme.setMode(mode);
      if (themes !== null) options.theme.apply(themes, project === null ? null : project.theme, mode);
    }
    function applyFont() { doc.documentElement.style.setProperty("--fs", String(fontScale)); }
    function render() {
      if (destroyed) return;
      const sound = options.sound.getSettings(), icon = options.icon;
      const projectThemes = themes !== null && project !== null ? `<section class="settings-section">
        <h4 id="project-theme-title">主题</h4><div class="theme-options" role="group" aria-labelledby="project-theme-title">
        ${themes.map(item => `<button type="button" class="theme-option" data-act="project-theme" data-theme-id="${esc(item.id)}" aria-pressed="${project.theme === item.id}">
          <span class="theme-swatches" aria-hidden="true"><i style="background:${item.light["--accent"]}"></i><i style="background:${item.dark["--accent"]}"></i></span>
          <span>${esc(item.name)}</span>${project.theme === item.id ? icon("check", "sm") : ""}</button>`).join("")}</div>
        <p class="settings-hint">左浅右深 · 为这个项目保存</p></section>` : "";
      options.renderer.replace(host, `<section class="settings-section"><h4>显示模式</h4><div class="cs-theme-options">
        ${[["system", "跟随系统"], ["light", "浅色"], ["dark", "深色"]].map(([id, label]) => `<button type="button" data-act="theme-select" data-theme-mode="${id}" aria-pressed="${mode === id}">${label}</button>`).join("")}</div></section>
        ${projectThemes}<section class="settings-section"><div class="settings-row"><h4>字号</h4><span class="settings-value" id="font-scale-value">${pct(fontScale)}</span>
          <button type="button" class="settings-link" data-act="font-reset" ${fontScale === 1 ? "disabled" : ""}>恢复标准</button></div>
          <input class="settings-range" type="range" min="${FONT_MIN}" max="${FONT_MAX}" step="0.01" value="${fontScale}" data-setting="font" aria-label="字号"></section>
        <section class="settings-section"><div class="settings-row"><h4>提示音</h4><span class="gap"></span>
          <button class="switch ${sound.muted ? "" : "on"}" data-act="sound-mute" role="switch" aria-checked="${!sound.muted}" aria-label="提示音"><span class="track"></span></button></div>
          <div class="settings-row"><span class="settings-sub">音量</span><span class="settings-value" id="sound-volume-value">${pct(sound.volume)}</span>
          <button type="button" class="settings-link" data-act="sound-test" ${sound.muted ? "disabled" : ""}>试听</button></div>
          <input class="settings-range" type="range" min="0" max="1" step="0.01" value="${sound.volume}" data-setting="volume" aria-label="提示音音量" ${sound.muted ? "disabled" : ""}></section>`);
    }
    function selectMode(value) {
      if (destroyed || !MODES.includes(value)) return;
      mode = value; apply(); persist("crew.theme", mode); render(); options.onModeChange?.();
    }
    function setFont(value) {
      if (destroyed || !Number.isFinite(value) || value < FONT_MIN || value > FONT_MAX) return;
      fontScale = value; applyFont(); persist("crew.fontScale", fontScale);
    }
    async function projectTheme(value) {
      if (destroyed || saving || project === null || !themes?.some(item => item.id === value)) return;
      const name = project.name; saving = true;
      try {
        const result = await options.saveProjectTheme(name, value);
        if (destroyed || project?.name !== name) return;
        project = {...project, theme:result.theme}; apply(); render();
        await options.onProjectTheme?.(name, result.theme);
      } finally { saving = false; }
    }
    function input(event) {
      const element = event.target;
      if (!host.contains(element)) return;
      // Preserve the slider node and pointer capture while dragging.
      if (element.dataset.setting === "font") {
        setFont(Number(element.value)); host.querySelector("#font-scale-value").textContent = pct(fontScale);
        host.querySelector('[data-act="font-reset"]').disabled = fontScale === 1;
      } else if (element.dataset.setting === "volume") {
        const value = Number(element.value);
        if (!Number.isFinite(value) || value < 0 || value > 1) return;
        options.sound.setVolume(value); host.querySelector("#sound-volume-value").textContent = pct(value);
      }
    }
    const media = win.matchMedia("(prefers-color-scheme: dark)"); media.addEventListener("change", apply);
    host.addEventListener("input", input);
    const unregister = options.actions?.register("appearance", {
      theme:() => selectMode(MODES[(MODES.indexOf(mode) + 1) % MODES.length]),
      "theme-select":({element}) => selectMode(element.dataset.themeMode),
      "project-theme":({element}) => projectTheme(element.dataset.themeId),
      "font-reset":() => { setFont(1); render(); },
      "sound-mute":() => { options.sound.setMuted(!options.sound.getSettings().muted); render(); },
      "sound-test":() => options.sound.preview("decision")
    });
    apply(); applyFont(); render();
    return {mode:() => mode, render,
      sync(value) { if (destroyed) return; themes = value.themes; project = value.project === null ? null : {name:value.project.name, theme:value.project.theme}; apply(); render(); },
      destroy() { if (destroyed) return; destroyed = true; unregister?.(); media.removeEventListener("change", apply); host.removeEventListener("input", input); }};
  }
  const exported = {create};
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  else window.CrewAppearanceUI = exported;
})();
