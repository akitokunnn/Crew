"""Current-user Windows logon task; registration never starts or stops a process.

InteractiveToken keeps the user's network identity without storing a password.
Locking the desktop leaves it running; sleep, hibernation and logoff do not.
The pythonw entry point starts the existing CLI in a hidden console so its own
console children inherit that hidden console rather than opening new windows.
"""
import argparse
import base64
import json
import subprocess
import sys
import traceback
import urllib.error
import urllib.request
from pathlib import Path


TASK_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$name = 'Crew Web (' + $sid + ')'
$scheduler = New-Object -ComObject Schedule.Service
$scheduler.Connect()
$folder = $scheduler.GetFolder('\')
$task = @($folder.GetTasks(1) | Where-Object { $_.Name -eq $name })[0]
if ($request.action -eq 'install') {
    $definition = $scheduler.NewTask(0)
    $definition.RegistrationInfo.Description = 'Crew: current-user logon, hidden web service.'
    $definition.Principal.UserId = $sid
    $definition.Principal.LogonType = 3
    $definition.Principal.RunLevel = 0
    $definition.Settings.MultipleInstances = 2
    $definition.Settings.ExecutionTimeLimit = 'PT0S'
    $definition.Settings.DisallowStartIfOnBatteries = $false
    $definition.Settings.StopIfGoingOnBatteries = $false
    $definition.Settings.AllowHardTerminate = $false
    $trigger = $definition.Triggers.Create(9)
    $trigger.UserId = $sid
    $action = $definition.Actions.Create(0)
    $action.Path = $request.executable
    $action.Arguments = $request.arguments
    $action.WorkingDirectory = $request.root
    $task = $folder.RegisterTaskDefinition($name, $definition, 6, $sid, $null, 3, $null)
} elseif ($request.action -eq 'uninstall' -and $null -ne $task) {
    $folder.DeleteTask($name, 0)
    $task = $null
}
$result = @{installed=($null -ne $task); task_name=$name}
if ($null -ne $task) {
    $action = $task.Definition.Actions.Item(1)
    $result.state = @('unknown','disabled','queued','ready','running')[[int]$task.State]
    $result.root = $action.WorkingDirectory
    $result.command = $action.Path + ' ' + $action.Arguments
    $result.last_result = $task.LastTaskResult
}
$result | ConvertTo-Json -Compress
'''


START_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$name = 'Crew Web (' + $sid + ')'
$scheduler = New-Object -ComObject Schedule.Service
$scheduler.Connect()
$folder = $scheduler.GetFolder('\')
$task = @($folder.GetTasks(1) | Where-Object { $_.Name -eq $name })[0]
if ($null -ne $task) {
    $null = $task.Run($null)
}
@{installed=($null -ne $task); task_name=$name} | ConvertTo-Json -Compress
'''


def _windows():
    if sys.platform != "win32":
        raise OSError("登录自启只支持 Windows")


def _hidden_console():
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    return {"startupinfo": startup, "creationflags": subprocess.CREATE_NEW_CONSOLE}


def configure(action, home, plugin_root, runner=subprocess.run, executable=None):
    """Operate only this user's task, without a board connection."""
    _windows()
    if action not in ("install", "status", "uninstall", "start"):
        raise ValueError("登录自启操作必须是 install / status / uninstall / start")
    request = {"action": action}
    if action == "install":
        common = runner(["git", "-C", str(plugin_root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                        check=True, capture_output=True, text=True, encoding="utf-8")
        root = Path(common.stdout.strip()).resolve().parent
        entry = root / "plugin" / "crew" / "autostart.py"
        for required in (entry, root / "plugin" / "crew_cli.py"):
            if not required.is_file():
                raise FileNotFoundError("主检出尚无自启入口，请先合入主检出: " + str(required))
        pythonw = Path(sys.executable if executable is None else executable).with_name("pythonw.exe")
        if not pythonw.is_file():
            raise FileNotFoundError("找不到隐藏启动解释器: " + str(pythonw))
        request.update(executable=str(pythonw), root=str(root),
                       arguments=subprocess.list2cmdline([str(entry), "--home", str(Path(home).resolve())]))
    script = START_SCRIPT if action == "start" else TASK_SCRIPT
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    # CREATE_NO_WINDOW already hides this host. -WindowStyle requires a console
    # window and can abort Windows PowerShell before it executes the script.
    result = runner(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                     "-EncodedCommand", encoded], input=json.dumps(request, ensure_ascii=False),
                    capture_output=True, text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise OSError("登录自启任务操作失败（退出码 %s）: %s" %
                      (result.returncode, result.stderr.strip() or result.stdout.strip() or "PowerShell 未返回错误输出"))
    return json.loads(result.stdout)


def describe(result, action):
    if not result["installed"]:
        return "已取消登录自启；不会停止当前服务。" if action == "uninstall" else "尚未安装登录自启。"
    states = {"unknown": "未知", "disabled": "已禁用", "queued": "排队中", "ready": "就绪", "running": "运行中"}
    lines = ["登录自启已安装；任务：" + result["task_name"],
             "状态：%s；最近结果：%s" % (states[result["state"]], result["last_result"]),
             "工作目录：" + result["root"], "命令：" + result["command"],
             "下次登录后隐藏启动，不会立即启动或重启服务。锁屏继续运行，睡眠、休眠或注销期间不能接收消息。"]
    return "\n".join(lines)


def _existing_server(timeout=3):
    # A manually started Crew may already own the default port at logon.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://127.0.0.1:47811/api/state", timeout=timeout) as response:
            state = json.load(response)
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ConnectionRefusedError):
            return False
        raise OSError("无法确认默认端口上的服务，不重复启动: " + str(exc)) from exc
    if not isinstance(state, dict) or not all(key in state for key in ("projects", "leaders", "candidates")):
        raise OSError("47811 端口已被其他服务占用，不启动 Crew")
    return True


def run(home, root=None, process_factory=subprocess.Popen, executable=None, probe=_existing_server):
    """Task entry point: wait for one hidden CLI process, without restart/kill."""
    _windows()
    home = Path(home).resolve()
    root = Path(__file__).resolve().parents[2] if root is None else Path(root).resolve()
    python = Path(sys.executable if executable is None else executable).with_name("python.exe")
    command = [str(python), "-u", str(root / "plugin" / "crew_cli.py"), "--home", str(home), "web", "--port", "47811"]
    home.mkdir(parents=True, exist_ok=True)
    with (home / "web-autostart.log").open("a", encoding="utf-8") as log:
        try:
            if probe():
                log.write("默认端口已有 Crew 网页服务，保留原服务，不重复启动。\n")
                return 0
            child = process_factory(command, cwd=str(root), stdin=subprocess.DEVNULL, stdout=log,
                                    stderr=subprocess.STDOUT, **_hidden_console())
            return child.wait()
        except Exception:
            traceback.print_exc(file=log)
            return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crew 登录自启内部入口")
    parser.add_argument("--home", type=Path, required=True)
    raise SystemExit(run(parser.parse_args().home))
