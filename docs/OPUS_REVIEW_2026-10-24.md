# Claude Opus 5.5 第一轮只读审查原文

此审查完成于每 leader 信任模式与成员载体切换的新需求之前。以下三项 P2 已由主控复核修复，当前修复、后续复审与验收结果见 [0.1.1 交付记录](DESKTOP_0.1.1.md)。原文保留其当时结论。

## 结论：可以有条件上线

没有发现 P1。发现 3 个 P2，都不会让当前这台机器在切换时丢数据。

- **P2-1（工具更新）**：只在用户手动点“更新”时触发，自动更新默认关闭。建议上线前修，至少在上线说明里写明“慢网络下别点更新”。
- **P2-2（VS Code 扫描）**：当前生产库副本已验证能扫出 2 个运行中、9 个历史会话，属于潜伏问题。
- **P2-3（历史文档归组）**：只影响从聊天历史自动索引出来的卡片，用 `crew doc add` 手动登记的不受影响。

主控如果在上线前修改任何源码，`final-integrity.json` 的哈希和三份 EXE 都失效，需要重新打包、重新做 own/reuse 启动验证。

## P2 问题

**P2-1　安装器 180 秒超时后被强杀，与“绝不中途杀安装器”相矛盾**

- `plugin/crew/tool_updates.py:397`：`result = inspector.run(output["command"], timeout=180)`
- `plugin/crew/tool_updates.py:69-73`：超时后执行 `taskkill.exe /PID … /T /F`
- `desktop/src/tool-updates.cjs:59`：`timeout:operation === 'update' ? 240000 : 45000`；同文件 `:167` 的注释写着 “never kill it halfway through”
- **触发**：网络慢或代理慢时，`npm install -g @anthropic-ai/claude-code@latest` 超过 180 秒，会被整树强杀。
- **影响**：npm 在替换全局包时被打断，没有机会回滚，`claude`/`codex` 可能完全不可用，之后 Crew 所有会话都起不来。界面会如实显示“失败”，不会误报成功，但安装已经损坏。另外，如果 Python 辅助进程自己超过 240 秒，Electron 只杀掉 Python；npm 变成孤儿继续跑，而更新锁已经释放，这时再点一次更新就会有两个 npm 并发写。
- **最小改法**：安装命令超时改成 20–30 分钟或者不设超时，Electron 端相应放到更大。超时或失败后重新检查安装，若检测不到版本，就明确提示“安装可能不完整，请执行 `npm i -g …` 修复”。
- **验证**：给 `test_tool_updates` 加一个用例：mock 的 run 模拟长耗时，断言安装阶段不会调用 taskkill；再加一个用例：失败后检测不到版本时给出修复提示。

**P2-3　历史索引会把已有卡片改挂到最后提到它的会话名下**

- `plugin/crew/documents.py:151-155`：`if source != 'manual' and stamp < old['updated_at']: return …` 之后执行 `UPDATE documents SET session_id=?,leader_session_id=?,submitter=?,group_name=?…`
- **触发**：B 组成员的聊天里出现过 `docs/x.md`，已被自动索引。之后 A 组 leader 在聊天里写“参考 docs/x.md”，下一次 refresh 时这张卡片的 submitter、分组、leader 就变成 A 组。
- **影响**：B 组 220px 文档栏里这份文档消失，分组筛选结果错误。
- **最小改法**：如果旧记录本身也是自动索引来的，就不再改 `session_id/leader_session_id/submitter/group_name`，只更新 `kind`/`updated_at`（标题已有逻辑保留旧值）。
- **验证**：在 `test_documents` 里造两个不同组的 journal，先后提到同一路径，断言卡片仍归第一次提到它的组。

**P2-2　任意一个项目或转录文件出现“永久性”失败，所有项目的 VS Code 扫描都会停住**

