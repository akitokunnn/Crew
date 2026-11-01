# Crew 报告截图预览

决定日期：2026-11-02。对应 `rebuild/v2` 的 0.3.5 迭代；版本号不代表已安装或正在运行的后台已更新。

## 问题与决定

交付报告是 Crew 自己保存和展示的自包含 HTML。外部浏览器工具返回“访问宿主不可用”时，报告可能已经写好，但 AI 无法取得截图核对阅读效果。报告预览因此由已经连接的 Crew 桌面提供，共用现有 Electron 运行环境；普通网页浏览和产品交互测试继续使用相应浏览器工具。

目标动作 `report.preview` 接受草稿正文或已保存报告编号。后台校验真实 Crew 身份、项目、任务归属和执行作用域，再交给桌面渲染。桌面只取得待预览的报告正文与固定视口，返回 PNG 和控制台错误；后台保存截图并返回绝对路径。预览不发布报告、不改变任务状态、不启动模型，也不自动给出视觉通过结论。

桌面视口为 1280 × 900，手机视口为 390 × 844。每次截取当前视口；长报告的首屏截图不能证明所有滚动内容都已检查。

## CC 与 Codex 的同一入口

CC 先保存 UTF-8 自包含 HTML，再请求截图：

```powershell
crew goal report.preview <目标ID> --html-file <报告.html> --data '{"viewport":"desktop"}'
crew goal report.preview <目标ID> --html-file <报告.html> --data '{"viewport":"mobile"}'
```

命令返回 `preview.path` 的 PNG 绝对路径；CC 必须用 Read 查看图片，然后判断字体、换行、遮挡和阅读顺序。目标执行内可以省略目标编号，使用宿主绑定的 `CREW_GOAL_ID`；总对话必须明确目标。复杂 JSON 可写 UTF-8 文件并用 `--file` 代替 `--data`。

Codex 使用宿主动态工具 `crew_goal`：

```json
{
  "action": "report.preview",
  "goal_id": "<目标ID>",
  "data": {
    "document_html": "<!doctype html><meta charset=\"utf-8\"><h1>报告草稿</h1>",
    "viewport": "mobile"
  }
}
```

成功响应先返回 `inputText` 元数据，再附 `inputImage` 原生图片，`imageUrl` 使用 PNG data URI。Codex 需实际查看该图后再判断。图片附件读取失败时保留已成功截图的回执和 `input_image_error`，按 `preview.path` 用原生 `view_image` 打开，不重新执行已经成功的截图。

预览已保存的报告时，CC 的 `--data` 或 Codex 的 `data` 使用 `report_id` 代替 `document_html`。同一请求只选一种来源。leader 与分配到任务的成员共用入口，总对话和目标执行沿用各自的真实身份与目标权限；不能传另一个人的身份或越过项目边界。

## 返回与限制

成功结果包含 `ok: true` 和 `preview`：截图编号、PNG 绝对路径、实际宽高、`console_errors`，以及表示截图完成的状态。`visual_verification: not_performed` 明确表示程序只完成采集；AI 看图后的判断应另写入验收证据。控制台错误原样保留，不能只看截图成功就忽略错误。

HTML 上限为 512 KiB，CSS/JS 内联，图片嵌入 data URI。截图保存在当前 HOME 的 `report-previews/`，单张 PNG 上限为 8 MiB。后台验证 PNG 格式、完整性和视口尺寸。预览窗口与普通用户数据、登录状态及本机文件访问隔离，不接受任意 URL 或路径作为待浏览内容。

CLI 和 Codex 都需要正在运行的后台与已连接的桌面预览宿主。`report.preview` 按只读权限检查任务，即使任务已交付或暂停，也不把预览当作恢复执行；它始终走宿主，不在 CLI 或原生工具里离线启动浏览器。桌面退出、租约失效、超时或渲染失败时保留真实原因，报告视觉检查记为未验证。外部工具明确拒绝访问的内容，不能改用这条入口绕过拒绝。

截图只能帮助核对阅读排版。按钮、折叠、筛选、排序等行为需要另行交互测试；截图里出现控件不等于已经操作并验证。报告预览失败也不自动推翻已取得的代码、Git 或业务验收证据。

## 协议核对与验收

原生图片字段由本机 Codex CLI 0.165.0 执行 `codex app-server generate-json-schema --experimental --out <临时目录>` 生成的 `DynamicToolCallResponse.json` 核实：允许 `{ "type": "inputImage", "imageUrl": "..." }`。此操作只生成协议文件，没有启动模型轮次。

验证只使用新建的合成报告、隔离 HOME 与假载体：CLI 和 Codex 工具验证同一动作的载荷、身份、真实失败和图片返回；桌面验收再通过真实 Electron 渲染核对 PNG、固定视口、控制台错误及关闭宿主后的失败。不能用接口单测代替渲染验收，也不能用截图生成代替人工视觉或产品交互判断。
