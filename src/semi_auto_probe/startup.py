from __future__ import annotations

import importlib
import logging
import os
import select
import sys
import time
from types import ModuleType
from typing import Callable, TextIO

from .logging_utils import configure_logging, print_startup_banner


RESTORE_FLAGS = {"--restore-last", "--load-last", "--last"}
PROMPT_SKIP_FLAGS = {"-h", "--help", "--measure-debug"}


def _read_console_key() -> str | None:
    if os.name == "nt":
        import msvcrt

        return msvcrt.getwch() if msvcrt.kbhit() else None
    try:
        readable, _, _ = select.select([sys.stdin], [], [], 0)
    except (OSError, ValueError):
        return None
    return sys.stdin.read(1) if readable else None


def prompt_restore_latest(
    *,
    timeout_seconds: float = 3.0,
    key_reader: Callable[[], str | None] | None = None,
    output: TextIO | None = None,
) -> bool:
    """Ask for latest FocusMap/alignment restore without blocking past the timeout."""
    stream = output or sys.stdout
    reader = key_reader or _read_console_key
    if key_reader is None and not sys.stdin.isatty():
        return False

    stream.write(
        "Restore latest FocusMap and alignment parameters? "
        f"[y/N] ({timeout_seconds:g}s timeout): "
    )
    stream.flush()
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while time.monotonic() < deadline:
        key = reader()
        if key:
            normalized = key.lower()
            if normalized == "y":
                stream.write("Y\n")
                stream.flush()
                return True
            if normalized in {"n", "\r", "\n"}:
                stream.write("N\n")
                stream.flush()
                return False
        time.sleep(0.05)
    stream.write("No response; using defaults.\n")
    stream.flush()
    return False


def with_restore_choice(
    argv: list[str],
    *,
    prompt: Callable[[], bool] | None = None,
) -> list[str]:
    args = list(argv)
    if any(flag in args for flag in RESTORE_FLAGS | PROMPT_SKIP_FLAGS):
        return args
    if (prompt or prompt_restore_latest)():
        args.append("--restore-last")
    return args


class ConsoleStartupProgress:
    """Write concise, de-duplicated startup progress updates to the CLI."""

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self.logger = logger or configure_logging()
        self._last_update: tuple[str, int] | None = None

    def __call__(self, status: str, value: int) -> None:
        update = (str(status), max(0, min(100, int(value))))
        if update == self._last_update:
            return
        self._last_update = update
        message, percentage = update
        self.logger.info("Startup %3d%% | %s", percentage, message)


def launch_loaded_app(
    app_module: ModuleType,
    argv: list[str] | None,
    *,
    startup_progress: Callable[[str, int], None],
    on_ready: Callable[[], None] | None,
) -> None:
    """Launch an already-imported application with startup progress reporting."""
    app_module.main(
        argv,
        startup_progress=startup_progress,
        on_ready=on_ready,
        show_banner=False,
    )


def main(argv: list[str] | None = None) -> None:
    print_startup_banner()
    logger = configure_logging()
    launch_argv = with_restore_choice(list(sys.argv[1:] if argv is None else argv))
    if any(flag in launch_argv for flag in RESTORE_FLAGS):
        logger.info("Startup restore | Latest FocusMap and alignment parameters selected.")
    else:
        logger.info("Startup restore | Using default parameters.")
    progress = ConsoleStartupProgress(logger=logger)
    progress("Loading application components...", 5)
    try:
        loaded = importlib.import_module("semi_auto_probe.app")
    except BaseException as exc:
        print(f"[startup failed] {exc}", file=sys.stderr, flush=True)
        raise

    progress("Preparing the main window...", 58)
    launch_loaded_app(
        loaded,
        launch_argv,
        startup_progress=progress,
        on_ready=None,
    )
