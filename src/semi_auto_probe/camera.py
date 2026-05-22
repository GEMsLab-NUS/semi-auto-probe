from __future__ import annotations

import importlib
import os
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import CAMERA_CONTROL_MODE_AUTO, CAMERA_CONTROL_MODE_MANUAL, normalize_camera_control_mode

DEFAULT_CAMERA_SOURCE = "auto"
CAMERA_SOURCE_ENV = "SEMI_AUTO_PROBE_CAMERA_SOURCE"
CAMERA_COLOR_ORDER_ENV = "SEMI_AUTO_PROBE_CAMERA_COLOR_ORDER"
CAMERA_FLIP_VERTICAL_ENV = "SEMI_AUTO_PROBE_CAMERA_FLIP_VERTICAL"
CAMERA_FOURCC_ENV = "SEMI_AUTO_PROBE_CAMERA_FOURCC"
MIICAM_SDK_ENV = "SEMI_AUTO_PROBE_MIICAM_SDK"
_OPENCV_AUTO_BACKENDS = ("dshow", "msmf", "any")


@dataclass(frozen=True)
class CameraFrame:
    width: int
    height: int
    ppm_bytes: bytes
    focus_scores: dict[str, float]
    image_bgr: object
    captured_at: float


@dataclass(frozen=True)
class CameraSettings:
    exposure_mode: str = CAMERA_CONTROL_MODE_AUTO
    exposure: float = 0.0
    gain_mode: str = CAMERA_CONTROL_MODE_AUTO
    gain: float = 0.0


@dataclass(frozen=True)
class CameraSourceSpec:
    kind: str
    index: int = 0
    device_id: str | None = None
    opencv_backends: tuple[str, ...] = _OPENCV_AUTO_BACKENDS


def camera_source_choices(max_opencv_index: int = 4, max_toupcam_index: int = 2) -> tuple[str, ...]:
    sources = [DEFAULT_CAMERA_SOURCE]
    sources.extend(f"miicam:{index}" for index in range(max_toupcam_index + 1))
    sources.extend(f"opencv:{index}" for index in range(max_opencv_index + 1))
    sources.extend(f"opencv-msmf:{index}" for index in range(max_opencv_index + 1))
    sources.extend(f"opencv-dshow:{index}" for index in range(max_opencv_index + 1))
    sources.extend(f"toupcam:{index}" for index in range(max_toupcam_index + 1))
    return tuple(sources)


def normalize_camera_source(value: str | int | None, default: str = DEFAULT_CAMERA_SOURCE) -> str:
    if value is None:
        text = default
    else:
        text = str(value).strip()
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


def _camera_source_candidates(source: str, default_index: int) -> list[CameraSourceSpec]:
    normalized = normalize_camera_source(source)
    if normalized == DEFAULT_CAMERA_SOURCE:
        return [
            CameraSourceSpec("miicam", index=0),
            CameraSourceSpec("toupcam", index=0),
            CameraSourceSpec("opencv", index=default_index),
        ]
    if normalized == "opencv":
        return [CameraSourceSpec("opencv", index=default_index)]
    if normalized == "toupcam":
        return [CameraSourceSpec("toupcam", index=0)]
    if normalized == "miicam":
        return [CameraSourceSpec("miicam", index=0)]
    if normalized.startswith("miicam:"):
        selector = normalized.split(":", 1)[1].strip()
        if selector.isdigit():
            return [CameraSourceSpec("miicam", index=int(selector))]
    if normalized.startswith("toupcam:"):
        selector = normalized.split(":", 1)[1].strip()
        if selector.isdigit():
            return [CameraSourceSpec("toupcam", index=int(selector))]
        if selector:
            return [CameraSourceSpec("toupcam", device_id=selector)]
    for prefix, backends in (
        ("opencv:", _OPENCV_AUTO_BACKENDS),
        ("opencv-dshow:", ("dshow",)),
        ("dshow:", ("dshow",)),
        ("opencv-msmf:", ("msmf",)),
        ("msmf:", ("msmf",)),
        ("opencv-any:", ("any",)),
        ("any:", ("any",)),
    ):
        if normalized.startswith(prefix):
            index_text = normalized.split(":", 1)[1].strip()
            if index_text.isdigit():
                return [CameraSourceSpec("opencv", index=int(index_text), opencv_backends=backends)]
    raise ValueError(f"Unsupported camera source: {source!r}. Use auto, miicam:0, opencv:0, opencv-msmf:0, opencv-dshow:0, or toupcam:0.")


