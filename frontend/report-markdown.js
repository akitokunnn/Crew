"use strict";
(() => {
  const article = document.querySelector("article.crew-md-doc");
  const source = article.querySelector("template").content.textContent;
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
  article.innerHTML = markdown.render(source);
})();
