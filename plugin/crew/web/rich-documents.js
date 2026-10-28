"use strict";
// The report remains readable Markdown while diagram rendering loads locally.
// No CDN, diagram click callbacks, or permissive HTML are required.
(() => {
  let library = null;
  let serial = 0;
  function load() {
    if (library) return library;
    library = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "/vendor/mermaid.min.js";
      script.onload = () => {
        if (!window.mermaid?.render) {
          library = null;
          reject(Error("图示组件没有正确加载"));
          return;
        }
        window.mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          suppressErrorRendering: true,
          maxTextSize: 50000,
          maxEdges: 300,
          flowchart: { htmlLabels: false },
          theme: "neutral",
          fontFamily: '"HarmonyOS Sans SC", system-ui, sans-serif',
        });
        resolve(window.mermaid);
      };
      script.onerror = () => {
        script.remove();
        library = null;
        reject(Error("无法读取本地图示组件，请检查连接后重新打开资料"));
      };
      document.head.append(script);
    });
    return library;
  }
  async function enhance(host) {
    const blocks = [...host.querySelectorAll("pre > code.language-mermaid")];
    for (const code of blocks) {
      if (code.dataset.diagram) continue;
      code.dataset.diagram = "loading";
      const source = code.textContent;
      const pre = code.parentElement;
      try {
        const mermaid = await load();
        if (!pre.isConnected) continue;
        const { svg } = await mermaid.render(
          `crew-diagram-${++serial}`,
          source,
        );
        if (!pre.isConnected) continue;
        const figure = document.createElement("figure");
        figure.className = "crew-document-diagram";
        figure.setAttribute("aria-label", "报告图示");
        figure.innerHTML = svg;
        const details = document.createElement("details");
        const summary = document.createElement("summary");
        summary.textContent = "图示源码";
        details.append(summary, pre.cloneNode(true));
        figure.append(details);
        pre.replaceWith(figure);
      } catch (error) {
        if (!pre.isConnected) continue;
        code.dataset.diagram = "failed";
        const reason = document.createElement("p");
        reason.className = "crew-document-diagram-error";
        reason.setAttribute("role", "status");
        reason.textContent = `图示未能绘制，原文保留：${error.message}`;
        pre.before(reason);
      }
    }
  }
  window.CrewRichDocuments = { enhance };
})();
