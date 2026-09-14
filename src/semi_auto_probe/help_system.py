"""Context-sensitive access to the packaged Microsoft HTML Help manual."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path


HELP_FILENAME = "SemiAutoProbeHelp.chm"
HELP_FILE_ENV = "SEMI_AUTO_PROBE_HELP_FILE"
HH_DISPLAY_TOC = 0x0001
HH_HELP_CONTEXT = 0x000F


@dataclass(frozen=True)
class HelpTopic:
    context_id: int
    filename: str
    title: str


HELP_TOPICS: dict[str, HelpTopic] = {
    "Welcome": HelpTopic(1000, "welcome.htm", "欢迎与手册说明"),
    "QuickStart": HelpTopic(1010, "quick-start.htm", "快速开始"),
    "Interface": HelpTopic(1020, "interface.htm", "界面与状态"),
    "Safety": HelpTopic(1030, "safety.htm", "安全、探针与急停"),
    "Main": HelpTopic(1100, "main.htm", "Main：手动运动与实时视觉"),
    "Communication": HelpTopic(1200, "communication.htm", "Communication：控制器通信"),
    "AutoFocus": HelpTopic(1300, "autofocus.htm", "AutoFocus"),
    "FocusMap": HelpTopic(1400, "focusmap.htm", "FocusMap"),
    "LayoutBond": HelpTopic(1500, "layoutbond.htm", "LayoutBond"),
    "ImgStitch": HelpTopic(1600, "imgstitch.htm", "ImgStitch"),
    "ImgMatrix": HelpTopic(1700, "imgmatrix.htm", "ImgMatrix"),
    "AutoTest": HelpTopic(1800, "autotest.htm", "AutoTest"),
    "WireBondMapping": HelpTopic(1810, "wirebond-mapping.htm", "Wire-bond Mapping"),
    "WorkflowEditor": HelpTopic(1820, "workflow-editor.htm", "Measurement Workflow v2"),
    "MeasurementNodes": HelpTopic(1830, "measurement-nodes.htm", "测量节点与仪器"),
    "EdgeTrace": HelpTopic(1900, "edgetrace.htm", "EdgeTrace"),
    "AI Agent": HelpTopic(2000, "ai-agent.htm", "AI Agent"),
    "AgentTemplates": HelpTopic(2010, "agent-templates.htm", "AI Agent 测试模板"),
    "Config": HelpTopic(2100, "config.htm", "Config"),
    "WebDashboard": HelpTopic(2200, "web-dashboard.htm", "Web Dashboard"),
    "DataFiles": HelpTopic(2300, "data-files.htm", "文件、会话与导出"),
    "Shortcuts": HelpTopic(2400, "shortcuts.htm", "快捷键"),
    "Troubleshooting": HelpTopic(2500, "troubleshooting.htm", "故障排查"),
    "Glossary": HelpTopic(2600, "glossary.htm", "术语表"),
    "ReleaseNotes": HelpTopic(2700, "release-notes.htm", "版本与文档基线"),
}

PAGE_HELP_TOPICS: dict[str, str] = {
    "Main": "Main",
    "Communication": "Communication",
    "AutoFocus": "AutoFocus",
    "FocusMap": "FocusMap",
    "LayoutBond": "LayoutBond",
    "ImgStitch": "ImgStitch",
    "ImgMatrix": "ImgMatrix",
    "AutoTest": "AutoTest",
    "EdgeTrace": "EdgeTrace",
    "AI Agent": "AI Agent",
    "Config": "Config",
}


@dataclass(frozen=True)
class HelpOpenResult:
    opened: bool
    topic_key: str
    location: Path | None
    message: str


def normalize_topic_key(page_or_topic: str | None) -> str:
    """Resolve an application page name or a manual topic key."""
    if not page_or_topic:
        return "Welcome"
    if page_or_topic in HELP_TOPICS:
        return page_or_topic
    return PAGE_HELP_TOPICS.get(page_or_topic, "Welcome")


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def help_file_candidates() -> tuple[Path, ...]:
    """Return candidates in override, packaged, then development-build order."""
    candidates: list[Path] = []
    override = os.environ.get(HELP_FILE_ENV, "").strip()
    if override:
        path = Path(override).expanduser()
        candidates.append(path / HELP_FILENAME if path.is_dir() else path)
    package_dir = Path(__file__).resolve().parent
    repo_root = _repository_root()
    candidates.extend(
        (
            package_dir / "assets" / HELP_FILENAME,
            repo_root / "docs" / "help" / "build" / HELP_FILENAME,
            repo_root / "docs" / "help" / HELP_FILENAME,
        )
    )
    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if resolved not in unique:
            unique.append(resolved)
    return tuple(unique)


def find_help_file() -> Path | None:
    return next((path for path in help_file_candidates() if path.is_file()), None)


def source_topic_path(topic_key: str) -> Path | None:
    topic = HELP_TOPICS[normalize_topic_key(topic_key)]
    path = _repository_root() / "docs" / "help" / "topics" / topic.filename
    return path.resolve() if path.is_file() else None


def _open_chm_context(chm_path: Path, context_id: int, parent_handle: int) -> bool:
    """Open one mapped topic with the native HTML Help API."""
    html_help = ctypes.WinDLL("hhctrl.ocx").HtmlHelpW
    html_help.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_size_t]
    html_help.restype = ctypes.c_void_p
    window = html_help(
        ctypes.c_void_p(parent_handle or 0),
        str(chm_path),
        HH_HELP_CONTEXT,
        ctypes.c_size_t(context_id),
    )
    return bool(window)


def _open_chm_with_viewer(chm_path: Path, context_id: int) -> None:
    viewer = Path(os.environ.get("WINDIR", r"C:\Windows")) / "hh.exe"
    if not viewer.is_file():
        raise FileNotFoundError(f"Windows HTML Help viewer not found: {viewer}")
    subprocess.Popen(
        [str(viewer), "-mapid", str(context_id), f"ms-its:{chm_path}"],
        close_fds=True,
    )


def open_help(page_or_topic: str | None = None, *, parent_handle: int = 0) -> HelpOpenResult:
    """Open the mapped CHM topic, falling back to source HTML during development."""
    topic_key = normalize_topic_key(page_or_topic)
    topic = HELP_TOPICS[topic_key]
    chm_path = find_help_file()
    if chm_path is not None and sys.platform == "win32":
        try:
            if _open_chm_context(chm_path, topic.context_id, parent_handle):
                return HelpOpenResult(True, topic_key, chm_path, f"Help opened: {topic.title}.")
        except (AttributeError, OSError):
            pass
        try:
            _open_chm_with_viewer(chm_path, topic.context_id)
            return HelpOpenResult(True, topic_key, chm_path, f"Help opened: {topic.title}.")
        except OSError as exc:
            return HelpOpenResult(False, topic_key, chm_path, f"Could not open Help: {exc}")

    source_path = source_topic_path(topic_key)
    if source_path is not None:
        try:
            opened = bool(webbrowser.open(source_path.as_uri()))
        except (OSError, webbrowser.Error) as exc:
            return HelpOpenResult(False, topic_key, source_path, f"Could not open Help: {exc}")
        if opened:
            return HelpOpenResult(True, topic_key, source_path, f"Help source opened: {topic.title}.")

    checked = ", ".join(str(path) for path in help_file_candidates())
    return HelpOpenResult(
        False,
        topic_key,
        None,
        f"Help file is not installed. Checked: {checked}",
    )
