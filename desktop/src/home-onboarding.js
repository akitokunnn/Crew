'use strict';
(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
  const STEPS = [
    ['认识 Crew', '像带团队一样，让多个 AI 一起把事做完。'],
    ['连接 AI 引擎', '选一个已登录的引擎，Crew 会默认用它开始新对话。'],
    ['选文件夹并开局', '一个普通文件夹就能开工。']
  ];
  const FEATURES = [
    ['Leader', '给每块工作找一位长期负责人。它会自主推进、交付结果，关键处等你拍板。'],
    ['思维导图 / 架构图', '在导图里理清想法，再用架构图划分工作、安排负责人。'],
    ['作战室', '先和领头聊出计划，你敲定后再组队推进。']
  ];
  const MODES = [['system', '跟随系统'], ['light', '浅色'], ['dark', '深色']];
  const LOGIN = {claude:'claude', codex:'codex login'};

  function status(entry) {
    if (!entry.installed) return {tone:'off', label:'未安装', hint:'安装后重新检测。'};
    if (entry.error) return {tone:'error', label:'检测失败', hint:entry.error};
    if (entry.logged_in === true) return {tone:'ok', label:'已登录'};
    return {tone:'warn', label:entry.logged_in === false ? '未登录' : '登录未确认', command:LOGIN[entry.id]};
  }
  const usable = entry => entry.installed && entry.logged_in === true && !entry.error;
  function defaultEngine(saved, readiness) {
    const ready = readiness.filter(usable);
    if (ready.some(entry => entry.id === saved)) return saved;
    return ready.length === 1 ? ready[0].id : null;
  }
  function shouldOpen({remote, projects, pendingAction, status:outcome}) {
    return !remote && !pendingAction && projects === 0 && outcome === 'pending';
  }
  const canSave = state => state.snapshot && !state.checking && !state.readinessError && !state.settingsError
    && state.readiness.some(entry => entry.id === state.engine && usable(entry));

  function runtimeMarkup(state) {
    const alerts = [state.readinessError && `引擎检测失败：${state.readinessError}`, state.settingsError && `读取 AI 设置失败：${state.settingsError}`]
      .filter(Boolean).map(text => `<p class="ob-alert" role="alert">${esc(text)}</p>`).join('');
    return `${alerts}<div class="ob-runtimes-head"><span>${state.checking ? '正在检测…' : '默认引擎'}</span><button type="button" class="ob-link" data-ob-act="recheck" ${state.checking ? 'disabled' : ''}>重新检测</button></div>
      <div class="ob-runtimes" aria-label="默认引擎" role="radiogroup">${state.readiness.map(entry => {
        const view = status(entry), chosen = state.engine === entry.id;
        return `<article class="ob-runtime" data-chosen="${chosen}"><div class="ob-runtime-top"><strong>${esc(entry.name)}</strong><span class="ob-pill" data-tone="${view.tone}">${view.label}</span></div>
          ${view.hint ? `<p>${esc(view.hint)}</p>` : ''}
          ${view.command ? `<p>在终端运行 <code>${view.command}</code></p><button type="button" class="ob-link" data-ob-act="copy" data-runtime="${entry.id}">复制命令</button>` : ''}
          ${usable(entry) ? `<label class="ob-engine"><input type="radio" name="ob-engine" data-ob-field="engine" value="${entry.id}" ${chosen ? 'checked' : ''}>${chosen ? '默认引擎' : '设为默认引擎'}</label>` : ''}
        </article>`;
      }).join('')}</div>`;
  }
  function appearanceMarkup(state) {
    const scale = state.fontScale;
    return `<div class="ob-appearance"><button type="button" class="ob-link" data-ob-act="appearance" aria-expanded="${state.appearance}">外观 · ${MODES.find(([id]) => id === state.mode)[1]} · ${Math.round(scale * 100)}% ▾</button>
      ${state.appearance ? `<section class="ob-appearance-panel" aria-label="外观设置"><div class="ob-seg" aria-label="显示模式">${MODES.map(([id, label]) => `<button type="button" data-ob-act="mode" data-mode="${id}" aria-pressed="${state.mode === id}">${label}</button>`).join('')}</div>
        <div class="ob-row"><label for="ob-font">字号</label><output for="ob-font" data-ob-font-label>${Math.round(scale * 100)}%</output><button type="button" class="ob-link" data-ob-act="font-reset" ${scale === 1 ? 'disabled' : ''}>恢复标准</button></div><input id="ob-font" class="ob-range" type="range" min="0.9" max="1.4" step="0.01" value="${scale}" data-ob-field="font"></section>` : ''}</div>`;
  }
  function workspaceMarkup(state) {
    const {paths, text} = window.CrewWorkspaceStart;
    const folder = state.folder;
    const enabled = folder && (folder.kind === 'existing' || state.folderName.trim());
    return `<div class="ob-folder"><div class="ob-folder-actions">
      <button type="button" class="button secondary" data-ob-act="open-existing">打开已有文件夹</button><button type="button" class="button secondary" data-ob-act="new-folder">新建空白文件夹</button></div>
      ${folder ? `<p class="ob-folder-location"><b>${folder.kind === 'new' ? '保存到' : '已选'}</b> ${esc(folder.directory)}</p>` : ''}
      ${folder?.kind === 'new' ? `<label class="ob-folder-name">文件夹名称<input type="text" data-ob-field="folder-name" maxlength="100" value="${esc(state.folderName)}" ${folder.project ? 'readonly' : ''}></label>` : ''}</div>
      <h3 class="ob-start-title">进去后先做什么？</h3><div class="ob-choices">${paths.map(path => `<article class="ob-choice" data-start="${path.id}"><div>${text(path)}</div><button type="button" class="button ${path.recommended ? '' : 'secondary'}" data-ob-act="start" data-start="${path.id}" aria-label="${path.title}，进入工作区" ${enabled ? '' : 'disabled'}>进入</button></article>`).join('')}</div>`;
  }
  function markup(state) {
    const [title, lead] = STEPS[state.step];
    const isLast = state.step === STEPS.length - 1;
    const body = state.step === 0 ? `<div class="ob-features">${FEATURES.map(([title, detail]) => `<article class="ob-feature"><h3>${title}</h3><p>${detail}</p></article>`).join('')}</div>`
      : state.step === 1 ? runtimeMarkup(state) : workspaceMarkup(state);
    return `<div class="ob-sheet"><nav class="ob-rail" aria-label="引导步骤"><div class="ob-brand"><img class="mark" src="../assets/crew.svg" width="24" height="24" alt=""><span>Crew</span></div><ol class="ob-steps">${STEPS.map(([label], index) => `<li><button type="button" class="ob-step" data-ob-act="step" data-step="${index}" ${index === state.step ? 'aria-current="step"' : ''} ${index >= state.step ? 'disabled' : ''}><i aria-hidden="true">${index + 1}</i><span>${label}</span></button></li>`).join('')}</ol></nav>
      <div class="ob-main"><header class="ob-head"><h2 id="ob-title" tabindex="-1">${title}</h2><p class="ob-lead">${lead}</p></header><div class="ob-body" ${state.pending ? 'inert' : ''}>${body}</div>
      ${state.error ? `<p class="ob-alert ob-foot-alert" role="alert">${esc(state.error)}</p>` : ''}
      <footer class="ob-foot">${appearanceMarkup(state)}<span class="ob-foot-space"></span><button type="button" class="ob-skip" data-ob-act="skip" ${state.pending ? 'disabled' : ''}>跳过引导</button>
      ${state.step ? `<button type="button" class="button secondary" data-ob-act="back" ${state.pending ? 'disabled' : ''}>上一步</button>` : ''}
      ${isLast ? `<button type="button" class="button secondary" data-ob-act="start" ${state.pending || !state.folder || state.folder.kind === 'new' && !state.folderName.trim() ? 'disabled' : ''}>仅进入工作区</button>`
        : `<button type="button" class="button" data-ob-act="next" ${state.pending || state.step === 1 && !canSave(state) ? 'disabled' : ''}>${state.step === 0 ? '开始配置' : '下一步'}</button>`}</footer></div></div>`;
  }

  let current = null;
  function open({bridge = window.crewDesktop, onChoose, onWorkspace, onClose} = {}) {
    if (current) { current.dialog.querySelector('#ob-title').focus(); return current; }
    const state = {step:0, checking:false, readiness:[], readinessError:'', snapshot:null, settingsError:'', engine:null,
      folder:null, folderName:'', appearance:false, mode:'system', fontScale:1, pending:false, error:'', opened:false};
    const dialog = document.createElement('dialog');
    dialog.className = 'onboarding'; dialog.setAttribute('aria-labelledby', 'ob-title');
    document.body.append(dialog);
    function focusKey(node) {
      if (!dialog.contains(node)) return null;
      if (node.dataset.obField) return `[data-ob-field="${node.dataset.obField}"]${node.type === 'radio' ? `[value="${node.value}"]` : ''}`;
      if (!node.dataset.obAct) return null;
      const extra = ['runtime', 'mode', 'step', 'start'].find(name => node.dataset[name] !== undefined);
      return `[data-ob-act="${node.dataset.obAct}"]` + (extra ? `[data-${extra}="${node.dataset[extra]}"]` : '');
    }
    function render(focusTitle = false) {
      const key = focusKey(document.activeElement), scroll = dialog.querySelector('.ob-body')?.scrollTop;
      dialog.innerHTML = markup(state);
      const target = focusTitle ? dialog.querySelector('#ob-title') : key && dialog.querySelector(key);
      target?.focus({preventScroll:true});
      if (!focusTitle && scroll) dialog.querySelector('.ob-body').scrollTop = scroll;
    }
    function applyAppearance(value) {
      state.mode = value.mode; state.fontScale = value.fontScale; render();
    }
    const stopTheme = bridge.onTheme(value => {
      if (dialog.querySelector('[data-ob-field="font"]:active')) return;
      if (value.mode !== state.mode || value.fontScale !== state.fontScale) applyAppearance(value);
    });
    async function detect() {
      state.checking = true; state.readinessError = ''; state.settingsError = ''; render();
      const [readiness, settings] = await Promise.allSettled([bridge.runtimeReadiness(), bridge.settingsGet()]);
      if (readiness.status === 'fulfilled') state.readiness = readiness.value.runtimes;
      else state.readinessError = readiness.reason.message;
      if (settings.status === 'fulfilled') state.snapshot = settings.value;
      else state.settingsError = settings.reason.message;
      if (state.snapshot && !state.readinessError && !state.settingsError) {
        state.engine = defaultEngine(state.engine ?? state.snapshot.ai.default.runtime, state.readiness);
      }
      state.checking = false; render();
    }
    function go(step) {
      state.step = step; state.error = ''; state.appearance = false; render(true);
      if (step === 1 && !state.checking && !state.snapshot) void detect();
    }
    function close() {
      stopTheme(); dialog.close(); dialog.remove(); current = null; onClose?.();
    }
    async function run(work) {
      if (state.pending) return;
      state.pending = true; state.error = ''; render();
      try { return await work(); }
      catch (error) { state.error = error.message; }
      finally { state.pending = false; if (dialog.open) render(); }
    }
    async function next() {
      if (state.step === 0) { go(1); return; }
      if (!canSave(state)) return;
      const saved = state.snapshot.ai.default;
      if (state.engine !== saved.runtime) {
        const ai = {...state.snapshot.ai, default:{runtime:state.engine, model:null, effort:null, speed:null}};
        state.snapshot = await bridge.settingsSave({ai});
      }
      go(2);
    }
    async function choose(kind) {
      const folder = await onChoose(kind);
      if (!folder) return false;
      state.folder = folder;
      return folder.kind === 'new';
    }
    async function finish(choice) {
      if (!state.opened) {
        state.opened = await onWorkspace(state.folder, state.folderName, choice);
        if (!state.opened) return;
      }
      await bridge.onboardingSave('completed'); close();
    }
    dialog.addEventListener('click', event => {
      const button = event.target.closest('[data-ob-act]');
      if (!button || button.disabled || state.pending) return;
      const act = button.dataset.obAct;
      if (act === 'step') go(Number(button.dataset.step));
      else if (act === 'next') void run(next);
      else if (act === 'back') go(state.step - 1);
      else if (act === 'skip') void run(async () => { await bridge.onboardingSave('skipped'); close(); });
      else if (act === 'recheck') void detect();
      else if (act === 'appearance') { state.appearance = !state.appearance; render(); }
      else if (act === 'copy') void run(() => bridge.copyLoginCommand(button.dataset.runtime));
      else if (act === 'mode') void run(async () => applyAppearance(await bridge.setTheme(button.dataset.mode)));
      else if (act === 'font-reset') void run(async () => applyAppearance(await bridge.setFontScale(1)));
      else if (act === 'open-existing' || act === 'new-folder') {
        void run(() => choose(act === 'open-existing' ? 'existing' : 'new')).then(newFolder => {
          if (newFolder) dialog.querySelector('[data-ob-field="folder-name"]').focus();
        });
      }
      else if (act === 'start') void run(() => finish(button.dataset.start));
    });
    dialog.addEventListener('input', event => {
      const field = event.target.dataset.obField;
      if (field === 'folder-name') {
        state.folderName = event.target.value;
        for (const start of dialog.querySelectorAll('[data-ob-act="start"]')) start.disabled = !state.folderName.trim();
      } else if (field === 'font') {
        dialog.querySelector('[data-ob-font-label]').textContent = Math.round(Number(event.target.value) * 100) + '%';
      }
    });
    dialog.addEventListener('change', event => {
      const field = event.target.dataset.obField;
      if (field === 'engine') { state.engine = event.target.value; render(); }
      else if (field === 'font') void run(async () => applyAppearance(await bridge.setFontScale(Number(event.target.value))));
    });
    dialog.addEventListener('cancel', event => {
      event.preventDefault();
      if (state.appearance) { state.appearance = false; render(); return; }
      if (!state.pending) void run(async () => { await bridge.onboardingSave('skipped'); close(); });
    });
    current = {dialog, state};
    render(); dialog.showModal(); dialog.querySelector('#ob-title').focus();
    void bridge.theme().then(value => { if (dialog.open) applyAppearance(value); },
      error => { if (dialog.open) { state.error = error.message; render(); } });
    return current;
  }

  window.CrewOnboarding = {open, shouldOpen, status, usable, defaultEngine, markup, isOpen:() => current !== null};
})();
