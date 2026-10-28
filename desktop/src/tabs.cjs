'use strict';
const { loopback } = require('./core.cjs');
const { presentation } = require('./workspace-activity.cjs');

// Each workspace owns one persistent document. Selecting a tab changes only
// native visibility/focus; closing it never owns or stops a Crew backend.
class WorkspaceTabs {
  constructor(window, { origin, createView, onChange = () => {}, onHome = () => {},
    external = () => {}, openProject = () => {}, workspaceSettings = () => {}, onTitleChange = () => {}, onKeyboard = () => {},
    readActivity = null, setTimer = setInterval, clearTimer = clearInterval,
    onNavigationError = error => console.error('Workspace navigation failed:', error) }) {
    if (!window?.contentView || typeof createView !== 'function') throw new TypeError('A window and view factory are required');
    this.window = window; this.origin = loopback(origin); this.createView = createView;
    this.onChange = onChange; this.onHome = onHome; this.external = external;
    this.openProject = openProject; this.workspaceSettings = workspaceSettings; this.onKeyboard = onKeyboard;
    this.onTitleChange = onTitleChange;
    this.onNavigationError = onNavigationError;
    this.entries = new Map(); this.activeId = 'home'; this.counter = 0; this.disposed = false;
    this.bounds = { x: 0, y: 76, width: 0, height: 0 };
    this.readActivity=readActivity;this.activityBusy=false;this.clearTimer=clearTimer;
    this.activityTimer=readActivity?setTimer(()=>{void this.refreshActivity();},5000):null;
    this.activityTimer?.unref?.();
  }

  _public(tab) { if(tab.kind==='workbench')return{id:tab.id,name:'工作台',project:null,kind:'workbench',loading:tab.loading,error:tab.error};
    return { id: tab.id, name: tab.project.display_name || tab.project.name, project: tab.project.name, loading: tab.loading, error: tab.error,
    readOnly:Boolean(tab.project.read_only), activity:tab.project.read_only ? {tone:'idle',label:'已保存的只读历史',badge:0,unread:0,items:[],stale:false,error:''} : tab.activity||null }; }
  async refreshActivity() {
    if(this.disposed||this.activityBusy||!this.readActivity||!this.entries.size)return;
    const tabs=[...this.entries.values()].filter(tab=>tab.kind!=='workbench'&&!tab.project.read_only);
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
    const workbench=this.entries.get('workbench');
    return { tabs: [...(workbench?[this._public(workbench)]:[]),{ id: 'home', name: '工作区管理', project: null, loading: false, error: false },
      ...[...this.entries.values()].filter(tab=>tab.kind!=='workbench').map(tab => this._public(tab))], activeId: this.activeId };
  }
  get activeView() { return this.entries.get(this.activeId)?.view || null; }
  reconnect(origin) {
    if ([...this.entries.values()].some(tab=>tab.kind!=='workbench')) throw new Error('已有工作区标签时不能更换服务地址');
    this.origin = loopback(origin);
    this._notify();
    const workbench=this.entries.get('workbench');
    if(workbench)return this._load(workbench,this.origin+'/');
  }
  _notify() { if (!this.disposed) this.onChange(this.state()); }
  _alive(tab) { return !this.disposed && this.entries.get(tab.id) === tab; }
  _error(tab) {
    if (this._alive(tab)) {
      tab.error = true; tab.loading = false; tab.view.setVisible(false); this._notify();
    }
  }
  _openProject(tab, name, url) {
    try {
      const pending = url ? this.openProject(name,{url}) : this.openProject(name);
      if (pending && typeof pending.catch === 'function') pending.catch(error => this.onNavigationError(error));
    } catch (error) { this.onNavigationError(error); }
  }

