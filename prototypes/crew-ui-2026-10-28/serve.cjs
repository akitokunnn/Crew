/* Serves this standalone prototype only. Never imports Crew or opens a database. */
"use strict";
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const root = __dirname;
const types = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".md": "text/plain; charset=utf-8",
};
const server = http.createServer((request, response) => {
  if (request.method !== "GET" && request.method !== "HEAD") {
    response.writeHead(405);
    response.end();
    return;
  }
  let requested;
  try {
    requested = decodeURIComponent(
      new URL(request.url, "http://localhost").pathname,
    );
  } catch {
    response.writeHead(400);
    response.end();
    return;
  }
  const target = path.resolve(
    root,
    "." + (requested === "/" ? "/index.html" : requested),
  );
  if (!target.startsWith(root + path.sep) || !types[path.extname(target)]) {
    response.writeHead(404);
    response.end("Not found");
    return;
  }
  fs.readFile(target, (error, bytes) => {
    if (error) {
      response.writeHead(404);
      response.end("Not found");
      return;
    }
    response.writeHead(200, {
      "Content-Type": types[path.extname(target)],
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    });
    response.end(request.method === "HEAD" ? undefined : bytes);
  });
});
server.on("error", (error) => {
  process.stderr.write(error.message + "\n");
  process.exitCode = 1;
});
server.listen(
  Number(process.env.CREW_PROTOTYPE_PORT || 4318),
  "127.0.0.1",
  () =>
    process.stdout.write(
      "Crew sample prototype: http://127.0.0.1:" + server.address().port + "\n",
    ),
);
