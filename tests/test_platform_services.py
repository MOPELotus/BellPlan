"""Platform services use mocks only; tests never change a real Windows session."""

import unittest
from contextlib import contextmanager
from subprocess import CompletedProcess, TimeoutExpired
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from bellplan import platform_services as platform


class PlatformServicesTests(unittest.TestCase):
    def audio_fixture(self):
        endpoint = MagicMock()
        endpoint.GetMasterVolumeLevelScalar.return_value = 0.42
        endpoint.GetMute.return_value = 1
        device = SimpleNamespace(EndpointVolume=endpoint, FriendlyName="扬声器")
        comtypes = SimpleNamespace(CoInitialize=MagicMock(), CoUninitialize=MagicMock())
        utility = SimpleNamespace(GetSpeakers=MagicMock(return_value=device))
        return endpoint, device, comtypes, utility

    @contextmanager
    def fake_audio(self):
        endpoint, device, comtypes, utility = self.audio_fixture()
        with (
            patch.object(platform.sys, "platform", "win32"),
            patch.dict(
                "sys.modules",
                {
                    "comtypes": comtypes,
                    "pycaw": SimpleNamespace(),
                    "pycaw.pycaw": SimpleNamespace(AudioUtilities=utility),
                },
            ),
        ):
            yield endpoint, device, comtypes, utility

    def registry_fixture(self):
        registry = MagicMock()
        registry.HKEY_CURRENT_USER = 1
        registry.KEY_READ = 2
        registry.KEY_SET_VALUE = 4
        registry.REG_SZ = 1
        registry.REG_EXPAND_SZ = 2
        return registry

    def test_actual_audio_snapshot_and_com_lifecycle(self):
        with self.fake_audio() as (endpoint, device, comtypes, utility):
            self.assertEqual(
                platform.get_system_volume(), {"volume": 42.0, "muted": True, "device": "扬声器"}
            )
            endpoint.SetMasterVolumeLevelScalar.assert_not_called()
            comtypes.CoInitialize.assert_called_once()
            comtypes.CoUninitialize.assert_called_once()

    def test_volume_write_reads_back_without_implicitly_unmuting(self):
        with self.fake_audio() as (endpoint, *_):
            result = platform.set_system_volume(55)
            endpoint.SetMasterVolumeLevelScalar.assert_called_once_with(0.55, None)
            endpoint.SetMute.assert_not_called()
            self.assertEqual(result["volume"], 42)

    def test_mute_write_has_actual_readback(self):
        with self.fake_audio() as (endpoint, *_):
            result = platform.set_system_mute(False)
            endpoint.SetMute.assert_called_once_with(0, None)
            self.assertTrue(result["muted"])

    def test_audio_failure_balances_com_and_is_reported(self):
        with self.fake_audio() as (_, __, comtypes, utility):
            utility.GetSpeakers.side_effect = RuntimeError("endpoint unavailable")
            with self.assertRaisesRegex(OSError, "endpoint unavailable"):
                platform.get_system_volume()
            comtypes.CoUninitialize.assert_called_once()

    def test_no_default_endpoint_is_reported(self):
        with self.fake_audio() as (_, __, ___, utility):
            utility.GetSpeakers.return_value = None
            with self.assertRaisesRegex(OSError, "默认音频输出设备"):
                platform.get_system_volume()

    def test_volume_rejects_invalid_inputs_without_device_access(self):
        with patch.object(platform, "_audio_device") as device:
            for value in (True, False, -1, 101, float("nan"), float("inf"), "50", None):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    platform.set_system_volume(value)
            device.assert_not_called()

    def test_boolean_settings_reject_non_boolean(self):
        for value in (1, 0, "true", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                platform.set_system_mute(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                platform.set_start_at_login(value)

    def test_startup_command_handles_source_and_frozen_paths(self):
        with (
            patch.object(platform.sys, "executable", r"C:\Program Files\Python\pythonw.exe"),
            patch.object(platform.sys, "frozen", False, create=True),
        ):
            self.assertEqual(
                platform.startup_command(), '"C:\\Program Files\\Python\\pythonw.exe" -m bellplan'
            )
        with (
            patch.object(platform.sys, "executable", r"C:\Program Files\BellPlan\BellPlan.exe"),
            patch.object(platform.sys, "frozen", True, create=True),
        ):
            self.assertEqual(
                platform.startup_command(), '"C:\\Program Files\\BellPlan\\BellPlan.exe"'
            )

    def test_registry_read_only_recognizes_this_installation(self):
        registry = self.registry_fixture()
        registry.QueryValueEx.return_value = (platform.startup_command(), registry.REG_SZ)
        with patch.object(platform, "_registry", return_value=registry):
            self.assertTrue(platform.get_start_at_login())
        registry.SetValueEx.assert_not_called()
        registry.DeleteValue.assert_not_called()
        registry.QueryValueEx.return_value = ("different.exe", registry.REG_SZ)
        with patch.object(platform, "_registry", return_value=registry):
            self.assertFalse(platform.get_start_at_login())

    def test_missing_startup_registration_is_false(self):
        registry = self.registry_fixture()
        registry.OpenKey.side_effect = FileNotFoundError()
        with patch.object(platform, "_registry", return_value=registry):
            self.assertFalse(platform.get_start_at_login())

    def test_enable_startup_writes_only_own_hkcu_value(self):
        registry = self.registry_fixture()
        key = registry.CreateKey.return_value.__enter__.return_value
        with patch.object(platform, "_registry", return_value=registry):
            platform.set_start_at_login(True)
        registry.CreateKey.assert_called_once_with(registry.HKEY_CURRENT_USER, platform.STARTUP_KEY)
        registry.SetValueEx.assert_called_once_with(
            key, platform.STARTUP_VALUE, 0, registry.REG_SZ, platform.startup_command()
        )

    def test_disable_startup_only_deletes_own_value_and_tolerates_absence(self):
        registry = self.registry_fixture()
        key = registry.OpenKey.return_value.__enter__.return_value
        with patch.object(platform, "_registry", return_value=registry):
            platform.set_start_at_login(False)
        registry.DeleteValue.assert_called_once_with(key, platform.STARTUP_VALUE)
        registry.DeleteValue.side_effect = FileNotFoundError()
        with patch.object(platform, "_registry", return_value=registry):
            platform.set_start_at_login(False)

    def test_lock_calls_native_api_and_propagates_failure(self):
        user32 = MagicMock()
        user32.LockWorkStation.return_value = 1
        with patch.object(platform, "_user32", return_value=user32):
            platform.lock_workstation()
        user32.LockWorkStation.assert_called_once_with()
        user32.LockWorkStation.return_value = 0
        with (
            patch.object(platform, "_user32", return_value=user32),
            self.assertRaisesRegex(OSError, "锁屏失败"),
        ):
            platform.lock_workstation()

    def test_power_arguments_never_force_running_apps(self):
        with patch.object(platform, "_shutdown_command") as command:
            platform.request_power("shutdown")
            command.assert_called_once_with(["/s", "/t", "0"])
            command.reset_mock()
            platform.request_power("restart", delay=0)
            command.assert_called_once_with(["/r", "/t", "0"])
            command.reset_mock()
            platform.cancel_power()
            command.assert_called_once_with(["/a"])

    def test_power_rejects_delay_that_would_implicitly_force_close(self):
        with patch.object(platform, "_shutdown_command") as command:
            for delay in (True, -1, 1, 60, 1.5, "0"):
                with self.subTest(delay=delay), self.assertRaises(ValueError):
                    platform.request_power("shutdown", delay)
            for action in ("sleep", "reboot", "", None):
                with self.subTest(action=action), self.assertRaises(ValueError):
                    platform.request_power(action)
            command.assert_not_called()

    def test_power_uses_native_executable_without_shell(self):
        with (
            patch.object(platform.sys, "platform", "win32"),
            patch.object(platform.subprocess, "run", return_value=CompletedProcess([], 0)) as run,
        ):
            platform.request_power("restart")
        arguments = run.call_args.args[0]
        self.assertTrue(arguments[0].endswith("shutdown.exe"))
        self.assertEqual(arguments[1:], ["/r", "/t", "0"])
        self.assertFalse(run.call_args.kwargs["shell"])

    def test_power_exit_code_and_timeout_are_errors(self):
        with (
            patch.object(platform.sys, "platform", "win32"),
            patch.object(
                platform.subprocess,
                "run",
                return_value=CompletedProcess([], 5, "", "Access denied"),
            ),
        ):
            with self.assertRaisesRegex(OSError, "Access denied"):
                platform.cancel_power()
        with (
            patch.object(platform.sys, "platform", "win32"),
            patch.object(
                platform.subprocess, "run", side_effect=TimeoutExpired("shutdown.exe", 15)
            ),
        ):
            with self.assertRaisesRegex(OSError, "系统电源命令无法完成"):
                platform.cancel_power()

    def test_non_windows_reports_unsupported_without_side_effects(self):
        with (
            patch.object(platform.sys, "platform", "linux"),
            patch.object(platform.subprocess, "run") as run,
        ):
            for operation in (
                platform.get_system_volume,
                lambda: platform.set_system_volume(30),
                lambda: platform.set_system_mute(True),
                platform.get_start_at_login,
                lambda: platform.set_start_at_login(True),
                platform.lock_workstation,
                lambda: platform.request_power("shutdown"),
                platform.cancel_power,
            ):
                with self.subTest(operation=operation), self.assertRaises(OSError):
                    operation()
            run.assert_not_called()
            self.assertFalse(platform.support_info()["systemVolume"])


class StartupStatusTests(unittest.TestCase):
    def registry_fixture(self, current=None, approval=b"\x02" + b"\0" * 11):
        registry = MagicMock()
        registry.HKEY_CURRENT_USER = 1
        registry.KEY_READ = 2
        registry.REG_SZ, registry.REG_EXPAND_SZ, registry.REG_BINARY = 1, 2, 3
        registry.QueryValueEx.side_effect = [
            (current if current is not None else platform.startup_command(), registry.REG_SZ),
            (approval, registry.REG_BINARY),
        ]
        return registry

    def status(self, registry):
        with (
            patch.object(platform.sys, "platform", "win32"),
            patch.object(platform, "_registry", return_value=registry),
        ):
            return platform.get_startup_status()

    def test_current_registration_and_known_approved_layout(self):
        result = self.status(self.registry_fixture())
        self.assertTrue(result["supported"])
        self.assertTrue(result["matchesCurrentPath"])
        self.assertTrue(result["enabled"])
        self.assertFalse(result["blockedByWindows"])
        self.assertEqual(result["error"], "")

    def test_moved_executable_is_detected(self):
        result = self.status(self.registry_fixture(current='"C:\\Old Folder\\BellPlan.exe"'))
        self.assertFalse(result["matchesCurrentPath"])
        self.assertFalse(result["enabled"])
        self.assertNotEqual(result["expectedCommand"], result["currentCommand"])

    def test_known_disabled_layout_does_not_claim_enabled(self):
        for state in (3, 7):
            with self.subTest(state=state):
                result = self.status(self.registry_fixture(approval=bytes([state]) + b"\0" * 11))
                self.assertTrue(result["matchesCurrentPath"])
                self.assertTrue(result["blockedByWindows"])
                self.assertFalse(result["enabled"])

    def test_unknown_approval_is_not_inferred_as_enabled(self):
        for value in (b"", b"\2" * 4, b"\x99" + b"\0" * 11, b"\2\1" + b"\0" * 10):
            with self.subTest(value=value):
                result = self.status(self.registry_fixture(approval=value))
                self.assertIsNone(result["blockedByWindows"])
                self.assertFalse(result["enabled"])
                self.assertTrue(result["error"])

    def test_absent_approval_does_not_block_registration(self):
        registry = self.registry_fixture()
        registry.QueryValueEx.side_effect = [
            (platform.startup_command(), registry.REG_SZ),
            FileNotFoundError(),
        ]
        result = self.status(registry)
        self.assertFalse(result["blockedByWindows"])
        self.assertTrue(result["enabled"])
        self.assertEqual(result["approvalState"], "notPresent")

    def test_missing_run_value_is_reported_without_writing(self):
        registry = self.registry_fixture()
        registry.QueryValueEx.side_effect = [FileNotFoundError(), FileNotFoundError()]
        result = self.status(registry)
        self.assertFalse(result["matchesCurrentPath"])
        self.assertEqual(result["currentCommand"], "")
        registry.SetValueEx.assert_not_called()
        registry.CreateKey.assert_not_called()

    def test_access_denied_remains_observable(self):
        registry = self.registry_fixture()
        registry.OpenKey.side_effect = PermissionError("Access denied")
        result = self.status(registry)
        self.assertFalse(result["enabled"])
        self.assertIn("Access denied", result["error"])

    def test_repair_updates_only_run_after_opt_in(self):
        original = {"blockedByWindows": False, "error": "", "matchesCurrentPath": False}
        repaired = {"blockedByWindows": False, "error": "", "matchesCurrentPath": True}
        with (
            patch.object(platform.sys, "platform", "win32"),
            patch.object(platform, "get_startup_status", side_effect=[original, repaired]),
            patch.object(platform, "set_start_at_login") as write,
        ):
            self.assertEqual(platform.repair_start_at_login(), repaired)
            write.assert_called_once_with(True)

    def test_repair_respects_disabled_or_unknown_windows_approval(self):
        for blocked, error in ((True, ""), (None, "unknown layout"), (False, "Access denied")):
            status = {"blockedByWindows": blocked, "error": error, "matchesCurrentPath": False}
            with (
                self.subTest(blocked=blocked),
                patch.object(platform.sys, "platform", "win32"),
                patch.object(platform, "get_startup_status", return_value=status),
                patch.object(platform, "set_start_at_login") as write,
            ):
                self.assertEqual(platform.repair_start_at_login(), status)
                write.assert_not_called()

    def test_current_registration_is_not_needlessly_rewritten(self):
        status = {"blockedByWindows": False, "error": "", "matchesCurrentPath": True}
        with (
            patch.object(platform.sys, "platform", "win32"),
            patch.object(platform, "get_startup_status", return_value=status),
            patch.object(platform, "set_start_at_login") as write,
        ):
            self.assertEqual(platform.repair_start_at_login(), status)
            write.assert_not_called()

    def test_repair_write_failure_is_reported(self):
        status = {"blockedByWindows": False, "error": "", "matchesCurrentPath": False}
        with (
            patch.object(platform.sys, "platform", "win32"),
            patch.object(platform, "get_startup_status", return_value=status),
            patch.object(platform, "set_start_at_login", side_effect=OSError("registry denied")),
        ):
            self.assertIn("registry denied", platform.repair_start_at_login()["error"])

    def test_non_windows_status_is_explicit_and_repair_unsupported(self):
        with (
            patch.object(platform.sys, "platform", "linux"),
            patch.object(platform, "_registry") as registry,
        ):
            result = platform.get_startup_status()
            self.assertFalse(result["supported"])
            self.assertFalse(result["enabled"])
            self.assertTrue(result["error"])
            with self.assertRaises(OSError):
                platform.repair_start_at_login()
            registry.assert_not_called()


class WindowsEventsTests(unittest.TestCase):
    def setUp(self):
        from bellplan.windows_events import WindowsEvents

        self.events = WindowsEvents()
        self.resumes, self.suspends, self.devices = [], [], []
        self.events.resume.connect(lambda: self.resumes.append(True))
        self.events.suspend.connect(lambda: self.suspends.append(True))
        self.events.devicesChanged.connect(lambda: self.devices.append(True))

    def tearDown(self):
        self.events.close()

    def test_power_and_device_codes_emit_only_notifications(self):
        from bellplan import windows_events as events

        self.events.observe(events.WM_POWERBROADCAST, events.PBT_APMSUSPEND)
        for code in (
            events.PBT_APMRESUMEAUTOMATIC,
            events.PBT_APMRESUMESUSPEND,
            events.PBT_APMRESUMECRITICAL,
        ):
            self.events.observe(events.WM_POWERBROADCAST, code)
        self.events.observe(events.WM_DEVICECHANGE, 0x8004)
        self.assertEqual(len(self.suspends), 1)
        self.assertEqual(len(self.resumes), 3)
        self.assertEqual(len(self.devices), 1)
        self.events.observe(0, 0)
        self.events.observe(events.WM_POWERBROADCAST, 0xFFFF)
        self.assertEqual(len(self.resumes), 3)

    def test_native_filter_parses_message_without_consuming_it(self):
        import ctypes

        from bellplan import windows_events as events

        message = events._Message()
        message.message = events.WM_POWERBROADCAST
        message.wParam = events.PBT_APMRESUMEAUTOMATIC
        with patch.object(events.sys, "platform", "win32"):
            self.assertEqual(
                self.events._filter.nativeEventFilter(
                    b"windows_generic_MSG", ctypes.addressof(message)
                ),
                (False, 0),
            )
        self.assertEqual(self.resumes, [True])

    def test_native_filter_ignores_non_windows_null_and_other_event_types(self):
        from bellplan import windows_events as events

        with patch.object(events.sys, "platform", "linux"):
            self.assertEqual(
                self.events._filter.nativeEventFilter(b"windows_generic_MSG", 1), (False, 0)
            )
        with patch.object(events.sys, "platform", "win32"):
            self.assertEqual(
                self.events._filter.nativeEventFilter(b"windows_generic_MSG", 0), (False, 0)
            )
            self.assertEqual(
                self.events._filter.nativeEventFilter(b"xcb_generic_event_t", 1), (False, 0)
            )
        self.assertFalse(self.resumes)

    def test_install_and_remove_are_idempotent_and_do_not_hook_other_platforms(self):
        from bellplan import windows_events as events

        application = MagicMock()
        with patch.object(events.sys, "platform", "linux"):
            self.assertFalse(self.events.install(application))
            application.installNativeEventFilter.assert_not_called()
        with patch.object(events.sys, "platform", "win32"):
            self.assertTrue(self.events.install(application))
            self.assertTrue(self.events.install(application))
            application.installNativeEventFilter.assert_called_once_with(self.events._filter)
            self.events.close()
            self.events.close()
            application.removeNativeEventFilter.assert_called_once_with(self.events._filter)


if __name__ == "__main__":
    unittest.main()
