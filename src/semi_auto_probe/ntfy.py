"""Non-blocking privacy-safe ntfy notifications and logging integration."""
from __future__ import annotations

import logging
import queue
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


logger = logging.getLogger(__name__)
DEFAULT_NTFY_SERVER_URL = "https://ntfy.sh"
DEFAULT_NTFY_TOPIC = "SAP_alerts"
_TOPIC_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


def normalize_ntfy_server_url(value: str) -> str:
    url = str(value or DEFAULT_NTFY_SERVER_URL).strip().rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("ntfy server URL must start with http:// or https://.")
    return url


def normalize_ntfy_topic(value: str) -> str:
    topic = str(value or "").strip()
    if not _TOPIC_PATTERN.fullmatch(topic):
        raise ValueError("ntfy topic must contain 1-64 letters, numbers, underscores, or hyphens.")
    return topic


def notification_time(timestamp: float | None = None) -> str:
    value = datetime.now().astimezone() if timestamp is None else datetime.fromtimestamp(timestamp).astimezone()
    return value.strftime("%Y-%m-%d %H:%M:%S")


def format_error_notification(timestamp: float | None = None) -> str:
    return f"Time: {notification_time(timestamp)}\nAction: Check local log"


def format_autotest_completion(point_count: int, timestamp: float | None = None) -> str:
    return f"Points: {int(point_count)}\nTime: {notification_time(timestamp)}"


def format_test_notification(topic: str, timestamp: float | None = None) -> str:
    return f"Topic: {normalize_ntfy_topic(topic)}\nStatus: Connected\nTime: {notification_time(timestamp)}"


def publish_ntfy(
    server_url: str,
    topic: str,
    message: str,
    *,
    title: str = "Semi Auto Probe",
    priority: str = "default",
    tags: str = "",
    timeout: float = 5.0,
) -> None:
    """Publish one UTF-8 message using ntfy's HTTP API."""
    url = f"{normalize_ntfy_server_url(server_url)}/{quote(normalize_ntfy_topic(topic), safe='')}"
    headers = {"Title": str(title), "Priority": str(priority), "User-Agent": "semi-auto-probe"}
    if tags:
        headers["Tags"] = str(tags)
    request = Request(url, data=str(message).encode("utf-8"), headers=headers, method="POST")
    with urlopen(request, timeout=float(timeout)) as response:
        response.read()


@dataclass(frozen=True)
class _Notification:
    server_url: str
    topic: str
    message: str
    title: str
    priority: str
    tags: str


class NtfyNotifier:
    """Small daemon queue so notification I/O never blocks measurement threads."""

    def __init__(self, *, enabled: bool, server_url: str, topic: str, timeout: float = 5.0) -> None:
        self._lock = threading.Lock()
        self._enabled = False
        self._server_url = DEFAULT_NTFY_SERVER_URL
        self._topic = DEFAULT_NTFY_TOPIC
        self._timeout = float(timeout)
        self._queue: queue.Queue[_Notification] = queue.Queue(maxsize=100)
        self._thread = threading.Thread(target=self._run, name="ntfy-notifier", daemon=True)
        self._thread.start()
        self.configure(enabled=enabled, server_url=server_url, topic=topic)

    def configure(self, *, enabled: bool, server_url: str, topic: str) -> None:
        server = normalize_ntfy_server_url(server_url)
        normalized_topic = normalize_ntfy_topic(topic)
        with self._lock:
            self._enabled = bool(enabled)
            self._server_url = server
            self._topic = normalized_topic

    def notify(self, message: str, *, title: str = "Semi Auto Probe",
               priority: str = "default", tags: str = "") -> bool:
        with self._lock:
            if not self._enabled:
                return False
            item = _Notification(
                self._server_url, self._topic, str(message), str(title), str(priority), str(tags)
            )
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            logger.warning("ntfy notification queue is full; newest message was dropped.")
            return False
        return True

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                publish_ntfy(
                    item.server_url, item.topic, item.message,
                    title=item.title, priority=item.priority, tags=item.tags, timeout=self._timeout,
                )
            except Exception as exc:
                logger.warning("ntfy notification failed: %s", exc)
            finally:
                self._queue.task_done()


class NtfyErrorHandler(logging.Handler):
    """Send a generic alert for ERROR/CRITICAL without exporting log contents."""

    def __init__(self, notifier: NtfyNotifier, *, enabled: bool = True) -> None:
        super().__init__(level=logging.ERROR)
        self.notifier = notifier
        self.enabled = bool(enabled)

    def emit(self, record: logging.LogRecord) -> None:
        if not self.enabled or record.name == __name__:
            return
        try:
            self.notifier.notify(
                format_error_notification(record.created),
                title=f"SAP | {record.levelname}",
                priority="urgent" if record.levelno >= logging.CRITICAL else "high",
                tags="rotating_light" if record.levelno >= logging.CRITICAL else "warning",
            )
        except Exception:
            self.handleError(record)


def install_ntfy_error_handler(
    app_logger: logging.Logger, notifier: NtfyNotifier, *, enabled: bool = True
) -> NtfyErrorHandler:
    """Install once on the application logger, updating an existing handler."""
    for handler in app_logger.handlers:
        if isinstance(handler, NtfyErrorHandler):
            handler.notifier = notifier
            handler.enabled = bool(enabled)
            return handler
    handler = NtfyErrorHandler(notifier, enabled=enabled)
    app_logger.addHandler(handler)
    return handler
