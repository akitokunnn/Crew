"use strict";
// Remote admission configuration only. This module does not provision a proxy,
// deploy a tunnel, change the host service or persist any passphrase.
(() => {
  const esc = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (char) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[char],
    );
  const retainedForms = new Map();
  const statusOf = (error) => Number(error?.status) || 0;
  const reason = (error) => error?.message || String(error);
  function create({ host, client, actions, toast = () => {}, onLoggedOut }) {
    if (
      !host ||
      typeof client?.request !== "function" ||
      typeof actions?.register !== "function"
    )
      throw Error(
        "Remote settings requires host, client.request and actions.register",
      );
    let destroyed = false,
      sequence = 0;
    const state = {
      session: null,
      config: null,
      form: null,
      version: null,
      passphrase: "",
      currentPassphrase: "",
      loading: false,
      saving: false,
      loggingOut: false,
      error: "",
      note: "",
      conflict: false,
      uncertain: false,
      reviewed: false,
    };
    host.classList.add("crew-remote-settings");
    const formFrom = (config) => ({
      enabled: config.enabled,
      origins: config.origins.join("\n"),
    });
    const remember = () => {
      if (!state.session?.origin || !state.form) return;
      if (
        state.config &&
        JSON.stringify(state.form) === JSON.stringify(formFrom(state.config)) &&
        !state.conflict &&
        !state.uncertain
      )
        retainedForms.delete(state.session.origin);
      else
        retainedForms.set(state.session.origin, {
          ...state.form,
          version: state.version,
        });
    };
    const clearSecrets = () => {
      state.passphrase = "";
      state.currentPassphrase = "";
      for (const name of ["new-passphrase", "current-passphrase"]) {
        const input = host.querySelector?.(`[data-remote-field="${name}"]`);
        if (input) input.value = "";
      }
    };
    const dirty = () =>
      state.config &&
      state.form &&
      (state.passphrase ||
        JSON.stringify(state.form) !== JSON.stringify(formFrom(state.config)));
    const canSave = () =>
      Boolean(
        state.config &&
          state.session?.authenticated &&
          !state.loading &&
          !state.saving &&
          !state.conflict &&
          !state.uncertain &&
          dirty(),
      );
    function controls() {
      const save = host.querySelector?.('[data-act="remote-settings-save"]');
      if (save) save.disabled = !canSave();
      const note = host.querySelector?.("[data-remote-draft]");
      if (note)
        note.textContent = dirty() ? "尚未保存；口令仅保留在当前页面内存" : "";
    }
    function reviewMarkup() {
      if (!state.conflict && !state.uncertain) return "";
      return `<section class="rs-conflict" role="alert"><strong>${state.uncertain ? "保存结果尚未确认" : "设置版本已变化"}</strong><p>当前表单保留，未自动重发。${state.config ? `草稿依据 v${esc(state.version)}，当前读取到 v${esc(state.config.version)}。` : ""}</p><button type="button" data-act="remote-settings-reload">读取最新配置</button>${state.reviewed ? `<details><summary>查看服务器当前配置</summary><p>${state.config.enabled ? "已启用" : "未启用"}</p><pre>${esc(state.config.origins.join("\n") || "尚无入口")}</pre></details><div class="rs-review-actions"><button type="button" data-act="remote-settings-keep">保留表单，采用 v${esc(state.config.version)}</button><button type="button" data-act="remote-settings-use-server">使用服务器配置</button></div>` : ""}</section>`;
    }
    function paint() {
      if (destroyed) return;
      const local = state.session?.local;
      let body = "";
      if (state.loading && !state.config)
        body =
          '<p class="rs-loading" role="status">正在读取远程设置与登录状态…</p>';
      else if (!state.session?.authenticated)
        body =
          '<p class="rs-empty">当前没有已认证的访问身份。请先登录 Crew，再修改远程设置。</p>';
      else if (state.config && state.form)
        body = `<p class="rs-deployment">${state.config.deployment === "not_verified" ? "公网部署尚未验证" : "部署状态：" + esc(state.config.deployment || "未确认")} · 保存访问规则不会建立公网连接。</p>
        <div class="rs-enable-row"><div><h2>启用远程访问</h2><p>电脑与手机通过已配置的 HTTPS 入口访问同一后台。</p></div><label class="rs-check"><input type="checkbox" data-remote-field="enabled" ${state.form.enabled ? "checked" : ""} ${state.saving ? "disabled" : ""}><span>启用</span></label></div>
        <label class="rs-field">允许的 HTTPS 入口<textarea data-remote-field="origins" rows="3" spellcheck="false" placeholder="https://crew.example.com" ${state.saving ? "readonly" : ""}>${esc(state.form.origins)}</textarea><small>每行一个 origin，最多 10 个。只填写协议、域名和可选端口，不含路径。</small></label>
        <label class="rs-field">${state.config.password_configured ? "更换登录口令" : "设置登录口令"}<input type="password" data-remote-field="new-passphrase" autocomplete="new-password" minlength="12" maxlength="4096" ${state.saving ? "disabled" : ""}><small>${state.config.password_configured ? "留空保留现有口令。" : "启用入口前需要设置口令。"}新口令需 12–4096 个字符。</small></label>
        ${local ? "" : `<label class="rs-field">确认当前登录口令<input type="password" data-remote-field="current-passphrase" autocomplete="current-password" maxlength="4096" ${state.saving ? "disabled" : ""}><small>从远程修改访问规则时，后端要求再次确认当前口令。</small></label>`}
        <details class="rs-guidance"><summary>保存会影响哪些连接？</summary><p>保存任何远程配置都会使现有远程登录失效，需要重新登录。本机授权访问仍可使用。</p><p>本页仅管理 Crew 的允许来源和登录规则。反向代理、公网域名和实际外部连通性需要另行部署与验证。</p></details>${reviewMarkup()}
        <div class="rs-footer"><span data-remote-draft>${dirty() ? "尚未保存；口令仅保留在当前页面内存" : ""}</span><button class="rs-primary" type="button" data-act="remote-settings-save" ${canSave() ? "" : "disabled"}>${state.saving ? "正在保存…" : "保存访问规则"}</button></div>`;
      else body = '<p class="rs-empty">未取得远程配置。请显式重新读取。</p>';
      host.innerHTML = `<div class="rs-shell"><header class="rs-header"><h1>远程访问</h1><p>${state.session ? `${local ? "本机访问" : state.session.authenticated ? "远程已登录" : "远程未登录"}${state.session.origin ? ` · ${esc(state.session.origin)}` : ""}` : "登录状态未确认"}</p>${state.session && !local && state.session.authenticated ? `<button class="rs-link" type="button" data-act="remote-settings-logout" ${state.loggingOut || state.saving ? "disabled" : ""}>${state.loggingOut ? "正在退出…" : "退出远程登录"}</button>` : ""}</header>${state.error ? `<div class="rs-error" role="alert"><p>${esc(state.error)}</p><button type="button" data-act="remote-settings-reload">重新读取</button></div>` : ""}${state.note ? `<p class="rs-note" role="status">${esc(state.note)}</p>` : ""}${body}</div>`;
      // Never serialize secrets into HTML, retained draft maps or browser storage.
      const password = host.querySelector?.(
        '[data-remote-field="new-passphrase"]',
      );
      if (password) password.value = state.passphrase;
      const current = host.querySelector?.(
        '[data-remote-field="current-passphrase"]',
      );
      if (current) current.value = state.currentPassphrase;
    }
    async function load() {
      const seq = ++sequence;
      state.loading = true;
      state.error = "";
      paint();
      const results = await Promise.allSettled([
        client.request("/api/auth/session"),
        client.request("/api/remote"),
      ]);
      if (destroyed || seq !== sequence) return;
      const [session, config] = results;
      if (session.status === "fulfilled") state.session = session.value;
      if (config.status === "fulfilled") {
        const value = config.value;
        if (
          typeof value.version !== "number" ||
          typeof value.enabled !== "boolean" ||
          !Array.isArray(value.origins)
        )
          state.error = "远程配置返回格式无效";
        else {
          const changed =
            state.form && (dirty() || state.conflict || state.uncertain);
          state.config = value;
          const retained =
            !state.form && state.session?.origin
              ? retainedForms.get(state.session.origin)
              : null;
          if (retained) {
            state.form = {
              enabled: retained.enabled,
              origins: retained.origins,
            };
            state.version = retained.version;
            state.conflict = state.version !== value.version;
          } else if (!changed) {
            state.form = formFrom(value);
            state.version = value.version;
          }
          if (changed && state.version !== value.version) state.conflict = true;
          if (state.conflict || state.uncertain) state.reviewed = true;
          remember();
        }
      }
      const errors = results
        .filter((result) => result.status === "rejected")
        .map((result) => reason(result.reason));
      if (errors.length) state.error = errors.join("；");
      state.loading = false;
      paint();
    }
    function validate() {
      const origins = state.form.origins
        .split(/\r?\n/)
        .map((value) => value.trim())
        .filter(Boolean);
      if (origins.length > 10) throw Error("HTTPS 入口最多 10 个");
      for (const origin of origins) {
        let parsed;
        try {
          parsed = new URL(origin);
        } catch {
          throw Error(`入口格式无效：${origin}`);
        }
        if (
          parsed.protocol !== "https:" ||
          parsed.username ||
          parsed.password ||
          !parsed.hostname ||
          parsed.pathname !== "/" ||
          parsed.search ||
          parsed.hash ||
          ["localhost", "127.0.0.1", "[::1]"].includes(parsed.hostname)
        )
          throw Error(
            `入口必须是完整 HTTPS origin，不能含路径、账号或查询：${origin}`,
          );
      }
      if (
        state.passphrase &&
        (state.passphrase.length < 12 || state.passphrase.length > 4096)
      )
        throw Error("新登录口令需 12–4096 个字符");
      if (
        state.form.enabled &&
        (!origins.length ||
          (!state.config.password_configured && !state.passphrase))
      )
        throw Error("启用远程访问需要 HTTPS 入口和登录口令");
      if (!state.session.local && !state.currentPassphrase)
        throw Error("远程修改需要确认当前登录口令");
      return origins;
    }
    async function save() {
      if (destroyed || !canSave()) return;
      let origins;
      try {
        origins = validate();
      } catch (error) {
        state.error = reason(error);
        paint();
        return;
      }
      const seq = sequence;
      const body = {
        version: state.version,
        enabled: state.form.enabled,
        origins,
      };
      if (state.passphrase) body.passphrase = state.passphrase;
      if (!state.session.local)
        body.current_passphrase = state.currentPassphrase;
      state.saving = true;
      state.error = "";
      state.note = "";
      paint();
      try {
        const result = await client.request("/api/remote/configure", body);
        if (destroyed || seq !== sequence) return;
        state.config = result;
        state.form = formFrom(result);
        state.version = result.version;
        state.conflict = false;
        state.uncertain = false;
        state.reviewed = false;
        clearSecrets();
        remember();
        state.note = state.session.local
          ? "访问规则已保存。现有远程登录已失效，需要重新登录；本机可以继续使用。公网部署仍未验证。"
          : "访问规则已保存，当前远程登录已失效，请重新登录。公网部署仍未验证。";
        toast(state.note);
        if (!state.session.local) {
          state.session = { ...state.session, authenticated: false };
          paint();
          onLoggedOut?.({ reason: "configuration_changed" });
        }
      } catch (error) {
        if (destroyed || seq !== sequence) return;
        state.error = reason(error);
        if (statusOf(error) === 409) {
          state.conflict = true;
          state.reviewed = false;
        } else if (!statusOf(error)) {
          state.uncertain = true;
          state.reviewed = false;
          state.error = `保存结果未确认：${reason(error)}。先重新读取核对，不会自动重发。`;
        }
      } finally {
        state.saving = false;
        if (!destroyed && seq === sequence) paint();
      }
    }
    async function logout() {
      if (destroyed || state.loggingOut || state.saving || state.session?.local)
        return;
      const seq = sequence;
      state.loggingOut = true;
      state.error = "";
      paint();
      try {
        const result = await client.request("/api/auth/logout", {});
        if (destroyed || seq !== sequence) return;
        if (result.authenticated !== false)
          throw Error("退出响应未确认登录已结束");
        state.session = { ...state.session, authenticated: false };
        clearSecrets();
        state.note = "远程登录已退出。";
        toast(state.note);
        paint();
        onLoggedOut?.({ reason: "logout" });
      } catch (error) {
        if (!destroyed && seq === sequence) state.error = reason(error);
      } finally {
        state.loggingOut = false;
        if (!destroyed && seq === sequence) paint();
      }
    }
    function useLatest(keep) {
      if (!state.config || !state.reviewed) return;
      if (!keep) {
        state.form = formFrom(state.config);
        clearSecrets();
      }
      state.version = state.config.version;
      state.conflict = false;
      state.uncertain = false;
      state.error = "";
      state.reviewed = false;
      remember();
      paint();
    }
    const unregister = actions.register("remote-settings", {
      "remote-settings-reload": () =>
        state.saving || state.loggingOut ? undefined : load(),
      "remote-settings-save": save,
      "remote-settings-logout": logout,
      "remote-settings-keep": () => useLatest(true),
      "remote-settings-use-server": () => useLatest(false),
    });
    function input(event) {
      const element = event.target,
        field = element.dataset?.remoteField;
      if (!field || !state.form || state.saving) return;
      if (field === "enabled") state.form.enabled = Boolean(element.checked);
      else if (field === "origins") state.form.origins = element.value;
      else if (field === "new-passphrase") state.passphrase = element.value;
      else if (field === "current-passphrase")
        state.currentPassphrase = element.value;
      remember();
      controls();
    }
    host.addEventListener("input", input);
    host.addEventListener("change", input);
    return {
      open() {
        if (destroyed)
          return Promise.reject(Error("Remote settings was destroyed"));
        host.hidden = false;
        return load();
      },
      destroy() {
        if (destroyed) return;
        remember();
        clearSecrets();
        destroyed = true;
        ++sequence;
        unregister();
        host.removeEventListener("input", input);
        host.removeEventListener("change", input);
        host.classList.remove("crew-remote-settings");
      },
    };
  }
  const exported = { create };
  if (typeof module !== "undefined" && module.exports)
    module.exports = exported;
  else window.CrewRemoteSettings = exported;
})();
