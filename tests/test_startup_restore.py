from __future__ import annotations

import unittest
import time
from unittest import mock

from semi_auto_probe.app import ProbeApp, build_arg_parser, main


class StartupRestoreArgumentTests(unittest.TestCase):
    def test_restore_last_aliases_enable_startup_restore(self) -> None:
        parser = build_arg_parser()

        for flag in ("--restore-last", "--load-last", "--last"):
            with self.subTest(flag=flag):
                args = parser.parse_args([flag])
                self.assertTrue(args.restore_last)

    def test_main_passes_restore_flag_to_app(self) -> None:
        with (
            mock.patch("semi_auto_probe.app.print_startup_banner"),
            mock.patch("semi_auto_probe.app.ProbeApp") as app_class,
        ):
            app = app_class.return_value

            main(["--restore-last"])

        app_class.assert_called_once_with(restore_last=True)
        app.mainloop.assert_called_once_with()

    def test_main_defaults_to_plain_startup(self) -> None:
        with (
            mock.patch("semi_auto_probe.app.print_startup_banner"),
            mock.patch("semi_auto_probe.app.ProbeApp") as app_class,
        ):
            app = app_class.return_value

            main([])

        app_class.assert_called_once_with(restore_last=False)
        app.mainloop.assert_called_once_with()

    def test_main_keeps_splash_until_runtime_readiness(self) -> None:
        progress = mock.Mock()
        on_ready = mock.Mock()
        with (
            mock.patch("semi_auto_probe.app.print_startup_banner"),
            mock.patch("semi_auto_probe.app.ProbeApp") as app_class,
        ):
            app = app_class.return_value

            main([], startup_progress=progress, on_ready=on_ready)

        app_class.assert_called_once_with(restore_last=False, startup_progress=progress)
        app.show_when_startup_ready.assert_called_once_with(progress, on_ready)
        on_ready.assert_not_called()
        app.mainloop.assert_called_once_with()

    def test_main_window_stays_hidden_until_camera_and_controller_settle(self) -> None:
        app = ProbeApp.__new__(ProbeApp)
        app._startup_revealed = False
        app._startup_camera_settled = False
        app._startup_connection_attempted = False
        app._startup_connection_pending = False
        app._startup_controller_settled = False
        app._startup_restore_settled = True
        app._startup_ready_started_at = time.monotonic()
        app._startup_progress_message = None
        app._startup_progress = mock.Mock()
        app._startup_on_ready = mock.Mock()
        app.after = mock.Mock(return_value="poll-job")
        app.update_idletasks = mock.Mock()
        app.deiconify = mock.Mock()
        app._maximize_default_window = mock.Mock()
        app.lift = mock.Mock()

        app._poll_startup_readiness()

        app.deiconify.assert_not_called()
        app._startup_on_ready.assert_not_called()

        app._startup_camera_settled = True
        app._startup_connection_attempted = True
        app._startup_controller_settled = True
        app._poll_startup_readiness()

        app._startup_on_ready.assert_called_once_with()
        app.deiconify.assert_called_once_with()
        app._maximize_default_window.assert_called_once_with()
        app.lift.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