def _bool_from_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _format_property(value: float | int | None) -> str:
    if value is None or value <= -10_000:
        return "--"
    if abs(value) >= 100:
        return f"{value:.0f}"
    return f"{value:.2f}".rstrip("0").rstrip(".")


class _CameraBackend:
    label = "camera"

    @property
    def is_open(self) -> bool:
        raise NotImplementedError

    def open(self) -> None:
        raise NotImplementedError

    def read_bgr(self) -> object | None:
        raise NotImplementedError

    def apply_settings(self, settings: CameraSettings) -> None:
        del settings

    def property_text(self, settings: CameraSettings) -> str:
        del settings
        return "EXP --  GAIN --"

    def close(self) -> None:
        raise NotImplementedError


class _OpenCvCameraBackend(_CameraBackend):
    def __init__(
        self,
        cv2: Any,
        *,
        index: int,
        width: int,
        height: int,
        backends: tuple[str, ...],
    ) -> None:
        self._cv2 = cv2
        self.index = index
        self.width = width
        self.height = height
        self.backends = backends
        self._capture = None
        self.label = f"OpenCV camera {index}"
        self._active_backend = ""

    @property
    def is_open(self) -> bool:
        return bool(self._capture and self._capture.isOpened())

    def open(self) -> None:
        if self.is_open:
            return
        errors: list[str] = []
        for backend_name in self.backends:
            capture = self._open_capture(backend_name)
            if capture and capture.isOpened():
                self._capture = capture
                self._active_backend = backend_name
                self.label = f"OpenCV {backend_name.upper()} camera {self.index}"
                self._configure_capture()
                return
            if capture:
                capture.release()
            errors.append(backend_name)
        raise RuntimeError(f"Could not open OpenCV camera index {self.index}; tried {', '.join(errors)}.")

    def _open_capture(self, backend_name: str) -> Any:
        if backend_name == "any":
            return self._cv2.VideoCapture(self.index)
        cap_name = {"dshow": "CAP_DSHOW", "msmf": "CAP_MSMF"}.get(backend_name)
        if cap_name is None:
            return self._cv2.VideoCapture(self.index)
        api_preference = getattr(self._cv2, cap_name, None)
        if api_preference is None:
            return None
        return self._cv2.VideoCapture(self.index, api_preference)

    def _configure_capture(self) -> None:
        assert self._capture is not None
        cv2 = self._cv2
        fourcc = os.environ.get(CAMERA_FOURCC_ENV, "").strip().upper()
        if fourcc and hasattr(cv2, "VideoWriter_fourcc"):
            for token in (part.strip() for part in fourcc.split(",")):
                if len(token) == 4:
                    self._capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*token))
                    break
        self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self._capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    def read_bgr(self) -> object | None:
        if not self.is_open:
            self.open()
        assert self._capture is not None
        ok, frame = self._capture.read()
        return frame if ok else None

    def apply_settings(self, settings: CameraSettings) -> None:
        if not self._capture:
            return
        cv2 = self._cv2
        exposure_mode = normalize_camera_control_mode(settings.exposure_mode)
        gain_mode = normalize_camera_control_mode(settings.gain_mode)
        if exposure_mode == CAMERA_CONTROL_MODE_AUTO:
            self._set_first_supported(cv2.CAP_PROP_AUTO_EXPOSURE, (0.75, 1.0))
        else:
            self._set_first_supported(cv2.CAP_PROP_AUTO_EXPOSURE, (0.25, 0.0))
            self._set_property(cv2.CAP_PROP_EXPOSURE, float(settings.exposure))

        if gain_mode == CAMERA_CONTROL_MODE_MANUAL:
            self._set_property(cv2.CAP_PROP_GAIN, float(settings.gain))

    def property_text(self, settings: CameraSettings) -> str:
        if not self._capture:
            return "EXP --  GAIN --"
        cv2 = self._cv2
        exposure = self._capture.get(cv2.CAP_PROP_EXPOSURE)
        gain = self._capture.get(cv2.CAP_PROP_GAIN)
        auto_exposure = self._capture.get(cv2.CAP_PROP_AUTO_EXPOSURE)
        exposure_mode = "A" if normalize_camera_control_mode(settings.exposure_mode) == CAMERA_CONTROL_MODE_AUTO else "M"
        gain_mode = "A" if normalize_camera_control_mode(settings.gain_mode) == CAMERA_CONTROL_MODE_AUTO else "M"
        return (
            f"EXP {_format_property(exposure)} {exposure_mode}  "
            f"GAIN {_format_property(gain)} {gain_mode}  "
            f"AUTO {_format_property(auto_exposure)}"
        )

    def _set_first_supported(self, property_id: int, values: tuple[float, ...]) -> bool:
        for value in values:
            if self._set_property(property_id, value):
                return True
        return False

    def _set_property(self, property_id: int, value: float) -> bool:
        if not self._capture:
            return False
        try:
            return bool(self._capture.set(property_id, value))
        except Exception:
            return False

    def close(self) -> None:
        if self._capture:
            self._capture.release()
            self._capture = None


