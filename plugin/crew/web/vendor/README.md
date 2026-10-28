# 前端本地依赖

- `markdown-it.min.js`：沿用现有 Markdown 阅读组件，许可见 `markdown-it.LICENSE`。
- `mermaid.min.js`：Mermaid **11.15.0** 官方 npm 包 `dist/mermaid.min.js`，许可见 `mermaid.LICENSE`。仅报告存在 Mermaid fenced code 时本地加载，不访问 CDN。图示使用 strict 模式，禁止点击回调，并限制节点边与文本规模；绘制失败保留原文。来源：`https://registry.npmjs.org/mermaid/-/mermaid-11.15.0.tgz`，渲染接口见 https://mermaid.js.org/config/usage 。

该分发文件 SHA-256：`70137e77bb273bb2ef972b86e8b0400cca8be53cb25bfc45911a186dc98665de`。保留官方文件的空白与 LF，不对压缩分发文件重新格式化。
