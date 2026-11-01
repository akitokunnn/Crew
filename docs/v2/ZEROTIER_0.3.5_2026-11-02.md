# Crew V2 0.3.5：ZeroTier 和旧连接迁移

本轮按用户决定先交付 ZeroTier 访问，不购买域名或配置 Cloudflare。复用原局域网认证、业务 API、聊天事件流和任务/助手身份，不新增聊天后端。起点是 `rebuild/v2` 的 `b2b19a3`，包含此前助手动态用量和等待计时的修复。

## 使用

1. 运行新版 Crew，打开“设置 → 局域网访问”，选择 ZeroTier。
2. 选中已连接的 ZeroTier IPv4 地址，开启访问。只绑定选中的地址；已有全部网络监听会明确显示。
3. 手机或另一台电脑加入并开启同一 ZeroTier 网络，且设备已获该网络授权。
4. 在浏览器打开 Crew 显示的地址，在原电脑生成一次性配对码并输入。之后在授权有效期内直接访问，无需每次重新配对。
5. 离开当前对话可回工作区或全局助手。关闭访问只关闭网络入口；电脑需要保持运行，已有任务与历史继续由原后台持有。

地址变动或网卡断开时会显示实际错误，不会静默改成全部网络。切换绑定地址需要短暂重建监听，已完成输入不自动重发。设备会话和草稿规则沿用 0.3.4：授权最长 7 天，配对码 5 分钟有效且一次使用，草稿保存在各浏览器自己的存储中。

ZeroTier 网络本身由 ZeroTier 管理。Crew 不读取其管理令牌，不改变网络成员、路由、网卡或防火墙。当前只支持私有 IPv4（10/8、172.16/12、192.168/16，及本机回环）；Windows 按真实网卡识别，其他系统会明确标记发现信息不完整。普通 HTTP 下仍要求配对，不因处于 ZeroTier 网络就跳过认证。

## 模块和操作边界

`network_addresses.py` 使用 Windows IP Helper API 发现接口，短暂缓存，不开启 PowerShell 子进程；`remote_access.py` 管理绑定、配对和会话，`remote_http.py` 只持有其网络连接。开始监听时重新检查地址，界面只读取其快照并通过现有动作接口修改。

`browser-utils.js` 统一非安全上下文的请求标识和复制能力。聊天、快速想法、成果快照、阶段说明和运维共用标识生成；重试仍保留原操作标识。Git 复制失败保留完整 SHA 可供手动复制。

## QQ 和 Lark

按用户明确授权，将 `C:/Users/admin/.crew` 的 QQ、Lark 凭据与本人接收者、提醒偏好导入 `C:/Users/admin/.crew-v2`。目标五个配置文件此前不存在，本次仅创建这些文件：`qq.json`、`notify.json`、`lark.json`、`lark-notify.json`、`notification-routing.json`。两渠道启用，Lark 使用原 feishu 地区，主动提醒沿用 both，manual_away=false。旧文件字节及目标现有数据库保持不变。

迁移入口是离线 `connection_import`，持有既有 HOME 服务锁，不启动模型或接收连接。重复导入跳过相同配置，不覆盖新版不同值；只保留账号、接收者与偏好，重置旧项目选择、leader 编号、扫描时间和投递代际。正式数据未因开发测试而启动或迁移。

实际提醒由显式 `connection_probe --send` 执行，QQ/Lark 各一条本人测试消息，两边平台均返回消息 ID。回执在发送前创建；相同回执重复运行只返回 reused，不再发送，网络状态未知时也不盲目重发。平台 accepted 不等于用户手机已读。脱敏记录在 `Temp/connection-migration-2026-11-02/actual-import.json` 和 `actual-notification.json`，未在文档记录凭据或接收者 ID。

配置导入和单次通知均没有启动旧版或新版正式收信后台。下次启动 V2 会沿用导入配置；未停止旧实例或修改 ZeroTier 服务。

## 验证

所有开发交互使用隔离 HOME、真实 HTTP/SQLite 与假 AI 载体；真实外部发送仅限用户授权的 QQ/Lark 两条测试提醒。浏览器错误检查保留预期的错码、断网和授权失效 HTTP 错误，其他控制台错误及后台 stderr 均为空。

