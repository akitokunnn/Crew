"use strict";
(() => {
  const originalFetch = window.fetch.bind(window);
  const unavailable = {remote:false, client:"", local_controls:false, local_desktop:false, authenticated:false,
    host_admin:false, native_handoff:false, vscode_continue:false};
  const flags = ["remote", "local_controls", "local_desktop", "host_admin", "native_handoff", "vscode_continue"];
  let context = unavailable;
  let expired = false;
  function loginUrl() { return "/remote/login?next=" + encodeURIComponent(location.pathname + location.search + location.hash); }
  function message(text, login = false) {
    let node = document.getElementById("access-notice");
    if (!node) { node = document.createElement("section"); node.id = "access-notice"; node.className = "access-notice"; node.setAttribute("role", "alert"); document.body.append(node); }
    node.replaceChildren();
    const label = document.createElement("span"); label.textContent = text; node.append(label);
    const action = document.createElement(login ? "a" : "button");
    action.className = "btn"; action.textContent = login ? "重新连接" : "重新读取";
    if (login) action.href = loginUrl(); else action.onclick = () => location.reload();
    node.append(action);
  }
  function requireLogin() {
    if (expired) return;
    expired = true; context = {...unavailable, remote:true};
    document.documentElement.dataset.accessRemote = "true";
    message("连接已失效。重新连接后继续，草稿仍保存在这个浏览器中。", true);
    window.dispatchEvent(new Event("crew-access-expired"));
  }
  window.fetch = async (input, options) => {
    const response = await originalFetch(input, options);
    const url = new URL(typeof input === "string" || input instanceof URL ? input : input.url, location.href);
    if (response.status === 401 && url.origin === location.origin && url.pathname.startsWith("/api/")) requireLogin();
    return response;
  };
  const ready = (async () => {
    try {
      const response = await window.fetch("/api/access/context");
      const result = await response.json();
      if (!response.ok) throw Error(result.error || "无法确认连接状态");
      if (!result || !["local", "desktop", "browser"].includes(result.client) || flags.some(flag => typeof result[flag] !== "boolean")) throw Error("连接状态不完整，请重新读取。");
      context = result;
      document.documentElement.dataset.accessRemote = String(context.remote);
      return true;
    } catch (error) { if (!expired) message(error.message || "无法连接 Crew"); return false; }
  })();
  window.CrewAccess = {ready, get remote() { return context.remote; }, get client() { return context.client; },
    get local_controls() { return context.local_controls; }, get local_desktop() { return context.local_desktop; },
    get host_admin() { return context.host_admin; }, get native_handoff() { return context.native_handoff; },
    get vscode_continue() { return context.vscode_continue; }, get expired() { return expired; }};
})();
