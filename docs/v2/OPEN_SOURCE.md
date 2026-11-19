# 原生能力与开源方案取舍

这是 V2 实施前的源码与官方资料调查。没有启动第三方生产服务，也没有把候选项目的宣传说明当作本机验收结果。

| 参考 | 可采用的模式 | 本轮取舍 |
| --- | --- | --- |
| [Happier](https://github.com/happier-dev/happier) | Claude 原生 SDK / Codex App Server；原生会话身份与宿主控制分离 | 参考原生接续和本地控制权设计，不引入额外同步服务作为初版前提 |
| [Harnss](https://github.com/OpenSource03/harnss) | 桌面 UI 内保持应用 session ID 与 Codex native thread ID 两个身份 | 沿用 Crew 已有 app-server 适配，保留同 native ID，补足往返历史与独占控制 |
| [Cotal](https://github.com/Cotal-AI/Cotal) | 在线消息与持久消息使用同一身份去重，确认成功不重复唤醒 | 用于审视 Crew 消息、动作回执与重复任务启动，不整体替换现有任务状态 |
| [Paradev](https://github.com/ParadevAI/Paradev) | 多会话与工作区的桌面组织 | 仅参考交互；其 ELv2 许可与 MIT 项目不同，本轮未复制代码 |

调查的源码锚点：Happier `ae70fc7129e56ce17919e61028555eaa9956b7e0` 的 `apps/cli/src/backends/codex/appServer/bootstrapCodexTuiHome.ts`；Harnss `dc1dfd8a33caa46a1eefcfe9e14697b27ac4c33d` 的 `electron/src/ipc/codex-sessions.ts`；Cotal `8866e1e1a05be1e6f07c5eb7dc398f743fb8a465` 的 `extensions/connector-core/smoke/cross-path-dedup.smoke.ts`。

## Codex 桌面接续（已删除）

0.3.3 起的“交给 Codex 桌面 / 回到 Crew”和“在 VS Code 中继续”于 2026-11-19 按用户裁决删除：实际几乎没用，且依赖 Codex 内部写入锁行为和 Windows 专用桥接组件，维护成本高。

仍然有效的结论：Crew 自己启动 app-server，不连接桌面进程。同一 Codex 线程同一时间只有一个 writer；用户自己在 Codex 客户端打开 Crew 的线程时，Crew 续接会如实报占用（`codex_binding.WriterBusy`），不换 ID、不抢占。`thread/unsubscribe` 本身不足以释放 writer。

## 为什么保留现有协议适配

现有 Crew 已有 CC 与 Codex 真实协议、持久任务、消息路由、人工答复、桌面标签等基础。初版的主要缺口是交互和生命周期的一致性。整体嵌入另一套产品会引入第二套身份、数据和控制权。V2 先把这些边界修完整，再按实际缺口引入经过隔离验证的组件。