| 验证 | 实际结果与记录 |
| --- | --- |
| Python 全量（3 个模块分组） | 2,363 项：2,356 通过、7 跳过、失败 0；最终日志汇总见 `Temp/zerotier-2026-11-02/python-final-summary.json`。分组清单与 unittest discover 的测试 ID 多重集合核对一致，首组修正夹具后完整重跑 |
| `node --test plugin/tests/test_*.js` | 234 项通过，失败 0，`js-tests-final.log` |
| `npm --prefix desktop test` | 188 项通过，失败 0，`desktop-tests.log` |
| `node plugin/tests/smoke_remote_access.cjs --zerotier --lan-address 10.190.170.83` | 真实 Chrome：32 项，1440px 与 390px；配对、聊天、拍板、助手草稿、待办、文件、断线恢复、快速想法、阶段编辑、成果恢复和完整 Git SHA 复制；21 张截图，`Temp/zerotier-browser/current/evidence.json` |
| 同一浏览器脚本加 `--address-disappeared` | 4 项通过；模拟发现数据中原地址消失，原地址仍明确显示不可用，重新选择不启动访问；`Temp/zerotier-browser/address-change/evidence.json` |
| `python -X utf8 plugin/tests/smoke_remote_peer.py --address 10.190.170.83` | 独立 WSL 客户端 9 项通过，`Temp/zerotier-peer/evidence.json` |
| `node desktop/scripts/smoke-remote-access.cjs` | 最终 0.3.5 源码 Electron 窗口、IPC、预加载、真实配对与撤销，7 项通过、9 张截图，无模型启动；`Temp/remote-desktop/current/evidence.json` |
| `node desktop/test/operations-smoke.cjs` | 运维真实 Electron 渲染交互 31 项通过，`desktop/smoke-output/operations/` |

完整 Python 首跑暴露了旧测试的后台轮询竞争，以及 Git 组件 VM 漏载共享工具两个测试夹具问题。分别修正夹具并复验，没有改变后台轮询的生产行为，也没有用替身代替共享复制实现。原始失败日志保留在 `python-full.log`；Git 模块另有 47 项全绿记录。

反证并非静态检查：装回旧 HTTP 调用后，真实浏览器在快速想法保存处报 `crypto.randomUUID is not a function`；装回旧界面缺少明确 ZeroTier 选择；移除原地址不可用选项后，浏览器原生选择值变成其他地址。同类后端反证覆盖按网段猜测接口、跳过非本机地址校验、跳过启动时刷新，以及丢失接收者、Lark-only 离开偏好和误报未知通知响应。上述旧错均确实触发失败，恢复修复后通过。

截图示例：`Temp/zerotier-browser/current/00b-zerotier-settings-phone.png`、`04-phone-chat.png`、`08c-http-versions.png`，以及 `Temp/zerotier-browser/address-change/00d-address-unavailable.png`。浏览器和 WSL 测试后台均已退出。

## 构建与源码

运行 `npm --prefix desktop run build` 生成 `desktop/dist/Crew-V2-0.3.5-portable.exe`、`Crew-V2-0.3.5-setup.exe` 和 `Crew-V2-0.3.5-win-x64.zip`。最后界面修正后重新构建，不直接修改产物。

使用 `node desktop/scripts/verify-bundle.cjs` 对比桌面资源、后端及技能与当前源码的内容哈希，再分别对 `win-unpacked/Crew V2.exe` 和便携 EXE 执行 `desktop/scripts/smoke.cjs`，核对 0.3.5、隔离 HOME 启动及所属后台退出。安装包只验证生成与图标，未安装覆盖正式应用。完整日志在 `Temp/zerotier-2026-11-02/`，包内源码清单在 `desktop/smoke-output/bundle-source.json`。

最终 `win-unpacked` 包内 258 个文件与源码一致，清单摘要为 `95fa7754b613f084641f13bc89f295a3f31d4a025de1b065d802820501a54f18`。界面截图和完整源码窗口验收完成后再次核对，不以旧包的同名文件作为通过依据。便携包另验真实解压启动和退出；其临时解压目录在退出时自动清理，内容清单校验针对未压缩包。

| 产物 | SHA-256 |
| --- | --- |
| `Crew-V2-0.3.5-portable.exe` | `cd95207cdcb0adc2077213c9484108f40baee4879c60b9860633951c385a70e9` |
| `Crew-V2-0.3.5-setup.exe` | `d239055b5c428c5c985b994095b7e7bae56eb4e6ef899c3d69f05134abbf2009` |
| `Crew-V2-0.3.5-win-x64.zip` | `a67c693c87fca3f1bb45352c2819bfae63beeb85e40c8aa69f828e2586109d91` |

本轮交付到 `rebuild/v2`；未合入保留 V1 的 main，也没有启动或替换用户正式后台。

## 尚未验证

已证明的网络边界：WSL 客户端 `172.18.190.231` 通过独立网络栈访问这台电脑 ZeroTier 网卡上的 `10.190.170.83`，真实 HTTP 配对、拒绝未登录、聊天去重、实时事件、文件预览、撤销授权和关闭入口通过。本机其他监听保持工作；测试后台退出。该测试没有创建第二个 ZeroTier 成员，不能据此宣称已测过真实 ZeroTier 节点之间的覆盖网络传输或实体手机。

测试前只读检查发现现有 Python Public 入站规则已是 Allow，与 0.3.4 历史验收时的 Block 不同。本轮未更改防火墙，旧报告不作为当前规则的依据。
