# 0.3.3：官方 Codex 桌面接回

记录日期：2026-10-30。工作分支 `rebuild/v2`；主干 V1 不变。本记录对应 0.3.2 之后的接续修复。没有更新或重启正式 Crew。

## 技术路线与实际复用

Codex 的执行、会话存储、模型发现、压缩与工具协议继续使用官方 app-server。Crew 保留按总会话、任务执行、全局助手分别绑定原生 ID 的既有实现。

本次新增的是 Windows 官方桌面的窄适配器：通过该桌面已有的 CLI 路径覆盖机制，透传原生 stdio；通过原生 `thread/unsubscribe` 和零卸载延迟释放空闲对话。专用桌面 profile 与用户原来打开的桌面分开；原生 HOME 和 thread ID 保持一致。单个会话的释放、返回不停止其他 Crew 会话。

实际阅读了 Happier、Happy、Harnss、Cotal、codex-local-remote、codex-desktop-linux、codex-web 的源码及许可。Windows 官方桌面的共享连接已有公开实现，不能说“GitHub 没人做”。但共享 app-server 会广播工具请求，且已加载线程可能忽略 resume 配置；直接整搬不能保留 Crew 的逐成员身份和工具归属。本次没有复制这些项目未使用的代码，具体核验提交、可复用组件和边界见 [开源实现核验](OPEN_SOURCE_REUSE_2026-10-30.md)。

## 改动

- 交出前检查官方桌面及适配器，缺组件时保留 Crew 原有运行连接。
- 由后台打开正确 HOME、正确 ID 的专用官方桌面，等待适配器真实就绪；网页不再调用系统 deep link 去猜现有窗口。
- 回到 Crew 前确认桌面空闲、没有待答原生请求；只卸载该对话，确认真实关闭通知及完整加载清单均已释放后，才冷恢复 Crew 并同步历史。
- 官方桌面默认屏蔽 `thread/closed`。适配器为自身接收此确认，同时保留桌面原来的通知偏好。桌面完成 initialize 后没有再发 initialized 通知的路径，也能正确判断就绪。
- 再次交出时，由该线程原来的桌面拥有者使用原始参数恢复；其他后台连接不能争抢。恢复不发送模型输入，不自动重放失败消息。
- 成员在两种入口都保留原生子代理禁用配置，使用同一份角色限制；leader 的原桌面配置保留。
- 桌面等待不占用 Crew 全局锁；真实线程并发测试确认其他会话仍可读取状态及发送。
- 真实原生错误保留到界面，失败后立即刷新保存的原因；成功但刷新失败不会重复操作。
- 便携版的适配器按内容哈希保存到稳定 HOME 目录，避免 Crew 退出后官方桌面找不到已解压的程序。

## 真实验证，不以协议探针代替桌面

本机官方桌面 `26.1023.6621.0`，原生 CLI `0.165.0-alpha.1.2`。所有本次真实模型 turn 均为用户授权的 `gpt-5.6-luna / low`；隔离 HOME、项目和 profile。没有归档测试线程、修改锁文件、替换原生 ID或强行接管其他窗口。

两条隔离原生会话合计 6 个真实 turn：

1. 前期探针起源会话：官方桌面记住 `CREW_ROUNDTRIP_73`，接回 Crew 后正确回答。此线程没有 Crew 动态工具，不能用它证明工具恢复。
2. 正常从 Crew 新对话表单创建的会话：Crew 调用一次 `crew_whoami` 并记住 `CREW_TOOL_42` → 官方桌面回复代号 → 桌面保持打开时接回 Crew，再调用一次 `crew_whoami` → 再次交出，在原桌面标签直接发送，模型准确复述 Crew 新输入 → 再次接回。

第二条会话的 4 个模型 turn 全部完成；两次 `crew_whoami` 各有一个不同 call ID、一个真实结果，返回同一 Crew 会话 ID。两次桌面输入、回复和完成记录各导入一次。两条原生会话 `archived=0`，测试没有创建目标或成员。

最终成功旅程的浏览器 page/console 错误为 0。真实 UI 截图与原始记录在 `Temp/v2-desktop-real-2026-10-30/controlled-product/`；原生只读核对在该目录上层的 `controlled-final-checks.json`。

保留了实际失败：首次原生测试误等被桌面屏蔽的关闭通知，返回 HTTP 409；第二次交出暴露已打开标签直接发送 `thread not found`。对应截图、输入和原生原因未覆盖。修复后才完成上述成功旅程。

## 回归与反证

Python 命令在 `plugin` 目录执行，`PYTHONPATH=tests;.`；Chrome 脚本使用宿主已提供的 Playwright，并将 `NODE_PATH` 指向其依赖目录，没有给产品增加浏览器依赖。

