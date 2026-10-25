"use strict";
const CrewGitSettings = (() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[char]);
  const fields = snapshot => ({admin_session_id:snapshot.config?.admin_session_id || "", base:snapshot.config?.base || snapshot.choices.base || "",
    stable:snapshot.config ? snapshot.config.stable || "" : snapshot.choices.stable || "", branch_rules:snapshot.config?.branch_rules || ""});
  function create(options) {
    const dialog = document.createElement("dialog");
    dialog.className = "git-settings-dialog"; dialog.setAttribute("aria-labelledby", "git-settings-title"); document.body.append(dialog);
    let project = null, snapshot = null, draft = null, busy = false, loading = false, generation = 0, confirmation = false, returnTo = null;
    const dirty = () => snapshot !== null && JSON.stringify(draft) !== JSON.stringify(fields(snapshot));
    const selected = () => snapshot?.leaders.find(item => item.session_id === draft?.admin_session_id);
    function note(message, failed = false) {
      const node = dialog.querySelector("[data-git-settings-note]");
      node.textContent = message; node.classList.toggle("is-error", failed); node.setAttribute("role", failed ? "alert" : "status");
    }
    function controls() {
      for (const node of dialog.querySelectorAll("input,select,textarea,button")) node.disabled = busy || (loading && node.dataset.gitSetting !== "close");
      const save = dialog.querySelector('[data-git-setting="save"]');
      if (save) save.disabled = busy || loading || !snapshot?.available || !draft?.admin_session_id || !draft?.base || (!dirty() && snapshot.config !== null);
    }
    function adminInfo() {
      const leader = selected(), node = dialog.querySelector("[data-git-admin-info]");
      if (!node) return;
      node.innerHTML = leader ? `<span>${esc(leader.runtime === "codex" ? "Codex" : "Claude Code")} · ${esc(leader.model || "原生默认模型")}${leader.effort ? ` · ${esc(leader.effort)}` : ""}</span><code title="${esc(leader.cwd)}">${esc(leader.cwd)}</code><button type="button" class="gits-link" data-git-setting="leader">打开负责人对话与大脑设置 ↗</button>` : '<span>选择已有 leader 即可绑定，不会新开会话。</span>';
    }
    function render(message = "", failed = false) {
      const name = snapshot?.project.display_name || snapshot?.project.name || project || "";
      let body = `<div class="gits-empty">${loading ? "正在读取项目 Git 设置…" : '<button type="button" class="btn" data-git-setting="reload">重新读取</button>'}</div>`;
      if (snapshot) {
        const option = (value, label, current) => `<option value="${esc(value)}" ${value === current ? "selected" : ""}>${esc(label)}</option>`;
        const branchSelect = (id, value, empty) => `<select name="${id}" id="gits-${id}">${empty ? option("", "不设置稳定分支", value) : option("", "选择主干分支", value)}${value && !snapshot.choices.branches.includes(value) ? option(value, `${value} · 本地已不存在`, value) : ""}${snapshot.choices.branches.map(name => option(name, name, value)).join("")}</select>`;
        body = `${snapshot.available ? "" : `<div class="gits-warning" role="alert"><strong>仓库读取未完成</strong><pre>${esc(snapshot.reason)}</pre><button type="button" class="btn" data-git-setting="reload">重新检查仓库</button></div>`}<form id="git-settings-form">
          <section class="gits-section"><div class="gits-section-head"><h3>负责人</h3><span class="gits-status">${snapshot.config ? "已启用 Git 管理" : "尚未绑定管理员"}</span></div><p>只有绑定的管理员负责推送、PR 与合并。保存不会叫醒或中断任何 AI，下次正常对话时读取最新配置。</p>
          <label for="gits-admin">Git 管理员</label><select id="gits-admin" name="admin_session_id">${option("", "选择本项目已有 leader", draft.admin_session_id)}${draft.admin_session_id && !selected() ? option(draft.admin_session_id, "原管理员已结束，请重新选择", draft.admin_session_id) : ""}${snapshot.leaders.map(item => option(item.session_id, item.name, draft.admin_session_id)).join("")}</select><div class="gits-admin-info" data-git-admin-info></div>
          ${snapshot.config ? "" : '<button type="button" class="gits-link" data-git-setting="create">没有合适的负责人？新建专用 Git 管理员 ↗</button>'}</section>
          <section class="gits-section"><h3>分支与合并</h3><p>主干接收日常改动，稳定分支用于发布。分支选择与合并规则按项目独立保存。</p><div class="gits-branches"><div><label for="gits-base">主干分支</label>${branchSelect("base", draft.base, false)}</div><div><label for="gits-stable">稳定分支 <small>可选</small></label>${branchSelect("stable", draft.stable, true)}</div></div>
          <label for="gits-policy">合并规则</label><textarea id="gits-policy" name="branch_rules" rows="6" placeholder="例如：功能分支先提交 PR；测试和 CI 通过后合入 develop；发版合入 master 前请我确认。">${esc(draft.branch_rules)}</textarea><p class="gits-hint">写清 PR、验收、提交格式与清理规则。项目 AGENTS.md 和远端分支保护继续生效；管理员也能用 crew git policy 维护这里。</p></section>
          <details class="gits-section gits-repository"><summary>仓库与权限来源 <span>查看地址与配置范围</span></summary><label>工作目录</label><code>${esc(snapshot.project.root)}</code><label>远程仓库 <small>来自 git remote -v</small></label>${snapshot.remotes.length ? snapshot.remotes.map(item => `<div class="gits-remote"><b>${esc(item.name)} <small>${esc(item.direction)}</small></b><code>${esc(item.url)}</code></div>`).join("") : '<p>未配置远程仓库。</p>'}<p class="gits-hint">远程地址与凭据由 Git / GitHub CLI 管理；GitHub 的审核、CI 和分支保护在仓库 Settings 中设置，Crew 会遵守这些规则。</p><button type="button" class="gits-link" data-git-setting="defaults">新建管理员的默认载体、模型与思考强度 ↗</button></details>
          ${snapshot.config ? `<section class="gits-disable"><div><strong>停用 Git 管理</strong><p>解除管理绑定，保留负责人、组员、请求记录和所有分支。</p></div><button type="button" class="btn danger" data-git-setting="disable">停用…</button></section>` : ""}</form>`;
      }
      dialog.innerHTML = `<header class="gits-header"><div><span>${esc(name)} · 仅此项目</span><h2 id="git-settings-title">Git 设置</h2></div><div><button type="button" class="gits-link" data-git-setting="reload">读取最新</button><button type="button" class="icon-btn" data-git-setting="close" aria-label="关闭 Git 设置">×</button></div></header><div class="gits-body">${body}</div><footer class="gits-footer"><p data-git-settings-note role="${failed ? "alert" : "status"}" class="${failed ? "is-error" : ""}">${esc(message)}</p><div>${confirmation === "reload" ? '<button type="button" class="btn" data-git-setting="cancel-disable">保留草稿</button><button type="button" class="btn" data-git-setting="discard">放弃草稿，重新读取</button>' : confirmation ? '<span>确认解除绑定？</span><button type="button" class="btn" data-git-setting="cancel-disable">返回</button><button type="button" class="btn danger" data-git-setting="confirm-disable">确认停用</button>' : '<button type="button" class="btn" data-git-setting="close">关闭</button><button type="submit" form="git-settings-form" class="btn primary" data-git-setting="save">保存设置</button>'}</div></footer>`;
      adminInfo(); controls();
    }
    async function read() {
      if (busy) return;
      const request = ++generation; loading = true; render();
      try {
        const next = await options.read(project);
        if (generation !== request) return;
        snapshot = next; draft = fields(next); confirmation = false; loading = false; render();
      } catch (error) { if (generation === request) { loading = false; render(error.message, true); } }
    }
    function close() {
      if (busy) return;
      generation++; loading = false; dialog.close(); returnTo?.isConnected && returnTo.focus({preventScroll:true});
    }
    async function save(disable = false) {
      if (!snapshot || busy) return;
      busy = true; controls(); note("正在保存…");
      try {
        const next = await options.save({project, revision:snapshot.revision, ...(disable ? {enabled:false} : draft)});
        snapshot = {...snapshot, config:next.config, revision:next.revision}; draft = fields(snapshot); busy = false; confirmation = false;
        render(disable ? "已解除 Git 管理绑定，原负责人和会话仍然保留。" : "已保存，未唤醒负责人。下次正常对话读取最新设置。");
        options.onSaved?.(next);
      } catch (error) { busy = false; controls(); note(error.message, true); }
    }
    dialog.addEventListener("submit", event => { if (event.target.id === "git-settings-form") { event.preventDefault(); void save(); } });
    dialog.addEventListener("input", event => {
      if (busy || !draft || !Object.hasOwn(draft, event.target.name)) return;
      draft[event.target.name] = event.target.value; controls(); note(dirty() ? "有未保存修改 · 关闭窗口会保留草稿。" : ""); adminInfo();
    });
    dialog.addEventListener("click", event => {
      const action = event.target.closest("[data-git-setting]")?.dataset.gitSetting;
      if (busy || !action) return;
      if (action === "close") close();
      else if (action === "reload") { if (dirty()) { confirmation = "reload"; render("重新读取会覆盖未保存草稿。"); } else void read(); }
      else if (action === "discard") void read();
      else if (action === "disable" || action === "cancel-disable") { confirmation = action === "disable"; render(); }
      else if (action === "confirm-disable") void save(true);
      else if (action === "leader" && selected()) { const sid = draft.admin_session_id; close(); options.onLeader?.(sid); }
      else if (action === "defaults") { close(); options.onDefaults?.(); }
      else if (action === "create") { close(); options.onCreate?.(); }
    });
    dialog.addEventListener("cancel", event => { event.preventDefault(); close(); });
    return {open(name) { returnTo = document.activeElement; if (project !== name) { snapshot = null; draft = null; project = name; }
      dialog.showModal(); if (snapshot && dirty()) render("已恢复未保存草稿。"); else void read(); }, close};
  }
  return {create};
})();
