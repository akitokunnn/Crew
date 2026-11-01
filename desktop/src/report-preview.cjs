'use strict';
const {randomUUID} = require('node:crypto');

// A report gets its own renderer, with no desktop preload, persistent profile,
// network access or Node. This is a report capture surface, not a browser API.
class ReportPreviewRenderer {
  constructor({BrowserWindow, timeoutMs = 20000}) {
    this.BrowserWindow = BrowserWindow;
    this.timeoutMs = timeoutMs;
    this.windows = new Set();
    this.closed = false;
  }

  async render(job) {
    if (this.closed) throw Error('报告预览桌面宿主已关闭');
    if (!job || typeof job.html !== 'string' || !job.html.trim() ||
        ![[1280,900],[390,844]].some(([w,h]) => w === job.width && h === job.height)) {
      throw Error('报告预览内容或视口无效');
    }
    const win = new this.BrowserWindow({show:false, width:job.width, height:job.height,
      useContentSize:true, skipTaskbar:true,
      webPreferences:{contextIsolation:true, nodeIntegration:false, sandbox:true, webSecurity:true,
        backgroundThrottling:false, offscreen:true, partition:'report-preview-' + randomUUID()}});
    this.windows.add(win);
    const wc = win.webContents, session = wc.session, errors = [];
    const record = message => {
      const text = String(message).slice(0, 2000);
      if (errors.length < 30 && !errors.includes(text)) errors.push(text);
    };
    session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    session.setPermissionCheckHandler(() => false);
    session.on('will-download', event => event.preventDefault());
    session.webRequest.onBeforeRequest((details, callback) => {
      const allowed = details.url.startsWith('data:') || details.url.startsWith('about:') || details.url.startsWith('blob:');
      if (!allowed) record('报告预览已阻止外部资源：' + details.url);
      callback({cancel:!allowed});
    });
    wc.setWindowOpenHandler(() => { record('报告预览已阻止打开新窗口'); return {action:'deny'}; });
    const preventNavigation = event => { event.preventDefault(); record('报告预览已阻止页面导航'); };
    wc.on('will-navigate', preventNavigation);
    wc.on('will-frame-navigate', preventNavigation);
    wc.on('console-message', event => {
      if (event.level === 'error' || event.level === 3) record(event.message);
    });
    let timer, closed;
    const failure = new Promise((_resolve, reject) => {
      timer = setTimeout(() => reject(Error('报告预览渲染超时')), this.timeoutMs);
      closed = () => reject(Error('报告预览窗口已关闭'));
      win.once('closed', closed);
      wc.once('render-process-gone', (_event, details) => reject(Error('报告预览渲染进程退出：' + details.reason)));
    });
    const render = async () => {
      await wc.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(job.html));
      // Loading includes the sandboxed report iframe. Wait in each frame's own
      // context so inline fonts/charts settle without weakening origin isolation.
      const ready = 'document.fonts.ready.then(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))).then(() => true)';
      for (const frame of [wc.mainFrame, ...wc.mainFrame.frames]) await frame.executeJavaScript(ready);
      const picture = await wc.capturePage(undefined, {stayHidden:true});
      if (picture.isEmpty()) throw Error('报告预览未取得有效画面');
      const png = picture.resize({width:job.width, height:job.height}).toPNG();
      return {png_base64:png.toString('base64'), width:job.width, height:job.height, console_errors:errors};
    };
    try { return await Promise.race([failure, render()]); }
    finally {
      clearTimeout(timer);
      win.removeListener('closed', closed);
      this.windows.delete(win);
      if (!win.isDestroyed()) win.destroy();
      session.webRequest.onBeforeRequest(null);
    }
  }

  close() {
    this.closed = true;
    for (const win of this.windows) if (!win.isDestroyed()) win.destroy();
    this.windows.clear();
  }
}

// The existing backend owns requests, scope and artifacts. The desktop only
// consumes its authenticated queue, keeping a lease while a render is running.
class ReportPreviewClient {
  constructor({base, request, renderer, retryMs = 5000, onError = error => console.error('Crew 报告预览：', error.message)}) {
    this.base = base;
    this.request = request;
    this.renderer = renderer;
    this.onError = onError;
    this.retryMs = retryMs;
    this.lastError = null;
    this.abort = new AbortController();
    this.worker = null;
    this.pending = new Set();
    this.closed = false;
    this.closing = null;
  }

  call(action, body, timeout = 8000, signal = this.abort.signal) {
    return this.request(this.base, '/api/report-preview/worker/' + action, body, timeout, signal);
  }

  async start() {
    if (this.closed) throw Error('报告预览桌面宿主已关闭');
    if (this.worker) return;
    try { await this.register(); }
    catch (error) { if (!this.closed) this.note(error); }
    if (this.closed) {
      if (this.worker) await this.call('close', this.worker, 3000, AbortSignal.timeout(3000));
      return;
    }
    this.loop = this.poll().catch(error => { if (!this.closed) this.note(error); });
  }

  async register() {
    const registration = await this.call('register', {});
    if (typeof registration?.worker_id !== 'string' || typeof registration?.token !== 'string') {
      throw Error('报告预览桌面宿主登记响应无效');
    }
    this.worker = {worker_id:registration.worker_id, token:registration.token};
  }

  note(error) {
    if (this.lastError === error.message) return;
    this.lastError = error.message;
    this.onError(error);
  }

  async retry() {
    if (this.closed) return;
    await new Promise(resolve => {
      const finish = () => { clearTimeout(timer); this.abort.signal.removeEventListener('abort', finish); resolve(); };
      const timer = setTimeout(finish, this.retryMs);
      this.abort.signal.addEventListener('abort', finish, {once:true});
    });
  }

  async poll() {
    while (!this.closed) {
      try {
        if (!this.worker) await this.register();
        const worker = this.worker;
        const {job} = await this.call('next', worker);
        if (this.closed) return;
        this.lastError = null;
        if (job) {
          const task = this.capture(job, worker);
          this.pending.add(task);
          task.catch(error => { if (!this.closed) this.note(error); }).finally(() => this.pending.delete(task));
        }
      } catch (error) {
        if (this.closed) return;
        this.note(error);
        if (error.status === 409) this.worker = null;
        // Retry transport/lease setup only. A capture whose receipt was lost
        // stays with its original worker and is never automatically repeated.
        await this.retry();
      }
    }
  }

  async capture(job, worker) {
    let result;
    try { result = await this.renderer.render(job); }
    catch (error) { result = {error:error.message || String(error), console_errors:[]}; }
    if (!this.closed) await this.call('result', {...worker, job_id:job.id, ...result}, 10000);
  }

  close() {
    if (this.closing) return this.closing;
    this.closed = true;
    this.abort.abort();
    this.renderer.close();
    this.closing = (async () => {
      await this.loop;
      if (this.worker) {
        try { await this.call('close', this.worker, 3000, AbortSignal.timeout(3000)); }
        catch (error) { this.onError(error); }
      }
      await Promise.allSettled([...this.pending]);
    })();
    return this.closing;
  }
}

module.exports = {ReportPreviewRenderer, ReportPreviewClient};