- Python 全套：`python -X utf8 -m unittest discover -s tests -v`，2279 项，失败 0，跳过 7 项；完整日志 `Temp/v2-codex-bridge-build/python-all.log`。后续变更的受影响部分另行重跑。
- Python 受影响模块：`test_codex_desktop test_codex_guard test_codex_runtime test_native_handoff test_personal_native`，101 项，失败 0。
- 原生适配器：`test_codex_desktop_bridge`，19 项，失败 0。在真实编译的适配器与独立假原生进程间验证；覆盖通知过滤、成员配置、双连接、分页、归属、恢复、错误、进程退出和不重放。最终编译版本另通过真实原生 CLI 的 4 项零模型检查，包括启动配置逐字节透传及真实关闭确认。
- 前端：`node --test plugin/tests/test_*.js`，214 项，失败 0。
- 桌面：`npm --prefix desktop test`，184 项，失败 0。
- Chrome 真实 HTTP/SQLite 错误恢复：`node plugin/tests/smoke_native_handoff_error.cjs`，2 步通过；期望的 HTTP 409 单独记录，页面及其他控制台错误为 0。
- 旧行为反证：旧全局锁阻塞其他会话；缺原生 ready 判断、漏实时执行禁写、丢失 cold-resume 指令、关闭通知被过滤、allow 不恢复及加载清单只读第一页，均有实际失败记录。不是只对现有输出抄答案。

## 范围和显示边界

本次真实桌面往返验证为 Windows、总对话、leader；任务执行、成员、全局助手通过共用交接接口的隔离回归覆盖。没有把这些专项回归说成每一种角色都已分别跑真实桌面。Claude Haiku 首次回复及同原生 ID 恢复的真实测试已完成，证据另存本轮早先的 Haiku 验收目录；没有新做 Claude 压缩或 Claude 桌面接续。

当前已打开的官方桌面标签可能暂存旧的可见历史：再次交出后模型实际包含 Crew 新消息并能正确回复，但桌面历史列表未必立即补齐 Crew 中新增的条目。未发现支持的外部刷新入口，未把它写成完全无缝的历史显示。没有补丁修改官方应用或猜测未支持参数。

实际核验当前安装版：深链接只有普通导航，内部新窗口操作没有外部 URL/CLI 映射；`thread/closed` 没有历史刷新处理，`thread/started` 对已有会话也只更新元数据。没有通过伪造通知或修改官方应用掩盖这一限制。具体限长源码证据在 `Temp/v2-desktop-link-review/installed-thread-refresh-review.json`。

此前 `The browser access host is unavailable` 属于另一个原生浏览器组件故障；本次没有足够原始日志确认其根因，也没有声称修复。

## 复测

在正常 Crew 新建一个 Luna 对话，发送一句容易核对的话。空闲后点“在 Codex 桌面继续”，在打开的专用窗口回复；再点 Crew“回到 Crew”，核对历史和回复。再交出一次，在原桌面标签直接发送；无需归档对话或退出整个桌面。两边轮流输入。初次使用官方桌面可能要求用户处理其正常的 Windows 权限提示。

旧版本已交给普通 Codex 窗口的对话仍保留原绑定；先释放那个客户端的写入连接并接回，再重新交出，才能进入专用窗口路径。

## 构建与交付

最终三个 0.3.3 安装产物分别构建并核验，均未部署或安装到正式环境。包内 247 个源码/资产与最终源文件一致；ZIP 内 265 个文件与目录版对应文件一致。目录版和便携版均在独立 HOME 实际启动、退出，3 种 EXE 图标检查通过；NSIS 只完成构建和图标核验，没有实际安装。

桥接源 SHA256：`16961cfa08d260cfb6563128abb7496c5e1e4d4be7e12200b7040a79e5277d48`。

| 产物 | SHA256 |
| --- | --- |
| `Crew-V2-0.3.3-portable.exe` | `c795724456a1bc7d32d34bcb42e0780f0be97a2440a7981ae9af8270bd9456f4` |
| `Crew-V2-0.3.3-setup.exe` | `226b0f7d6ca3c4320457e8b324be03b9567a4e7ede0a206fcfec7b2b51da9279` |
| `Crew-V2-0.3.3-win-x64.zip` | `d95c12854db190629347db62b9d5e68410adae4e5d9f856f06cb18fcae235f7a` |

最终构建与隔离启动截图在 `Temp/v2-codex-bridge-build/verified/`。失败候选包与已结束的测试 HOME 按用户要求整体移入系统临时目录的 `待删除/Crew-2026-10-30/`，没有借清理改动正式数据。
