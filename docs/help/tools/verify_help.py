#!/usr/bin/env python3
"""Validate the Semi Auto Probe HTML Help source and an optional CHM artifact.

The checker intentionally uses only the Python standard library so it can run on
a clean Windows workstation before HTML Help Workshop is installed.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


PROJECT_NAME = "SemiAutoProbeHelp.hhp"
TOC_NAME = "SemiAutoProbeHelp.hhc"
INDEX_NAME = "SemiAutoProbeHelp.hhk"
MAP_NAME = "context_ids.h"

EXPECTED_TOPICS = (
    "welcome",
    "quick-start",
    "interface",
    "safety",
    "main",
    "communication",
    "autofocus",
    "focusmap",
    "layoutbond",
    "imgstitch",
    "imgmatrix",
    "autotest",
    "wirebond-mapping",
    "workflow-editor",
    "measurement-nodes",
    "edgetrace",
    "ai-agent",
    "agent-templates",
    "config",
    "web-dashboard",
    "data-files",
    "shortcuts",
    "troubleshooting",
    "glossary",
    "release-notes",
)

REQUIRED_CONTEXTS = {
    "IDH_PAGE_MAIN": "topics/main.htm",
    "IDH_PAGE_COMMUNICATION": "topics/communication.htm",
    "IDH_PAGE_AUTOFOCUS": "topics/autofocus.htm",
    "IDH_PAGE_FOCUSMAP": "topics/focusmap.htm",
    "IDH_PAGE_LAYOUTBOND": "topics/layoutbond.htm",
    "IDH_PAGE_IMGSTITCH": "topics/imgstitch.htm",
    "IDH_PAGE_IMGMATRIX": "topics/imgmatrix.htm",
    "IDH_PAGE_AUTOTEST": "topics/autotest.htm",
    "IDH_PAGE_EDGETRACE": "topics/edgetrace.htm",
    "IDH_PAGE_AI_AGENT": "topics/ai-agent.htm",
    "IDH_PAGE_CONFIG": "topics/config.htm",
}

EXPECTED_CONTEXT_IDS = {
    "IDH_WELCOME": 1000,
    "IDH_QUICK_START": 1010,
    "IDH_INTERFACE": 1020,
    "IDH_SAFETY": 1030,
    "IDH_PAGE_MAIN": 1100,
    "IDH_PAGE_COMMUNICATION": 1200,
    "IDH_PAGE_AUTOFOCUS": 1300,
    "IDH_PAGE_FOCUSMAP": 1400,
    "IDH_PAGE_LAYOUTBOND": 1500,
    "IDH_PAGE_IMGSTITCH": 1600,
    "IDH_PAGE_IMGMATRIX": 1700,
    "IDH_PAGE_AUTOTEST": 1800,
    "IDH_WIREBOND_MAPPING": 1810,
    "IDH_WORKFLOW_EDITOR": 1820,
    "IDH_MEASUREMENT_NODES": 1830,
    "IDH_PAGE_EDGETRACE": 1900,
    "IDH_PAGE_AI_AGENT": 2000,
    "IDH_AGENT_TEMPLATES": 2010,
    "IDH_PAGE_CONFIG": 2100,
    "IDH_WEB_DASHBOARD": 2200,
    "IDH_DATA_FILES": 2300,
    "IDH_SHORTCUTS": 2400,
    "IDH_TROUBLESHOOTING": 2500,
    "IDH_GLOSSARY": 2600,
    "IDH_RELEASE_NOTES": 2700,
}
FORBIDDEN_TAGS = {
    "applet",
    "audio",
    "base",
    "canvas",
    "details",
    "embed",
    "form",
    "frame",
    "frameset",
    "iframe",
    "object",
    "picture",
    "script",
    "source",
    "summary",
    "svg",
    "template",
    "video",
}

DANGEROUS_SCHEMES = {"data", "javascript", "vbscript"}


def _norm(value: str) -> str:
    return value.strip().replace("\\", "/")


@dataclass
class Results:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: int = 0

    def check(self, condition: bool, message: str) -> None:
        self.checks += 1
        if not condition:
            self.errors.append(message)

    def warn(self, condition: bool, message: str) -> None:
        if not condition:
            self.warnings.append(message)


class DocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.ids: set[str] = set()
        self.tags: set[str] = set()
        self.events: list[str] = []
        self.title_depth = 0
        self.title_text: list[str] = []
        self.charset_utf8 = False
        self.stylesheets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        self.tags.add(tag)
        values = {name.lower(): value or "" for name, value in attrs}
        if tag == "title":
            self.title_depth += 1
        if values.get("id"):
            self.ids.add(values["id"])
        if tag == "a" and values.get("name"):
            self.ids.add(values["name"])
        if tag == "meta":
            charset = values.get("charset", "").lower()
            content = values.get("content", "").lower().replace(" ", "")
            self.charset_utf8 = self.charset_utf8 or charset == "utf-8" or "charset=utf-8" in content
        if tag == "link" and "stylesheet" in values.get("rel", "").lower():
            self.stylesheets.append(values.get("href", ""))
        for name, value in attrs:
            lname = name.lower()
            if lname.startswith("on"):
                self.events.append(lname)
            if lname in {"href", "src"} and value:
                self.links.append((lname, value))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title" and self.title_depth:
            self.title_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.title_depth:
            self.title_text.append(data)


class SitemapParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.locals: list[str] = []
        self.names: list[str] = []
        self.sitemap_objects = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value or "" for name, value in attrs}
        if tag.lower() == "object" and values.get("type", "").lower() == "text/sitemap":
            self.sitemap_objects += 1
        if tag.lower() != "param":
            return
        name = values.get("name", "").lower()
        value = values.get("value", "").strip()
        if name == "local" and value:
            self.locals.append(_norm(value))
        elif name == "name" and value:
            self.names.append(value)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


def read_utf8(path: Path, results: Results) -> str:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        results.errors.append(f"无法读取 {path}: {exc}")
        return ""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        results.errors.append(f"文件不是 UTF-8：{path} ({exc})")
        return ""


def parse_hhp(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip().upper()
            sections.setdefault(current, [])
        elif current:
            sections[current].append(line)
    return sections


def key_values(lines: list[str]) -> dict[str, str]:
    output: dict[str, str] = {}
    for line in lines:
        if "=" in line:
            key, value = line.split("=", 1)
            output[key.strip()] = value.strip()
    return output


def resolve_inside(root: Path, base: Path, ref: str) -> Path | None:
    value = unquote(urlsplit(ref).path).replace("\\", "/")
    if not value:
        return base
    candidate = (base / value).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def parse_sitemap(path: Path, results: Results, *, unique_locals: bool) -> SitemapParser:
    parser = SitemapParser()
    text = read_utf8(path, results)
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:  # HTMLParser can expose malformed attribute cases.
        results.errors.append(f"无法解析 {path.name}: {exc}")
    results.check(parser.sitemap_objects > 0, f"{path.name} 没有 text/sitemap 条目")
    if unique_locals:
        results.check(len(parser.locals) == len(set(parser.locals)), f"{path.name} 含重复 Local 条目")
    return parser


def verify_project(root: Path, results: Results) -> tuple[set[str], dict[str, str]]:
    project = root / PROJECT_NAME
    toc = root / TOC_NAME
    index = root / INDEX_NAME
    context_header = root / MAP_NAME
    for path in (project, toc, index, context_header, root / "assets" / "help.css"):
        results.check(path.is_file(), f"缺少必要文件：{path}")
    if results.errors:
        return set(), {}

    sections = parse_hhp(read_utf8(project, results))
    for name in ("OPTIONS", "WINDOWS", "FILES", "ALIAS", "MAP"):
        results.check(name in sections, f"HHP 缺少 [{name}] 段")

    for section_name in ("OPTIONS", "WINDOWS", "ALIAS"):
        malformed = [line for line in sections.get(section_name, []) if "=" not in line]
        results.check(not malformed, f"HHP [{section_name}] 含无等号的非法行：{malformed}")

    options = key_values(sections.get("OPTIONS", []))
    required_options = {
        "Binary Index": "Yes",
        "Binary TOC": "Yes",
        "Contents file": TOC_NAME,
        "Default topic": r"topics\welcome.htm",
        "Full-text search": "No",
        "Index file": INDEX_NAME,
    }
    for key, expected in required_options.items():
        results.check(options.get(key, "").lower() == expected.lower(), f"HHP 选项 {key} 应为 {expected!r}")
    results.check(options.get("Language", "").lower().startswith("0x804"), "HHP Language 必须为简体中文 0x804")
    compiled = _norm(options.get("Compiled file", ""))
    results.check(compiled.endswith(".chm"), "HHP Compiled file 必须指向 .chm")

    declared_files = {_norm(line) for line in sections.get("FILES", [])}
    expected_topic_paths = {f"topics/{name}.htm" for name in EXPECTED_TOPICS}
    for rel in sorted(expected_topic_paths):
        results.check(rel in declared_files, f"HHP [FILES] 未声明主题：{rel}")
    for rel in sorted(declared_files):
        path = root / Path(rel)
        results.check(path.is_file(), f"HHP [FILES] 引用了不存在的文件：{rel}")

    alias = {_norm(key): _norm(value) for key, value in key_values(sections.get("ALIAS", [])).items()}
    results.check(len(alias) == len(sections.get("ALIAS", [])), "HHP [ALIAS] 含重复符号")
    include_match = None
    for line in sections.get("MAP", []):
        match = re.fullmatch(r"#include\s+[\"<]?([^\">]+)[\">]?", line, re.IGNORECASE)
        if match:
            include_match = _norm(match.group(1))
            break
    results.check(include_match == MAP_NAME, f"HHP [MAP] 必须包含 {MAP_NAME}")

    map_text = read_utf8(context_header, results)
    contexts: dict[str, int] = {}
    for name, number in re.findall(r"^\s*#define\s+(IDH_[A-Z0-9_]+)\s+(\d+)\s*$", map_text, re.MULTILINE):
        contexts[name] = int(number)
    results.check(bool(contexts), f"{MAP_NAME} 未定义上下文 ID")
    results.check(contexts == EXPECTED_CONTEXT_IDS, f"{MAP_NAME} 的稳定 ID 集合或数值发生变化")
    results.check(len(contexts.values()) == len(set(contexts.values())), f"{MAP_NAME} 含重复数值 ID")
    results.check(set(alias) == set(contexts), "HHP [ALIAS] 与 context_ids.h 的符号集合不一致")
    for symbol, expected in REQUIRED_CONTEXTS.items():
        results.check(symbol in contexts, f"缺少应用页面上下文：{symbol}")
        results.check(alias.get(symbol) == expected, f"{symbol} 必须映射到 {expected}")
    for symbol, rel in sorted(alias.items()):
        results.check(rel in declared_files, f"{symbol} 映射目标未列入 [FILES]：{rel}")

    toc_data = parse_sitemap(toc, results, unique_locals=True)
    toc_locals = set(toc_data.locals)
    results.check(expected_topic_paths == toc_locals, "TOC 必须且只能包含规定的 25 个主题页面")
    index_data = parse_sitemap(index, results, unique_locals=False)
    results.check(len(index_data.names) >= 12, "Index 关键词过少（至少需要 12 个）")
    results.check(expected_topic_paths <= set(index_data.locals), "Index 必须覆盖全部 25 个主题页面")
    for rel in index_data.locals:
        results.check(rel in expected_topic_paths, f"Index 指向未知主题：{rel}")

    return declared_files, alias


def verify_css(root: Path, results: Results) -> None:
    css_path = root / "assets" / "help.css"
    css = read_utf8(css_path, results).lower()
    unsafe_patterns = {
        "CSS expression()": r"expression\s*\(",
        "ActiveX behavior": r"\bbehavior\s*:",
        "外部 @import": r"@import\b",
        "脚本 URL": r"(?:javascript|vbscript)\s*:",
        "CSS Grid": r"display\s*:\s*(?:inline-)?grid\b",
        "CSS Flexbox": r"display\s*:\s*(?:inline-)?flex\b",
        "CSS 自定义属性": r"(^|[;{])\s*--[\w-]+\s*:",
        "CSS var()": r"\bvar\s*\(",
        "sticky 定位": r"position\s*:\s*sticky\b",
    }
    for label, pattern in unsafe_patterns.items():
        results.check(re.search(pattern, css, re.MULTILINE) is None, f"help.css 使用了 CHM 不安全或不兼容的特性：{label}")


def verify_topics(root: Path, declared_files: set[str], results: Results) -> None:
    topic_dir = root / "topics"
    topic_paths = [topic_dir / f"{name}.htm" for name in EXPECTED_TOPICS]
    parsed: dict[Path, DocumentParser] = {}
    for path in topic_paths:
        if not path.is_file():
            continue
        text = read_utf8(path, results)
        parser = DocumentParser()
        try:
            parser.feed(text)
            parser.close()
        except Exception as exc:
            results.errors.append(f"无法解析 {path.relative_to(root)}: {exc}")
            continue
        parsed[path.resolve()] = parser
        rel = path.relative_to(root).as_posix()
        results.check(parser.charset_utf8, f"{rel} 未声明 UTF-8 字符集")
        results.check(bool("".join(parser.title_text).strip()), f"{rel} 缺少非空 <title>")
        results.check(not (parser.tags & FORBIDDEN_TAGS), f"{rel} 使用危险或旧 CHM 不兼容标签：{', '.join(sorted(parser.tags & FORBIDDEN_TAGS))}")
        results.check(not parser.events, f"{rel} 含内联事件处理器：{', '.join(sorted(set(parser.events)))}")
        styles = {_norm(value) for value in parser.stylesheets}
        results.check("../assets/help.css" in styles, f"{rel} 未引用统一样式 ../assets/help.css")

    for source, parser in parsed.items():
        source_rel = source.relative_to(root.resolve()).as_posix()
        for attribute, ref in parser.links:
            parts = urlsplit(ref)
            scheme = parts.scheme.lower()
            results.check(scheme not in DANGEROUS_SCHEMES, f"{source_rel} 含危险 {attribute}：{ref}")
            if scheme:
                results.check(scheme in {"http", "https", "mailto"}, f"{source_rel} 含不安全 URI scheme：{ref}")
                continue
            if ref.startswith("//"):
                continue
            target = source if not parts.path else resolve_inside(root, source.parent, ref)
            results.check(target is not None, f"{source_rel} 的链接越出帮助目录：{ref}")
            if target is None:
                continue
            results.check(target.is_file(), f"{source_rel} 的本地链接断开：{ref}")
            if not target.is_file():
                continue
            target_rel = target.relative_to(root.resolve()).as_posix()
            results.check(target_rel in declared_files, f"{source_rel} 引用的文件未列入 HHP [FILES]：{target_rel}")
            if parts.fragment and target.suffix.lower() == ".htm":
                target_parser = parsed.get(target.resolve())
                if target_parser is None:
                    target_parser = DocumentParser()
                    target_parser.feed(read_utf8(target, results))
                    parsed[target.resolve()] = target_parser
                anchor = unquote(parts.fragment)
                results.check(anchor in target_parser.ids, f"{source_rel} 指向不存在的锚点：{ref}")

    actual = {path.name for path in topic_dir.glob("*.htm")} if topic_dir.is_dir() else set()
    expected = {f"{name}.htm" for name in EXPECTED_TOPICS}
    results.check(actual == expected, f"topics 目录页面集合不符；缺少 {sorted(expected - actual)}，多出 {sorted(actual - expected)}")


def locate_hh(explicit: Path | None) -> Path | None:
    if explicit:
        return explicit
    windows = Path(r"C:\Windows\hh.exe")
    if windows.is_file():
        return windows
    found = shutil.which("hh.exe") or shutil.which("hh")
    return Path(found) if found else None


def verify_decompiled(chm: Path, hh_exe: Path, results: Results) -> None:
    results.check(hh_exe.is_file(), f"找不到 hh.exe：{hh_exe}")
    if not hh_exe.is_file():
        return
    with tempfile.TemporaryDirectory(prefix="semi-auto-probe-help-") as temp_name:
        output = Path(temp_name) / "decompiled"
        try:
            completed = subprocess.run(
                [str(hh_exe), "-decompile", str(output), str(chm)],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            results.errors.append(f"hh.exe 反编译失败：{exc}")
            return
        deadline = time.monotonic() + 15.0
        expected = {f"topics/{name}.htm" for name in EXPECTED_TOPICS}
        found: set[str] = set()
        while time.monotonic() < deadline:
            found = {path.relative_to(output).as_posix().lower() for path in output.rglob("*.htm")}
            if expected <= found:
                break
            time.sleep(0.25)
        results.check(expected <= found, f"CHM 反编译缺少主题：{sorted(expected - found)}；hh.exe 返回码 {completed.returncode}")


def verify_chm(chm: Path, hh_exe: Path | None, decompile: bool, results: Results) -> None:
    results.check(chm.is_file(), f"CHM 不存在：{chm}")
    if not chm.is_file():
        return
    try:
        size = chm.stat().st_size
        payload = chm.read_bytes()
        magic = payload[:4]
    except OSError as exc:
        results.errors.append(f"无法读取 CHM：{exc}")
        return
    results.check(size >= 4096, f"CHM 文件异常小：{size} bytes")
    results.check(magic == b"ITSF", f"CHM 缺少 ITSF 文件头：{magic!r}")
    results.check(b"$FIftiMain" not in payload, "CHM 意外包含全文索引；项目应只发布目录与关键字索引")
    if decompile:
        if hh_exe is None:
            results.warnings.append("找不到 hh.exe，已跳过反编译内容核对")
        else:
            verify_decompiled(chm, hh_exe, results)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="docs/help 目录")
    parser.add_argument("--chm", type=Path, help="可选：同时检查已编译 CHM")
    parser.add_argument("--hh-exe", type=Path, help="可选：用于反编译检查的 hh.exe")
    parser.add_argument("--no-decompile", action="store_true", help="检查 CHM 文件头但不调用 hh.exe 反编译")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    results = Results()
    declared, _alias = verify_project(root, results)
    if declared:
        verify_css(root, results)
        verify_topics(root, declared, results)
    if args.chm:
        verify_chm(args.chm.resolve(), locate_hh(args.hh_exe), not args.no_decompile, results)

    for warning in results.warnings:
        print(f"WARNING: {warning}")
    for error in results.errors:
        print(f"ERROR: {error}")
    if results.errors:
        print(f"FAILED: {len(results.errors)} error(s), {results.checks} checks")
        return 1
    print(f"OK: {results.checks} checks passed for {root}")
    if args.chm:
        print(f"OK: CHM verified at {args.chm.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
