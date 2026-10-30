# 约束与工具说明的实际字符量

用户问的是“系统规则、工具说明、Crew 守则、技能目录全部算上有多少”，不能只数 Crew 自己的角色文件，也不能把字符数当成 token 数。本次只读检查了实际 Crew 会话的保存记录；没有输出提示全文、认证或密钥。

## 已经量到的部分

下面第一组来自用户实际的 GPT-6.1-Sol leader 总会话，版本为此前运行的 0.3.1。取第一次 `turn_context` 之前的原生启动记录，不把之后的聊天历史混进系统说明：

| 项目 | Unicode 字符数 | 口径 |
| --- | ---: | --- |
| 保存的 Crew 行为配置 | 4994 | 含通用原则、leader 守则、报告、上下文及分隔文字；角色守则本身 4204 |
| 原生启动的 developer 消息合计 | 52249 | 已包含原生基础说明、技能/环境说明与 Crew 注入的配置，不再另加上一行 |
| 原生基础说明 | 21546 | 已包含在 developer 消息中，不能重复加总 |
| 启动记录中的 additional_tools 目录 | 58169 | 以 UTF-8 解码后的 JSON 序列化文本计数，包含工具结构和说明；不等于原生渲染给模型的纯文字 |
| Crew 动态工具 schema | 17687 | 27 个工具的配置 JSON；与原生工具目录可能重叠，不能再叠加宣称总量 |

实际全局助手的同类记录为 developer 文本 46965 字符、additional_tools JSON 41016 字符；Crew 自己只登记 2 个专用工具，schema JSON 为 1375 字符。

所以，这个环境的初始化说明确实很大，“约 8 万字符”有数量级上的依据，但它仍是估算。可见说明文本约 5.2 万、工具 JSON 约 5.8 万是两种格式的实测；不能把两者直接冒充精确的模型上下文字符数，更不能由此换算精确 token。大部分内容并不等于 Crew 又给用户增加了业务限制。

原生内建工具最终如何渲染、不可见平台规则和服务端处理不能由这些记录完整确定，完整模型上下文的精确总量仍未知。另本轮隔离 Luna 的原生基础说明是 17730 字符，与实际 Sol 会话不同，不能用隔离模型的数字替换用户会话。

## 本轮减少的重复和误导

- 不再在初始化和每条正常输入重复灌入旧组信任说明；任务的“由我拍板／自主推进”是新任务唯一的决策控制。
- 宿主已经提供角色守则时，派工简报不再要求 Codex 重读整份同一守则。
- 不把长授权正文、系统配置或未知状态历史当成关系图里的任务标题；关系图只展示当前协作的简短摘要。
- 规则不新增“自主推进不能改文件／调用服务”的限制。正式记录中该说法是 AI 给一次发布测试自行添加的约束，本轮修正其来源与提示，不能冒充用户的硬性要求。

## 复跑

`scripts/audit-crew-prompts.py` 不启动 AI、不迁移数据库；指定 HOME 时使用 SQLite `mode=ro` 和 `query_only=ON`。只输出字符数、散列和必要元数据：

```powershell
python scripts/audit-crew-prompts.py
python scripts/audit-crew-prompts.py --home <Crew数据目录> --session <Crew会话ID> --applied
python scripts/audit-crew-prompts.py --rollout <已保存的原生rollout.jsonl>
```

第一条数当前源码默认配置；第二条数实际保存的总会话配置清单，不能把当前源码当作过去已发送内容；第三条统计保存的原生启动说明和工具目录。工具 schema 均标明来自当前源码。

本次证据在 `Temp/v2-feedback-2026-10-30/prompt-audit-reproducible.json`、`prompt-audit-saved-native-initial.json`、`native-prompt-measurements.json`。这些是本机临时验收证据，不将用户对话或提示全文纳入 Git。
