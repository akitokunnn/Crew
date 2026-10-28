"use strict";
// One explicitly selected transport for JSON, documents and live events. Losing
// a real connection never selects synthetic data or retries a mutation.
((root, factory) => {
  const exported = factory();
  if (typeof module !== "undefined" && module.exports)
    module.exports = exported;
  else {
    root.CrewClient = exported;
    const parameters = new URLSearchParams(root.location.search);
    const mode = parameters.get("data") === "mock" ? "mock" : "real";
    const transport =
      mode === "mock"
        ? root.CrewMock.create({
            storage: root.localStorage,
            scenario: parameters.get("scenario") || "",
            environment: root,
          })
        : null;
    exported.current = exported.create({ environment: root, mode, transport });
  }
})(typeof window === "undefined" ? globalThis : window, () => {
  function failure(message, status, details) {
    const error = new Error(message);
    error.status = status;
    error.details = details;
    return error;
  }

  function create({
    environment = globalThis,
    mode = "real",
    transport = null,
  } = {}) {
    if (!["real", "mock"].includes(mode))
      throw new TypeError("Unknown Crew transport mode");
    if (mode === "mock" && !transport)
      throw new TypeError("Synthetic transport must be explicitly supplied");
    const origin = environment.location?.origin || "http://127.0.0.1";
    function sameOrigin(path) {
      const url = new URL(path, origin);
      if (
        url.origin !== origin ||
        !["http:", "https:"].includes(url.protocol)
      ) {
        throw failure("Crew 数据请求必须使用当前服务的同源地址。", 400, {
          path,
        });
      }
      return url.pathname + url.search;
    }
    async function fetchRaw(path, options = {}) {
      const headers = new (environment.Headers || globalThis.Headers)(
        options.headers || {},
      );
      if (options.body !== undefined && options.body !== null)
        headers.set("X-Crew", "1");
      const safe = sameOrigin(path),
        init = { ...options, headers, credentials: "same-origin" };
      return mode === "mock"
        ? transport.fetch(safe, init)
        : environment.fetch(safe, init);
    }
    async function fetchResponse(path, body, options = {}) {
      const headers = new (environment.Headers || globalThis.Headers)(
        options.headers || {},
      );
      if (body !== undefined) headers.set("Content-Type", "application/json");
      const response = await fetchRaw(path, {
        ...options,
        headers,
        method: body === undefined ? options.method || "GET" : "POST",
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      if (!response.ok) {
        const raw = await response.text();
        let details;
        try {
          details = JSON.parse(raw);
        } catch (_) {
          details = { error: raw || response.statusText };
        }
        throw failure(
          details.error || `请求失败（${response.status}）`,
          response.status,
          details,
        );
      }
      return response;
    }
    const client = {
      mode,
      synthetic: mode === "mock",
      fetch: fetchRaw,
      request(path, body, options) {
        return fetchResponse(path, body, options).then((response) =>
          response.status === 204 ? null : response.json(),
        );
      },
      text(path, options) {
        return fetchResponse(path, undefined, options).then((response) =>
          response.text(),
        );
      },
      async blob(path, options) {
        return (await fetchResponse(path, undefined, options)).blob();
      },
      resourceURL(path) {
        const safe = sameOrigin(path);
        return mode === "mock" ? transport.resourceURL(safe) : safe;
      },
      eventSource(path) {
        const safe = sameOrigin(path);
        return mode === "mock"
          ? transport.eventSource(safe)
          : new environment.EventSource(safe, { withCredentials: true });
      },
      // Fetch-stream callers such as the existing transcript parser use this
      // rather than a direct fetch, keeping mock streams completely offline.
      async stream(path, options = {}) {
        return fetchResponse(path, undefined, options);
      },
    };
    const bridge = environment.crewDesktop;
    client.desktop = {
      available: mode === "real" && Boolean(bridge),
      can(name) {
        return mode === "real" && typeof bridge?.[name] === "function";
      },
      async invoke(name, ...args) {
        if (mode === "mock")
          return {
            synthetic: true,
            opened: false,
            available: false,
            reason: "样例模式仅演示界面，不操作电脑。",
          };
        if (typeof bridge?.[name] !== "function")
          throw failure("此操作需要 Crew 桌面端。", 501, { capability: name });
        return bridge[name](...args);
      },
      async openFolder(path, context = {}) {
        if (mode === "mock")
          return {
            synthetic: true,
            opened: false,
            path,
            reason: "样例文件夹，不操作电脑。",
          };
        if (typeof bridge?.openFolder === "function")
          return bridge.openFolder(path);
        // Existing browser host exposes /api/files/reveal. Do not pretend a
        // browser without that host has opened a local directory.
        if (context.document_id || context.session_id) {
          const access = await client.request("/api/auth/session");
          if (!access.local)
            return {
              opened: false,
              path,
              reason: "远程网页提供路径；请在执行电脑打开文件夹。",
            };
          const result = await client.request(
            `/api/files/reveal${context.project ? `?project=${encodeURIComponent(context.project)}` : ""}`,
            {
              path,
              ...context,
            },
          );
          return { opened: true, ...result };
        }
        return {
          opened: false,
          path,
          available: false,
          reason: "请在 Crew 桌面端打开此路径，或复制路径到本机文件管理器。",
        };
      },
      async openExternal(url) {
        const target = new URL(url);
        if (!["http:", "https:"].includes(target.protocol))
          throw failure("只允许打开 HTTP 或 HTTPS 网页。", 400, { url });
        if (mode === "mock")
          return {
            synthetic: true,
            opened: false,
            url: target.href,
            reason: "样例网页，不访问外部服务。",
          };
        if (typeof bridge?.openExternal === "function")
          return bridge.openExternal(target.href);
        const opened = environment.open?.(
          target.href,
          "_blank",
          "noopener,noreferrer",
        );
        return {
          opened: Boolean(opened),
          url: target.href,
          reason: opened ? null : "请使用此链接打开网页。",
        };
      },
    };
    return client;
  }
  return { create, failure };
});
