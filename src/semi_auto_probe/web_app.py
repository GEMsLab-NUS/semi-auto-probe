from __future__ import annotations

import atexit
import hmac
import json
import mimetypes
import os
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


WEB_DIR = Path(__file__).parent / "web"
STATIC_DIR = WEB_DIR / "static"
ACCESS_TOKEN_ENV = "SEMI_AUTO_PROBE_WEB_TOKEN"
AUTOTEST_SESSION_DIR_ENV = "SEMI_AUTO_PROBE_AUTOTEST_SESSION_DIR"
PID_FILE_ENV = "SEMI_AUTO_PROBE_WEB_PID_FILE"
DEFAULT_PID_FILE = Path.cwd() / ".runtime" / "semi-auto-probe-web.pid"
DEFAULT_AUTOTEST_SESSION_DIR = Path.cwd() / "autotest_session"
JSON_PREVIEW_MAX_BYTES = 2 * 1024 * 1024
TEXT_PREVIEW_MAX_BYTES = 128 * 1024
IMAGE_SUFFIXES = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
TEXT_SUFFIXES = {".csv", ".json", ".log", ".md", ".txt", ".yaml", ".yml"}
KNOWN_TOP_LEVEL_CATEGORIES = ("images", "iv", "wobb", "b1500")
DEFAULT_CAMERA_SOURCE = "auto"
DIRECT_CAMERA_LABELS = {
    0: "ProbeOM",
    1: "EmbeddedCam",
    2: "MonitorCam",
}


@dataclass
class WebStatus:
    auth_required: bool
    session_root: str
    session_root_exists: bool
    session_count: int
    latest_session_id: str | None
    latest_session_modified_at: str | None
    active_http_requests: int
    total_http_requests: int
    total_file_downloads: int
    last_error: str | None


