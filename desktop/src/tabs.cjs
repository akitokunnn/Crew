'use strict';
const { loopback } = require('./core.cjs');
const { presentation } = require('./workspace-activity.cjs');

// The global assistant is a separate page that lives to the right of the
// workspaces, so switching to or from it pushes whole pages sideways.
const SLIDE_MS = 340;
// Chromium holds the window's frames until a brand-new page's renderer is up,
// so a slide into a new page waits for its document, briefly; a slow backend
// still slides its loading surface in.
const NEW_PAGE_WAIT_MS = 160;
const easeOut = t => 1 - (1 - t) ** 3;
const easeOutInverse = p => 1 - (1 - p) ** (1 / 3);

// Each workspace owns one persistent document. Selecting a tab changes only
// page visibility/focus; closing it never owns or stops a Crew backend.
// Tab views live inside one stage view that clips them to the view area, so a
// sliding page never covers the window gutters.
class WorkspaceTabs {
  constructor(window, { origin, createView, createStage, prefersReducedMotion, onChange = () => {}, onHome = () => {},
    external = () => {}, openProject = () => {}, workspaceSettings = () => {}, onTitleChange = () => {}, onKeyboard = () => {},
    readActivity = null, setTimer = setInterval, clearTimer = clearInterval,
    now = () => performance.now(), scheduleFrame = callback => setTimeout(callback, 8), cancelFrame = clearTimeout,
    onNavigationError = error => console.error('Workspace navigation failed:', error) }) {
    if (!window?.contentView || typeof createView !== 'function' || typeof createStage !== 'function') throw new TypeError('A window, stage and view factory are required');
    if (typeof prefersReducedMotion !== 'function') throw new TypeError('The system reduced-motion setting reader is required');
    this.window = window; this.origin = loopback(origin); this.createView = createView;
    this.prefersReducedMotion = prefersReducedMotion; this.now = now; this.scheduleFrame = scheduleFrame; this.cancelFrame = cancelFrame;
    this.slide = null;
    this.onChange = onChange; this.onHome = onHome; this.external = external;
    this.openProject = openProject; this.workspaceSettings = workspaceSettings; this.onKeyboard = onKeyboard;
    this.onTitleChange = onTitleChange;
    this.onNavigationError = onNavigationError;
    this.entries = new Map(); this.activeId = 'home'; this.counter = 0; this.disposed = false;
    this.bounds = { x: 0, y: 76, width: 0, height: 0 };
    this.stage = createStage(); window.contentView.addChildView(this.stage);
    this.stage.setBounds(this.bounds); this.stage.setVisible(false);
    this.readActivity=readActivity;this.activityBusy=false;this.clearTimer=clearTimer;
    this.activityTimer=readActivity?setTimer(()=>{void this.refreshActivity();},5000):null;
    this.activityTimer?.unref?.();
  }

