"""Windows desktop services, imported lazily and never simulated on other systems.

All mutating functions are explicit calls. Importing this module does not change
volume, startup registration, session state or power state.
"""

from __future__ import annotations

import ctypes
import math
import ntpath
import os
import subprocess
import sys
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

STARTUP_VALUE = "BellPlan"
STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"


def _require_windows():
    if sys.platform != "win32":
        raise OSError("此操作仅支持 Windows 10/11")


def _boolean(value, label):
    if type(value) is not bool:
        raise ValueError(f"{label}必须为布尔值")
    return value


def _volume_value(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("系统音量必须为 0–100 的数字")
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError("系统音量必须为 0–100 的数字")
    return float(value)


@contextmanager
def _audio_device():
    """Balance COM for the calling thread and obtain the default render endpoint."""
    _require_windows()
    try:
        import comtypes
        from pycaw.pycaw import AudioUtilities
    except ImportError as exc:
        raise OSError("系统音量组件不可用，请安装 Windows 依赖 pycaw") from exc
    initialized = False
    try:
        comtypes.CoInitialize()
        initialized = True
        device = AudioUtilities.GetSpeakers()
        if device is None:
            raise OSError("未找到默认音频输出设备")
        yield device
    except OSError:
        raise
    except Exception as exc:
        raise OSError(f"无法访问系统音量：{exc}") from exc
    finally:
        if initialized:
            comtypes.CoUninitialize()


def _volume_snapshot(device):
    endpoint = device.EndpointVolume
    return {
        "volume": round(float(endpoint.GetMasterVolumeLevelScalar()) * 100, 2),
        "muted": bool(endpoint.GetMute()),
        "device": str(device.FriendlyName),
    }


def get_system_volume():
    """Read the actual default device master volume and mute state."""
    with _audio_device() as device:
        return _volume_snapshot(device)


def set_system_volume(value):
    """Set master volume, preserving mute state; return the actual readback."""
    scalar = _volume_value(value) / 100
    with _audio_device() as device:
        device.EndpointVolume.SetMasterVolumeLevelScalar(scalar, None)
        return _volume_snapshot(device)


def set_system_mute(muted):
    """Change the default device mute state and return the actual readback."""
    muted = _boolean(muted, "静音状态")
    with _audio_device() as device:
        device.EndpointVolume.SetMute(int(muted), None)
        return _volume_snapshot(device)


def _registry():
    _require_windows()
    import winreg

    return winreg


def startup_command():
    """The current executable or installed Python module; no shell expansion."""
    executable = sys.executable
    if not executable:
        raise OSError("无法确定当前程序路径")
    if not (os.path.isabs(executable) or ntpath.isabs(executable)):
        executable = os.path.abspath(executable)
    arguments = [executable]
    if not getattr(sys, "frozen", False):
        arguments.extend(["-m", "bellplan"])
    return subprocess.list2cmdline(arguments)


def get_start_at_login():
    """Whether this installation is registered in the current user's Run key."""
    registry = _registry()
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, STARTUP_KEY, 0, registry.KEY_READ) as key:
            value, kind = registry.QueryValueEx(key, STARTUP_VALUE)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise OSError(f"无法读取登录自启动设置：{exc}") from exc
    return kind in (registry.REG_SZ, registry.REG_EXPAND_SZ) and value == startup_command()


def _startup_approval(registry):
    """Read known StartupApproved layouts without ever writing approval state.

    This registry format is not a stable Windows public API. Only recognized
    12-byte layouts are interpreted; every other form stays unknown.
    """
    try:
        with registry.OpenKey(
            registry.HKEY_CURRENT_USER, STARTUP_APPROVED_KEY, 0, registry.KEY_READ
        ) as key:
            value, kind = registry.QueryValueEx(key, STARTUP_VALUE)
    except FileNotFoundError:
        return False, "notPresent", ""
    except OSError as exc:
        return None, "unavailable", f"无法读取 Windows 启动审批状态：{exc}"
    if kind != registry.REG_BINARY or not isinstance(value, bytes) or len(value) != 12:
        return None, "unknown", "Windows 启动审批数据格式未知"
    if value[1:4] != b"\0\0\0":
        return None, "unknown", "Windows 启动审批数据格式未知"
    if value[0] in (3, 7):
        return True, "disabled", ""
    if value[0] in (2, 6):
        return False, "enabled", ""
    return None, "unknown", "Windows 启动审批状态未知"


