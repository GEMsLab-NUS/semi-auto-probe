from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from semi_auto_probe import app as app_module
from semi_auto_probe import help_system
from semi_auto_probe.app import ProbeApp


class HelpSystemTests(unittest.TestCase):
    def test_every_application_page_has_a_mapped_topic(self):
        self.assertEqual(
            set(help_system.PAGE_HELP_TOPICS),
            {
                "Main",
                "Communication",
                "AutoFocus",
                "FocusMap",
                "LayoutBond",
                "ImgStitch",
                "ImgMatrix",
                "AutoTest",
                "EdgeTrace",
                "AI Agent",
                "Config",
            },
        )
        self.assertTrue(
            set(help_system.PAGE_HELP_TOPICS.values()).issubset(help_system.HELP_TOPICS)
        )

    def test_every_help_topic_has_a_unique_context_and_filename(self):
        topics = list(help_system.HELP_TOPICS.values())

        self.assertEqual(
            len({topic.context_id for topic in topics}),
            len(topics),
            "Every HELP_TOPICS context ID must be unique.",
        )
        self.assertEqual(
            len({topic.filename.casefold() for topic in topics}),
            len(topics),
            "Every HELP_TOPICS filename must be unique on Windows.",
        )

    def test_unknown_page_uses_welcome_topic(self):
        self.assertEqual(help_system.normalize_topic_key(None), "Welcome")
        self.assertEqual(help_system.normalize_topic_key("not-a-page"), "Welcome")

    def test_environment_override_is_the_first_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            custom = Path(temporary) / "custom-help.chm"
            custom.write_bytes(b"ITSF")
            with patch.dict(
                help_system.os.environ,
                {help_system.HELP_FILE_ENV: str(custom)},
                clear=False,
            ):
                self.assertEqual(help_system.help_file_candidates()[0], custom.resolve())
                self.assertEqual(help_system.find_help_file(), custom.resolve())

    def test_windows_opens_the_context_id_from_the_chm(self):
        with tempfile.TemporaryDirectory() as temporary:
            chm = Path(temporary) / help_system.HELP_FILENAME
            chm.write_bytes(b"ITSF")
            with (
                patch.object(help_system.sys, "platform", "win32"),
                patch.object(help_system, "find_help_file", return_value=chm),
                patch.object(help_system, "_open_chm_context", return_value=True) as native,
            ):
                result = help_system.open_help("AutoFocus", parent_handle=42)
        self.assertTrue(result.opened)
        self.assertEqual(result.topic_key, "AutoFocus")
        native.assert_called_once_with(
            chm,
            help_system.HELP_TOPICS["AutoFocus"].context_id,
            42,
        )

    def test_source_html_is_the_development_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            topic = Path(temporary) / "main.htm"
            topic.write_text("<html></html>", encoding="utf-8")
            with (
                patch.object(help_system, "find_help_file", return_value=None),
                patch.object(help_system, "source_topic_path", return_value=topic),
                patch.object(help_system.webbrowser, "open", return_value=True) as browser,
            ):
                result = help_system.open_help("Main")
        self.assertTrue(result.opened)
        self.assertEqual(result.location, topic)
        browser.assert_called_once_with(topic.as_uri())


class HelpRoutingTests(unittest.TestCase):
    @staticmethod
    def make_app_shell(*, page: str, wire_bond_enabled: bool = False) -> ProbeApp:
        app = ProbeApp.__new__(ProbeApp)
        app.current_page = page
        app.probe_config = SimpleNamespace(wire_bond_enabled=wire_bond_enabled)
        app.status_var = Mock()
        app.winfo_id = Mock(return_value=73)
        return app

    def test_f1_keypress_routes_directly_to_context_help(self):
        app = self.make_app_shell(page="Main")
        event = SimpleNamespace(keysym="F1")

        with patch.object(app, "_open_context_help", return_value="break") as handler:
            result = ProbeApp._on_key_press(app, event)

        self.assertEqual(result, "break")
        handler.assert_called_once_with(event)

    def test_autotest_f1_selects_probe_or_wire_bond_topic(self):
        for wire_bond_enabled, expected_topic in (
            (False, "AutoTest"),
            (True, "WireBondMapping"),
        ):
            with self.subTest(wire_bond_enabled=wire_bond_enabled):
                app = self.make_app_shell(
                    page="AutoTest",
                    wire_bond_enabled=wire_bond_enabled,
                )
                opened = help_system.HelpOpenResult(
                    True,
                    expected_topic,
                    None,
                    "Help opened.",
                )

                with (
                    patch.object(app_module, "open_help", return_value=opened) as open_help,
                    patch.object(app_module.messagebox, "showerror") as showerror,
                ):
                    result = ProbeApp._open_context_help(app)

                self.assertEqual(result, "break")
                open_help.assert_called_once_with(expected_topic, parent_handle=73)
                app.status_var.set.assert_called_once_with("Help opened.")
                showerror.assert_not_called()

    def test_child_window_help_topic_overrides_the_current_page(self):
        app = self.make_app_shell(page="AutoTest", wire_bond_enabled=True)
        top_level = SimpleNamespace(help_topic="WorkflowEditor")
        event = SimpleNamespace(
            widget=SimpleNamespace(winfo_toplevel=lambda: top_level),
        )
        opened = help_system.HelpOpenResult(
            True,
            "WorkflowEditor",
            None,
            "Help opened.",
        )

        with patch.object(app_module, "open_help", return_value=opened) as open_help:
            result = ProbeApp._open_context_help(app, event)

        self.assertEqual(result, "break")
        open_help.assert_called_once_with("WorkflowEditor", parent_handle=73)


if __name__ == "__main__":
    unittest.main()
