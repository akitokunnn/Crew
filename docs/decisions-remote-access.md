# 局域网访问与后续公网入口

决定日期：2026-11-01。对应 `rebuild/v2` 的远端浏览器访问迭代；起点为 `c13e6bb`、桌面 0.3.3。本文记录选型，不代表已部署、已开放公网或已完成最终交互验收。

## 当前决定

先提供局域网浏览器访问。主后台继续仅监听本机，由独立的局域网访问模块管理监听、设备配对和授权；桌面设置仅调用该模块的读取与操作接口，不保存第二套业务状态。关闭访问、撤销设备与关闭原来的本机服务是不同操作。

公网延后，用户有域名后再实施。当前不安装或启动 cloudflared、不配置公网域名、不修改系统防火墙。邮件验证码、域名与证书均不在本次局域网功能中自行实现。

## 方案比较

| 方案 | 适用与需要维护的内容 | 流式聊天与主要限制 | 决定 |
| --- | --- | --- | --- |
| 独立局域网访问模块 | 同一网络里的手机与电脑；在本机开启访问、生成配对码、撤销设备 | 沿用 Crew 的事件流与历史；可达性仍受实际网络和防火墙影响，不因局域网地址自动获得授权 | 本轮实现 |
| 固定 Cloudflare Tunnel + Access 邮件 OTP | 后续公网访问；需要 Cloudflare 账号、Zero Trust 组织、活动域名，维护隧道凭据和邮箱允许策略 | 支持 `text/event-stream`；浏览器通过邮件验证码认证，入口使用 HTTPS；仍需保留 Crew 的访问范围和设备控制 | 有域名后采用 |
| Protected Quick Tunnel | 临时演示；无需 Cloudflare 账号或域名，使用 `--allowed-mail` 指定邮箱 | 官方明确不支持 SSE；随机地址、无可用性保证。不能直接作为 Crew 完整聊天入口，需要额外的轮询模式才能考虑 | 当前不采用 |

固定 Tunnel 的官方浏览器访问流程、域名要求和邮箱策略见 [Private web application](https://developers.cloudflare.com/cloudflare-one/setup/secure-private-apps/private-web-app/)。邮件 OTP 是现成身份方式，不需要 Crew 自建发信和验证码验证；新组织需按官方步骤显式添加 OTP。[One-time PIN login](https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/one-time-pin/)

Protected Quick Tunnels 自 cloudflared 2026.9.3 起提供邮箱认证，功能已经发布。其无需账号的优势不能抵消当前 SSE 限制；不使用未记录的参数或改响应类型来假定限制已经消失。[官方发布](https://blog.cloudflare.com/protected-quick-tunnels/)、[当前限制](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)

## 实施边界

- 不把原后台直接改成监听 `0.0.0.0`。原来的服务复用、后台控制和本机文件能力继续使用本机入口。
- 设备认证与允许访问的功能必须先检查，再转发或调用领域功能。原有 `X-Crew: 1` 和本机 Host/Origin 检查不是远端用户认证；不能仅改写这些头就把请求视为本机操作。
- 一次验证码认证不等于单项目权限。若以后需要把某个工作区分享给别人，应另行明确工作区范围，并对聊天、事件流、附件及全局接口使用一致授权；不能仅靠 URL 中的项目参数隔离。
- 事件流保留真实顺序、事件 ID 和断线续读，不重复发送已经成功的操作。普通 Tunnel 根据 `Content-Type: text/event-stream` 使用流式转发。[官方流式说明](https://developers.cloudflare.com/tunnel/troubleshooting/)
- 后续公网可由 cloudflared 的 `originRequest.access` 使用 `required`、`teamName` 和 `audTag` 验证 Access JWT。局域网入口不能因收到同名请求头而跳过设备认证。[Origin parameters](https://developers.cloudflare.com/tunnel/reference/origin-parameters/)
- 公网 HTTPS 与 LAN 本地传输是两件事。本轮不把配对描述为端到端加密，也不自动安装设备信任证书。若后续增加局域网 HTTPS，需明确其他设备的证书信任方式。

## 可复用组件

- `cloudflared`：官方隧道和 Access 校验，Apache-2.0；公网实施时直接复用官方发行版，不自建穿透或邮件 OTP。[源码与许可证](https://github.com/cloudflare/cloudflared)
- Caddy：Apache-2.0，可选的独立反向代理、TLS 和密码认证；已支持 SSE 即时刷新，但会增加一个受管理程序和局域网证书配置。[反向代理](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)、[HTTPS](https://caddyserver.com/docs/automatic-https)、[许可证](https://github.com/caddyserver/caddy/blob/master/LICENSE)
- `http-proxy-middleware`：MIT，可在需要 Node 代理时复用；读取时最新为 4.2.1，Node 要求为 `^22.15.0 || ^24.0.0 || >=26.0.0`。其 `responseInterceptor` 会关闭流式响应，不能用于聊天事件通道。此库仅为候选，本轮未因研究而安装或引入。[源码与限制](https://github.com/chimurai/http-proxy-middleware)、[许可证](https://github.com/chimurai/http-proxy-middleware/blob/master/LICENSE)

本轮代码、自动测试与真实浏览器验证由对应交付记录说明；本文不以官方支持情况代替实际设备连通性或交互验收。