class AutoTestSessionRepository:
    def __init__(self, root: Path) -> None:
        self.root = root

    @classmethod
    def from_environment(cls) -> "AutoTestSessionRepository":
        return cls(Path(os.environ.get(AUTOTEST_SESSION_DIR_ENV, str(DEFAULT_AUTOTEST_SESSION_DIR))))

    def root_path(self) -> Path:
        return self.root.expanduser().resolve()

    def root_exists(self) -> bool:
        return self.root_path().is_dir()

    def session_dirs(self) -> list[Path]:
        root = self.root_path()
        if not root.is_dir():
            return []
        dirs = [path for path in root.iterdir() if path.is_dir()]
        dirs.sort(key=lambda path: self._safe_stat(path).st_mtime if self._safe_stat(path) else 0.0, reverse=True)
        return dirs

    def session_count(self) -> int:
        return len(self.session_dirs())

    def list_sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        capped_limit = min(max(int(limit), 1), 500)
        return [self.session_summary(session_dir) for session_dir in self.session_dirs()[:capped_limit]]

    def latest_session(self) -> dict[str, Any] | None:
        dirs = self.session_dirs()
        return self.session_summary(dirs[0]) if dirs else None

    def session_summary(self, session_dir: Path) -> dict[str, Any]:
        stats = self._scan_files(session_dir, include_files=False)
        created_at = self._created_at_from_session_id(session_dir.name)
        modified_at = self._iso_from_timestamp(stats["modified_timestamp"] or self._path_mtime(session_dir))
        status = self._session_status(stats["file_count"], stats["modified_timestamp"])
        return {
            "id": session_dir.name,
            "created_at": created_at,
            "modified_at": modified_at,
            "status": status,
            "relative_path": session_dir.name,
            "size_bytes": stats["size_bytes"],
            "file_count": stats["file_count"],
            "counts": stats["counts"],
            "categories": stats["categories"],
        }

    def session_detail(self, session_id: str, *, file_limit: int = 5000, json_limit: int = 300) -> dict[str, Any]:
        session_dir = self.resolve_session_dir(session_id)
        summary = self.session_summary(session_dir)
        scan = self._scan_files(session_dir, include_files=True, file_limit=file_limit)
        json_files = [file for file in scan["files"] if file["kind"] == "json"]
        json_documents = [
            self.json_document_summary(session_dir, file["path"])
            for file in json_files[: min(max(json_limit, 0), 1000)]
        ]
        devices = self._summarize_devices(json_documents)
        result_counts: dict[str, int] = {}
        for item in json_documents:
            result_type = str(item.get("result_type") or "metadata")
            result_counts[result_type] = result_counts.get(result_type, 0) + 1

        return {
            "summary": summary,
            "files": scan["files"],
            "file_limit": scan["file_limit"],
            "file_total": scan["file_count"],
            "json_documents": json_documents,
            "json_total": len(json_files),
            "devices": devices,
            "result_counts": result_counts,
        }

    def json_preview(self, session_id: str, file_path: str) -> dict[str, Any]:
        session_dir = self.resolve_session_dir(session_id)
        path = self.resolve_session_file(session_id, file_path)
        if path.suffix.lower() != ".json":
            raise HTTPException(status_code=400, detail="Only JSON files can be previewed as JSON.")
        payload = self._read_json_payload(path)
        return {
            "path": self._relative_path(path, session_dir),
            "size_bytes": self._path_size(path),
            "modified_at": self._iso_from_timestamp(self._path_mtime(path)),
            "summary": self._summarize_json_payload(path, payload),
            "content": payload,
        }

    def text_preview(self, session_id: str, file_path: str) -> dict[str, Any]:
        session_dir = self.resolve_session_dir(session_id)
        path = self.resolve_session_file(session_id, file_path)
        if path.suffix.lower() not in TEXT_SUFFIXES:
            raise HTTPException(status_code=400, detail="This file type does not support text preview.")
        if self._path_size(path) > TEXT_PREVIEW_MAX_BYTES:
            raise HTTPException(status_code=413, detail="Text preview is limited to 128 KiB.")
        return {
            "path": self._relative_path(path, session_dir),
            "size_bytes": self._path_size(path),
            "modified_at": self._iso_from_timestamp(self._path_mtime(path)),
            "content": path.read_text(encoding="utf-8", errors="replace"),
        }

    def json_document_summary(self, session_dir: Path, file_path: str) -> dict[str, Any]:
        path = self._resolve_file_in_session(session_dir, file_path)
        try:
            payload = self._read_json_payload(path)
            return self._summarize_json_payload(path, payload)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, HTTPException) as exc:
            return {
                "path": self._relative_path(path, session_dir),
                "name": path.name,
                "result_type": "invalid_json",
                "error": str(exc),
            }

    def resolve_session_dir(self, session_id: str) -> Path:
        if not session_id or "/" in session_id or "\\" in session_id or session_id in {".", ".."}:
            raise HTTPException(status_code=404, detail="AutoTest session not found.")
        root = self.root_path()
        session_dir = (root / session_id).resolve()
        if not self._is_relative_to(session_dir, root) or not session_dir.is_dir():
            raise HTTPException(status_code=404, detail="AutoTest session not found.")
        return session_dir

    def resolve_session_file(self, session_id: str, file_path: str) -> Path:
        session_dir = self.resolve_session_dir(session_id)
        return self._resolve_file_in_session(session_dir, file_path)

    def _resolve_file_in_session(self, session_dir: Path, file_path: str) -> Path:
        normalized = file_path.strip().replace("\\", "/")
        if not normalized or normalized.startswith("/") or "/../" in f"/{normalized}/":
            raise HTTPException(status_code=404, detail="File not found.")
        path = (session_dir / normalized).resolve()
        if not self._is_relative_to(path, session_dir) or not path.is_file():
            raise HTTPException(status_code=404, detail="File not found.")
        return path

    def _scan_files(self, session_dir: Path, *, include_files: bool, file_limit: int = 5000) -> dict[str, Any]:
        counts = {"files": 0, "json": 0, "csv": 0, "images": 0, "other": 0}
        categories = {name: {"file_count": 0, "size_bytes": 0} for name in (*KNOWN_TOP_LEVEL_CATEGORIES, "other")}
        size_bytes = 0
        modified_timestamp = 0.0
        files: list[dict[str, Any]] = []
        capped_limit = min(max(int(file_limit), 1), 20000)

        for path in self._iter_files(session_dir):
            stat = self._safe_stat(path)
            if stat is None:
                continue
            relative_path = self._relative_path(path, session_dir)
            top_category = self._top_category(relative_path)
            category = top_category if top_category in categories else "other"
            kind = self._file_kind(path)
            counts["files"] += 1
            if kind == "json":
                counts["json"] += 1
            elif kind == "csv":
                counts["csv"] += 1
            elif kind == "image":
                counts["images"] += 1
            else:
                counts["other"] += 1
            categories[category]["file_count"] += 1
            categories[category]["size_bytes"] += stat.st_size
            size_bytes += stat.st_size
            modified_timestamp = max(modified_timestamp, stat.st_mtime)
            if include_files and len(files) < capped_limit:
                files.append(
                    {
                        "path": relative_path,
                        "name": path.name,
                        "directory": str(Path(relative_path).parent).replace("\\", "/"),
                        "category": category,
                        "kind": kind,
                        "extension": path.suffix.lower(),
                        "size_bytes": stat.st_size,
                        "modified_at": self._iso_from_timestamp(stat.st_mtime),
                    }
                )

        files.sort(key=lambda item: (str(item["category"]), str(item["path"]).lower()))
        return {
            "counts": counts,
            "categories": categories,
            "size_bytes": size_bytes,
            "file_count": counts["files"],
            "modified_timestamp": modified_timestamp,
            "files": files,
            "file_limit": capped_limit,
        }

    def _iter_files(self, session_dir: Path) -> list[Path]:
        try:
            return sorted((path for path in session_dir.rglob("*") if path.is_file()), key=lambda item: item.as_posix().lower())
        except OSError:
            return []

    def _read_json_payload(self, path: Path) -> Any:
        if self._path_size(path) > JSON_PREVIEW_MAX_BYTES:
            raise HTTPException(status_code=413, detail="JSON preview is limited to 2 MiB.")
        return json.loads(path.read_text(encoding="utf-8"))

    def _summarize_json_payload(self, path: Path, payload: Any) -> dict[str, Any]:
        session_dir = self._session_dir_for_file(path)
        relative_path = self._relative_path(path, session_dir) if session_dir else path.name
        if not isinstance(payload, dict):
            return {
                "path": relative_path,
                "name": path.name,
                "result_type": "json",
                "created_at": None,
                "device": None,
                "measurement": {},
                "statistics": {},
            }
        device = payload.get("device") if isinstance(payload.get("device"), dict) else None
        return {
            "path": relative_path,
            "name": path.name,
            "result_type": payload.get("result_type") or payload.get("format") or "json",
            "created_at": payload.get("created_at"),
            "device": device,
            "measurement": self._compact_scalar_mapping(payload.get("measurement")),
            "statistics": self._compact_scalar_mapping(payload.get("statistics")),
            "csv_file": payload.get("csv_file"),
            "json_file": payload.get("json_file"),
        }

    def _session_dir_for_file(self, path: Path) -> Path | None:
        root = self.root_path()
        try:
            relative = path.resolve().relative_to(root)
        except ValueError:
            return None
        if not relative.parts:
            return None
        return root / relative.parts[0]

    @staticmethod
    def _summarize_devices(json_documents: list[dict[str, Any]]) -> dict[str, Any]:
        names: set[str] = set()
        rows: set[int] = set()
        cols: set[int] = set()
        for item in json_documents:
            device = item.get("device")
            if not isinstance(device, dict):
                continue
            name = device.get("name")
            if name:
                names.add(str(name))
            if isinstance(device.get("row"), int):
                rows.add(int(device["row"]))
            if isinstance(device.get("col"), int):
                cols.add(int(device["col"]))
        return {
            "count": len(names),
            "sample": sorted(names)[:12],
            "rows": len(rows),
            "cols": len(cols),
        }

    @staticmethod
    def _compact_scalar_mapping(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        compact: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(item, (str, int, float, bool)) or item is None:
                compact[str(key)] = item
        return compact

    @staticmethod
    def _file_kind(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".json":
            return "json"
        if suffix == ".csv":
            return "csv"
        if suffix in IMAGE_SUFFIXES:
            return "image"
        if suffix in TEXT_SUFFIXES:
            return "text"
        return "binary"

    @staticmethod
    def _top_category(relative_path: str) -> str:
        first = relative_path.split("/", 1)[0].lower()
        return first if first in KNOWN_TOP_LEVEL_CATEGORIES else "other"

    @staticmethod
    def _session_status(file_count: int, modified_timestamp: float) -> str:
        if file_count <= 0:
            return "empty"
        if modified_timestamp and time.time() - modified_timestamp < 300:
            return "active"
        return "complete"

    @staticmethod
    def _created_at_from_session_id(session_id: str) -> str | None:
        try:
            return datetime.strptime(session_id, "%Y%m%d_%H%M%S").isoformat(timespec="seconds")
        except ValueError:
            return None

    @staticmethod
    def _iso_from_timestamp(timestamp: float | None) -> str | None:
        if not timestamp:
            return None
        return datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")

    @staticmethod
    def _relative_path(path: Path, root: Path) -> str:
        return path.resolve().relative_to(root.resolve()).as_posix()

    @staticmethod
    def _safe_stat(path: Path):
        try:
            return path.stat()
        except OSError:
            return None

    @classmethod
    def _path_mtime(cls, path: Path) -> float:
        stat = cls._safe_stat(path)
        return stat.st_mtime if stat else 0.0

    @classmethod
    def _path_size(cls, path: Path) -> int:
        stat = cls._safe_stat(path)
        return stat.st_size if stat else 0

    @staticmethod
    def _is_relative_to(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False


class WebProbeService:
    def __init__(self) -> None:
        self._metrics_lock = threading.RLock()
        self._active_http_requests = 0
        self._total_http_requests = 0
        self._total_file_downloads = 0
        self._clients: dict[str, dict[str, Any]] = {}
        self._last_error: str | None = None

    def repository(self) -> AutoTestSessionRepository:
        return AutoTestSessionRepository.from_environment()

    def status(self) -> WebStatus:
        repository = self.repository()
        latest = repository.latest_session()
        return WebStatus(
            auth_required=bool(os.environ.get(ACCESS_TOKEN_ENV)),
            session_root=str(repository.root_path()),
            session_root_exists=repository.root_exists(),
            session_count=repository.session_count(),
            latest_session_id=latest["id"] if latest else None,
            latest_session_modified_at=latest["modified_at"] if latest else None,
            active_http_requests=self._active_http_requests,
            total_http_requests=self._total_http_requests,
            total_file_downloads=self._total_file_downloads,
            last_error=self._last_error,
        )

    def begin_request(self, request: Request) -> str:
        client_id = self._client_id_from_request(request)
        with self._metrics_lock:
            self._active_http_requests += 1
            self._total_http_requests += 1
            entry = self._clients.setdefault(
                client_id,
                {
                    "ip": client_id,
                    "user_agent": request.headers.get("user-agent", "-"),
                    "active_requests": 0,
                    "total_requests": 0,
                    "file_downloads": 0,
                    "last_path": "",
                    "last_seen": 0.0,
                },
            )
            entry["active_requests"] = int(entry["active_requests"]) + 1
            entry["total_requests"] = int(entry["total_requests"]) + 1
            entry["last_path"] = request.url.path
            entry["last_seen"] = time.time()
            entry["user_agent"] = request.headers.get("user-agent", "-")
            return client_id

    def end_request(self, client_id: str) -> None:
        with self._metrics_lock:
            self._active_http_requests = max(0, self._active_http_requests - 1)
            if client_id in self._clients:
                self._clients[client_id]["active_requests"] = max(0, int(self._clients[client_id]["active_requests"]) - 1)

    def record_file_download(self, request: Request, file_path: str) -> None:
        client_id = self._client_id_from_request(request)
        with self._metrics_lock:
            self._total_file_downloads += 1
            entry = self._clients.setdefault(
                client_id,
                {
                    "ip": client_id,
                    "user_agent": request.headers.get("user-agent", "-"),
                    "active_requests": 0,
                    "total_requests": 0,
                    "file_downloads": 0,
                    "last_path": "",
                    "last_seen": 0.0,
                },
            )
            entry["file_downloads"] = int(entry.get("file_downloads", 0)) + 1
            entry["last_path"] = file_path
            entry["last_seen"] = time.time()
            entry["user_agent"] = request.headers.get("user-agent", "-")

    def connections(self) -> dict[str, Any]:
        now = time.time()
        with self._metrics_lock:
            clients = []
            for entry in self._clients.values():
                clients.append(
                    {
                        "ip": entry["ip"],
                        "user_agent": entry["user_agent"],
                        "active_requests": entry["active_requests"],
                        "active_camera_streams": 0,
                        "file_downloads": entry.get("file_downloads", 0),
                        "total_requests": entry["total_requests"],
                        "last_path": entry["last_path"],
                        "last_seen_seconds_ago": round(now - float(entry["last_seen"]), 1) if entry["last_seen"] else None,
                    }
                )
            clients.sort(key=lambda item: (int(item["active_requests"]), int(item["file_downloads"]), int(item["total_requests"])), reverse=True)
            return {
                "active_http_requests": self._active_http_requests,
                "active_camera_streams": 0,
                "total_http_requests": self._total_http_requests,
                "total_file_downloads": self._total_file_downloads,
                "client_count": len(clients),
                "clients": clients,
            }

    @staticmethod
    def _client_id_from_request(request: Request) -> str:
        cf_ip = request.headers.get("cf-connecting-ip")
        if cf_ip:
            return cf_ip.strip()
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",", 1)[0].strip()
        return request.client.host if request.client else "unknown"

    @staticmethod
    def _parse_camera_source(source: str) -> tuple[str, str | None, int | None]:
        if source in {"auto", "desktop"}:
            return source, None, None
        if source == "auto-direct":
            return "direct", DEFAULT_CAMERA_SOURCE, None
        if source.startswith("direct:"):
            try:
                index = int(source.split(":", 1)[1])
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid direct camera source.") from exc
            if index < 0 or index > 16:
                raise HTTPException(status_code=400, detail="Camera index out of range.")
            return "direct", f"opencv:{index}", index
        normalized = WebProbeService._normalize_camera_source(source)
        if normalized.startswith(("miicam", "opencv", "toupcam", "dshow", "msmf", "any")):
            return "direct", normalized, None
        raise HTTPException(status_code=400, detail="Unsupported camera source.")

    @staticmethod
    def _direct_camera_label(source: str) -> str:
        normalized = WebProbeService._normalize_camera_source(source.replace("auto-direct", DEFAULT_CAMERA_SOURCE))
        if normalized == DEFAULT_CAMERA_SOURCE:
            return "Auto direct camera"
        if normalized.startswith("miicam:"):
            return f"MiiCam SDK {normalized.split(':', 1)[1]}"
        if normalized.startswith("toupcam:"):
            return f"ToupCam SDK {normalized.split(':', 1)[1]}"
        if normalized.startswith("opencv:"):
            index = int(normalized.split(":", 1)[1])
            return DIRECT_CAMERA_LABELS.get(index, f"OpenCV camera {index}")
        return normalized

    @staticmethod
    def _normalize_camera_source(value: str | int | None, default: str = DEFAULT_CAMERA_SOURCE) -> str:
        text = default if value is None else str(value).strip()
        if not text:
            return default
        lowered = text.lower()
        if lowered.isdigit():
            return f"opencv:{lowered}"
        if lowered in {"auto", "opencv", "opencv-auto"}:
            return lowered.replace("opencv-auto", "opencv")
        if lowered in {"mmgr", "miicam", "miicam-sdk"}:
            return "miicam"
        if lowered.startswith("direct:"):
            return f"opencv:{lowered.split(':', 1)[1]}"
        return lowered


service = WebProbeService()
app = FastAPI(
    title="Semi Auto Probe Web",
    version="0.2.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

cors_origins = [item.strip() for item in os.environ.get("SEMI_AUTO_PROBE_WEB_CORS_ORIGINS", "").split(",") if item.strip()]
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["X-Access-Token", "Content-Type"],
        allow_credentials=False,
    )

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def collect_request_metrics(request: Request, call_next):
    client_id = ""
    if not request.url.path.startswith("/internal/"):
        client_id = service.begin_request(request)
    try:
        return await call_next(request)
    finally:
        if not request.url.path.startswith("/internal/"):
            service.end_request(client_id)


def pid_file_path() -> Path:
    return Path(os.environ.get(PID_FILE_ENV, str(DEFAULT_PID_FILE)))


def write_pid_file() -> None:
    path = pid_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()), encoding="ascii")
    atexit.register(remove_pid_file)