  _public(tab) { if (tab.kind === 'assistant') return {id:tab.id,kind:'assistant',name:'助手',project:null,loading:tab.loading,error:tab.error};
    return { id: tab.id, name: tab.project.display_name || tab.project.name, project: tab.project.name, loading: tab.loading, error: tab.error,
    readOnly:Boolean(tab.project.read_only), activity:tab.project.read_only ? {tone:'idle',label:'已保存的只读历史',badge:0,unread:0,items:[],stale:false,error:''} : tab.activity||null }; }
  async refreshActivity() {
    if(this.disposed||this.activityBusy||!this.readActivity||!this.entries.size)return;
    const tabs=[...this.entries.values()].filter(tab=>tab.kind !== 'assistant' && !tab.project.read_only);
    if(!tabs.length)return;
    this.activityBusy=true;let changed=false;
    try{
      const result=await this.readActivity(tabs.map(tab=>tab.project.name));
      if(!Array.isArray(result?.projects))throw Error('项目动态返回格式不正确');
      for(const tab of tabs){
        if(!this._alive(tab))continue;
        const snapshot=result.projects.find(item=>item.project===tab.project.name);
        if(!snapshot||snapshot.error){tab.activity=presentation(tab.activitySnapshot,true,snapshot?.error||'暂时没有读到项目动态');}
        else{tab.activitySnapshot=snapshot;tab.activity=presentation(snapshot);}
        changed=true;
      }
    }catch(error){for(const tab of tabs)if(this._alive(tab)){tab.activity=presentation(tab.activitySnapshot,true,error.message);changed=true;}}
    finally{this.activityBusy=false;if(changed&&!this.disposed)this._notify();}
  }
  state() {
    // The assistant tab always sits last, matching its title-bar entry on the right.
    const tabs = [...this.entries.values()].sort((a, b) => (a.kind === 'assistant') - (b.kind === 'assistant'));
    return { tabs: [{ id: 'home', name: '工作区', project: null, loading: false, error: false },
      ...tabs.map(tab => this._public(tab))], activeId: this.activeId,
      transition: this.slide ? { from: this.slide.from, to: this.slide.to } : null };
  }
  get activeView() { return this.entries.get(this.activeId)?.view || null; }
  reconnect(origin) {
    if (this.entries.size !== 0) throw new Error('已有工作区标签时不能更换服务地址');
    this.origin = loopback(origin);
    this._notify();
  }
  _notify() { if (!this.disposed) this.onChange(this.state()); }
  _alive(tab) { return !this.disposed && this.entries.get(tab.id) === tab; }
  _syncPageVisibility(tab) {
    const wc = tab.view.webContents;
    if (!this._alive(tab) || wc.isDestroyed() || this._destination(tab, wc.getURL()).kind !== 'same') return;
    const visible = this.activeId === tab.id && !tab.error;
    if (tab.pageVisible === visible) return;
    tab.pageVisible = visible;
    // WebContentsView.setVisible does not reliably update document.visibilityState.
    // Hidden pages must release their event stream without unloading drafts.
    void wc.executeJavaScript(`window.crewTabVisible = ${visible}; window.dispatchEvent(new CustomEvent('crew-tab-visibility', {detail:{visible:window.crewTabVisible}}));`)
      .catch(error => { if (this._alive(tab) && !wc.isDestroyed()) this.onNavigationError(error); });
  }
  _error(tab) {
    if (this._alive(tab)) {
      tab.error = true; tab.loading = false;
      if (this.slide && [this.slide.from, this.slide.to].includes(tab.id)) { this._stopSlide(); this._focus(); }
      this._layout(); this._syncPageVisibility(tab); this._notify();
    }
  }
  _openProject(tab, name, hash = '') {
    try {
      const pending = this.openProject(name, hash);
      if (pending && typeof pending.catch === 'function') pending.catch(error => this.onNavigationError(error));
    } catch (error) { this.onNavigationError(error); }
  }