- `plugin/crew/web.py:133-136`：`for project in projects: roots = vscode_sessions.project_roots(project["root"]) … cache.list(...)`，循环里没有逐项目容错。
- `plugin/crew/vscode_sessions.py:72-73`：Git 返回非 0 时直接 `raise subprocess.CalledProcessError(...)`
- `plugin/crew/vscode_sessions.py:111`：`[json.loads(line) for line in data.splitlines() …]`
- `plugin/crew/vscode_sessions.py:142`：`record["message"]["content"]`，字段缺失会抛 KeyError。
- **触发**：
  - 某个已登记项目的目录被删除，或者是非 Git 目录（`/api/project/init` 只检查 `is_dir`），`git -C` 返回 128。
  - 或者某份转录文件因崩溃在头尾 64KB 窗口里留下一行坏 JSON。
- **影响**：`_refresh_vscode` 每 10 秒失败一次，并且永远失败。正在运行的服务表现为列表停在旧快照；刚启动的新服务（正是本次切换后的状态）没有快照，表现为待收编列表为空、持续报错。现有测试（`test_vscode_service.py:512/541/553`）只覆盖“下一轮就恢复”的临时失败。
- **最小改法**：按项目、按文件分别捕获 `ValueError/KeyError/TypeError/CalledProcessError`。失败的项目沿用它自己的上次结果，失败的文件跳过；错误信息里写明是哪个来源，其余项目照常更新。
- **验证**：加用例：项目 A 的根目录不存在，项目 B 正常，断言 B 的会话照常列出；某个文件中间有坏行，断言同目录下其它会话照常列出。

## 改进建议（不阻塞上线）

1. GET `/api/documents` 每次都在 `write_lock` 加 `BEGIN IMMEDIATE` 下执行 `refresh`（`web.py:876-879`、`documents.py:231`）。前端有 30 秒节流，开多个标签时会周期性占住写锁。建议 GET 只读列表，或者给索引加一个独立的锁。
2. 同一份文件在 worktree 和 merge 后的根目录会被当成两张卡片（`path_key` 是绝对路径）。worktree 删掉后，原卡片显示“文件已不在原位置”。可以考虑按仓库内相对路径合并。
3. `crew web` 和登录自启（`autostart.py:187`，固定 `--port 47811`）不拿 `service.lock`，与桌面版同时冷启动时仍有很小的竞态窗口。
4. `tabs.cjs:33-34`：在页面内跳转到另一个项目时，如果打开失败，被标成错误并隐藏的是**发起跳转的健康标签**。这个标签要重载才能恢复，可能丢掉未发送的草稿。应改为 toast 提示，而不是 `_error(tab)`。
5. `Ctrl+Q` 和托盘“退出”（`main.cjs:119`、`:330-336`）在会话忙时也会直接停掉自己拥有的服务。建议退出前检查有没有忙碌会话并弹确认。
6. npm 安装期间，Crew 投递通知可能新起 Claude 进程，导致 npm 报 EBUSY。可以考虑安装期间暂停后端新起会话。

## 必须保留的 3 条正确性检查

1. **文档登记的不覆盖规则**：`documents.py:143` 历史索引不覆盖手动登记；`UNIQUE(project,path_key)` 防重复建卡；`ORDER BY COALESCE(user_important,ai_important)` 保证用户星标优先；`pin` 只能走 HTTP，AI 没有对应工具。
2. **document_id 的可信访问链**：`web.py:887-913` 用文档自己的 `session_id` 覆盖请求里的值，`resolve()` 之后再按项目根和 worktree 复核一次；`raw=1` 返回带 CSP `sandbox` 头（`web.py:1067`）；POST 必须带 `X-Crew` 头。
3. **后端与更新的防重复、防误报**：`core.cjs:131-136` 只有连接被拒才自起服务，退出码 75 就借用已有服务；`tool_updates.py:386` 在锁内、安装前重新枚举进程，`:404-406` 安装后同时核对版本、prefix 和安装来源，才算“更新成功”。

## 对上线步骤的意见（方案方向正确，需要补这些条件）

