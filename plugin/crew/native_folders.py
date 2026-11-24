"""Open a local report folder in Windows Explorer."""

import base64
import subprocess


_OPEN_IN_EXPLORER = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
trap { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
$path = [IO.Path]::GetFullPath([Console]::In.ReadToEnd())
$folder = $path.TrimEnd('\')
Add-Type -Namespace Crew -Name ExplorerWindow -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern System.IntPtr GetForegroundWindow();
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern bool SetForegroundWindow(System.IntPtr window);
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern bool IsIconic(System.IntPtr window);
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern bool ShowWindowAsync(System.IntPtr window, int command);
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern int GetWindowLongW(System.IntPtr window, int index);
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern bool SetWindowPos(System.IntPtr window, System.IntPtr after, int x, int y, int w, int h, uint flags);
'@
$shell = New-Object -ComObject Shell.Application
$previous = [Crew.ExplorerWindow]::GetForegroundWindow()
Invoke-Item -LiteralPath $path
$timer = [Diagnostics.Stopwatch]::StartNew()
do {
    foreach ($window in $shell.Windows()) {
        try {
            $url = [Uri]$window.LocationURL
            if (!$url.IsFile -or $url.LocalPath.TrimEnd('\') -ne $folder) { continue }
            $handle = [IntPtr][long]$window.HWND
        } catch { continue }  # An Explorer window can close while the list is being read.
        $foreground = [Crew.ExplorerWindow]::GetForegroundWindow()
        if ($foreground -eq $handle -or $foreground -ne $previous) { return }
        if ([Crew.ExplorerWindow]::IsIconic($handle)) {
            [void][Crew.ExplorerWindow]::ShowWindowAsync($handle, 9)  # SW_RESTORE
        }
        if ([Crew.ExplorerWindow]::SetForegroundWindow($handle)) { return }
        # If Windows denies activation from the backend, put Explorer ahead of Crew
        # without changing keyboard focus or leaving Explorer always on top.
        if (!([Crew.ExplorerWindow]::GetWindowLongW($handle, -20) -band 8)) {  # WS_EX_TOPMOST
            $noMoveSizeOrActivate = 0x0013
            if ([Crew.ExplorerWindow]::SetWindowPos($handle, [IntPtr](-1), 0, 0, 0, 0, $noMoveSizeOrActivate)) {
                [void][Crew.ExplorerWindow]::SetWindowPos($handle, [IntPtr](-2), 0, 0, 0, 0, $noMoveSizeOrActivate)
            }
        }
        return
    }
    Start-Sleep -Milliseconds 50
} while ($timer.ElapsedMilliseconds -lt 2000)
"""


def open_windows(path):
    command = base64.b64encode(_OPEN_IN_EXPLORER.encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", command],
            input=path, capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW, timeout=10,
        )
    except subprocess.TimeoutExpired as exc:
        raise OSError("等待资源管理器打开文件夹超时") from exc
    if result.returncode:
        raise OSError("打开文件夹失败：" + (result.stderr.strip() or str(result.returncode)))
