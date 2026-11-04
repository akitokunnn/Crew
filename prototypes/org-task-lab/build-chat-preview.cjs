"use strict";

const fs = require("node:fs");
const path = require("node:path");
const destination = process.argv[2];
if (!destination || !path.isAbsolute(destination)) throw new Error("请指定交互预览的绝对输出路径。");
const read = file => fs.readFileSync(path.join(__dirname, file), "utf8").replace(/\r\n/g, "\n");
const styles = ["styles.css", "graph-view.css", "chat-panel.css", "chat-demo.css"].map(read).join("\n").replace(/^html[^\n]*\nbody[^\n]*\n/, "");
const scripts = ["data.js", "model.js", "graph-view.js", "app.js", "chat-panel-view.js", "chat-demo.js"].map(read).join("\n");
const logo = `data:image/svg+xml;base64,${Buffer.from(read("crew.svg")).toString("base64")}`;
const html = `<style>
${styles}
</style>
<div id="crew-chat-org-preview" aria-label="Crew 对话与右侧组织架构"></div>
<script>
${scripts}
CrewChatOrgDemo.mount(document.getElementById("crew-chat-org-preview"), { inline: true, logo: ${JSON.stringify(logo)} });
</script>
`;
fs.mkdirSync(path.dirname(destination), { recursive: true });
fs.writeFileSync(destination, html);
console.log(`对话右侧架构预览已生成 ${destination} (${Buffer.byteLength(html)} bytes)`);