def get_startup_status():
    """Read registration and Windows approval separately, without mutation.

    ``enabled`` is an observed registration status, not a promise that group
    policy or other Windows settings will allow a future login launch.
    """
    result = {
        "supported": sys.platform == "win32",
        "enabled": False,
        "matchesCurrentPath": False,
        "currentCommand": "",
        "expectedCommand": startup_command(),
        "error": "",
        "blockedByWindows": None,
        "approvalState": "unsupported" if sys.platform != "win32" else "unknown",
    }
    if not result["supported"]:
        result["error"] = "登录自启动状态仅支持 Windows 10/11"
        return result
    try:
        registry = _registry()
        try:
            with registry.OpenKey(
                registry.HKEY_CURRENT_USER, STARTUP_KEY, 0, registry.KEY_READ
            ) as key:
                value, kind = registry.QueryValueEx(key, STARTUP_VALUE)
        except FileNotFoundError:
            value, kind = "", registry.REG_SZ
        if kind not in (registry.REG_SZ, registry.REG_EXPAND_SZ) or not isinstance(value, str):
            result["error"] = "登录自启动命令格式未知"
        else:
            result["currentCommand"] = value
            result["matchesCurrentPath"] = value == result["expectedCommand"]
        blocked, approval, error = _startup_approval(registry)
        result["blockedByWindows"] = blocked
        result["approvalState"] = approval
        if error:
            result["error"] = "; ".join(filter(None, [result["error"], error]))
        result["enabled"] = (
            result["matchesCurrentPath"] and blocked is False and not result["error"]
        )
    except OSError as exc:
        result["error"] = f"无法读取登录自启动设置：{exc}"
    return result


def repair_start_at_login():
    """Repair the Run command after explicit user opt-in, preserving OS choices.

    The caller must first check the user's saved startAtLogin preference. This
    function does not create scheduled tasks, services or alternate registrations
    and never modifies StartupApproved or policy keys.
    """
    _require_windows()
    status = get_startup_status()
    if status["blockedByWindows"] is not False or status["error"]:
        return status
    if not status["matchesCurrentPath"]:
        try:
            set_start_at_login(True)
        except OSError as exc:
            status["error"] = str(exc)
            return status
        status = get_startup_status()
    return status


def set_start_at_login(enabled):
    """Register only this program under HKCU; no administrator access needed."""
    enabled = _boolean(enabled, "登录自启动状态")
    registry = _registry()
    try:
        if enabled:
            with registry.CreateKey(registry.HKEY_CURRENT_USER, STARTUP_KEY) as key:
                registry.SetValueEx(key, STARTUP_VALUE, 0, registry.REG_SZ, startup_command())
        else:
            try:
                with registry.OpenKey(
                    registry.HKEY_CURRENT_USER, STARTUP_KEY, 0, registry.KEY_SET_VALUE
                ) as key:
                    registry.DeleteValue(key, STARTUP_VALUE)
            except FileNotFoundError:
                pass
    except OSError as exc:
        raise OSError(f"无法保存登录自启动设置：{exc}") from exc


def _user32():
    _require_windows()
    return ctypes.WinDLL("user32", use_last_error=True)


def lock_workstation():
    """Lock the interactive Windows session; report failure from LockWorkStation."""
    library = _user32()
    library.LockWorkStation.argtypes = []
    library.LockWorkStation.restype = wintypes.BOOL
    if not library.LockWorkStation():
        code = ctypes.get_last_error() if hasattr(ctypes, "get_last_error") else 0
        raise OSError(code, "Windows 锁屏失败")


def _shutdown_command(arguments):
    _require_windows()
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "shutdown.exe"
    try:
        completed = subprocess.run(
            [str(executable), *arguments],
            shell=False,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OSError(f"系统电源命令无法完成：{exc}") from exc
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise OSError(
            f"系统电源命令失败（{completed.returncode}）" + (f"：{detail}" if detail else "")
        )


def request_power(action, delay=0):
    """Schedule shutdown/restart without forcing applications to close.

    Windows shutdown /t values above zero imply /f even when it is not written.
    To avoid that implicit force behavior, this service only accepts delay=0;
    the application owns its cancellable countdown and calls this at zero.
    """
    if action not in ("shutdown", "restart"):
        raise ValueError("系统动作必须为 shutdown 或 restart")
    if type(delay) is not int or delay != 0:
        raise ValueError("请由应用管理倒计时，结束后使用 delay=0 提交系统动作")
    _shutdown_command(["/s" if action == "shutdown" else "/r", "/t", "0"])


def cancel_power():
    """Cancel a shutdown already submitted to Windows; the app owns its timer."""
    _shutdown_command(["/a"])


def support_info():
    """Report platform capability, without probing or mutating system services."""
    windows = sys.platform == "win32"
    return {
        "platform": sys.platform,
        "systemVolume": windows,
        "startAtLogin": windows,
        "lockWorkstation": windows,
        "powerActions": windows,
        "systemVolumeBackend": "Windows Core Audio / pycaw" if windows else "",
    }
