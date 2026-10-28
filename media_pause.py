"""暂停后台正在播放的音频 / 视频 (指令 V 被领取时用, MECHANICS 22.2).

走 Windows 的系统媒体控制 (SMTC, 浏览器 / 播放器 / 视频软件都会在这里登记): 把每个正在播放的会话暂停,
没有在播放的不碰 (所以不会像"播放 / 暂停"键那样把停着的变成播放). 暂停是一次性的, 之后不会替用户恢复.
在后台线程里调用 PowerShell, 不卡界面; 系统太旧 (没有 SMTC) 或出错就什么也不做.
"""

import subprocess
import sys
import threading

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $task.Wait(-1) | Out-Null
    $task.Result
}
$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
$mgr = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
$paused = 0
foreach ($s in $mgr.GetSessions()) {
    if ($s.GetPlaybackInfo().PlaybackStatus -eq 4) {      # 4 = Playing
        $null = Await ($s.TryPauseAsync()) ([bool])
        $paused++
    }
}
Write-Output $paused
"""


def pause_all_blocking(timeout: float = 20.0) -> int:
    """暂停所有正在播放的媒体会话, 返回暂停了几个 (失败返回 -1)."""
    if sys.platform != "win32":
        return -1
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _SCRIPT],
            capture_output=True, text=True, timeout=timeout, creationflags=flags)
        if out.returncode != 0:
            return -1
        return int((out.stdout or "0").strip().splitlines()[-1])
    except Exception:       # noqa: BLE001
        return -1


def pause_all():
    """后台线程里暂停, 立刻返回."""
    threading.Thread(target=pause_all_blocking, daemon=True).start()


if __name__ == "__main__":
    print("paused:", pause_all_blocking())
