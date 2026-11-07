# Crew V2 0.3.4：局域网访问

本轮在 `rebuild/v2` 增加同一网络中的个人远程访问。公网方案已比较，用户尚无域名，Cloudflare 配置延期；本次没有开通公网、安装 cloudflared 或修改系统防火墙。

## 使用

1. 在运行 Crew 的电脑上打开“设置 → 局域网访问”，点击“开启局域网访问”。
2. 手机或另一台电脑连接同一网络，在浏览器打开设置中显示的地址。多网卡可能显示多个地址，应使用对应网络的地址。
3. 点击“生成配对码”，在另一台设备输入 8 位数字。码在 5 分钟内有效，只能使用一次，重新生成后旧码失效。
4. 连接后可切换已经登记的工作区及全局助手，继续聊天、处理任务、拍板、待办和查看报告。首次连接不自动发送消息或唤醒 AI。
5. 在原电脑的设置中可以移除单台或全部设备，或关闭访问。设备授权最长 7 天；撤销后重新配对。关闭远程访问不停止本机正在执行的任务。

电脑需要开机并保持 Crew 运行。已启用的访问设置会在下次启动后台时恢复；端口冲突会明确显示错误，本机聊天继续可用。网络隔离或防火墙可能阻止其他设备连接，程序不会自动修改这些设置。

如果原电脑能打开地址、另一台设备超时，应检查系统的应用入站规则和当前网络。Windows 的显式阻止规则优先于普通允许规则；仅新增一个允许端口可能仍无法连接。应由设备所有者检查 Crew 所用 Python 的相关规则，只允许自己信任的网络和所需端口，不必关闭整个防火墙。[Microsoft 官方规则说明](https://learn.microsoft.com/en-us/windows/security/operating-system-security/network-security/windows-firewall/rules)

## 范围与交互

这是本人访问同一个 Crew HOME，配对后能访问其中所有工作区与全局助手，不是向其他人分享单个项目。手机窄屏使用菜单切换工作区、主页和全局助手。

聊天与事件流沿用现有身份、消息 ID 和历史。断网后继续读取事件，不重发已经提交的输入；登录失效时保留本浏览器草稿并提供重新连接。不同设备的浏览器草稿独立保存，不宣称跨设备同步。

本机设置、工作区导入迁移、系统文件打开、VS Code 和 Codex 桌面交接等需要操作原电脑的能力不向远程页面开放；后台同样拒绝这些请求。文字、图片和任务 HTML 报告使用现有受限预览。尚不支持网页预览的文件格式显示明确说明，不自动在原电脑弹出文件。

局域网 HTTP 未加密，只适合可信网络。配对采用程序生成的一次性数字码，设备凭据为随机值，只持久保存摘要；Cookie 禁止脚本读取。页面、静态文件、API、附件和流式事件统一认证，不信任伪造的代理身份头。

## 模块边界

| 所有者 | 职责 |
| --- | --- |
| `remote_access.py` | 开关、一次性配对、设备授权、撤销与配置持久化；不持有聊天、任务或数据库对象 |
| `remote_http.py` | 独立 HTTP 监听、请求连接及停止；不关闭业务 Service |
| `remote_routes.py` | 明确列举允许的浏览器能力、限制 AI 身份字段和安全回跳 |
| `web.py` | 组合监听、认证和已有功能分发，复用现有浏览器端口策略与事件顺序 |
| `access-context.js` | 读取实际访问能力，登录失效提示；不重试业务 POST |
| `remote-access-ui.js` | 桌面及本机网页共用设置面板，由读取/操作接口注入，无第二份设备状态 |

没有新增聊天引擎、任务数据库、代理改写层或邮件验证码实现。后续公网复用 Cloudflare 官方 Tunnel 与 Access；[选型记录](../decisions-remote-access.md) 记录具体原因及官方限制。

## 验证记录

- Python 全量发现运行 2327 项：2318 通过、7 项因 Windows 符号链接权限或需显式开启的原生路径测试而跳过；2 项旧生命周期测试夹具漏掉新增的远程资源所有者而报错。已补全夹具并保留先广播摘要取消、再关闭其他资源的顺序，随后在最终源码重跑相关 41 项全部通过。未把首次有错误的全量记录改写为全量一次通过，也未重跑第二轮全量；原日志为 `Temp/lan-access-2026-11-01/python-full.log`，最终相关日志为 `python-final-focused.log`。
- `python -X utf8 -m unittest test_remote_access test_remote_http test_remote_routes test_remote_integration test_exit_lifecycle -q`：最终源码 41 项通过，覆盖 CC/Codex 总会话、任务与全局助手、消息去重、真实 SQLite、附件、报告、SSE 续读与撤销、端口选择、后台重启和独立关闭。假载体，不调用真实 AI。
- `node --test plugin/tests/test_*.js`：219 项通过。原 V2 页面 28 项、助手回复 3 项浏览器回归通过。
- `npm --prefix desktop test`：188 项通过。
- `node plugin/tests/smoke_remote_access.cjs --lan-address 10.245.2.200`：实际 Chrome、真实后台，22 项通过，非预期控制台错误 0。包括 390px 手机视口、1440px 独立浏览器、全局助手、拍板、TODO、报告、断网恢复、过期后保留草稿。截图和访问地址记录在 `Temp/remote-browser/current/`。
- `node desktop/scripts/smoke-remote-access.cjs`：实际源码 Electron 主进程、preload 和首页，7 项通过、9 张截图、控制台错误 0、模型启动 0。通过真实 IPC 点通开启、配对、移除、关闭和返回。截图在 `Temp/remote-desktop/current/`。测试使用隔离 HOME 与 loopback 随机端口；不将其描述为跨设备连通。
- 装回旧移动端布局、桌面误露导航按钮、缺失桌面 IPC、认证旁路、禁用端口及三种身份绕过后，检查均明确失败；恢复后重新通过。记录在 `Temp/remote-browser/`、`Temp/remote-desktop/` 和 `Temp/lan-access-2026-11-01/`。
- `npm --prefix desktop run build`：生成 0.3.4 目录版、ZIP、portable 与安装包。`verify-bundle.cjs` 校对最终包的 254 个应用与后端文件，摘要与源码一致；目录版与 portable 的隔离启动退出均通过，三种 EXE 图标检查通过。基础包验收不声称包内设置已逐按钮测试。结果在 `Temp/lan-access-2026-11-01/` 和 `desktop/smoke-output/`。

产物：`desktop/dist/Crew-V2-0.3.4-portable.exe`、`Crew-V2-0.3.4-setup.exe`、`Crew-V2-0.3.4-win-x64.zip`。此记录不代表正式安装已替换；测试均为隔离实例。已结束的三个手工桌面 smoke HOME 移入系统临时目录中的 `待删除/Crew-2026-11-01/`，截图和日志保留。

实际跨设备的验证仍有边界：本机 Chrome 使用真实以太网地址，证明应用绑定和浏览器流程；它不等同于另一台设备穿过防火墙。WSL 客户端 `172.18.190.231` 到以太网 `10.245.2.200` 的连接实测超时；只读检查发现当前以太网为 Public，并有 Python TCP/UDP 入站显式阻止规则。未修改这些规则，没有收到实体手机成功连接的回执；该设备可达性尚未验证通过，不能据此宣称手机已能从其他设备连通。

实体手机的软键盘、触屏输入和休眠恢复也未由视口模拟替代。公网、Cloudflare 邮箱验证和真实 AI 均不在这次验证中。
