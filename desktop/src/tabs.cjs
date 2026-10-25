'use strict';
const { loopback } = require('./core.cjs');

// Each workspace owns one persistent document. Selecting a tab changes only
// native visibility/focus; closing it never owns or stops a Crew backend.
class WorkspaceTabs {
  constructor(window, { origin, createView, onChange = () => {}, onHome = () => {},
    external = () => {}, openProject = () => {}, workspaceSettings = () => {}, onKeyboard = () => {}, onNavigationError = error => console.error('Workspace navigation failed:', error) }) {
    if (!window?.contentView || typeof createView !== 'function') throw new TypeError('A window and view factory are required');
    this.window = window; this.origin = loopback(origin); this.createView = createView;
    this.onChange = onChange; this.onHome = onHome; this.external = external;
    this.openProject = openProject; this.workspaceSettings = workspaceSettings; this.onKeyboard = onKeyboard;
    this.onNavigationError = onNavigationError;
    this.entries = new Map(); this.activeId = 'home'; this.counter = 0; this.disposed = false;
    this.bounds = { x: 0, y: 76, width: 0, height: 0 };
  }

  _public(tab) { return { id: tab.id, name: tab.project.display_name || tab.project.name, project: tab.project.name, loading: tab.loading, error: tab.error }; }
  state() {
    return { tabs: [{ id: 'home', name: '工作区', project: null, loading: false, error: false },
      ...[...this.entries.values()].map(tab => this._public(tab))], activeId: this.activeId };
  }
  get activeView() { return this.entries.get(this.activeId)?.view || null; }
  reconnect(origin) {
    if (this.entries.size !== 0) throw new Error('已有工作区标签时不能更换服务地址');
    this.origin = loopback(origin);
    this._notify();
  }
  _notify() { if (!this.disposed) this.onChange(this.state()); }
  _alive(tab) { return !this.disposed && this.entries.get(tab.id) === tab; }
  _error(tab) {
    if (this._alive(tab)) {
      tab.error = true; tab.loading = false; tab.view.setVisible(false); this._notify();
    }
  }
  _openProject(tab, name) {
    try {
      const pending = this.openProject(name);
      if (pending && typeof pending.catch === 'function') pending.catch(error => this.onNavigationError(error));
    } catch (error) { this.onNavigationError(error); }
  }

  _destination(tab, value) {
    let url;
    try { url = new URL(value); } catch (_) { return { kind: 'blocked' }; }
    if (url.username || url.password) return { kind: 'blocked' };
    if (url.origin === this.origin && url.pathname === '/') {
      const settings = url.searchParams.getAll('workspace-settings');
      if (settings.length === 1 && settings[0] === tab.project.name && url.searchParams.size === 1) return { kind:'workspace-settings' };
      const names = url.searchParams.getAll('project');
      if (names.length === 0 && url.searchParams.size === 0) return { kind: 'home' };
      if (names.length !== 1 || !names[0] || url.searchParams.size !== 1) return { kind: 'blocked' };
      return { kind: names[0] === tab.project.name ? 'same' : 'project', name: names[0] };
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
    if (target.kind === 'workspace-settings') { this.workspaceSettings(tab.project.name); return; }
    if (target.kind === 'project') { this._openProject(tab, target.name); return; }
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
        else if (target.kind === 'workspace-settings') this.workspaceSettings(tab.project.name);
        else if (target.kind === 'project') this._openProject(tab, target.name);
        else if (target.kind === 'external') this.external(url);
      }
      return { action: 'deny' };
    });
    wc.on('before-input-event', (event, input) => { if (this._alive(tab)) this.onKeyboard(event, input); });
    wc.on('did-start-loading', () => {
      if (this._alive(tab)) {
        tab.loading = true; tab.error = false; tab.view.setVisible(this.activeId === tab.id); this._notify();
      }
    });
    wc.on('did-stop-loading', () => { if (this._alive(tab)) { tab.loading = false; this._notify(); } });
    const navigated = (_event, url, isMainFrame = true) => {
      if (!this._alive(tab) || isMainFrame === false) return;
      if (this._destination(tab, url).kind !== 'same') { this._error(tab); return; }
      tab.error = false; tab.view.setVisible(this.activeId === tab.id); this._notify();
    };
    wc.on('did-navigate', navigated);
    wc.on('did-navigate-in-page', navigated);
    wc.on('did-fail-load', (_event, code, _description, _url, isMainFrame) => {
      if (code !== -3 && isMainFrame !== false) this._error(tab);
    });
    wc.on('render-process-gone', () => this._error(tab));
    wc.on('destroyed', () => { if (this._alive(tab)) this.close(tab.id); });
  }

  async open(project) {
    if (this.disposed) throw new Error('Workspace tabs are closed');
    if (!project || typeof project.name !== 'string' || !project.name.trim()) throw new TypeError('A registered project name is required');
    const existing = [...this.entries.values()].find(tab => tab.project.name === project.name);
    if (existing) { this.updateProject(project); this.select(existing.id); return this._public(existing); }
    const view = this.createView(project);
    if (!view?.webContents) throw new TypeError('View factory must return a WebContentsView');
    const tab = { id: 'workspace-' + ++this.counter, project: { ...project }, view, loading: true, error: false };
    this.entries.set(tab.id, tab);
    this._wire(tab);
    this.window.contentView.addChildView(view);
    view.setBounds(this.bounds);
    if (typeof view.setBorderRadius === 'function') view.setBorderRadius(8);
    this.select(tab.id);
    try {
      await view.webContents.loadURL(this.origin + '/?project=' + encodeURIComponent(project.name));
      if (this._alive(tab)) { tab.loading = false; this._notify(); }
    } catch (error) {
      if (this._alive(tab)) { this._error(tab); throw error; }
    }
    return this._public(tab);
  }

  select(id) {
    if (this.disposed || id !== 'home' && !this.entries.has(id)) return false;
    this.activeId = id;
    for (const tab of this.entries.values()) tab.view.setVisible(tab.id === id && !tab.error);
    if (id === 'home') { this.window.webContents.focus(); this.onHome(); }
    else {
      const tab = this.entries.get(id), wc = tab.view.webContents;
      if (tab.error) this.window.webContents.focus();
      else if (!wc.isDestroyed()) wc.focus();
    }
    this._notify();
    return true;
  }

  updateProject(project) {
    const tab = [...this.entries.values()].find(item => item.project.name === project.name);
    if (!tab) return false;
    tab.project = {...tab.project, ...project}; this._notify(); return true;
  }
  closeProject(name) {
    const tab = [...this.entries.values()].find(item => item.project.name === name);
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
    this.entries.delete(id);
    tab.view.setVisible(false);
    this.window.contentView.removeChildView(tab.view);
    if (!tab.view.webContents.isDestroyed()) tab.view.webContents.close();
    if (active) {
      const remaining = this.state().tabs;
      this.select(remaining[Math.min(index, remaining.length - 1)].id);
    } else this._notify();
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
    for (const tab of this.entries.values()) tab.view.setBounds(this.bounds);
  }
  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    for (const tab of this.entries.values()) {
      this.window.contentView.removeChildView(tab.view);
      if (!tab.view.webContents.isDestroyed()) tab.view.webContents.close();
    }
    this.entries.clear(); this.activeId = 'home';
  }
}

module.exports = { WorkspaceTabs };
