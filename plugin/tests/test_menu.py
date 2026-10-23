"""Explorer menu tests replace every registry and GUI operation."""
import tests  # Install the production-service connection guard.
import contextlib
import ctypes
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from crew import cli, menu


PLUGIN = Path(__file__).resolve().parents[1]


class FakeKey:
    def __init__(self, registry, path):
        self.registry = registry
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeRegistry:
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.keys = {}
        self.calls = []

    def OpenKey(self, root, path, reserved, access):
        self.calls.append(("read", root, path))
        if path not in self.keys:
            raise FileNotFoundError(path)
        return FakeKey(self, path)

    def CreateKeyEx(self, root, path, reserved, access):
        self.calls.append(("create", root, path))
        self.keys.setdefault(path, {})
        return FakeKey(self, path)

    def SetValueEx(self, key, name, reserved, kind, value):
        self.keys[key.path][name] = value

    def QueryValueEx(self, key, name):
        return self.keys[key.path][name], self.REG_SZ

    def DeleteKey(self, root, path):
        self.calls.append(("delete", root, path))
        del self.keys[path]


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=PLUGIN, prefix="menu-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "home with 中文 & space"
        self.plugin = self.base / "Crew 插件"
        (self.plugin / "crew" / "web").mkdir(parents=True)
        (self.plugin / "crew" / "menu.py").touch()
        (self.plugin / "crew" / "web" / "logo.ico").touch()
        self.python = self.base / "Python Runtime" / "python.exe"
        self.python.parent.mkdir()
        self.python.with_name("pythonw.exe").touch()
        self.registry = FakeRegistry()

    def configure(self, action):
        return menu.configure(action, self.home, self.plugin, registry=self.registry, executable=self.python)

    def test_install_writes_two_hkcu_entries_with_quoted_windowless_percent_v_command(self):
        result = self.configure("install")
        self.assertTrue(result["installed"])
        self.assertEqual((r"Software\Classes\Directory\shell\Crew",
                          r"Software\Classes\Directory\Background\shell\Crew"), menu.KEYS)
        expected = '"%s" "%s" --home "%s" --cwd "%%V\\."' % (
            self.python.with_name("pythonw.exe"), self.plugin / "crew" / "menu.py", self.home)
        for key in menu.KEYS:
            self.assertEqual("在这里启动 Crew", self.registry.keys[key][""])
            self.assertEqual('"%s",0' % (self.plugin / "crew" / "web" / "logo.ico"), self.registry.keys[key]["Icon"])
            self.assertEqual(expected, self.registry.keys[key + r"\command"][""])
        self.assertTrue(all(call[1] == "HKCU" for call in self.registry.calls))
        self.assertFalse(self.home.exists())

    @unittest.skipUnless(sys.platform == "win32", "Windows command-line parsing")
    def test_percent_v_command_quotes_spaces_and_drive_roots(self):
        self.configure("install")
        command = self.registry.keys[menu.KEYS[0] + r"\command"][""]
        parse = ctypes.windll.shell32.CommandLineToArgvW
        parse.argtypes = (ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int))
        parse.restype = ctypes.POINTER(ctypes.c_wchar_p)
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = (ctypes.c_void_p,)
        free.restype = ctypes.c_void_p
        for selected in ("C:\\", str(self.home), str(self.home) + "\\"):
            with self.subTest(selected=selected):
                count = ctypes.c_int()
                parsed = parse(command.replace("%V", selected), ctypes.byref(count))
                try:
                    arguments = [parsed[index] for index in range(count.value)]
                finally:
                    free(parsed)
                self.assertEqual([str(self.python.with_name("pythonw.exe")), str(self.plugin / "crew" / "menu.py"),
                                  "--home", str(self.home), "--cwd", selected + "\\."], arguments)
                self.assertEqual(Path(selected).resolve(), Path(arguments[-1]).resolve())

    def test_uninstall_removes_both_commands_before_their_parent_keys(self):
        self.configure("install")
        result = self.configure("uninstall")
        self.assertFalse(result["installed"])
        self.assertEqual({}, self.registry.keys)
        deleted = [call[2] for call in self.registry.calls if call[0] == "delete"]
        self.assertEqual([path for key in menu.KEYS for path in (key + r"\command", key)], deleted)
        self.assertEqual("已移除右键菜单。", menu.describe(result, "uninstall"))

    def test_status_reports_absent_and_installed_targets_without_writes(self):
        absent = self.configure("status")
        self.assertFalse(absent["installed"])
        self.assertEqual("尚未安装右键菜单。", menu.describe(absent, "status"))
        self.configure("install")
        self.registry.calls.clear()
        result = self.configure("status")
        self.assertTrue(result["installed"])
        self.assertTrue(all(call[0] == "read" for call in self.registry.calls))
        self.assertIn(str(self.plugin / "crew" / "menu.py"), menu.describe(result, "status"))

    def test_partial_install_is_reported_and_uninstall_removes_existing_entry(self):
        self.configure("install")
        del self.registry.keys[menu.KEYS[1] + r"\command"]
        del self.registry.keys[menu.KEYS[1]]
        self.assertIn("未完整安装", menu.describe(self.configure("status"), "status"))
        self.assertFalse(self.configure("uninstall")["installed"])

    def test_missing_pythonw_is_error_before_any_registry_write(self):
        self.python.with_name("pythonw.exe").unlink()
        with self.assertRaisesRegex(FileNotFoundError, "pythonw.exe"):
            self.configure("install")
        self.assertEqual([], self.registry.calls)

    def test_launch_runs_start_for_selected_directory_and_shows_error_message(self):
        def failed(argv):
            self.assertEqual(["--home", str(self.home), "start", "--cwd", str(self.base)], argv)
            print("BoardError: 当前目录不是 Git 仓库", file=menu.sys.stderr)
            return 1

        message_box = Mock()
        self.assertEqual(1, menu.launch(self.home, self.base, failed, message_box))
        message_box.assert_called_once_with("BoardError: 当前目录不是 Git 仓库")

    def test_successful_launch_does_not_open_message_box(self):
        message_box = Mock()
        self.assertEqual(0, menu.launch(self.home, self.base, Mock(return_value=0), message_box))
        message_box.assert_not_called()

    def test_cli_import_error_is_visible_in_message_box(self):
        original_import = __import__

        def missing(name, *args, **kwargs):
            if name == "crew.cli":
                raise ImportError("CLI 文件缺失")
            return original_import(name, *args, **kwargs)

        message_box = Mock()
        with patch("builtins.__import__", side_effect=missing):
            self.assertEqual(1, menu.launch(self.home, self.base, message_box=message_box))
        message_box.assert_called_once_with("ImportError: CLI 文件缺失")

    def test_error_message_uses_windows_error_message_box(self):
        user32 = Mock()
        with patch.object(menu.ctypes, "windll", Mock(user32=user32), create=True):
            menu.show_error("失败原因")
        user32.MessageBoxW.assert_called_once_with(None, "失败原因", "Crew 启动失败", 0x10)

    def test_cli_menu_routes_without_board_connection_or_agent_discovery(self):
        for action in ("install", "status", "uninstall"):
            with self.subTest(action=action), patch.object(menu, "configure", return_value={"installed": False, "entries": []}) as configure:
                with patch.object(cli.store, "connect", side_effect=AssertionError("must not open board")):
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(0, cli.main(["--home", str(self.home), "menu", action],
                                         agents_provider=lambda: self.fail("must not inspect Claude"), plugin_root=self.plugin))
                configure.assert_called_once_with(action, self.home, self.plugin)

    def test_repository_icon_has_multiple_png_sizes(self):
        data = (PLUGIN / "crew" / "web" / "logo.ico").read_bytes()
        self.assertEqual(b"\x00\x00\x01\x00", data[:4])
        self.assertGreaterEqual(int.from_bytes(data[4:6], "little"), 3)
