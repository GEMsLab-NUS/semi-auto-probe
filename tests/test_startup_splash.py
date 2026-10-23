from __future__ import annotations

import importlib
import unittest
from pathlib import Path
from unittest import mock


class StartupSplashTests(unittest.TestCase):
    def test_console_entrypoint_uses_lightweight_startup_module(self) -> None:
        pyproject = Path(__file__).parents[1] / "pyproject.toml"

        contents = pyproject.read_text(encoding="utf-8")

        self.assertIn('semi-auto-probe = "semi_auto_probe.startup:main"', contents)

    def test_package_main_uses_lightweight_startup_module(self) -> None:
        package_main = Path(__file__).parents[1] / "src" / "semi_auto_probe" / "__main__.py"

        contents = package_main.read_text(encoding="utf-8")

        self.assertIn("from .startup import main", contents)

    def test_loader_forwards_progress_and_ready_callbacks(self) -> None:
        startup = importlib.import_module("semi_auto_probe.startup")
        fake_app_module = mock.Mock()
        progress = mock.Mock()
        on_ready = mock.Mock()

        startup.launch_loaded_app(
            fake_app_module,
            ["--restore-last"],
            startup_progress=progress,
            on_ready=on_ready,
        )

        fake_app_module.main.assert_called_once_with(
            ["--restore-last"],
            startup_progress=progress,
            on_ready=on_ready,
            show_banner=False,
        )

    def test_console_progress_prints_each_startup_state_once(self) -> None:
        startup = importlib.import_module("semi_auto_probe.startup")
        logger = mock.Mock()
        progress = startup.ConsoleStartupProgress(logger=logger)

        progress("Loading application components...", 5)
        progress("Loading application components...", 5)
        progress("Preparing the main window...", 58)

        self.assertEqual(
            logger.info.call_args_list,
            [
                mock.call("Startup %3d%% | %s", 5, "Loading application components..."),
                mock.call("Startup %3d%% | %s", 58, "Preparing the main window..."),
            ],
        )

    def test_restore_prompt_accepts_yes(self) -> None:
        startup = importlib.import_module("semi_auto_probe.startup")

        accepted = startup.prompt_restore_latest(
            timeout_seconds=3,
            key_reader=lambda: "y",
            output=mock.Mock(),
        )

        self.assertTrue(accepted)

    def test_restore_prompt_defaults_to_no_after_timeout(self) -> None:
        startup = importlib.import_module("semi_auto_probe.startup")

        accepted = startup.prompt_restore_latest(
            timeout_seconds=0,
            key_reader=lambda: None,
            output=mock.Mock(),
        )

        self.assertFalse(accepted)

    def test_restore_choice_adds_restore_flag(self) -> None:
        startup = importlib.import_module("semi_auto_probe.startup")

        argv = startup.with_restore_choice([], prompt=lambda: True)

        self.assertEqual(argv, ["--restore-last"])

    def test_explicit_restore_flag_skips_prompt(self) -> None:
        startup = importlib.import_module("semi_auto_probe.startup")
        prompt = mock.Mock(return_value=False)

        argv = startup.with_restore_choice(["--restore-last"], prompt=prompt)

        self.assertEqual(argv, ["--restore-last"])
        prompt.assert_not_called()


if __name__ == "__main__":
    unittest.main()
