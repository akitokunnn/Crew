"""Current-user Explorer entries and their windowless pythonw entry point."""
import argparse
import contextlib
import ctypes
import io
import sys
from pathlib import Path


LABEL = "在这里启动 Crew"
KEYS = (
    r"Software\Classes\Directory\shell\Crew",
    r"Software\Classes\Directory\Background\shell\Crew",
)


def _registry():
    if sys.platform != "win32":
        raise OSError("资源管理器右键菜单只支持 Windows")
    import winreg
    return winreg


def status(registry):
    entries = []
    for path in KEYS:
        try:
            key = registry.OpenKey(registry.HKEY_CURRENT_USER, path, 0, registry.KEY_READ)
        except FileNotFoundError:
            entries.append({"key": path, "installed": False})
            continue
        with key:
            icon = registry.QueryValueEx(key, "Icon")[0]
        with registry.OpenKey(registry.HKEY_CURRENT_USER, path + r"\command", 0, registry.KEY_READ) as key:
            command = registry.QueryValueEx(key, "")[0]
        entries.append({"key": path, "installed": True, "command": command, "icon": icon})
    return {"installed": all(entry["installed"] for entry in entries), "entries": entries}


def configure(action, home, plugin_root, registry=None, executable=None):
    registry = _registry() if registry is None else registry
    if action not in ("install", "status", "uninstall"):
        raise ValueError("右键菜单操作必须是 install / status / uninstall")
    if action == "install":
        plugin_root = Path(plugin_root).resolve()
        entry = plugin_root / "crew" / "menu.py"
        icon = plugin_root / "crew" / "web" / "logo.ico"
        pythonw = Path(sys.executable if executable is None else executable).with_name("pythonw.exe")
        for required in (entry, icon, pythonw):
            if not required.is_file():
                raise FileNotFoundError("右键菜单所需文件不存在: " + str(required))
        # Explorer leaves a trailing backslash on drive roots; the dot keeps it from escaping the closing quote.
        command = '"%s" "%s" --home "%s" --cwd "%%V\\."' % (pythonw, entry, Path(home).resolve())
        for path in KEYS:
            with registry.CreateKeyEx(registry.HKEY_CURRENT_USER, path, 0, registry.KEY_SET_VALUE) as key:
                registry.SetValueEx(key, "", 0, registry.REG_SZ, LABEL)
                registry.SetValueEx(key, "Icon", 0, registry.REG_SZ, '"%s",0' % icon)
            with registry.CreateKeyEx(registry.HKEY_CURRENT_USER, path + r"\command", 0, registry.KEY_SET_VALUE) as key:
                registry.SetValueEx(key, "", 0, registry.REG_SZ, command)
    elif action == "uninstall":
        for entry in status(registry)["entries"]:
            if entry["installed"]:
                registry.DeleteKey(registry.HKEY_CURRENT_USER, entry["key"] + r"\command")
                registry.DeleteKey(registry.HKEY_CURRENT_USER, entry["key"])
    return status(registry)


def describe(result, action):
    if not any(entry["installed"] for entry in result["entries"]):
        return "已移除右键菜单。" if action == "uninstall" else "尚未安装右键菜单。"
    lines = ["右键菜单已安装。" if result["installed"] else "右键菜单未完整安装。"]
    for entry in result["entries"]:
        lines.append(entry["key"] + ("\n命令：" + entry["command"] if entry["installed"] else "：未安装"))
    return "\n".join(lines)


def show_error(message):
    ctypes.windll.user32.MessageBoxW(None, message, "Crew 启动失败", 0x10)


def launch(home, cwd, launcher=None, message_box=show_error):
    try:
        from crew.cli import main
    except ImportError as exc:
        message_box("ImportError: " + str(exc))
        return 1

    launcher = main if launcher is None else launcher
    output, error = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
        code = launcher(["--home", str(home), "start", "--cwd", str(cwd)])
    if code:
        message_box(error.getvalue().strip())
    return code


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    parser = argparse.ArgumentParser(description="Crew 右键菜单内部入口")
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--cwd", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(launch(args.home, args.cwd))
