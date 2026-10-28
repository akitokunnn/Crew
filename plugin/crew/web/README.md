# Crew Web 前端

正式工作台入口为 `index.html`；`workbench.js` 组合功能，业务状态由对应模块拥有。源码直接运行，无框架迁移或额外构建。

```powershell
# 明确标注的本地样例，不连接后端或真实 AI
node plugin/crew/web/dev-server.cjs --port 4320
# http://127.0.0.1:4320/?data=mock

# 显式连接独立本机后端，去掉 data=mock 才使用真实接口
node plugin/crew/web/dev-server.cjs --port 4321 --backend http://127.0.0.1:独立端口
```

API、流、文档字节及桌面能力都通过 `CrewClient.current`。真实连接失败不回退样例。新增功能通过 `CrewUIActions` 登记／卸载动作，沿用 `CrewDOMRenderer` 处理焦点和输入状态。

完整接线、验证与集成交接见 [前端接入记录](../../../docs/CREW_FRONTEND_INTEGRATION.md)。共享协议由后端维护在 `docs/CREW_API_CONTRACT.md`。本地第三方依赖说明见 [vendor/README.md](vendor/README.md)。