  _destination(tab, value) {
    let url;
    try { url = new URL(value); } catch (_) { return { kind: 'blocked' }; }
    if (url.username || url.password) return { kind: 'blocked' };
    if (/^codex:\/\/threads\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)) return {kind:'external'};
    if (url.origin === this.origin && url.pathname === '/assistant.html' && !url.search) {
      return {kind:tab.kind === 'assistant' ? 'same' : 'assistant'};
    }
    if (url.origin === this.origin && url.pathname === '/') {
      const settings = url.searchParams.getAll('workspace-settings');
      if (settings.length === 1 && settings[0] === tab.project?.name && url.searchParams.size === 1) return { kind:'workspace-settings' };
      const names = url.searchParams.getAll('project');
      if (names.length === 0 && url.searchParams.size === 0) return { kind: 'home' };
      if (names.length !== 1 || !names[0] || url.searchParams.size !== 1) return { kind: 'blocked' };
      return { kind: names[0] === tab.project?.name ? 'same' : 'project', name: names[0], hash:url.hash };
    }
    return { kind: ['http:', 'https:'].includes(url.protocol) ? 'external' : 'blocked' };
  }

  _guard(tab, event, url, redirect = false, isMainFrame = true) {
    if (!this._alive(tab)) { event.preventDefault(); return; }
    const target = this._destination(tab, url);
    if (target.kind === 'same') return;
    event.preventDefault();
    // A subframe cannot use navigation to select workspaces or open a browser.
    if (!isMainFrame) return;
    if (target.kind === 'home') { this.select('home'); return; }
    if (target.kind === 'assistant') { void this.openAssistant().catch(this.onNavigationError); return; }
    if (target.kind === 'workspace-settings') { this.workspaceSettings(tab.project.name); return; }
    if (target.kind === 'project') { this._openProject(tab, target.name, target.hash); return; }
    if (target.kind === 'external' && !redirect) this.external(url);
    else this._error(tab);
  }

  _wire(tab) {
    const wc = tab.view.webContents;
    wc.on('will-navigate', (event, url) => this._guard(tab, event, url));
    wc.on('will-redirect', (event, url, _inPlace, isMainFrame) => this._guard(tab, event, url, true, isMainFrame !== false));
    wc.setWindowOpenHandler(({ url }) => {
      if (this._alive(tab)) {
        const target = this._destination(tab, url);
        if (target.kind === 'same') this.select(tab.id);
        else if (target.kind === 'home') this.select('home');
        else if (target.kind === 'assistant') void this.openAssistant().catch(this.onNavigationError);
        else if (target.kind === 'workspace-settings') this.workspaceSettings(tab.project.name);
        else if (target.kind === 'project') this._openProject(tab, target.name, target.hash);
        else if (target.kind === 'external') this.external(url);
      }
      return { action: 'deny' };
    });
    wc.on('dom-ready', () => { tab.documentReady = true; });
    wc.on('before-input-event', (event, input) => { if (this._alive(tab)) this.onKeyboard(event, input); });
    wc.on('page-title-updated', event => {
      event.preventDefault();
      if (this._alive(tab) && tab.kind !== 'assistant' && this._destination(tab, wc.getURL()).kind === 'same') this.onTitleChange(tab.project.name);
    });
    wc.on('did-start-loading', () => {
      if (this._alive(tab)) {
        tab.loading = true; tab.error = false; this._layout(); this._notify();
      }
    });
    wc.on('did-stop-loading', () => { if (this._alive(tab)) { tab.loading = false; this._notify(); } });
    wc.on('did-finish-load', () => {
      tab.pageVisible = null;
      this._syncPageVisibility(tab);
    });
    const navigated = (_event, url, isMainFrame = true) => {
      if (!this._alive(tab) || isMainFrame === false) return;
      if (this._destination(tab, url).kind !== 'same') { this._error(tab); return; }
      tab.error = false; this._layout(); this._notify();
    };
    wc.on('did-navigate', navigated);
    wc.on('did-navigate-in-page', navigated);
    wc.on('did-fail-load', (_event, code, _description, _url, isMainFrame) => {
      if (code !== -3 && isMainFrame !== false) this._error(tab);
    });
    wc.on('render-process-gone', () => this._error(tab));
    wc.on('destroyed', () => { if (this._alive(tab)) this.close(tab.id); });
  }

  async open(project, hash = '') {
    if (this.disposed) throw new Error('Workspace tabs are closed');
    if (!project || typeof project.name !== 'string' || !project.name.trim()) throw new TypeError('A registered project name is required');
    const existing = [...this.entries.values()].find(tab => tab.project?.name === project.name);
    if (existing) {
      this.updateProject(project); this.select(existing.id);
      if (hash) await existing.view.webContents.executeJavaScript('location.hash=' + JSON.stringify(hash));
      return this._public(existing);
    }
    const view = this.createView(project);
    if (!view?.webContents) throw new TypeError('View factory must return a WebContentsView');
    const tab = { id: 'workspace-' + ++this.counter, project: { ...project }, view, loading: true, error: false, documentReady: false };
    this.entries.set(tab.id, tab);
    this._wire(tab);
    this.stage.addChildView(view);
    if (typeof view.setBorderRadius === 'function') view.setBorderRadius(8);
    this.select(tab.id);
    try {
      await view.webContents.loadURL(this.origin + '/?project=' + encodeURIComponent(project.name) + (hash.startsWith('#') ? hash : ''));
      if (this._alive(tab)) { tab.loading = false; this._syncPageVisibility(tab); this._notify(); }
    } catch (error) {
      if (this._alive(tab)) { this._error(tab); throw error; }
    }
    void this.refreshActivity();
    return this._public(tab);
  }

  async openAssistant() {
    if (this.disposed) throw new Error('Workspace tabs are closed');
    const existing = this.entries.get('assistant');
    if (existing) { this.select(existing.id); return this._public(existing); }
    const view = this.createView({kind:'assistant'});
    const tab = {id:'assistant',kind:'assistant',project:null,view,loading:true,error:false,documentReady:false};
    this.entries.set(tab.id,tab); this._wire(tab);
    this.stage.addChildView(view);
    if (typeof view.setBorderRadius === 'function') view.setBorderRadius(8);
    this.select(tab.id);
    try {
      await view.webContents.loadURL(this.origin + '/assistant.html');
      if (this._alive(tab)) { tab.loading=false;this._syncPageVisibility(tab);this._notify(); }
    } catch(error) { if (this._alive(tab)) { this._error(tab);throw error; } }
    return this._public(tab);
  }

  select(id) { return this._select(id, true); }
  _select(id, animate) {
    if (this.disposed || id !== 'home' && !this.entries.has(id)) return false;
    const from = this.activeId, slide = this.slide;
    this.activeId = id;
    if (slide && id === slide.from) {
      // Reverse in place: both pages keep their current offsets and head back.
      const t = easeOutInverse(1 - easeOut(this._elapsed(slide)));
      this.slide = { from: slide.to, to: slide.from, direction: -slide.direction, start: this.now() - t * SLIDE_MS, frame: slide.frame };
    } else if (!slide || id !== slide.to) {
      if (slide) this._stopSlide();
      if (animate && this._slides(from, id)) {
        // The clock starts at the first frame: creating a view can keep the main
        // thread busy, and the slide must not skip its opening.
        this.slide = { from, to: id, direction: id === 'assistant' ? 1 : -1, start: null, requested: this.now(), frame: null };
        this.slide.frame = this.scheduleFrame(() => this._frame());
      }
    }
    this._layout();
    for (const tab of this.entries.values()) this._syncPageVisibility(tab);
    if (id === 'home') this.onHome();
    if (!this.slide) this._focus();
    this._notify();
    return true;
  }
  _slides(from, to) {
    if (from === to || from !== 'assistant' && to !== 'assistant' || this.bounds.width === 0) return false;
    if ([from, to].some(id => id !== 'home' && this.entries.get(id).error)) return false;
    return !this.prefersReducedMotion();
  }
  _elapsed(slide) { return slide.start === null ? 0 : Math.min(1, (this.now() - slide.start) / SLIDE_MS); }
  _frame() {
    // A frame is scheduled only while a slide exists; stopping always cancels it.
    const slide = this.slide;
    if (slide.start === null) {
      const newPage = [slide.from, slide.to].some(id => this.entries.get(id)?.documentReady === false);
      if (newPage && this.now() - slide.requested < NEW_PAGE_WAIT_MS) { slide.frame = this.scheduleFrame(() => this._frame()); return; }
      slide.start = this.now();
    }
    if (this._elapsed(slide) < 1) {
      slide.frame = this.scheduleFrame(() => this._frame());
      this._layout(); return;
    }
    this.slide = null; this._layout(); this._focus(); this._notify();
  }
  _stopSlide() { this.cancelFrame(this.slide.frame); this.slide = null; }
  // The only place that decides native view geometry and visibility.
  _layout() {
    const { width, height } = this.bounds, slide = this.slide, offsets = new Map();
    if (slide) {
      const from = Math.round(-slide.direction * width * easeOut(this._elapsed(slide)));
      offsets.set(slide.from, from); offsets.set(slide.to, from + slide.direction * width);
    }
    let shown = false;
    for (const tab of this.entries.values()) {
      const visible = !tab.error && (offsets.has(tab.id) || tab.id === this.activeId);
      tab.view.setBounds({ x: offsets.get(tab.id) ?? 0, y: 0, width, height });
      tab.view.setVisible(visible); shown ||= visible;
    }
    this.stage.setVisible(shown);
  }
  _focus() {
    const tab = this.entries.get(this.activeId);
    if (!tab || tab.error) this.window.webContents.focus();
    else if (!tab.view.webContents.isDestroyed()) tab.view.webContents.focus();
  }

  updateProject(project) {
    const tab = [...this.entries.values()].find(item => item.project?.name === project.name);
    if (!tab) return false;
    const changed = tab.project.root !== project.root || tab.project.display_name !== project.display_name;
    tab.project = {...tab.project, ...project}; if (changed) this._notify(); return true;
  }
  closeProject(name) {
    const tab = [...this.entries.values()].find(item => item.project?.name === name);
    return tab ? this.close(tab.id) : false;
  }

  close(id) {
    if (this.disposed) return false;
    if (id === 'home') {
      if (this.entries.size) return false;
      this.window.close(); return true;
    }
    const tab = this.entries.get(id);
    if (!tab) return false;
    const order = this.state().tabs.map(item => item.id), index = order.indexOf(id), active = this.activeId === id;
    const sliding = this.slide && [this.slide.from, this.slide.to].includes(id);
    if (sliding) this._stopSlide();
    this.entries.delete(id);
    tab.view.setVisible(false);
    this.stage.removeChildView(tab.view);
    if (!tab.view.webContents.isDestroyed()) tab.view.webContents.close();
    if (active) {
      // The closed page is already gone; its neighbor appears without a slide.
      const remaining = this.state().tabs;
      this._select(remaining[Math.min(index, remaining.length - 1)].id, false);
    } else {
      this._layout();
      if (sliding) this._focus();
      this._notify();
    }
    return true;
  }

  cycle(delta) {
    if (!Number.isInteger(delta)) throw new TypeError('Tab cycle direction must be an integer');
    const ids = this.state().tabs.map(tab => tab.id), index = ids.indexOf(this.activeId);
    return this.select(ids[((index + delta) % ids.length + ids.length) % ids.length]);
  }
  back() {
    const wc = this.activeView?.webContents || this.window.webContents;
    if (wc.navigationHistory.canGoBack()) { wc.navigationHistory.goBack(); return true; }
    return false;
  }
  reload() {
    const tab = this.entries.get(this.activeId), wc = tab?.view.webContents || this.window.webContents;
    try {
      const pending = wc.reload();
      if (tab && pending && typeof pending.catch === 'function') pending.catch(() => this._error(tab));
      return true;
    } catch (error) {
      if (!tab) throw error;
      this._error(tab); return false;
    }
  }
  setBounds(bounds) {
    if (!bounds || !['x', 'y', 'width', 'height'].every(key => Number.isInteger(bounds[key]) && bounds[key] >= 0)) throw new TypeError('Tab bounds must be nonnegative integers');
    this.bounds = { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height };
    this.stage.setBounds(this.bounds); this._layout();
  }
  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    if(this.activityTimer!==null)this.clearTimer(this.activityTimer);
    if (this.slide) this._stopSlide();
    for (const tab of this.entries.values()) {
      this.stage.removeChildView(tab.view);
      if (!tab.view.webContents.isDestroyed()) tab.view.webContents.close();
    }
    this.window.contentView.removeChildView(this.stage);
    this.entries.clear(); this.activeId = 'home';
  }
}

module.exports = { WorkspaceTabs };
