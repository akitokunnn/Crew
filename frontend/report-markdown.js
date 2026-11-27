"use strict";
(() => {
  const markdown = window.markdownit({html:false});
  markdown.renderer.rules.table_open = () => '<div class="table-wrap"><table>';
  markdown.renderer.rules.table_close = () => '</table></div>\n';
  markdown.renderer.rules.link_open = (tokens, index, options, _env, renderer) => {
    const link = tokens[index];
    if (!link.attrGet("href").startsWith("#")) {
      link.attrSet("target", "_blank");
      link.attrSet("rel", "noopener noreferrer");
    }
    return renderer.renderToken(tokens, index, options);
  };
  function render(source, annotate) {
    const env = {};
    const tokens = markdown.parse(source, env);
    annotate?.(tokens);
    return markdown.renderer.render(tokens, markdown.options, env);
  }
  window.CrewReportMarkdown = {render};
  const article = document.querySelector("article.crew-md-doc");
  if (article) article.innerHTML = render(article.querySelector("template").content.textContent);
})();
