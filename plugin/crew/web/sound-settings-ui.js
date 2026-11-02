"use strict";
(() => {
  const drafts = new WeakMap();
  const events = [["leader", "leader 回复完成", "总会话和任务会话中的 leader 完成一轮回复时。"],
    ["assistant", "全局助手回复完成", "助手完成回复时，离开助手页面也能提醒。"],
    ["delivery", "任务交付", "任务交付完成、报告可查看时；阶段进展不响。"]];
  function mount(host, options) {
    const previous = drafts.get(host);
    let active = true, generation = 0, draft = previous?.draft || null, saved = previous?.saved || null, catalog = previous?.catalog || null, busy = false;
    host.innerHTML = '<p class="cs-hint" role="status">正在读取音效设置…</p>';
    function status(message, error = false) {
      const node = host.querySelector('[data-sound-status]');
      if (node) { node.textContent = message; node.setAttribute("role", error ? "alert" : "status"); }
    }
    function render() {
      host.innerHTML = `<section class="cs-card cs-sound-master">
        <div class="cs-card-heading"><div><h2>提示音</h2><p>所有工作区和全局助手共用，保存后生效。</p></div>
          <label class="cs-inherit"><input type="checkbox" data-sound-enabled ${draft.muted ? "" : "checked"}>启用提示音</label></div>
        <label class="cs-sound-volume">音量 <output data-sound-volume-value>${Math.round(draft.volume * 100)}%</output>
          <input type="range" min="0" max="100" step="1" value="${Math.round(draft.volume * 100)}" data-sound-volume aria-label="提示音音量"></label>
      </section><div class="cs-task-list">${events.map(([kind, title, description]) => `
        <section class="cs-card cs-sound-row"><div><h3>${title}</h3><p>${description}</p></div>
          <div class="cs-sound-choice"><label>提示音<select data-sound-kind="${kind}" aria-label="${title}音效">
            <option value="off" ${draft[kind] === "off" ? "selected" : ""}>关闭</option>
            ${Object.entries(catalog.presets).map(([id, preset]) => `<option value="${id}" ${draft[kind] === id ? "selected" : ""}>${preset.name}</option>`).join("")}
          </select></label><button type="button" class="cs-button" data-sound-preview="${kind}">试听</button></div>
        </section>`).join("")}</div>
      <p class="cs-footnote">试听使用当前选择。首次打开、刷新页面和切换工作区不会重播旧提醒；拍板与错误提示也受总开关和音量控制。</p>
      <div class="cs-sound-actions"><p data-sound-status role="status"></p>
        <button type="button" class="cs-button" data-sound-reset>撤销更改</button>
        <button type="button" class="cs-button cs-primary" data-sound-save>保存音效</button></div>`;
      controls();
    }
    function controls() {
      const changed = JSON.stringify(draft) !== JSON.stringify(saved);
      for (const control of host.querySelectorAll('input,select,button')) control.disabled = busy;
      for (const button of host.querySelectorAll('[data-sound-preview]')) button.disabled = busy || draft.muted || !draft.volume || draft[button.dataset.soundPreview] === "off";
      host.querySelector('[data-sound-save]').disabled = busy || !changed;
      host.querySelector('[data-sound-reset]').disabled = busy || !changed;
    }
    async function load() {
      const request = ++generation;
      try {
        const value = await options.read();
        if (!active || request !== generation) return;
        catalog = value.sound_catalog; CrewSound.configure(value.sounds, catalog, value.sound_revision);
        if (!draft || JSON.stringify(draft) === JSON.stringify(saved) || JSON.stringify(draft) === JSON.stringify(value.sounds)) { draft = CrewSound.getSettings(); saved = {...draft}; }
        render();
      } catch (error) {
        if (!active || request !== generation) return;
        if (!draft) host.innerHTML = '<p data-sound-status role="alert"></p><button class="cs-button" data-sound-retry>重试</button>';
        status(error.message, true);
      }
    }
    async function click(event) {
      const button = event.target.closest('button');
      if (!button || !host.contains(button) || button.disabled) return;
      if (button.hasAttribute('data-sound-retry')) { void load(); return; }
      if (button.dataset.soundPreview) {
        try { if (await CrewSound.preview(button.dataset.soundPreview, draft) && active) status("正在试听。"); }
        catch (error) { if (active) status(error.message, true); }
      } else if (button.hasAttribute('data-sound-reset')) { draft = {...saved}; render(); }
      else if (button.hasAttribute('data-sound-save')) {
        busy = true; controls(); status("正在保存…");
        try {
          const value = await options.save({sounds:{...draft}});
          if (!active) return;
          CrewSound.configure(value.sounds, value.sound_catalog, value.sound_revision);
          draft = CrewSound.getSettings(); saved = {...draft}; status("音效已保存，所有工作区和助手立即使用。");
        } catch (error) { if (active) status(error.message, true); }
        finally { if (active) { busy = false; controls(); } }
      }
    }
    function change(event) {
      const input = event.target;
      if (busy || !draft) return;
      if (input.hasAttribute('data-sound-enabled')) draft.muted = !input.checked;
      else if (input.hasAttribute('data-sound-volume')) {
        draft.volume = Number(input.value) / 100;
        host.querySelector('[data-sound-volume-value]').textContent = `${input.value}%`;
      } else if (input.dataset.soundKind) draft[input.dataset.soundKind] = input.value;
      else return;
      status("尚未保存。"); controls();
    }
    host.addEventListener("click", click); host.addEventListener("input", change);
    if (draft) render();
    void load();
    return () => {
      drafts.set(host, {draft, saved, catalog}); active = false; generation++;
      host.removeEventListener("click", click); host.removeEventListener("input", change);
    };
  }
  window.CrewSoundSettings = {mount};
})();