1. 旧 0.1.0 必须用托盘或菜单里的“退出 Crew”关闭。点窗口的关闭按钮只会收到托盘（`main.cjs:299`），服务不会停。
2. “idle”的范围要扩大：没有忙碌中的 turn、没有待投递通知，还要没有正在跑的频道摘要或决策简报、信任模式自动续跑、Git CI/fetch，并且没有排队中的 auto-resume（或者先确认它是存在数据库里的）。原因：关停时只关闭 AI 进程的 stdin，Electron 6 秒后强杀 Python（`chat.py:937-946`、`core.cjs:210`），忙碌的子进程会变成孤儿。
3. 备份放在旧进程完全退出之后、0.1.1 第一次连库之前。`store.connect` 会执行 schema 迁移，新增表也在这一步建立。如果坚持退出前备份，要接受这段时间内的写入不在备份里。
4. 端口释放之后，还要确认：没有残留的 `crew_desktop.py`/`web` Python 进程，也没有从旧服务派生出来的 claude/codex 孤儿进程。web.json 留下旧地址没关系，会走“连接被拒”分支重新起服务。
5. 0.1.1 启动后必须确认它是**拥有者**：`owned:true`、web.json 指向 7122、`service.log` 是新写的、10–20 秒内 VS Code 待收编列表非空、文档“重新整理”能成功。如果是 `owned:false`，说明它借用了别的服务，VS Code 修复并没有生效，应停下排查。
6. `--profile ui-011` 之后会长期存在一个问题：从开始菜单直接启动时用的是默认 profile，单实例锁不同，会起第二个实例去借用 7122。如果这时退出拥有者，借用方窗口会全部断开。建议只保留一个使用相同参数的快捷方式，或者确认 0.1.0 退役后改回默认 profile。
7. 回滚：保留 0.1.0 程序；先确认这次 schema 只有新增表或列，0.1.0 可以在迁移后的库上运行，再把回滚写进方案。

## 对 Git 收尾的意见

1. GitHub 不允许 PR 作者批准自己的 PR。如果推送和开 PR 都用用户的账号，规则要求的 1 人批准需要另一个有权限的账号来做，不能用管理员绕过。
2. 打包来源要一致：提交的内容必须和 `final-integrity.json` 里的哈希一致，否则要从提交后的代码重新打包。`git status` 里有 `plugin/git-view-test-5rwkaahl/`，它不在打包清单里，提交时不要用 `git add -A` 把它带进去。
3. 判断“已合并”之前先执行 `git fetch --prune`，和 `origin/main` 比较。如果仓库用 squash 合并，分支不会是 main 的祖先；这时应以“对应 PR 已合并”且“分支末端等于 PR 的 head SHA”为准，否则保留。
4. 工作树“干净”要包括未跟踪和被忽略的产出（`--untracked-files=all`/`--ignored`）。另外先查 Crew 的 `documents` 表里有没有路径在这个 worktree 下的文档：文档不复制，删掉 worktree 就等于删掉原文件或让卡片失效。还要确认没有未归档的 Crew 会话把它登记为 `worktree`，也没有 VS Code 窗口或进程以它为 cwd。
5. 删除远端分支属于对外操作，只删“PR 已合并、且合并后没有新提交”的。`codex/multi-runtime` 和有独有提交的分支一律保留。

## 审查范围说明

这是只读审查（只用了 Read/Grep/Glob），没有运行任何测试或命令，上面没有一条结论来自实际执行。

- **已读**：两份文档的相关章节；`documents.py`、`schema.sql`；`cli.py`/`codex_tools.py`/`web.py` 中文档、文件、VS Code 相关的段落；`documents.js` 和 `chat.js` 的预览部分；`main.cjs`、`tabs.cjs`、`core.cjs`、`preload.cjs`、`tool-updates.cjs`；`crew_desktop.py`、`autostart.py`、`startup.py` 的探测部分；`tool_updates.py`、`vscode_sessions.py`；`final-integrity.json` 和文档 smoke evidence。
- **未细读**：`chrome.cjs`、`home.js`、`home-tools.js`（只看了按钮启用条件）、`index.html` 和 CSS、大部分测试文件、workspaces evidence 的具体内容。

本次使用的模型：Claude Opus 5.5（`claude-opus-5-5`）。
