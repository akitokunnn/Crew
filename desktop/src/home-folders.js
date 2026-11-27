'use strict';
// Folder picker for a connected LAN host: lists only the host's folders through
// /api/op/workspace.host_folders. This computer's own folders use the system dialog instead.
(() => {
  const bridge = window.crewDesktop;
  function svg(paths) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    node.setAttribute('class', 'icon'); node.setAttribute('viewBox', '0 0 24 24'); node.setAttribute('aria-hidden', 'true');
    for (const value of paths) { const path = document.createElementNS(node.namespaceURI, 'path'); path.setAttribute('d', value); node.append(path); }
    return node;
  }
  const folderIcon = ['M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z'];
  function element(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }

  function pick({ title, confirm, start = '', host = '' }) {
    return new Promise(resolve => {
      let listing = null, selected = -1, showHidden = false, loading = false, error = '', request = 0;
      const dialog = element('dialog', 'host-folders'); dialog.setAttribute('aria-labelledby', 'host-folders-title');
      const header = element('header', 'hf-header'), heading = element('div');
      const titleNode = element('h2', '', title); titleNode.id = 'host-folders-title';
      heading.append(titleNode, element('p', 'hf-host', host ? '主机 ' + host + ' 上的文件夹' : '所连主机上的文件夹'));
      const close = element('button', 'hf-close', '×'); close.type = 'button'; close.setAttribute('aria-label', '关闭文件夹选择');
      header.append(heading, close);
      const toolbar = element('div', 'hf-toolbar');
      const up = element('button', 'button secondary hf-up'); up.type = 'button'; up.append(svg(['M12 19V5', 'm5 12 7-7 7 7']), document.createTextNode('上一级'));
      const homeButton = element('button', 'button secondary hf-home', '主目录'); homeButton.type = 'button';
      const roots = element('select', 'hf-roots'); roots.setAttribute('aria-label', '磁盘');
      const hiddenLabel = element('label', 'hf-hidden'), hiddenBox = element('input'); hiddenBox.type = 'checkbox'; hiddenLabel.append(hiddenBox, document.createTextNode('显示隐藏文件夹'));
      toolbar.append(up, homeButton, roots, hiddenLabel);
      const pathNode = element('p', 'hf-path');
      const list = element('div', 'hf-list'); list.setAttribute('role', 'listbox'); list.tabIndex = 0; list.setAttribute('aria-label', '文件夹（Enter 打开，Backspace 返回上一级）');
      const status = element('p', 'hf-status'); status.setAttribute('role', 'status');
      const footer = element('footer', 'hf-footer'), target = element('p', 'hf-target'), actions = element('div', 'hf-actions');
      const cancel = element('button', 'button secondary', '取消'); cancel.type = 'button';
      const choose = element('button', 'button primary hf-choose', confirm); choose.type = 'button';
      actions.append(cancel, choose); footer.append(target, actions);
      dialog.append(header, toolbar, pathNode, list, status, footer);
      document.body.append(dialog);

      const visible = () => (listing?.entries || []).filter(item => showHidden || !item.hidden);
      const chosen = () => { const entries = visible(); return selected >= 0 && entries[selected] ? entries[selected].path : listing?.path || ''; };
      function finish(value) { request++; dialog.close(); dialog.remove(); resolve(value); }
      function render() {
        const entries = visible();
        pathNode.textContent = listing ? listing.path : '';
        pathNode.title = listing ? listing.path : '';
        up.disabled = loading || !listing?.parent; homeButton.disabled = loading;
        roots.replaceChildren(); roots.hidden = !listing || listing.roots.length < 2; roots.disabled = loading;
        for (const root of listing?.roots || []) { const option = element('option', '', root); option.value = root; option.selected = listing.path.toLowerCase().startsWith(root.toLowerCase()); roots.append(option); }
        list.replaceChildren(); list.removeAttribute('aria-activedescendant'); list.setAttribute('aria-busy', String(loading));
        entries.forEach((item, index) => {
          const option = element('div', 'hf-entry'); option.id = 'hf-entry-' + index; option.dataset.index = String(index);
          option.setAttribute('role', 'option'); option.setAttribute('aria-selected', String(index === selected)); option.title = item.path;
          option.append(svg(folderIcon), element('span', 'hf-name', item.name));
          if (item.git) option.append(element('span', 'hf-badge', 'Git'));
          list.append(option);
        });
        if (selected >= 0 && entries[selected]) { list.setAttribute('aria-activedescendant', 'hf-entry-' + selected); list.querySelector('#hf-entry-' + selected).scrollIntoView({ block:'nearest' }); }
        const hiddenCount = (listing?.entries || []).length - entries.length;
        if (listing && !loading && !entries.length) list.append(element('p', 'hf-empty', hiddenCount ? '这里只有隐藏文件夹。可以勾选“显示隐藏文件夹”，或直接选择当前文件夹。' : '这个文件夹里没有子文件夹，可以直接选择它。'));
        status.classList.toggle('is-error', Boolean(error));
        status.textContent = error || (loading ? '正在读取主机文件夹…' : listing?.truncated ? '文件夹很多，只显示前 2000 个。' : '');
        status.hidden = !status.textContent;
        target.textContent = listing ? '将选择：' + chosen() : '';
        target.title = target.textContent;
        choose.disabled = loading || !listing;
      }
      async function load(path, keepError = false) {
        const current = ++request; loading = true; if (!keepError) error = ''; render();
        try {
          const value = await bridge.hostFolders(path);
          if (current !== request) return;
          listing = value; selected = -1; error = '';
        } catch (caught) {
          if (current !== request) return;
          // Keep the previous listing so another folder or disk can still be chosen.
          error = '无法打开这个文件夹：' + String(caught.message || caught).replace(/^Error invoking remote method '[^']+':\s*(?:Error:\s*)?/, '');
        } finally { if (current === request) { loading = false; render(); list.focus({ preventScroll:true }); } }
      }
      function open(index) { const item = visible()[index]; if (item && !loading) void load(item.path); }
      function select(index) { const count = visible().length; if (!count) return; selected = Math.max(0, Math.min(count - 1, index)); render(); }
      list.addEventListener('click', event => { const option = event.target.closest('.hf-entry'); if (option) select(Number(option.dataset.index)); });
      list.addEventListener('dblclick', event => { const option = event.target.closest('.hf-entry'); if (option) open(Number(option.dataset.index)); });
      list.addEventListener('keydown', event => {
        if (loading) return;
        const keys = { ArrowDown:() => select(selected + 1), ArrowUp:() => select(selected < 0 ? 0 : selected - 1), Home:() => select(0), End:() => select(visible().length - 1),
          Enter:() => open(selected), ArrowRight:() => open(selected), Backspace:() => listing?.parent && void load(listing.parent), ArrowLeft:() => listing?.parent && void load(listing.parent) };
        if (!keys[event.key]) return;
        event.preventDefault(); keys[event.key]();
      });
      up.addEventListener('click', () => { if (listing?.parent) void load(listing.parent); });
      homeButton.addEventListener('click', () => { void load(''); });
      roots.addEventListener('change', () => { void load(roots.value); });
      hiddenBox.addEventListener('change', () => { showHidden = hiddenBox.checked; selected = -1; render(); });
      close.addEventListener('click', () => finish(null));
      cancel.addEventListener('click', () => finish(null));
      choose.addEventListener('click', () => { if (listing && !loading) finish(chosen()); });
      dialog.addEventListener('cancel', event => { event.preventDefault(); finish(null); });
      dialog.showModal(); render(); list.focus();
      void load(start);
    });
  }
  window.CrewHostFolders = Object.freeze({ pick });
})();
