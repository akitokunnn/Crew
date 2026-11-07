每个实际执行并交付成果的任务维护一份持续更新的阶段报告。
第一段直接说明结果或当前结论，使用短段落；适合比较的属性用表格，分支关系用图，趋势使用真实数据。
阶段报告先 `crew goal report.get` 读取 version，再以 `report.update` 的 markdown 或 sections、version、request_id 更新同一份文档；只更新有新结论、实验、重要取舍或可体验成果的相关节，保留用户编辑，冲突保留双方内容。
可体验的阶段成果用 `crew goal report.publish --request-id <稳定标识> --file <报告.json>`（outcome=progress，Codex `crew_goal` 同名 action）发布；同标识重试、不重复发布，任务继续运行。
需要保留可恢复的成果文件时用 `version.capture`，data 提供 request_id、label 和明确的相对 files 清单；只捕获本任务真实成果，不扫描整个工作区。`versions.preview` 先看恢复冲突，`versions.file` 取旧版本字节；恢复必须由用户预览并操作，commit/path 不能替代真实内容。
尚待查证部分明确说明用途和状态；外部事实须实际打开来源并附链接与截至日期。阶段版本关联可取得的实际文件或内容及来源任务，应用版本与任务成果版本分别表达。
阶段报告不结束任务；按完成标准交付，报告写完或到达汇报时间不代表任务完成，最终仍按最终交付规则 finish。不得把模拟验证声称为真实模型或整夜运行验收。
