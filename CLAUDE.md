# Crew

请先读取并遵循仓库根目录的 [AGENTS.md](AGENTS.md)。这是 Codex 与 Claude Code 共用的开发指南。

修复或新增能力时按 AGENTS.md 的“举一反三”要求横向检查同类入口，尤其同时覆盖适用的 CC / Codex 路径，不只修用户当时提到的一边。

当前桌面版本读取 `desktop/package.json`，不要从 Claude 插件版本或旧交付报告推测。

日常修复按 AGENTS.md 的“本地试用与推送”：在 `C:\Tools\Crew-fixes\<任务>` 从 `live` 拉分支修改，验证后用 `scripts\live.ps1 merge` 合入本地 live，用户直接在正式 Crew 里试用；不为单个修复开 PR、跑 CI 或打包。合入 live 不用等用户确认；用户说“推”时再用 `scripts\live.ps1 ship` 整批合进 main。AI 仍需自查 diff、按改动规模验证并遵守其余保护规则。
