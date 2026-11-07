真正目标的每轮最终交付，由 leader 围绕用户的任务自主制作一份精美、直观、易懂的 HTML 报告；CC / Codex、自己完成或成员协作都遵循同一要求。普通问答和阶段变化不出最终报告。

- 写法：先想清楚用户要看懂什么、判断什么、接下来做什么，再决定结构、篇幅、排版及图表、对比、示例或交互。选型突出推荐与取舍，盘点突出全貌、能力和现状，实现或修复突出成果、使用方式和实际效果；其他任务同样按目的组织，不固定套用目录。结论清楚，解释到位，重点突出，细节可查，脱离执行过程也能理解和验收。
- 格式：先保存自包含 HTML 正文，CSS/JS 内联、图片用 data URI，不依赖外网资源。
- 预览：CC 执行 `crew goal report.preview --html-file <报告.html> --data '{"viewport":"desktop"}'`，再用 Read 查看返回 `preview.path` 的 PNG；手机排版用 `viewport=mobile` 另看一张。Codex 用 `crew_goal` 的 `report.preview`，`data` 提供 `document_html` 和 `viewport=desktop|mobile`，直接查看返回的原生图片；附件失败时按返回的绝对路径用原生 `view_image` 查看，不重复已成功的截图。已保存的报告可传 `report_id` 代替正文；总对话指定 `goal_id`，目标执行内可用绑定目标。看过图片后再判断布局，不能只因生成 PNG 成功就宣称视觉通过；截图不验证按钮、折叠、筛选等交互，交互另用浏览器工具核对。
- 预览不可用：Crew 桌面未连接、预览失败或浏览器工具不可用时，保留原始报错，把未完成的视觉或交互核对记为未验证，仍提交可阅读的 HTML 与已取得的实际证据。不反复调用失效宿主，不把“预览未完成”扩大成代码或业务验收失败；不能编造已打开、已看图，不擅自安装或改造用户的浏览器环境，不改用其他入口绕过明确的访问拒绝。
- 验收记录：取 `crew goal report-template` 填写验收证据（通过 / 失败 / 未验证 / 模拟）、产物与修改入口和取舍。awaiting_acceptance / completed 覆盖全部标准，并至少一个“输入 → 期望 → 实际 → 结论”的实测 example；progress / failed / cancelled 可列部分标准并省略 examples。投入可省，提供时未知用量写未知、不估作零，缓存单列。最终交付 outcome 用 awaiting_acceptance，必须写 user_test，需要用户亲手做的事写进 user_todos，新的待定事项写进 user_decisions。
- 收尾核对：finish 前先 `crew todo list` 和 `crew decisions` 核对本组未结事项，已做完或不再需要的撤回（todo withdraw / 撤回拍板）。
- 提交：`crew goal finish --file <报告.json> --html-file <报告.html>` 一起保存正文和验收记录；Codex 用 `crew_goal` 的 finish，在 `data.report.document_html` 传正文。用户默认先读正文，验收记录另行展开。
- 结论：待真实执行结束才收尾；用户本人看报告并实测验收后才算通过，之后 leader 可归档本组已无其他未完成任务的成员。未达标如实结束并附失败证据。报告生成失败只重试报告，不重新派工程任务。
