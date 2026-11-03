# Crew 组织架构交互方案

2026-11-04。三种展示共用同一组样例，帮助比较一个 leader 多任务时的成员归属表达。所有人物、任务、分工与对话内容均为样例。

## 打开与比较

直接用 Edge 或 Chrome 打开本目录的 [index.html](index.html)，无需安装依赖或启动服务。顶部可切换三个版本，负责人下拉可以查看第二组与空组。

| 方案 | 首先回答的问题 | 可以操作的内容 |
| --- | --- | --- |
| 任务分组 | 每个任务谁在做什么 | 展开或收起分工，点击任务、成员，查看跨任务关系 |
| 成员矩阵 | 每个人关联哪些任务 | 按行对照分工，点击交叉格查看指定任务中的成员，窄屏固定人员列并局部横向滚动 |
| 关系图谱 | leader、任务、成员如何连接 | 展开分支，点击成员后关闭详情，同一个人在所有任务中一起高亮 |

三个版本都能搜索任务名称、编号或成员，筛选任务状态，开关历史参与，进入成员详情、任务对话预览与总会话预览。任务内对话显式保留任务编号，总会话的任务作用域为 `null`。没有执行记录的分工显示“已分配 · 未启动”，对话预览显示尚无执行记录。

桌面任务分组采用双列成员，1100px 以下改为单列。浅色与深色跟随系统，减少动态效果跟随系统偏好。浏览器只保存本原型的版本、负责人和筛选偏好，键名前缀为 `crew-org-task-lab-`。

## 样例覆盖

- 林序组包含 6 位成员、4 项当前任务与 1 项已完成任务。知衡和听澜分别关联两项当前任务。
- 林序统筹所有本组任务，同时在组织架构任务中亲自执行。
- 任务有推进中、等你答复、已暂停、排队中和已完成状态。人员执行状态独立显示。
- 青禾当前待命，砚川只有历史参与。时雨在原任务中的分工已经交接。
- 时雨的历史执行使用 Codex，当前总会话使用 CC。任务节点、交叉格和任务对话使用执行记录的载体，矩阵行头保留人员当前载体。
- 见川组混合 CC 与 Codex，序白已分配但尚未产生执行记录。守望组没有成员和任务。
- 任务名称包含长中文，样例执行包含旧要求、旧轮次和失败后新一代执行。

## 后端盘点与接入依据

源码基线为 `rebuild/v2` 的 `d379671`，桌面 `package.json` 为 `0.3.7`。产品分支约定要求新版工作合入 `rebuild/v2`。

现有后端已经提供多对多关系。`goals.get()` 与 `goal_api.card()` 返回 `assignments`，工作区快照的 `goals` 已包含这些分工。任务详情提供 `executions` 与 `work_items`。目前正式组织图的 `renderRight()` / `memberNode()` 只展示 leader 与直属成员及全局任务文案，缺少按任务呈现分工。

本演示的 `data.js` 模拟原生人员、目标、分工和执行字段，`model.js` 生成三个视图共用的投影。

1. 当前执行按 `goal_id`、`session_id`、本轮 `round`、本要求版本 `requirement_version` 匹配，再选最新 `generation`。
2. `released` 分工以及 `completed / failed / cancelled` 任务属于历史参与，默认隐藏。
3. leader 默认 assignment 表达任务负责关系。图中的任务节点始终标记其统筹身份，有独立执行记录时再进入执行人员列表。
4. `active_goal_ids` 可以包含暂停或等待任务。当前关联数按分工计算，执行状态从独立执行记录投影。
5. 真实接入时，工作区快照可直接提供分工关系。精确执行状态需要组合任务详情中的执行记录，或给快照补充同一投影。

该交付覆盖独立设计演示。真实后台接口接入、运行状态刷新、正式桌面内嵌与原生 AI 操作仍需在选定展示方式后验收。

## 验证

```powershell
node --test prototypes/org-task-lab/model.test.cjs
node --check prototypes/org-task-lab/app.js
node --check prototypes/org-task-lab/group-views.js
node --check prototypes/org-task-lab/graph-view.js
```

7 项模型测试覆盖多任务独立分工、当前要求与最新执行、交接历史、跨组隔离、总会话作用域、真实状态与终态以及 leader 统筹与执行的区分。

浏览器验证使用 Playwright 和本机 Edge。测试依赖可装在忽略的临时目录，演示页面本身没有第三方依赖。

```powershell
npm install --prefix Temp/org-qa --no-save --no-package-lock playwright
$env:NODE_PATH = Join-Path (Get-Location) 'Temp/org-qa/node_modules'
Remove-Item Env:CREW_SESSION_ID -ErrorAction SilentlyContinue
Remove-Item Env:CLAUDE_CODE_SESSION_ID -ErrorAction SilentlyContinue
node prototypes/org-task-lab/smoke.cjs
```

实际结果为 9 条浏览器旅程通过，控制台错误 0。每个版本均在 1440、768、390px 验证多任务详情、任务与总会话切换、历史开关、搜索、状态筛选、空组与无执行记录。分组和图谱另验证折叠。深色与减少动态效果下也已截图。

截图与逐项结果位于仓库的 `Temp/org-qa/browser/`。本轮使用隔离浏览器上下文和静态文件，样例页面的 CSP 禁止网络连接。

设计采用 Achromatic ledger 的中性色界面，保留 Crew 字体、标志与暖灰灰绿配色，抽签 seed 为 `342562427`。主色用于关系高亮与执行状态。没有重抽。首轮截图后将桌面分工改为双列，解决首个任务占满首屏的问题。自动界面检查为 0 FAIL，保留的警告分别对应紧凑元数据字号，以及检查器把组织视图识别成需要图表主色的图表。主任务、人员、工作内容和执行状态均有文字表达。

## 对话内预览

`build-preview.cjs` 可将同一份源码生成三个完整可交互方案的对话内预览。输出路径必须为绝对路径。

```powershell
node prototypes/org-task-lab/build-preview.cjs C:/path/to/crew-organization.html
```

预览使用宿主提供的版本轮播。每个版本有独立状态和固定高度的滚动区域，切换方案时保持操作状态。预览文件通过数据 URI 内嵌 Crew 标志与全部样式、脚本。