def remove_pid_file() -> None:
    path = pid_file_path()
    try:
        if path.read_text(encoding="ascii").strip() == str(os.getpid()):
            path.unlink()
    except OSError:
        return


def require_access_token(
    x_access_token: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> None:
    expected = os.environ.get(ACCESS_TOKEN_ENV)
    if not expected:
        return
    if (x_access_token and hmac.compare_digest(x_access_token, expected)) or (token and hmac.compare_digest(token, expected)):
        return
    raise HTTPException(status_code=401, detail=f"Missing or invalid {ACCESS_TOKEN_ENV}.")


@app.on_event("startup")
def startup() -> None:
    write_pid_file()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status", dependencies=[Depends(require_access_token)])
def api_status() -> dict[str, Any]:
    return asdict(service.status())


@app.get("/api/connections", dependencies=[Depends(require_access_token)])
def api_connections() -> dict[str, Any]:
    return service.connections()


@app.get("/api/autotest/sessions", dependencies=[Depends(require_access_token)])
def api_autotest_sessions(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    repository = service.repository()
    sessions = repository.list_sessions(limit=limit)
    totals = {
        "sessions": len(sessions),
        "files": sum(int(item["file_count"]) for item in sessions),
        "json": sum(int(item["counts"]["json"]) for item in sessions),
        "csv": sum(int(item["counts"]["csv"]) for item in sessions),
        "images": sum(int(item["counts"]["images"]) for item in sessions),
        "size_bytes": sum(int(item["size_bytes"]) for item in sessions),
    }
    return {
        "root": str(repository.root_path()),
        "root_exists": repository.root_exists(),
        "total_session_count": repository.session_count(),
        "listed_session_count": len(sessions),
        "totals": totals,
        "sessions": sessions,
    }


@app.get("/api/autotest/sessions/latest", dependencies=[Depends(require_access_token)])
def api_autotest_latest_session() -> dict[str, Any]:
    latest = service.repository().latest_session()
    if latest is None:
        raise HTTPException(status_code=404, detail="No AutoTest sessions found.")
    return latest


@app.get("/api/autotest/sessions/{session_id}", dependencies=[Depends(require_access_token)])
def api_autotest_session_detail(
    session_id: str,
    file_limit: int = Query(default=5000, ge=1, le=20000),
    json_limit: int = Query(default=300, ge=0, le=1000),
) -> dict[str, Any]:
    return service.repository().session_detail(session_id, file_limit=file_limit, json_limit=json_limit)


@app.get("/api/autotest/sessions/{session_id}/json/{file_path:path}", dependencies=[Depends(require_access_token)])
def api_autotest_json_preview(session_id: str, file_path: str) -> dict[str, Any]:
    return service.repository().json_preview(session_id, file_path)


@app.get("/api/autotest/sessions/{session_id}/text/{file_path:path}", dependencies=[Depends(require_access_token)])
def api_autotest_text_preview(session_id: str, file_path: str) -> dict[str, Any]:
    return service.repository().text_preview(session_id, file_path)


@app.get("/api/autotest/sessions/{session_id}/files/{file_path:path}", dependencies=[Depends(require_access_token)])
def api_autotest_file(
    request: Request,
    session_id: str,
    file_path: str,
    download: bool = Query(default=False),
) -> FileResponse:
    path = service.repository().resolve_session_file(session_id, file_path)
    service.record_file_download(request, f"{session_id}/{file_path}")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name if download else None,
    )


@app.get("/api/ports", dependencies=[Depends(require_access_token)])
def api_ports() -> dict[str, Any]:
    return {"ports": [], "detail": "Serial monitoring is disabled in the AutoTest file browser."}


@app.get("/api/positions", dependencies=[Depends(require_access_token)])
def api_positions() -> dict[str, Any]:
    raise HTTPException(status_code=410, detail="Stage position monitoring is disabled in the AutoTest file browser.")


@app.get("/api/camera-sources", dependencies=[Depends(require_access_token)])
def api_camera_sources() -> dict[str, Any]:
    return {"selected": None, "sources": [], "camera_streaming_enabled": False}


@app.post("/api/camera-source", dependencies=[Depends(require_access_token)])
def api_camera_source(source: str = Query(default="")) -> dict[str, Any]:
    return {"selected_camera_source": None, "camera_streaming_enabled": False, "ignored_source": source}


@app.post("/internal/release-camera")
def internal_release_camera(request: Request) -> dict[str, Any]:
    client_host = request.client.host if request.client else ""
    if client_host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="Localhost only.")
    return {"released": False, "camera_streaming_enabled": False}


@app.get("/camera.mjpg", dependencies=[Depends(require_access_token)])
def camera_stream() -> dict[str, Any]:
    raise HTTPException(status_code=410, detail="Camera streaming is disabled. Use the AutoTest file APIs.")


def main() -> None:
    import uvicorn

    host = os.environ.get("SEMI_AUTO_PROBE_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("SEMI_AUTO_PROBE_WEB_PORT", "8000"))
    uvicorn.run("semi_auto_probe.web_app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