def _miicam_library_candidates() -> list[Path | str]:
    candidates: list[Path | str] = []
    configured = os.environ.get(MIICAM_SDK_ENV)
    if configured:
        configured_path = Path(configured)
        candidates.extend(_miicam_paths_from_root(configured_path))

    arch_dir = "x64" if struct.calcsize("P") * 8 == 64 else "x86"
    candidates.append(Path.cwd() / "miicam.dll")
    candidates.append(Path.home() / "Downloads" / "mmgr.20250415" / "win" / arch_dir / "miicam.dll")
    downloads = Path.home() / "Downloads"
    if downloads.exists():
        candidates.extend(path / "win" / arch_dir / "miicam.dll" for path in sorted(downloads.glob("mmgr.*"), reverse=True))
    candidates.append("miicam.dll")
    return candidates


def _miicam_paths_from_root(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    arch_dir = "x64" if struct.calcsize("P") * 8 == 64 else "x86"
    return [
        path / "miicam.dll",
        path / "win" / arch_dir / "miicam.dll",
        path / arch_dir / "miicam.dll",
    ]


class _MiiCamBackend(_CameraBackend):
    _EVENT_IMAGE = 0x0004
    _ERROR_EVENTS = {0x0080, 0x0081, 0x0082, 0x0085}

    def __init__(self, *, index: int = 0) -> None:
        self.index = index
        self.label = f"MiiCam {index}"
        self._ctypes = None
        self._dll = None
        self._dll_dir_handle = None
        self._handle = None
        self._buffer = None
        self._width = 0
        self._height = 0
        self._stride = 0
        self._image_ready = threading.Event()
        self._last_event_error: str | None = None
        self._callback = None
        self._callback_type = None

    @property
    def is_open(self) -> bool:
        return bool(self._handle)

    def open(self) -> None:
        if self.is_open:
            return
        import ctypes

        self._ctypes = ctypes
        self._dll = self._load_dll(ctypes)
        self._configure_functions(ctypes)
        self._handle = self._dll.Miicam_OpenByIndex(ctypes.c_uint(self.index))
        if not self._handle:
            raise RuntimeError(f"Could not open MiiCam index {self.index}.")
        self._width, self._height = self._get_size()
        self._stride = ((self._width * 24 + 31) // 32) * 4
        self._buffer = ctypes.create_string_buffer(self._stride * self._height)
        callback_factory = ctypes.WINFUNCTYPE if os.name == "nt" and hasattr(ctypes, "WINFUNCTYPE") else ctypes.CFUNCTYPE
        callback_type = self._callback_type or callback_factory(None, ctypes.c_uint, ctypes.c_void_p)
        self._callback = callback_type(self._on_camera_event)
        result = self._dll.Miicam_StartPullModeWithCallback(self._handle, self._callback, None)
        if int(result) < 0:
            self.close()
            raise RuntimeError(f"Could not start MiiCam stream: HRESULT 0x{int(result) & 0xFFFFFFFF:08x}.")

    def _load_dll(self, ctypes: Any) -> Any:
        errors: list[str] = []
        for candidate in _miicam_library_candidates():
            try:
                if isinstance(candidate, Path):
                    if not candidate.exists():
                        continue
                    if os.name == "nt" and hasattr(os, "add_dll_directory"):
                        self._dll_dir_handle = os.add_dll_directory(str(candidate.parent))
                    return ctypes.WinDLL(str(candidate)) if os.name == "nt" else ctypes.CDLL(str(candidate))
                return ctypes.WinDLL(candidate) if os.name == "nt" else ctypes.CDLL(candidate)
            except OSError as exc:
                errors.append(f"{candidate}: {exc}")
        detail = "; ".join(errors) if errors else "miicam.dll was not found"
        raise RuntimeError(
            f"Could not load MiiCam SDK. Set {MIICAM_SDK_ENV} to the mmgr SDK root or miicam.dll path. {detail}"
        )

    def _configure_functions(self, ctypes: Any) -> None:
        assert self._dll is not None
        self._dll.Miicam_OpenByIndex.argtypes = [ctypes.c_uint]
        self._dll.Miicam_OpenByIndex.restype = ctypes.c_void_p
        self._dll.Miicam_Close.argtypes = [ctypes.c_void_p]
        self._dll.Miicam_Close.restype = None
        self._dll.Miicam_Stop.argtypes = [ctypes.c_void_p]
        self._dll.Miicam_Stop.restype = ctypes.c_int
        self._dll.Miicam_get_Size.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
        self._dll.Miicam_get_Size.restype = ctypes.c_int
        callback_factory = ctypes.WINFUNCTYPE if os.name == "nt" and hasattr(ctypes, "WINFUNCTYPE") else ctypes.CFUNCTYPE
        callback_type = callback_factory(None, ctypes.c_uint, ctypes.c_void_p)
        self._callback_type = callback_type
        self._dll.Miicam_StartPullModeWithCallback.argtypes = [ctypes.c_void_p, callback_type, ctypes.c_void_p]
        self._dll.Miicam_StartPullModeWithCallback.restype = ctypes.c_int
        self._dll.Miicam_PullImageV2.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        self._dll.Miicam_PullImageV2.restype = ctypes.c_int
        self._dll.Miicam_put_AutoExpoEnable.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._dll.Miicam_put_AutoExpoEnable.restype = ctypes.c_int
        self._dll.Miicam_put_ExpoTime.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        self._dll.Miicam_put_ExpoTime.restype = ctypes.c_int
        self._dll.Miicam_put_ExpoAGain.argtypes = [ctypes.c_void_p, ctypes.c_ushort]
        self._dll.Miicam_put_ExpoAGain.restype = ctypes.c_int
        self._dll.Miicam_get_ExpoTime.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
        self._dll.Miicam_get_ExpoTime.restype = ctypes.c_int
        self._dll.Miicam_get_ExpoAGain.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ushort)]
        self._dll.Miicam_get_ExpoAGain.restype = ctypes.c_int

    def _get_size(self) -> tuple[int, int]:
        assert self._ctypes is not None
        assert self._dll is not None
        assert self._handle is not None
        width = self._ctypes.c_int()
        height = self._ctypes.c_int()
        result = self._dll.Miicam_get_Size(self._handle, self._ctypes.byref(width), self._ctypes.byref(height))
        if int(result) < 0 or width.value <= 0 or height.value <= 0:
            raise RuntimeError(f"Could not read MiiCam frame size: HRESULT 0x{int(result) & 0xFFFFFFFF:08x}.")
        return width.value, height.value

    def _on_camera_event(self, event: int, _ctx: object | None = None) -> None:
        if event == self._EVENT_IMAGE:
            self._image_ready.set()
        elif event in self._ERROR_EVENTS:
            self._last_event_error = f"MiiCam event {event:#x}"
            self._image_ready.set()

    def read_bgr(self) -> object | None:
        if not self.is_open:
            self.open()
        if not self._image_ready.wait(1.5):
            return None
        self._image_ready.clear()
        if self._last_event_error:
            raise RuntimeError(self._last_event_error)
        assert self._dll is not None
        assert self._handle is not None
        assert self._buffer is not None
        result = self._dll.Miicam_PullImageV2(self._handle, self._buffer, 24, None)
        if int(result) < 0:
            return None

        import numpy as np

        data = np.frombuffer(self._buffer, dtype=np.uint8)
        rows = data.reshape((self._height, self._stride))
        image = rows[:, : self._width * 3].reshape((self._height, self._width, 3))
        color_order = os.environ.get(CAMERA_COLOR_ORDER_ENV, "bgr").strip().lower()
        if color_order == "rgb":
            image = image[:, :, ::-1]
        return image.copy()

    def apply_settings(self, settings: CameraSettings) -> None:
        if not self._dll or not self._handle:
            return
        exposure_mode = normalize_camera_control_mode(settings.exposure_mode)
        gain_mode = normalize_camera_control_mode(settings.gain_mode)
        try:
            self._dll.Miicam_put_AutoExpoEnable(self._handle, 1 if exposure_mode == CAMERA_CONTROL_MODE_AUTO else 0)
        except Exception:
            pass
        if exposure_mode == CAMERA_CONTROL_MODE_MANUAL and settings.exposure > 0:
            try:
                self._dll.Miicam_put_ExpoTime(self._handle, max(1, int(settings.exposure)))
            except Exception:
                pass
        if gain_mode == CAMERA_CONTROL_MODE_MANUAL and settings.gain >= 0:
            try:
                self._dll.Miicam_put_ExpoAGain(self._handle, max(0, min(65535, int(settings.gain))))
            except Exception:
                pass

    def property_text(self, settings: CameraSettings) -> str:
        exposure_mode = "A" if normalize_camera_control_mode(settings.exposure_mode) == CAMERA_CONTROL_MODE_AUTO else "M"
        gain_mode = "A" if normalize_camera_control_mode(settings.gain_mode) == CAMERA_CONTROL_MODE_AUTO else "M"
        exposure = self._get_uint_property("Miicam_get_ExpoTime")
        gain = self._get_ushort_property("Miicam_get_ExpoAGain")
        return f"EXP {_format_property(exposure)} {exposure_mode}  GAIN {_format_property(gain)} {gain_mode}  SDK"

    def _get_uint_property(self, function_name: str) -> float | None:
        if not self._ctypes or not self._dll or not self._handle:
            return None
        value = self._ctypes.c_uint()
        result = getattr(self._dll, function_name)(self._handle, self._ctypes.byref(value))
        return float(value.value) if int(result) >= 0 else None

    def _get_ushort_property(self, function_name: str) -> float | None:
        if not self._ctypes or not self._dll or not self._handle:
            return None
        value = self._ctypes.c_ushort()
        result = getattr(self._dll, function_name)(self._handle, self._ctypes.byref(value))
        return float(value.value) if int(result) >= 0 else None

    def close(self) -> None:
        if self._dll and self._handle:
            try:
                self._dll.Miicam_Stop(self._handle)
            except Exception:
                pass
            try:
                self._dll.Miicam_Close(self._handle)
            finally:
                self._handle = None
        self._buffer = None
        self._image_ready.clear()
        if self._dll_dir_handle is not None:
            try:
                self._dll_dir_handle.close()
            except Exception:
                pass
            self._dll_dir_handle = None


