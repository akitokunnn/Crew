"""Open a workspace's Crew page through the installed web-service task."""
import contextlib
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

from . import autostart, board, git_process, store


def git_root(cwd, runner=subprocess.run):
    """A plain workspace needs no Git executable; repository markers require a valid Git root."""
    if git_process.repository_root(cwd) is None:
        return Path(cwd).resolve()
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    result = runner(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
                    capture_output=True, text=True, encoding="utf-8", **options)
    if result.returncode:
        raise board.BoardError("无法找到 Git 仓库根目录: " + result.stderr.strip())
    return Path(result.stdout.strip()).resolve()


def start(home, plugin_root, cwd, name=None, *, root_provider=git_root,
          probe=None, task=autostart.configure,
          open_browser=webbrowser.open, clock=time.monotonic, sleep=time.sleep):
    if probe is None:
        probe = lambda timeout=3: autostart._existing_server(timeout=timeout, home=home)

    root = root_provider(Path(cwd).resolve())
    with contextlib.closing(store.connect(home)) as conn:
        project = next((p for p in board.projects(conn) if store.norm_path(p["root"]) == store.norm_path(root)), None)
        if project is None and not (root / '.git').exists():
            project = board.project_for_path(conn, root)
        if project is None:
            project = board.init_project(conn, root.name if name is None else name, root)
    if not probe():
        result = task("start", home, plugin_root)
        if not result["installed"]:
            command = subprocess.list2cmdline(["crew", "--home", str(Path(home).resolve()), "autostart", "install"])
            raise board.BoardError("尚未安装登录自启任务，请先运行：" + command)
        deadline = clock() + 20
        while True:
            remaining = deadline - clock()
            if remaining <= 0:
                raise board.BoardError("等待 Crew 网页服务启动超时（20 秒），请检查自启任务和 web-autostart.log")
            if probe(timeout=min(3, remaining)):
                break
            delay = min(0.2, deadline - clock())
            if delay > 0:
                sleep(delay)
    url = autostart.service_url(home) + "/?" + urlencode({"project": project["name"]})
    try:
        opened = open_browser(url)
    except webbrowser.Error as exc:
        raise board.BoardError("无法打开浏览器: " + str(exc)) from exc
    if not opened:
        raise board.BoardError("无法打开浏览器，请手动打开：" + url)
    return {"name": project["name"], "url": url}
