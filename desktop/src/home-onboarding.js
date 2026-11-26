'use strict';
// First-run guide on the desktop home: execution tools, appearance, what Crew does differently and the
// first workspace. It only strings existing abilities together: the backend's readiness check and AI
// settings, the desktop display mode and font size, and the home page's own open / create flows.
(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
  const reason = error => String(error?.message || error).replace(/^Error invoking remote method '[^']+':\s*(?:Error:\s*)?/, '');
  const STEPS = [['runtime', '执行载体'], ['appearance', '外观'], ['features', 'Crew 的特色'], ['workspace', '第一个工作区']];
  const HEADS = {
    runtime: ['本机的 AI 执行载体', 'Crew 让 Claude Code 和 Codex 一起干活。下面是在这台电脑上检测到的情况，选一个作为默认大脑。'],
    appearance: ['外观', '显示模式对首页、所有工作区和窗口边框一起生效；字号作用于工作区、助手和想法墙页面，这个首页保持标准大小。之后在“设置 → 外观”也能改。'],
    features: ['Crew 和普通单聊有什么不同', '四个最常用到的能力，用到时就会看到它们。'],
    workspace: ['选第一个工作区', '工作区就是一个文件夹。普通目录即可，不需要 Git，Crew 也不会自动初始化仓库。']
  };
  const MODES = [['system', '跟随系统'], ['light', '浅色'], ['dark', '深色']];
  const FONT_MIN = 0.9, FONT_MAX = 1.4;
  const LOGIN_HINT = {claude: '在终端运行 claude，按提示登录后点“重新检测”。', codex: '在终端运行 codex login，登录后点“重新检测”。'};
  const FEATURES = [
    ['多载体混搭', 'Claude Code 和 Codex 可以在同一个工作区里配合：leader 用一个，任务里的执行用另一个，按活挑最合适的大脑。',
      '<circle cx="9" cy="12" r="5.5"/><circle cx="15" cy="12" r="5.5"/>'],
    ['上下文压缩小环', '头像旁的小环显示这段对话用掉了多少上下文，快满时提醒压缩，长对话也不丢重点。',
      '<circle cx="12" cy="12" r="7.5" opacity=".28"/><path d="M12 4.5a7.5 7.5 0 1 1-7.1 9.9"/>'],
    ['离开模式远程提醒', '离开电脑时打开离开模式，要你拍板、验收或动手的事会发到 QQ 或飞书，在手机上就能回。',
      '<rect x="7" y="3" width="10" height="18" rx="2.5"/><path d="M10.5 17.5h3"/><path d="M12 7.5v4l2.5 1.5"/>'],
    ['拍板与自主推进', '每个 leader 可以选：关键决定交给你拍板，或在你写定的范围内自主推进，到点再交付验收。',
      '<rect x="3" y="7" width="18" height="10" rx="5"/><circle cx="16" cy="12" r="3"/>']
  ];

  // ---- Pure helpers (also exercised by desktop/test/home-onboarding.test.cjs) ----
  function status(entry) {
    if (!entry.installed) return {tone:'off', label:'未安装', hint:'没有在这台电脑上找到。安装后点“重新检测”。'};
    if (entry.error) return {tone:'error', label:'检测失败', hint:entry.error};
    if (entry.logged_in === true) return {tone:'ok', label:'已登录', hint:entry.detail || '可以作为默认大脑'};
    return {tone:'warn', label:'未登录', hint:LOGIN_HINT[entry.id]};
  }
  // A tool is offered as the default brain once it is installed, not known to be signed out, and listed by the settings catalog.
  function usable(entry, catalog) {
    const listed = (catalog || []).find(item => item.id === entry.id);
    return Boolean(entry.installed && entry.logged_in !== false && listed?.available);
  }
  function initialDraft(saved, readiness, catalog) {
    const current = {...saved};
    if (current.runtime) return current;
    const ready = readiness.filter(entry => usable(entry, catalog));
    return ready.length === 1 ? {runtime:ready[0].id, model:null, effort:null, speed:null} : current;
  }
  // Same rule as the settings page: another tool clears the model, effort and speed chosen for the previous one.
  function changeRuntime(draft, id) { return draft.runtime === id ? draft : {runtime:id, model:null, effort:null, speed:null}; }
  function changeModel(draft, entry, modelId, speeds) {
    const model = (entry?.models || []).find(item => item.id === modelId);
    const effort = draft.effort !== null && model && Array.isArray(model.efforts) && !model.efforts.includes(draft.effort) ? null : draft.effort;
    const speed = draft.speed && !speeds(entry, modelId || null).some(tier => tier.id === draft.speed) ? null : draft.speed;
    return {...draft, model:modelId || null, effort, speed};
  }
  const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);
  // Shown by itself only on this computer's own first start: no workspace yet and the guide neither finished nor skipped.
  function shouldOpen({remote, projects, pendingAction, status: outcome}) {
    return !remote && !pendingAction && projects === 0 && outcome === 'pending';
  }

  // ---- Markup ----
  const icon = paths => `<svg class="ob-glyph" viewBox="0 0 24 24" aria-hidden="true">${paths}</svg>`;
  function railMarkup(step) {
    return `<nav class="ob-rail" aria-label="引导步骤"><div class="ob-brand"><img class="mark" src="../assets/crew.svg" width="24" height="24" alt=""><span>欢迎使用 Crew</span></div><ol class="ob-steps">${STEPS.map(([id, label], index) => `<li><button type="button" class="ob-step" data-ob-act="step" data-step="${index}"${index === step ? ' aria-current="step"' : ''} data-done="${index < step}"><i aria-hidden="true">${index + 1}</i><span>${esc(label)}</span></button></li>`).join('')}</ol><p class="ob-rail-note">大约一分钟，随时可以跳过。之后在“设置 → 外观”里能重新打开。</p></nav>`;
  }
  function speedMarkup(model, draft, state) {
    const entry = state.catalog.find(item => item.id === 'codex');
    const settings = window.CrewSettings;
    if (draft.runtime !== 'codex') return `<div class="ob-field"><span class="ob-label">速度档位</span><p class="ob-muted">速度档位只适用于 Codex；Claude Code 的快慢由模型和思考强度决定。</p></div>`;
    const tiers = [{id:null, name:settings.FOLLOW_CODEX_SPEED.name, description:settings.FOLLOW_CODEX_SPEED.description}, ...settings.speedChoices(entry, model)];
    const chosen = tiers.find(tier => tier.id === (draft.speed || null));
    const note = chosen ? chosen.description || (chosen.id === 'default' ? '标准速度与用量' : '生成更快，消耗更多用量') : `当前保存的是 ${esc(settings.speedName(entry, draft.speed))}，所选模型没有列出它`;
    return `<div class="ob-field"><span class="ob-label" id="ob-speed-label">速度档位</span><div class="ob-seg" role="radiogroup" aria-labelledby="ob-speed-label">${tiers.map(tier => `<button type="button" role="radio" data-ob-act="speed" data-speed="${esc(tier.id ?? '')}" aria-checked="${tier === chosen}">${esc(tier.id === null ? '跟随 Codex' : tier.name)}</button>`).join('')}</div><p class="ob-muted">${esc(note)}</p></div>`;
  }
  function runtimeMarkup(state) {
    if (state.checking && !state.readiness) return `<div class="ob-loading" role="status"><i aria-hidden="true"></i>正在检测本机的 Claude Code 和 Codex…</div>`;
    const alerts = [state.readinessError && `载体检测失败：${state.readinessError}`, state.settingsError && `读取 AI 设置失败：${state.settingsError}`].filter(Boolean)
      .map(text => `<p class="ob-alert" role="alert">${esc(text)}</p>`).join('');
    if (!state.readiness) return alerts;
    const cards = state.readiness.map(entry => {
      const view = status(entry), pickable = usable(entry, state.catalog), chosen = state.draft?.runtime === entry.id;
      return `<button type="button" class="ob-runtime" role="radio" data-ob-act="runtime" data-runtime="${esc(entry.id)}" aria-checked="${chosen}"${pickable ? '' : ' aria-disabled="true"'}><span class="ob-runtime-top"><strong>${esc(entry.name)}</strong><span class="ob-pill" data-tone="${view.tone}">${esc(view.label)}</span></span><span class="ob-runtime-hint">${esc(view.hint)}</span><span class="ob-runtime-pick">${chosen ? '默认大脑' : pickable ? '设为默认大脑' : '暂不可选'}</span></button>`;
    }).join('');
    const ready = state.readiness.some(entry => usable(entry, state.catalog));
    const draft = state.draft;
    let fields = '';
    if (!ready && state.catalog.length) fields = `<p class="ob-notice">还没有可用的执行载体。安装并登录 Claude Code 或 Codex 后点“重新检测”；也可以先跳过这一步，之后在“设置 → AI 大脑”里选。</p>`;
    else if (draft?.runtime && window.CrewSettings) {
      const entry = state.catalog.find(item => item.id === draft.runtime), models = entry?.models || [];
      const custom = draft.model && !models.some(item => item.id === draft.model) ? `<option value="${esc(draft.model)}" selected>当前 · ${esc(draft.model)}</option>` : '';
      fields = `<div class="ob-fields"><label class="ob-field"><span class="ob-label">模型</span><select data-ob-field="model"><option value="">原生默认</option>${models.map(item => `<option value="${esc(item.id)}" ${item.id === draft.model ? 'selected' : ''}>${esc(item.name || item.id)}</option>`).join('')}${custom}</select></label>${speedMarkup(draft.model, draft, state)}</div><p class="ob-muted ob-more">思考强度沿用原生默认；leader、任务执行和助手各用什么，可以在“设置 → AI 大脑”里细分。</p>`;
    }
    return `${alerts}<div class="ob-runtimes-head"><span class="ob-label" id="ob-runtime-label">默认大脑</span><button type="button" class="ob-link" data-ob-act="recheck"${state.checking ? ' disabled' : ''}>${state.checking ? '正在检测…' : '重新检测'}</button></div><div class="ob-runtimes" role="radiogroup" aria-labelledby="ob-runtime-label">${cards}</div>${fields}`;
  }
  function appearanceMarkup(state) {
    const scale = state.fontScale;
    return `<section class="ob-group"><h3 id="ob-mode-label">显示模式</h3><div class="ob-seg ob-seg-wide" role="radiogroup" aria-labelledby="ob-mode-label">${MODES.map(([id, label]) => `<button type="button" role="radio" data-ob-act="mode" data-mode="${id}" aria-checked="${state.mode === id}">${label}</button>`).join('')}</div></section>
      <section class="ob-group"><div class="ob-row"><h3>字号</h3><output data-ob-font-label>${Math.round(scale * 100)}%</output><button type="button" class="ob-link" data-ob-act="font-reset"${scale === 1 ? ' disabled' : ''}>恢复标准</button></div><input class="ob-range" type="range" min="${FONT_MIN}" max="${FONT_MAX}" step="0.01" value="${scale}" data-ob-field="font" aria-label="字号"><div class="ob-preview" data-ob-preview><span class="ob-preview-kicker">预览</span><strong>首页改版 · 第 2 轮</strong><p>leader 已完成这一轮，等你验收：按报告里的三步实测，结果和预期一致就点“通过”。</p></div></section>`;
  }
  function featuresMarkup() {
    return `<div class="ob-features">${FEATURES.map(([title, text, glyph]) => `<article class="ob-feature"><span class="ob-feature-mark">${icon(glyph)}</span><div><h3>${esc(title)}</h3><p>${esc(text)}</p></div></article>`).join('')}</div>`;
  }
  function workspaceMarkup() {
    return `<div class="ob-choices"><button type="button" class="ob-choice" data-ob-act="open-existing"><span class="ob-choice-mark">${icon('<path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v2M3 7h15a2 2 0 0 1 1.9 2.6l-2.3 7A2 2 0 0 1 15.7 18H4a2 2 0 0 1-2-2V9a2 2 0 0 1 1-2Z"/>')}</span><strong>打开已有文件夹</strong><span>已有代码或资料的文件夹，直接作为工作区打开。</span></button><button type="button" class="ob-choice" data-ob-act="new-folder"><span class="ob-choice-mark">${icon('<path d="M12 5v14M5 12h14"/>')}</span><strong>新建空白文件夹</strong><span>起个名字、选好位置，Crew 新建一个空文件夹作为工作区。</span></button></div><div class="ob-after"><strong>进了空白工作区之后</strong><span>有两个入口：「自己搭」直接进工作台，「助手开局」交给全局助手先出方案。</span></div><p class="ob-muted">点选后引导就结束，文件夹在首页原有的打开 / 新建流程里选。</p>`;
  }
  function bodyMarkup(state) {
    const id = STEPS[state.step][0];
    return id === 'runtime' ? runtimeMarkup(state) : id === 'appearance' ? appearanceMarkup(state) : id === 'features' ? featuresMarkup() : workspaceMarkup();
  }
  function markup(state) {
    const id = STEPS[state.step][0], [title, lead] = HEADS[id], last = state.step === STEPS.length - 1;
    return `<div class="ob-sheet">${railMarkup(state.step)}<div class="ob-main"><header class="ob-head"><p class="ob-eyebrow">第 ${state.step + 1} 步 · 共 ${STEPS.length} 步</p><h2 id="ob-title" tabindex="-1">${esc(title)}</h2><p class="ob-lead">${esc(lead)}</p></header><div class="ob-body">${bodyMarkup(state)}</div>${state.error ? `<p class="ob-alert ob-foot-alert" role="alert">${esc(state.error)}</p>` : ''}<footer class="ob-foot"><button type="button" class="ob-skip" data-ob-act="${state.closeFailed ? 'dismiss' : 'skip'}"${state.saving ? ' disabled' : ''}>${state.closeFailed ? '先关闭，下次启动再提醒' : '跳过引导'}</button><span class="ob-foot-space"></span>${state.step ? `<button type="button" class="button secondary" data-ob-act="back"${state.saving ? ' disabled' : ''}>上一步</button>` : ''}<button type="button" class="button" data-ob-act="${last ? 'finish' : 'next'}"${state.saving ? ' disabled' : ''}>${state.saving ? '正在保存…' : last ? '完成引导' : '下一步'}</button></footer></div></div>`;
  }

  // ---- Controller ----
  let current = null;
  function open({bridge = window.crewDesktop, onWorkspace, onClose} = {}) {
    if (current) { current.dialog.querySelector('#ob-title')?.focus(); return current; }
    const state = {step:0, checking:false, readiness:null, readinessError:'', catalog:[], snapshot:null, settingsError:'', draft:null,
      mode:'system', fontScale:1, saving:false, error:'', closeFailed:false};
    const dialog = document.createElement('dialog');
    dialog.className = 'onboarding'; dialog.setAttribute('aria-labelledby', 'ob-title');
    document.body.append(dialog);
    // While the slider is held, this dialog's own preview leads; the saved size arrives on release.
    let dragging = false;
    const stopTheme = bridge.onTheme(value => {
      state.mode = value.mode;
      if (dragging) return;
      state.fontScale = value.fontScale;
      if (STEPS[state.step][0] === 'appearance') render();
    });
    // Re-rendering replaces the controls; keyboard focus returns to the same control afterwards.
    function focusKey(node) {
      if (!node || !dialog.contains(node)) return null;
      if (node.dataset.obField) return `[data-ob-field="${node.dataset.obField}"]`;
      if (!node.dataset.obAct) return null;
      const extra = ['runtime', 'mode', 'speed', 'step'].find(name => node.dataset[name] !== undefined);
      return `[data-ob-act="${node.dataset.obAct}"]` + (extra ? `[data-${extra}="${CSS.escape(node.dataset[extra])}"]` : '');
    }
    function render(focusTitle = false) {
      const key = focusKey(document.activeElement);
      dialog.innerHTML = markup(state);
      // The page's CSP allows no inline style attribute; the preview size is set through the CSSOM.
      dialog.querySelector('[data-ob-preview]')?.style.setProperty('--ob-fs', String(state.fontScale));
      const target = focusTitle ? dialog.querySelector('#ob-title') : key && dialog.querySelector(key);
      target?.focus({preventScroll:!focusTitle});
    }
    function go(step) { state.step = step; state.error = ''; render(true); dialog.querySelector('.ob-body').scrollTop = 0; }
    async function detect() {
      state.checking = true; state.readinessError = ''; state.settingsError = ''; render();
      // The settings scripts (speed tiers) are the same ones the settings page injects; load them once.
      const [readiness, settings, ui] = await Promise.allSettled([bridge.runtimeReadiness(), bridge.settingsGet(), window.CrewSettings ? null : bridge.settingsUI()]);
      state.checking = false;
      if (readiness.status === 'fulfilled') state.readiness = readiness.value.runtimes; else state.readinessError = reason(readiness.reason);
      if (settings.status === 'fulfilled') { state.snapshot = settings.value; state.catalog = settings.value.runtimes; } else state.settingsError = reason(settings.reason);
      if (ui.status === 'rejected') state.settingsError = '设置界面加载失败：' + reason(ui.reason);
      if (state.readiness && state.snapshot) {
        const kept = state.draft && state.readiness.some(entry => entry.id === state.draft.runtime && usable(entry, state.catalog));
        if (!kept) state.draft = initialDraft(state.snapshot.ai.default, state.readiness, state.catalog);
      }
      render();
    }
    async function saveBrain() {
      if (!state.snapshot || !state.draft?.runtime || same(state.draft, state.snapshot.ai.default)) return true;
      state.saving = true; state.error = ''; render();
      try { state.snapshot = await bridge.settingsSave({ai:{...state.snapshot.ai, default:state.draft}}); state.catalog = state.snapshot.runtimes; return true; }
      catch (error) { state.error = '默认大脑没有保存：' + reason(error); return false; }
      finally { state.saving = false; render(); }
    }
    function close() {
      stopTheme(); dialog.close(); dialog.remove(); current = null; onClose?.();
    }
    async function complete(outcome, then) {
      state.saving = true; state.error = ''; render();
      try { await bridge.onboardingSave(outcome); }
      catch (error) { state.saving = false; state.closeFailed = true; state.error = '没能记下引导状态：' + reason(error); render(); return; }
      close(); then?.();
    }
    async function setMode(mode) {
      try { const value = await bridge.setTheme(mode); state.mode = value.mode; state.error = ''; }
      catch (error) { state.error = '显示模式没有保存：' + reason(error); }
      render();
    }
    async function setFont(value) {
      try { const saved = await bridge.setFontScale(value); state.fontScale = saved.fontScale; state.error = ''; }
      catch (error) { state.error = '字号没有保存：' + reason(error); }
      render();
    }
    dialog.addEventListener('click', event => {
      const button = event.target.closest('[data-ob-act]');
      if (!button || button.disabled || state.saving) return;
      const act = button.dataset.obAct;
      if (act === 'step') go(Number(button.dataset.step));
      else if (act === 'next') void (STEPS[state.step][0] === 'runtime' ? saveBrain() : Promise.resolve(true)).then(saved => { if (saved) go(state.step + 1); });
      else if (act === 'back') go(state.step - 1);
      else if (act === 'skip') void complete('skipped');
      else if (act === 'dismiss') close();
      else if (act === 'finish') void complete('completed');
      else if (act === 'recheck') void detect();
      else if (act === 'runtime') {
        if (button.getAttribute('aria-disabled') === 'true') return;
        state.draft = changeRuntime(state.draft, button.dataset.runtime); render();
      } else if (act === 'speed') { state.draft = {...state.draft, speed:button.dataset.speed || null}; render(); }
      else if (act === 'mode') void setMode(button.dataset.mode);
      else if (act === 'font-reset') void setFont(1);
      else if (act === 'open-existing' || act === 'new-folder') void complete('completed', () => onWorkspace(act === 'open-existing' ? 'open' : 'new'));
    });
    dialog.addEventListener('change', event => {
      const field = event.target.dataset.obField;
      if (field === 'model') {
        const entry = state.catalog.find(item => item.id === state.draft.runtime);
        state.draft = changeModel(state.draft, entry, event.target.value, window.CrewSettings.speedChoices); render();
      } else if (field === 'font') { dragging = false; void setFont(Number(event.target.value)); }
    });
    // Dragging previews the size here only; releasing the slider saves it for every Crew page.
    dialog.addEventListener('input', event => {
      if (event.target.dataset.obField !== 'font') return;
      dragging = true;
      const value = Number(event.target.value);
      dialog.querySelector('[data-ob-font-label]').textContent = Math.round(value * 100) + '%';
      dialog.querySelector('[data-ob-preview]').style.setProperty('--ob-fs', String(value));
    });
    // Escape is the same as “跳过引导”, so the guide does not return on the next start.
    dialog.addEventListener('cancel', event => { event.preventDefault(); if (state.saving) return; if (state.closeFailed) close(); else void complete('skipped'); });
    render();
    dialog.showModal();
    dialog.querySelector('#ob-title').focus();
    void bridge.theme().then(value => { state.mode = value.mode; state.fontScale = value.fontScale; if (STEPS[state.step][0] === 'appearance') render(); },
      error => { state.error = '读取外观设置失败：' + reason(error); render(); });
    void detect();
    current = {dialog, state};
    return current;
  }

  window.CrewOnboarding = {open, shouldOpen, status, usable, initialDraft, changeRuntime, changeModel, markup, STEPS,
    isOpen: () => current !== null};
})();