class _ToupCamBackend(_CameraBackend):
    def __init__(self, *, index: int = 0, device_id: str | None = None) -> None:
        self.index = index
        self.device_id = device_id
        self.label = f"ToupCam {device_id or index}"
        self._toupcam = None
        self._handle = None
        self._buffer: bytes | None = None
        self._width = 0
        self._height = 0
        self._stride = 0
        self._image_ready = threading.Event()
        self._last_event_error: str | None = None
        self._callback = self._on_camera_event

    @property
    def is_open(self) -> bool:
        return bool(self._handle)

    def open(self) -> None:
        if self.is_open:
            return
        self._toupcam = importlib.import_module("toupcam")
        devices = list(self._toupcam.Toupcam.EnumV2())
        if not devices:
            raise RuntimeError("ToupCam SDK is installed, but no ToupCam-compatible camera was detected.")
        device = self._select_device(devices)
        self.label = f"ToupCam {getattr(device, 'displayname', self.device_id or self.index)}"
        self._handle = self._toupcam.Toupcam.Open(device.id)
        if not self._handle:
            raise RuntimeError(f"Could not open {self.label}.")
        self._width, self._height = self._handle.get_Size()
        self._stride = ((self._width * 24 + 31) // 32) * 4
        self._buffer = bytes(self._stride * self._height)
        try:
            self._handle.StartPullModeWithCallback(self._callback, self)
        except Exception:
            self.close()
            raise

    def _select_device(self, devices: list[Any]) -> Any:
        if self.device_id:
            for device in devices:
                if str(getattr(device, "id", "")) == self.device_id:
                    return device
            raise RuntimeError(f"ToupCam device id {self.device_id!r} was not found.")
        if self.index < 0 or self.index >= len(devices):
            raise RuntimeError(f"ToupCam index {self.index} is out of range; {len(devices)} device(s) detected.")
        return devices[self.index]

    def _on_camera_event(self, event: int, _ctx: object | None = None) -> None:
        if self._toupcam is None:
            return
        image_event = getattr(self._toupcam, "TOUPCAM_EVENT_IMAGE", 0x0004)
        error_events = {
            getattr(self._toupcam, "TOUPCAM_EVENT_ERROR", 0x0080),
            getattr(self._toupcam, "TOUPCAM_EVENT_DISCONNECTED", 0x0081),
            getattr(self._toupcam, "TOUPCAM_EVENT_NOFRAMETIMEOUT", 0x0082),
            getattr(self._toupcam, "TOUPCAM_EVENT_NOPACKETTIMEOUT", 0x0085),
        }
        if event == image_event:
            self._image_ready.set()
        elif event in error_events:
            self._last_event_error = f"ToupCam event {event:#x}"
            self._image_ready.set()

    def read_bgr(self) -> object | None:
        if not self.is_open:
            self.open()
        if not self._image_ready.wait(1.5):
            return None
        self._image_ready.clear()
        if self._last_event_error:
            raise RuntimeError(self._last_event_error)
        assert self._handle is not None
        assert self._buffer is not None
        try:
            self._handle.PullImageV2(self._buffer, 24, None)
        except Exception as exc:
            hresult_exception = getattr(self._toupcam, "HRESULTException", Exception) if self._toupcam else Exception
            if isinstance(exc, hresult_exception):
                return None
            raise

        import numpy as np

        data = np.frombuffer(self._buffer, dtype=np.uint8)
        rows = data.reshape((self._height, self._stride))
        image = rows[:, : self._width * 3].reshape((self._height, self._width, 3))
        color_order = os.environ.get(CAMERA_COLOR_ORDER_ENV, "bgr").strip().lower()
        if color_order == "rgb":
            image = image[:, :, ::-1]
        return image.copy()

    def apply_settings(self, settings: CameraSettings) -> None:
        if not self._handle:
            return
        exposure_mode = normalize_camera_control_mode(settings.exposure_mode)
        gain_mode = normalize_camera_control_mode(settings.gain_mode)
        if hasattr(self._handle, "put_AutoExpoEnable"):
            try:
                self._handle.put_AutoExpoEnable(1 if exposure_mode == CAMERA_CONTROL_MODE_AUTO else 0)
            except Exception:
                pass
        if exposure_mode == CAMERA_CONTROL_MODE_MANUAL and settings.exposure > 0 and hasattr(self._handle, "put_ExpoTime"):
            try:
                self._handle.put_ExpoTime(int(settings.exposure))
            except Exception:
                pass
        if gain_mode == CAMERA_CONTROL_MODE_MANUAL and settings.gain >= 0 and hasattr(self._handle, "put_ExpoAGain"):
            try:
                self._handle.put_ExpoAGain(int(settings.gain))
            except Exception:
                pass

    def property_text(self, settings: CameraSettings) -> str:
        exposure_mode = "A" if normalize_camera_control_mode(settings.exposure_mode) == CAMERA_CONTROL_MODE_AUTO else "M"
        gain_mode = "A" if normalize_camera_control_mode(settings.gain_mode) == CAMERA_CONTROL_MODE_AUTO else "M"
        exposure = self._try_get("get_ExpoTime")
        gain = self._try_get("get_ExpoAGain")
        return f"EXP {_format_property(exposure)} {exposure_mode}  GAIN {_format_property(gain)} {gain_mode}  SDK"

    def _try_get(self, method_name: str) -> float | None:
        if not self._handle or not hasattr(self._handle, method_name):
            return None
        try:
            value = getattr(self._handle, method_name)()
        except Exception:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def close(self) -> None:
        if self._handle:
            try:
                self._handle.Close()
            finally:
                self._handle = None
        self._buffer = None
        self._image_ready.clear()


class UsbCamera:
    def __init__(
        self,
        index: int = 0,
        width: int = 960,
        height: int = 540,
        settings: CameraSettings | None = None,
        source: str | None = None,
        flip_vertical: bool | None = None,
    ) -> None:
        self.index = index
        self.width = width
        self.height = height
        self.settings = settings or CameraSettings()
        self.source = normalize_camera_source(source or os.environ.get(CAMERA_SOURCE_ENV) or DEFAULT_CAMERA_SOURCE)
        self.flip_vertical = _bool_from_env(CAMERA_FLIP_VERTICAL_ENV, True) if flip_vertical is None else flip_vertical
        self._cv2 = None
        self._backend: _CameraBackend | None = None
        self._last_frame_time: float | None = None
        self._fps = 0.0
        self._frame_count = 0
        self._property_text = "EXP --  GAIN --"

    @property
    def is_open(self) -> bool:
        return bool(self._backend and self._backend.is_open)

    @property
    def active_source_label(self) -> str:
        return self._backend.label if self._backend else self.source

    def open(self) -> None:
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("Missing dependency: install opencv-python with `pip install -r requirements.txt`.") from exc

        if hasattr(cv2, "setLogLevel"):
            cv2.setLogLevel(0)

        if self.is_open:
            return

        self._cv2 = cv2
        self._backend = self._open_backend()
        self._apply_settings()
        self._update_property_text()

    def _open_backend(self) -> _CameraBackend:
        assert self._cv2 is not None
        attempts: list[str] = []
        for spec in _camera_source_candidates(self.source, self.index):
            backend: _CameraBackend
            if spec.kind == "miicam":
                backend = _MiiCamBackend(index=spec.index)
            elif spec.kind == "toupcam":
                backend = _ToupCamBackend(index=spec.index, device_id=spec.device_id)
            elif spec.kind == "opencv":
                backend = _OpenCvCameraBackend(
                    self._cv2,
                    index=spec.index,
                    width=self.width,
                    height=self.height,
                    backends=spec.opencv_backends,
                )
            else:
                attempts.append(f"{spec.kind}: unsupported")
                continue
            try:
                backend.open()
                return backend
            except Exception as exc:
                attempts.append(f"{backend.label}: {exc}")
        detail = "; ".join(attempts) if attempts else "no backends were attempted"
        raise RuntimeError(f"Could not open camera source {self.source!r}. {detail}")

    def read(self, calculate_focus_scores: bool = True) -> CameraFrame | None:
        if not self.is_open:
            self.open()
        assert self._backend is not None
        assert self._cv2 is not None

        frame = self._backend.read_bgr()
        if frame is None:
            return None
        captured_at = time.monotonic()

        frame = self._normalize_frame(frame)
        raw_frame = frame.copy()
        focus_scores = self._focus_scores(frame) if calculate_focus_scores else {}
        self._draw_overlay(frame)
        rgb = self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2RGB)
        height, width = rgb.shape[:2]
        header = f"P6 {width} {height} 255\n".encode("ascii")
        return CameraFrame(width=width, height=height, ppm_bytes=header + rgb.tobytes(), focus_scores=focus_scores, image_bgr=raw_frame, captured_at=captured_at)

    def _normalize_frame(self, frame) -> object:
        assert self._cv2 is not None
        cv2 = self._cv2
        if len(frame.shape) == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        elif len(frame.shape) == 3 and frame.shape[2] == 4:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        if self.width > 0 and self.height > 0:
            height, width = frame.shape[:2]
            scale = min(self.width / max(width, 1), self.height / max(height, 1))
            if scale > 0 and abs(scale - 1.0) > 1e-6:
                target_width = max(1, int(round(width * scale)))
                target_height = max(1, int(round(height * scale)))
                interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
                frame = cv2.resize(frame, (target_width, target_height), interpolation=interpolation)
        if self.flip_vertical:
            frame = cv2.flip(frame, 0)
        return frame

    def _focus_scores(self, frame) -> dict[str, float]:
        assert self._cv2 is not None
        cv2 = self._cv2
        sample = cv2.resize(frame, (320, 180), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY)
        laplacian = cv2.Laplacian(gray, cv2.CV_64F).var()
        sobel_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        sobel_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
        tenengrad = float((sobel_x * sobel_x + sobel_y * sobel_y).mean())
        brenner = float(((gray[:, 2:].astype("float32") - gray[:, :-2].astype("float32")) ** 2).mean())
        return {
            "Laplacian": float(laplacian),
            "Tenengrad": tenengrad,
            "Brenner": brenner,
        }

    def _draw_overlay(self, frame) -> None:
        assert self._cv2 is not None
        now = time.perf_counter()
        if self._last_frame_time is not None:
            instant_fps = 1.0 / max(now - self._last_frame_time, 1e-6)
            self._fps = instant_fps if self._fps == 0.0 else self._fps * 0.85 + instant_fps * 0.15
        self._last_frame_time = now
        self._frame_count += 1
        if self._frame_count % 30 == 0:
            self._update_property_text()

        cv2 = self._cv2
        height, width = frame.shape[:2]
        cv2.putText(
            frame,
            f"FPS {self._fps:4.1f}",
            (14, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.72,
            (40, 255, 170),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            self._property_text,
            (14, 54),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.46,
            (210, 225, 240),
            1,
            cv2.LINE_AA,
        )

        hist_width = min(180, max(120, width // 4))
        hist_height = min(90, max(64, height // 5))
        x0 = width - hist_width - 14
        y0 = height - hist_height - 14
        roi = frame[y0 : y0 + hist_height, x0 : x0 + hist_width]
        dark = roi.copy()
        dark[:] = (6, 10, 15)
        cv2.addWeighted(dark, 0.58, roi, 0.42, 0, roi)
        cv2.rectangle(frame, (x0, y0), (x0 + hist_width, y0 + hist_height), (90, 110, 130), 1)

        sample = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY)
        hist = cv2.calcHist([gray], [0], None, [32], [0, 256]).flatten()
        max_value = float(hist.max()) or 1.0
        bin_width = hist_width / len(hist)
        for index, value in enumerate(hist):
            bar_height = int((value / max_value) * (hist_height - 18))
            x1 = int(x0 + index * bin_width)
            x2 = int(x0 + (index + 1) * bin_width) - 1
            y1 = y0 + hist_height - 6
            y2 = y1 - bar_height
            cv2.rectangle(frame, (x1, y2), (x2, y1), (80, 170, 255), -1)

        cv2.putText(frame, "LUMA", (x0 + 6, y0 + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (220, 230, 240), 1, cv2.LINE_AA)

    def _update_property_text(self) -> None:
        if not self._backend or self._cv2 is None:
            self._property_text = "EXP --  GAIN --"
            return

        self._property_text = self._backend.property_text(self.settings)

    def _apply_settings(self) -> None:
        if not self._backend or self._cv2 is None:
            return

        self._backend.apply_settings(self.settings)

    def close(self) -> None:
        if self._backend:
            self._backend.close()
            self._backend = None
