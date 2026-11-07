# 原生能力与开源方案取舍

这是 V2 实施前的源码与官方资料调查。没有启动第三方生产服务，也没有把候选项目的宣传说明当作本机验收结果。

| 参考 | 可采用的模式 | 本轮取舍 |
| --- | --- | --- |
| [Happier](https://github.com/happier-dev/happier) | Claude 原生 SDK / Codex App Server；原生会话身份与宿主控制分离 | 参考原生接续和本地控制权设计，不引入额外同步服务作为初版前提 |
| [Harnss](https://github.com/OpenSource03/harnss) | 桌面 UI 内保持应用 session ID 与 Codex native thread ID 两个身份 | 沿用 Crew 已有 app-server 适配，保留同 native ID，补足往返历史与独占控制 |
| [Cotal](https://github.com/Cotal-AI/Cotal) | 在线消息与持久消息使用同一身份去重，确认成功不重复唤醒 | 用于审视 Crew 消息、动作回执与重复任务启动，不整体替换现有任务状态 |
| [Paradev](https://github.com/ParadevAI/Paradev) | 多会话与工作区的桌面组织 | 仅参考交互；其 ELv2 许可与 MIT 项目不同，本轮未复制代码 |

调查的源码锚点：Happier `ae70fc7129e56ce17919e61028555eaa9956b7e0` 的 `apps/cli/src/backends/codex/appServer/bootstrapCodexTuiHome.ts`；Harnss `dc1dfd8a33caa46a1eefcfe9e14697b27ac4c33d` 的 `electron/src/ipc/codex-sessions.ts`；Cotal `8866e1e1a05be1e6f07c5eb7dc398f743fb8a465` 的 `extensions/connector-core/smoke/cross-path-dedup.smoke.ts`。

## Codex 桌面接续

[官方桌面命令](https://learn.chatgpt.com/docs/app/commands)提供 `codex://threads/<thread-id>` 深链；[原生命令说明](https://learn.chatgpt.com/docs/developer-commands)提供 `/app`，描述的是继续已有 saved chat。[App Server 文档](https://learn.chatgpt.com/docs/app-server)是 thread/start、resume、历史读取与 dynamicTools 行为的依据。

Crew 自己启动 app-server，不假装连接到桌面进程。交给桌面前必须空闲、释放 Crew 的原生进程并持久记录外部控制；接回必须使用同一 ID，恢复 Crew 指令与实际工具处理连接，并按原生 turn/item 身份导入新增历史。目标执行有自己的 native ID，不能以 leader 总对话替代。

2026-10-30 的隔离原生验证确认：Codex CLI 0.165.0 的 `thread/resume` 没有 `dynamicTools` 替换参数，原线程工具定义仍持久保留；不能据此声称原生桌面有 Crew 的工具处理器。另一个原生客户端仍持有 writer 时，接回必须如实保留冲突，关闭实际持有者后再恢复同 ID。`thread/unsubscribe` 本身不足以释放该版本的 writer。协议实测与尚未验证的桌面 UI 边界见 [原生反馈自检](NATIVE_FEEDBACK_SELF_CHECK_2026-10-30.md)。

深链不带 HOME 参数，Crew 绑定的原生 HOME 必须可由桌面读取。操作系统接受打开请求，不证明桌面已经接管；假载体同 ID 往返，也不证明真实桌面完成续聊。验收记录必须分别写清。

## 为什么保留现有协议适配

现有 Crew 已有 CC 与 Codex 真实协议、持久任务、消息路由、人工答复、桌面标签等基础。初版的主要缺口是交互和生命周期的一致性。整体嵌入另一套产品会引入第二套身份、数据和控制权。V2 先把这些边界修完整，再按实际缺口引入经过隔离验证的组件。
