# Crew Mac 安装与试用

## 安装前准备

Crew 适用于 Apple 芯片 Mac。本机后台需要 Python 3.11 或更新版本。用终端装好工具，并确认各条命令都能返回版本。

```sh
xcode-select --install
brew install python node
python3 --version
node --version
git --version
claude --version
codex --version
```

先装 [Homebrew](https://brew.sh/)，并按安装提示把 `brew shellenv` 加入登录 shell 配置。需要在本机运行 AI 会话时，按各自的安装说明安装并登录 Claude Code 或 Codex。[Claude Code](https://code.claude.com/docs/en/setup) · [Codex CLI](https://developers.openai.com/codex/cli/)

Crew 从 Finder 或程序坞打开时也会读取登录 shell 的 PATH。若命令只在终端中可用，检查 `~/.zprofile`、`~/.zshrc`，在终端用当前登录 shell 的 `-ilc` 模式确认可以找到工具，然后退出并重新打开 Crew。

## 在 macOS 15 上首次打开

1. 下载 `Crew-<版本>-mac-arm64.dmg`，打开后把 Crew 拖进「应用程序」。
2. 在「应用程序」里双击 Crew 一次。macOS 会拦下，关闭提示即可。
3. 打开「系统设置 → 隐私与安全性」，在「安全性」一栏点「仍要打开」，按提示确认。之后可正常双击 Crew。[Apple 官方打开步骤](https://support.apple.com/zh-cn/102445)

如果提示「Crew 已损坏，无法打开」，确认下载的是可信的 Crew 安装包后，在终端执行以下命令，再从「应用程序」打开。若仍提示损坏，请重新下载安装包。

```sh
xattr -dr com.apple.quarantine /Applications/Crew.app
```

## 连接局域网主机

1. 在主机的「设置 → 局域网访问」里选择客户端能访问的网口，开启访问，复制显示的地址并生成配对码。
2. 在 Mac 的「设置 → 连接与目录」里选择「局域网主机」，添加设备，填主机地址、配对码和本机设备名。
3. 选中已保存的主机并应用。Crew 重开后显示该主机的工作区。使用主机的文件和工具时，在界面中选择的是主机上的路径。

## 第一版范围

Crew 更新通过下载新的 dmg 安装。工具页提供 Claude Code 和 Codex 的版本检查，工具更新沿用原安装方式。开机自启、Finder 右键菜单、锁屏自动转发通知、局域网自动发现和 Intel Mac 支持暂未提供。[用户裁决与完整范围](decisions-mac-remote.md#第一版不做)

## 在自己的 Mac 上试用

| 检查项 | 怎么做 | 期望看到什么 |
| --- | --- | --- |
| 启动与 PATH | 在终端运行 `command -v python3 node git claude codex`，再从 Finder 双击 Crew。打开「工具与更新」检查版本，在临时 Git 工作区打开 Git 页。 | 本机后台启动，两个载体的版本可查，Git 页能读取同一个仓库。 |
| Python 版本 | 在「连接与目录」里用隔离数据目录检查一个 3.10 解释器，再检查一个不存在的路径，最后用 3.11+ 检查。也可用隔离参数启动，例如 `/Applications/Crew.app/Contents/MacOS/Crew --home "$(mktemp -d)" --port 0 --isolated-sources --python /不存在的/python3`。 | 版本过低时显示版本，无法执行时显示原因，两种提示都说明可用 `brew install python`。3.11+ 允许继续。 |
| 红绿灯与菜单 | 查看窗口左上角及屏幕顶部菜单。尝试最小化、缩放并恢复，点击 Crew 标题栏按钮。 | 红绿灯在 36 px 标题栏内居中，未遮住 Crew 按钮。菜单首项是 Crew，并有编辑和窗口菜单。 |
| 编辑与导航快捷键 | 在输入框试 `⌘A`、`⌘C`、`⌘V`、`⌘X`、`⌘Z`。用 `⌘T` 打开工作区首页，在有历史的页面试 `⌘[`。 | 输入框可全选、复制、粘贴、剪切和撤销，快捷键提示显示 `⌘T` 和 `⌘[`，导航与提示一致。 |
| 程序坞恢复 | 点红灯关窗，再点程序坞的 Crew 图标。 | 同一个窗口恢复并得到焦点，原工作区标签仍在。 |
| 菜单栏图标 | 在浅色和深色外观分别查看系统菜单栏右侧并点开 Crew 图标。 | 单色圆环与圆点在两种背景都可辨认，图标可打开 Crew 菜单。 |
| 更新入口 | 打开「工具与更新」，点击「检查更新」。 | Crew 显示 dmg 更新说明。载体显示已安装版本和可查询的新版本，更新和自动更新控件隐藏。 |
| Claude Code 和 hook | 选本机后台，在测试工作区启动 Claude Code 会话。让它读取 Crew 注入的会话身份，再尝试工作区守卫中已列入黑名单的无害测试命令。 | 会话收到正确的 Crew 身份，黑名单命令被守卫拦下。 |
| Codex 会话 | 在测试工作区启动 Codex，发一条简短消息，再试从 Crew 停止会话。 | 能收到对应会话的回复，停止后状态正常回到空闲或已停止。 |
| 文件和 Finder | 在本机工作区选一个测试文件，分别使用「打开」和「在文件夹中显示」。 | 文件由系统默认应用打开，Finder 打开对应目录并选中文件。 |
| 网口列表 | 打开本机「设置 → 局域网访问」，与 macOS 网络设置或 `ifconfig` 对照。 | 每个候选地址旁显示对应实际网口名称。 |
| Mac 作主机 | 在 Mac 开启局域网访问并生成码，在 Windows 的「连接与目录」配对、应用并重开客户端。 | Windows 显示 Mac 的工作区，Mac 的授权设备列表显示 Windows 桌面客户端。 |
| Mac 作客户端 | 在 Windows 主机生成配对码，按上面的连接步骤在 Mac 配对并打开一个工作区。 | Mac 显示 Windows 工作区，选目录时看到的是 Windows 主机文件夹。 |
| 退出再打开 | 在本机后台模式用 `⌘Q` 完全退出 Crew，马上从程序坞重新打开。 | 后端正常退出并重新启动，首页加载，未报端口占用。 |
