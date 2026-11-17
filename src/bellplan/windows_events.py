"""Read-only Windows resume/device notifications for the Qt event loop.

No wake timers, power policy changes, driver hooks or playback actions are used.
The application decides how to reset scheduling and refresh output devices.
"""

from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

WM_POWERBROADCAST = 0x0218
WM_DEVICECHANGE = 0x0219
PBT_APMSUSPEND = 0x0004
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMECRITICAL = 0x0006
PBT_APMRESUMEAUTOMATIC = 0x0012


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class _Message(ctypes.Structure):
    # Explicit Windows-sized fields keep the layout correct on x86/x64 and let
    # isolated non-Windows tests verify parsing without OS calls.
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint32),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_uint32),
        ("pt", _Point),
        ("lPrivate", ctypes.c_uint32),
    ]


class _NativeFilter(QAbstractNativeEventFilter):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def nativeEventFilter(self, event_type, message):
        if sys.platform != "win32" or bytes(event_type) not in (
            b"windows_generic_MSG",
            b"windows_dispatcher_MSG",
        ):
            return False, 0
        address = int(message) if message else 0
        if address:
            native = _Message.from_address(address)
            self.owner.observe(native.message, native.wParam)
        # Let Qt and Windows continue processing; this observer never swallows a
        # suspend query, vetoes power changes, or modifies a device notification.
        return False, 0


class WindowsEvents(QObject):
    resume = Signal()
    suspend = Signal()
    devicesChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._filter = _NativeFilter(self)
        self._application = None

    def install(self, application):
        """Install on a running Qt app; the caller retains this object."""
        if sys.platform != "win32":
            return False
        if self._application is application:
            return True
        self.close()
        application.installNativeEventFilter(self._filter)
        self._application = application
        return True

    def close(self):
        if self._application is not None:
            self._application.removeNativeEventFilter(self._filter)
            self._application = None

    def observe(self, message, wparam):
        """Translate already-received native codes; no system calls or playback."""
        if message == WM_POWERBROADCAST:
            if wparam == PBT_APMSUSPEND:
                self.suspend.emit()
            elif wparam in (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND, PBT_APMRESUMECRITICAL):
                self.resume.emit()
        elif message == WM_DEVICECHANGE:
            self.devicesChanged.emit()
