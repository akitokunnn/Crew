---
name: trust
description: 在全放行（bypassPermissions，不弹权限框）和逐条确认之间切换 Claude Code，VS Code 扩展和命令行一起切；或者报告当前状态。Crew 的守卫黑名单在所有模式下都生效。用户说 /trust、/crew:trust、"全开""全放行""开权限""关权限""权限状态"、trust mode 时使用。
---

# 全放行开关

三种用法：开（"全开""开权限"）、关（"关权限"）、查看（只说 /trust 或"权限状态"，只报告不改；报告后问一句要开还是关）。

## 涉及的设置

| 位置 | 键 | 开（全放行） | 关（逐条确认） |
|---|---|---|---|
| `%APPDATA%\Code\User\settings.json`（VS Code 新会话的启动模式，优先级最高） | `claudeCode.initialPermissionMode` | `"bypassPermissions"` | `"default"` |
| 同上 | `claudeCode.allowDangerouslySkipPermissions` | `true` | 不动 |
| `~/.claude/settings.json`（命令行和其它入口的默认模式） | `permissions.defaultMode` | `"bypassPermissions"` | `"auto"` |
| 同上 | `permissions.allow` 里的 8 条通配符（见下） | 补齐 | 删掉 |
| 同上 | `skipDangerousModePermissionPrompt` | `true` | 不动 |

8 条通配符供其它模式减少确认；全放行时 allow 不起作用。不要加 `Write(...)`：Claude Code 只认覆盖所有改文件工具的 `Edit(...)`，`Write(**)` 会导致启动时提示无效。

```
"Bash(*)"  "PowerShell(*)"  "WebFetch(*)"  "WebSearch(*)"
"Edit(**)"  "Edit(//**)"  "Read(**)"  "Read(//**)"
```

其它键一律不动，包括 `hooks`、`permissions.deny` / `ask`。守卫在 crew 插件里，黑名单在 `~/.crew/blacklist.txt`，都不归这个开关管。

## 步骤

1. 读两个设置文件（VS Code 的不存在就报告"没装 VS Code 扩展"，只处理另一个），按上表判断当前状态。
2. 开 / 关：只改表里列出的键，其它内容原样保留；写回后再读一遍确认落盘。
3. 自检守卫：用 Bash 跑 `echo` 加 `~/.crew/blacklist.txt` 里自检那一行的词（照原样写进命令，不要拼接或变形）。被拦下才正常；没被拦就报告"守卫失效"，并检查 crew 插件是否启用（`claude plugin list`）。
4. 报告（中文，一张表）：VS Code 启动模式、全局默认模式、通配符是否齐全、守卫自检结果、黑名单生效条数（不含注释和自检那条）。
5. 开 / 关之后补一句：新开的会话按新模式启动；已经开着的会话不会自己变，要在会话的模式下拉框里切一次，或者关掉重开。协作中的会话模式要一致，否则会话之间发的消息要对方用户先批准。

## 黑名单

改黑名单直接编辑 `~/.crew/blacklist.txt`，格式写在文件头，保存后马上生效。只放"任何时候都不能做、用户点头也不行"的事。
