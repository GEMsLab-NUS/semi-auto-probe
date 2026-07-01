from __future__ import annotations

import unittest
from unittest import mock

from semi_auto_probe.app import build_arg_parser, main


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


if __name__ == "__main__":
    unittest.main()
