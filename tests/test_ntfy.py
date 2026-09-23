import logging
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from semi_auto_probe.app import ProbeApp
from semi_auto_probe.ntfy import (
    NtfyErrorHandler,
    format_autotest_completion,
    format_error_notification,
    format_test_notification,
    normalize_ntfy_server_url,
    normalize_ntfy_topic,
    publish_ntfy,
)


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b"ok"


class NtfyTests(unittest.TestCase):
    def test_publish_uses_topic_and_utf8_body(self):
        with patch("semi_auto_probe.ntfy.urlopen", return_value=_Response()) as opened:
            publish_ntfy(
                "https://ntfy.sh/", "SAP_alerts", "AutoTest completed",
                title="SAP done", priority="high", tags="white_check_mark",
            )
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "https://ntfy.sh/SAP_alerts")
        self.assertEqual(request.data, b"AutoTest completed")
        self.assertEqual(request.get_header("Title"), "SAP done")
        self.assertEqual(request.get_header("Priority"), "high")

    def test_invalid_server_and_topic_are_rejected(self):
        for server in ("ftp://example.com", "ntfy.sh"):
            with self.subTest(server=server), self.assertRaises(ValueError):
                normalize_ntfy_server_url(server)
        for topic in ("", "contains space", "slash/topic", "x" * 65):
            with self.subTest(topic=topic), self.assertRaises(ValueError):
                normalize_ntfy_topic(topic)

    def test_error_handler_never_exports_log_message(self):
        notifier = Mock()
        handler = NtfyErrorHandler(notifier)
        record = logging.LogRecord(
            "semi_auto_probe.device", logging.ERROR, __file__, 1,
            "secret device path C:/private and value %s", (123,), None,
        )
        with patch("semi_auto_probe.ntfy.notification_time", return_value="2026-09-23 14:15:30"):
            handler.emit(record)
        message = notifier.notify.call_args.args[0]
        self.assertEqual(message, "Time: 2026-09-23 14:15:30\nAction: Check local log")
        self.assertEqual(notifier.notify.call_args.kwargs["title"], "SAP | ERROR")
        self.assertNotIn("secret", message)
        self.assertNotIn("private", message)
        self.assertNotIn("123", message)

    def test_structured_messages_are_compact(self):
        with patch("semi_auto_probe.ntfy.notification_time", return_value="2026-09-23 14:15:30"):
            self.assertEqual(
                format_error_notification(),
                "Time: 2026-09-23 14:15:30\nAction: Check local log",
            )
            self.assertEqual(
                format_autotest_completion(216),
                "Points: 216\nTime: 2026-09-23 14:15:30",
            )
            self.assertEqual(
                format_test_notification("SAP_alerts"),
                "Topic: SAP_alerts\nStatus: Connected\nTime: 2026-09-23 14:15:30",
            )

    def test_error_handler_can_be_disabled(self):
        notifier = Mock()
        handler = NtfyErrorHandler(notifier, enabled=False)
        handler.emit(logging.LogRecord("semi_auto_probe", logging.ERROR, __file__, 1, "error", (), None))
        notifier.notify.assert_not_called()

    def test_completion_respects_master_and_completion_switches(self):
        notifier = Mock()
        app = SimpleNamespace(
            probe_config=SimpleNamespace(ntfy_enabled=True, ntfy_notify_completions=True),
            ntfy_notifier=notifier,
        )
        self.assertTrue(ProbeApp._notify_ntfy_completion(app, "12 points", title="done"))
        notifier.notify.assert_called_once()
        app.probe_config.ntfy_notify_completions = False
        self.assertFalse(ProbeApp._notify_ntfy_completion(app, "again", title="done"))
        self.assertEqual(notifier.notify.call_count, 1)


if __name__ == "__main__":
    unittest.main()
