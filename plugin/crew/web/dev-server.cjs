"use strict";
// Frontend-only loopback server. An API upstream exists only with --backend.
const http = require("node:http");
const https = require("node:https");
const fs = require("node:fs");
const path = require("node:path");

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".png": "image/png",
  ".woff2": "font/woff2",
  ".json": "application/json; charset=utf-8",
};
function createServer({ root = __dirname, backend = null } = {}) {
  const directory = fs.realpathSync(root);
  const upstream = backend ? new URL(backend) : null;
  if (
    upstream &&
    (!["http:", "https:"].includes(upstream.protocol) ||
      upstream.username ||
      upstream.password ||
      upstream.search ||
      upstream.hash ||
      !["", "/"].includes(upstream.pathname))
  ) {
    throw new Error("--backend 必须是无账号、路径和查询参数的 HTTP(S) origin");
  }
  if (
    upstream &&
    !["127.0.0.1", "localhost", "[::1]"].includes(upstream.hostname)
  )
    throw new Error("开发代理只允许显式的 loopback 后端；不能代理公网远程服务");
  return http.createServer(async (request, response) => {
    const fail = (status, message) => {
      response.writeHead(status, {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
      });
      response.end(JSON.stringify({ error: message }));
    };
    let url;
    try {
      url = new URL(request.url, "http://127.0.0.1");
    } catch (_) {
      fail(400, "无效 URL");
      return;
    }
    if (url.pathname.startsWith("/api/")) {
      if (!upstream) {
        fail(
          503,
          "未配置后端代理。样例体验请使用 ?data=mock；真实联调请显式传 --backend。",
        );
        return;
      }
      const localHosts = [
        `127.0.0.1:${request.socket.localPort}`,
        `localhost:${request.socket.localPort}`,
      ];
      if (
        !localHosts.includes(request.headers.host) ||
        (request.headers.origin &&
          request.headers.origin !== "http://" + request.headers.host)
      ) {
        fail(403, "开发代理仅接受本机同源调用");
        return;
      }
      const headers = { ...request.headers };
      // This is an explicit local-only development proxy, not the remote
      // reverse proxy. Match the chosen local service's origin after checking
      // the caller. It cannot grant remote/forwarded requests localhost trust.
      headers.host = upstream.host;
      if (headers.origin) headers.origin = upstream.origin;
      for (const name of Object.keys(headers))
        if (name === "forwarded" || name.startsWith("x-forwarded-"))
          delete headers[name];
      delete headers["proxy-authorization"];
      delete headers["proxy-connection"];
      const proxy = (upstream.protocol === "https:" ? https : http).request(
        new URL(request.url, upstream),
        {
          method: request.method,
          headers,
        },
        (remote) => {
          response.writeHead(remote.statusCode, {
            ...remote.headers,
            "Cache-Control": "no-store",
          });
          remote.pipe(response);
        },
      );
      proxy.on("error", (error) => {
        if (!response.headersSent) fail(502, `后端连接失败：${error.message}`);
        else response.destroy(error);
      });
      request.on("aborted", () => proxy.destroy());
      response.on("close", () => {
        if (!response.writableEnded) proxy.destroy();
      });
      request.pipe(proxy);
      return;
    }
    if (!["GET", "HEAD"].includes(request.method)) {
      fail(405, "静态资源仅支持 GET 和 HEAD");
      return;
    }
    let decoded;
    try {
      decoded = decodeURIComponent(url.pathname);
    } catch (_) {
      fail(400, "URL 编码无效");
      return;
    }
    if (
      decoded.includes("\0") ||
      decoded.includes("\\") ||
      decoded.split("/").some((part) => part === ".." || part.startsWith("."))
    ) {
      fail(403, "资源路径不允许");
      return;
    }
    const candidate = path.resolve(
      directory,
      "." +
        (decoded === "/"
          ? url.searchParams.get("project")?.startsWith("storage:")
            ? "/storage-history.html"
            : "/index.html"
          : decoded),
    );
    if (!candidate.startsWith(directory + path.sep)) {
      fail(403, "资源路径越界");
      return;
    }
    try {
      const resolved = await fs.promises.realpath(candidate);
      if (!resolved.startsWith(directory + path.sep)) {
        fail(403, "资源链接越界");
        return;
      }
      const stat = await fs.promises.stat(resolved);
      const type = TYPES[path.extname(resolved).toLowerCase()];
      // Do not serve the development helper, tests, Python source or secrets.
      if (!stat.isFile() || !type) {
        fail(404, "资源不存在");
        return;
      }
      response.writeHead(200, {
        "Content-Type": type,
        "Content-Length": stat.size,
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
      });
      if (request.method === "HEAD") response.end();
      else fs.createReadStream(resolved).pipe(response);
    } catch (error) {
      fail(
        error.code === "ENOENT" ? 404 : 500,
        error.code === "ENOENT" ? "资源不存在" : "资源读取失败",
      );
    }
  });
}

if (require.main === module) {
  const args = process.argv.slice(2);
  const value = (name) => {
    const index = args.indexOf(name);
    return index < 0 ? null : args[index + 1];
  };
  const port = Number(value("--port") || 4320);
  if (!Number.isInteger(port) || port < 1 || port > 65535)
    throw new Error("端口无效");
  const backend = value("--backend");
  const server = createServer({ backend });
  server.on("error", (error) => {
    process.stderr.write(error.message + "\n");
    process.exitCode = 1;
  });
  server.listen(port, "127.0.0.1", () => {
    process.stdout.write(
      `Crew 前端样例：http://127.0.0.1:${port}/?data=mock\n`,
    );
    process.stdout.write(
      backend
        ? `本机 API 代理：${backend}；仅接受本机同源调用，Host/Origin 匹配显式选择的 loopback 服务。真实入口去掉 data=mock。\n`
        : "未连接后端；所有样例状态只保存在浏览器的 Crew synthetic 命名空间。\n",
    );
  });
  for (const signal of ["SIGINT", "SIGTERM"])
    process.on(signal, () => server.close());
}
module.exports = { createServer };
