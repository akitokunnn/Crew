"use strict";

const fs = require("node:fs");
const path = require("node:path");

const destination = process.argv[2];
if (!destination) throw new Error("请指定交互预览的绝对输出路径。");
if (!path.isAbsolute(destination)) throw new Error("预览输出路径必须是绝对路径。");
const read = file => fs.readFileSync(path.join(__dirname, file), "utf8");
const css = ["styles.css", "group-views.css", "graph-view.css"].map(read).join("\n").replace("html { color-scheme: light dark; }\nbody { margin: 0; }", "");
const scripts = ["data.js", "model.js", "group-views.js", "graph-view.js", "app.js"].map(read).join("\n");
const logo = `data:image/svg+xml;base64,${Buffer.from(read("crew.svg")).toString("base64")}`;
const fragment = `<style>
${css}
.crew-org-carousel > section { height: 960px; overflow: auto; scrollbar-width: thin; }
@media (max-width: 640px) { .crew-org-carousel > section { height: 900px; } }
</style>
<div class="viz-carousel crew-org-carousel" aria-label="Crew 组织架构方案" data-previous-label="上一个方案" data-next-label="下一个方案">
  <section data-variant="任务分组" aria-label="任务分组方案"><div id="crew-org-lanes" aria-label="任务分组设计"></div></section>
  <section data-variant="成员矩阵" aria-label="成员矩阵方案" hidden><div id="crew-org-matrix" aria-label="成员矩阵设计"></div></section>
  <section data-variant="关系图谱" aria-label="关系图谱方案" hidden><div id="crew-org-graph" aria-label="关系图谱设计"></div></section>
</div>
<script>
${scripts}
for (const variant of ["lanes", "matrix", "graph"]) {
  CrewOrgLab.mount(document.getElementById("crew-org-" + variant), { variant, inline: true, logo: ${JSON.stringify(logo)} });
}
</script>
`;
fs.mkdirSync(path.dirname(destination), { recursive: true });
fs.writeFileSync(destination, fragment);
console.log(`交互预览已生成 ${destination} (${Buffer.byteLength(fragment)} bytes)`);