  _destination(tab, value) {
    let url;
    try { url = new URL(value); } catch (_) { return { kind: 'blocked' }; }
    if (url.username || url.password) return { kind: 'blocked' };
    if (url.origin === this.origin && url.pathname === '/') {
      const hashQuery=url.hash.includes('?')?url.hash.slice(url.hash.indexOf('?')+1):'';
      const hash=new URLSearchParams(hashQuery);
      if(hash.getAll('project').length>1||hash.get('data')==='mock')return{kind:'blocked'};
      const settings = url.searchParams.getAll('workspace-settings');
      if (settings.length === 1 && tab.kind!=='workbench' && settings[0] === tab.project.name && url.searchParams.size === 1) return { kind:'workspace-settings' };
      if([...url.searchParams.keys()].some(key=>!['project','legacy','settings'].includes(key))||
         [...url.searchParams.keys()].some(key=>url.searchParams.getAll(key).length!==1))return{kind:'blocked'};
      const names = url.searchParams.getAll('project');
      const legacy=url.searchParams.get('legacy');
      if(legacy!==null&&legacy!=='1'||names.length&& !names[0]||url.searchParams.has('settings')&&legacy!=='1')return{kind:'blocked'};
      if(tab.kind==='workbench')return legacy==='1'&&names.length?{kind:'project',name:names[0],url:url.href}:{kind:'same',url:url.href};
      if(!names.length)return{kind:'workbench',url:url.href};
      if(legacy!=='1'&&url.hash&&!hash.get('project')){url.searchParams.delete('project');return{kind:'workbench',url:url.href};}
      const project=legacy==='1'?names[0]:(hash.get('project')||names[0]);
      if(project===tab.project.name)return{kind:'same',url:url.href};
      url.searchParams.set('project',project);
      return{kind:'project',name:project,url:url.href};
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
    if (target.kind === 'workbench') { void this.openWorkbench(target.url).catch(error=>this.onNavigationError(error)); return; }
    if (target.kind === 'workspace-settings') { this.workspaceSettings(tab.project.name); return; }
    if (target.kind === 'project') { this._openProject(tab, target.name, target.url); return; }
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
        else if (target.kind === 'workbench') void this.openWorkbench(target.url).catch(error=>this.onNavigationError(error));
        else if (target.kind === 'workspace-settings') this.workspaceSettings(tab.project.name);
        else if (target.kind === 'project') this._openProject(tab, target.name, target.url);
        else if (target.kind === 'external') this.external(url);
      }
      return { action: 'deny' };
    });
    wc.on('before-input-event', (event, input) => { if (this._alive(tab)) this.onKeyboard(event, input); });
    wc.on('page-title-updated', event => {
      event.preventDefault();
      if (this._alive(tab) && tab.kind!=='workbench' && this._destination(tab, wc.getURL()).kind === 'same') this.onTitleChange(tab.project.name);
    });
    wc.on('did-start-loading', () => {
      if (this._alive(tab)) {
        tab.loading = true; tab.error = false; tab.view.setVisible(this.activeId === tab.id); this._notify();
      }
    });
    wc.on('did-stop-loading', () => { if (this._alive(tab)) { tab.loading = false; this._notify(); } });
    const navigated = (_event, url, isMainFrame = true, inPage=false) => {
      if (!this._alive(tab) || isMainFrame === false) return;
      const target=this._destination(tab,url);
      if(inPage&&target.kind!=='same'&&tab.safeURL){
        // pushState/hash routing has already changed the address. Restore only
        // our last accepted route in the same document before verified routing.
        void wc.executeJavaScript('history.replaceState(history.state,"",'+JSON.stringify(tab.safeURL)+');window.dispatchEvent(new HashChangeEvent("hashchange"));')
          .then(()=>{if(this._alive(tab))this._guard(tab,{preventDefault(){}},url);}).catch(error=>{if(this._alive(tab))this.onNavigationError(error);});
        return;
      }
      if (target.kind !== 'same') { this._error(tab); return; }
      tab.safeURL=url;
      tab.error = false; tab.view.setVisible(this.activeId === tab.id); this._notify();
    };
    wc.on('did-navigate', navigated);
    wc.on('did-navigate-in-page', (event,url,isMainFrame)=>navigated(event,url,isMainFrame,true));
    wc.on('did-fail-load', (_event, code, _description, _url, isMainFrame) => {
      if (code !== -3 && isMainFrame !== false) this._error(tab);
    });
    wc.on('render-process-gone', () => this._error(tab));
    wc.on('destroyed', () => { if (this._alive(tab)) this.close(tab.id); });
  }

  async _load(tab,url){
    tab.loading=true;tab.error=false;this.select(tab.id);
    try{await tab.view.webContents.loadURL(url);if(this._alive(tab)){tab.loading=false;this._notify();}}
    catch(error){if(this._alive(tab)){this._error(tab);throw error;}}
    return this._public(tab);
  }

  async openWorkbench(url=null){
    if(this.disposed)throw new Error('Workspace tabs are closed');
    let tab=this.entries.get('workbench');
    const destination=url||this.origin+'/';
    if(this._destination({kind:'workbench'},destination).kind!=='same')throw new Error('工作台只允许真实服务根页面与同源路由');
    if(tab){this.select(tab.id);return url&&tab.view.webContents.getURL()!==destination?this._load(tab,destination):this._public(tab);}
    const view=this.createView(null);
    tab={id:'workbench',kind:'workbench',view,loading:true,error:false};
    this.entries.set(tab.id,tab);this._wire(tab);this.window.contentView.addChildView(view);view.setBounds(this.bounds);
    if(typeof view.setBorderRadius==='function')view.setBorderRadius(8);
    return this._load(tab,destination);
  }

  async open(project, intent={}) {
    if (this.disposed) throw new Error('Workspace tabs are closed');
    if (!project || typeof project.name !== 'string' || !project.name.trim()) throw new TypeError('A registered project name is required');
    const canonical=this.origin+'/?project='+encodeURIComponent(project.name)+(project.read_only?'&legacy=1':'');
    const target=intent.url||canonical;
    if(this._destination({project},target).kind!=='same')throw new Error('工作区地址不属于已核实的项目');
    const existing = [...this.entries.values()].find(tab => tab.kind!=='workbench'&&tab.project.name === project.name);
    if (existing) { this.updateProject(project); this.select(existing.id);return intent.url&&existing.view.webContents.getURL()!==target?this._load(existing,target):this._public(existing); }
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
      await view.webContents.loadURL(target);
      if (this._alive(tab)) { tab.loading = false; this._notify(); }
    } catch (error) {
      if (this._alive(tab)) { this._error(tab); throw error; }
    }
    void this.refreshActivity();
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
    const tab = [...this.entries.values()].find(item => item.kind!=='workbench'&&item.project.name === project.name);
    if (!tab) return false;
    const changed = tab.project.root !== project.root || tab.project.display_name !== project.display_name;
    tab.project = {...tab.project, ...project}; if (changed) this._notify(); return true;
  }
  closeProject(name) {
    const tab = [...this.entries.values()].find(item => item.kind!=='workbench'&&item.project.name === name);
    return tab ? this.close(tab.id) : false;
  }

  close(id) {
    if (this.disposed) return false;
    if(id==='workbench')return false;
    if (id === 'home') {
      if ([...this.entries.values()].some(tab=>tab.kind!=='workbench')) return false;
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
    if(this.activityTimer!==null)this.clearTimer(this.activityTimer);
    for (const tab of this.entries.values()) {
      this.window.contentView.removeChildView(tab.view);
      if (!tab.view.webContents.isDestroyed()) tab.view.webContents.close();
    }
    this.entries.clear(); this.activeId = 'home';
  }
}

module.exports = { WorkspaceTabs };
