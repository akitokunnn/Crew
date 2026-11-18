---
name: trust
description: 在全放行（bypassPermissions，不弹权限框）和逐条确认之间切换 Claude Code，VS Code 扩展和命令行一起切；或者报告当前状态。Crew 的守卫黑名单在所有模式下都生效。用户说 /trust、/crew:trust、"全开""全放行""开权限""关权限""权限状态"、trust mode 时使用。
---

# 全放行开关

三种用法：开（"全开""开权限"）、关（"关权限"）、查看（只说 /trust 或"权限状态"，只报告不改；报告后问一句要开还是关）。

## 涉及的设置

| 位置 | 键 | 开（全放行） | 关（逐条确认） |
|---|---|---|---|
| VS Code 用户设置 `settings.json`（VS Code 新会话的启动模式，优先级最高；按当前平台和实际在用的编辑器定位，Windows 标准版在 `%APPDATA%\Code\User\`） | `claudeCode.initialPermissionMode` | `"bypassPermissions"` | `"default"` |
| 同上 | `claudeCode.allowDangerouslySkipPermissions` | `true` | 不动 |
| `~/.claude/settings.json`（命令行和其它入口的默认模式） | `permissions.defaultMode` | `"bypassPermissions"` | `"auto"` |
| 同上 | `permissions.allow` 里的 8 条通配符（见下） | 补齐 | 删掉 |
| 同上 | `skipDangerousModePermissionPrompt` | `true` | 不动 |

8 条通配符供其它模式减少确认；全放行时 allow 不起作用。不要加 `Write(...)`：Claude Code 只认覆盖所有改文件工具的 `Edit(...)`，`Write(**)` 会导致启动时提示无效。

```
"Bash(*)"  "PowerShell(*)"  "WebFetch(*)"  "WebSearch(*)"
"Edit(**)"  "Edit(//**)"  "Read(**)"  "Read(//**)"
```

其它键一律不动，包括 `hooks`、`permissions.deny` / `ask`。守卫在 crew 插件里，黑名单是当前 Crew HOME 下的 `blacklist.txt`（环境变量 CREW_HOME 所指目录，未设置时为 `~/.crew`），都不归这个开关管。

## 步骤

1. 先确认当前平台和实际生效的配置位置，再读这两个设置文件，按上表判断当前状态。VS Code 设置文件不存在时只报告"没找到 VS Code 用户设置"，不据此断定扩展没装，先处理另一个。
2. 开 / 关：只改表里列出的键，其它内容原样保留；写回后再读一遍确认落盘。
3. 自检守卫：黑名单里有适用于 Bash 的自检行时，用 Bash 跑 `echo` 加那一行的词（照原样写进命令，不要拼接或变形）。被拦下才正常；没被拦就报告"守卫失效"，并检查 crew 插件是否启用（`claude plugin list`）。没有可用的自检行就报告"守卫未自检"，不能据此断定失效。
4. 报告（中文，一张表）：VS Code 启动模式、全局默认模式、通配符是否齐全、守卫自检结果、黑名单生效条数（不含注释和自检那条）。
5. 开 / 关之后补一句：新开的会话按新模式启动；已经开着的会话不会自己变，要在会话的模式下拉框里切一次，或者关掉重开。协作中的会话模式要一致，否则会话之间发的消息要对方用户先批准。

## 黑名单

改黑名单先确认是哪个 Crew 实例（同上，取当前 HOME），再直接编辑它的 `blacklist.txt`，格式写在文件头，保存后马上生效。只放"任何时候都不能做、用户点头也不行"的事。
