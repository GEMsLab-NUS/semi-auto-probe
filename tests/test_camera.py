import unittest
from pathlib import Path

import cv2
import numpy as np

from semi_auto_probe.camera import (
    CameraSettings,
    DEFAULT_CAMERA_SOURCE,
    _MiiCamBackend,
    _OpenCvCameraBackend,
    _ToupCamBackend,
    _camera_source_candidates,
    _miicam_paths_from_root,
    camera_source_choices,
    normalize_camera_source,
)
from semi_auto_probe.config import ProbeConfig, camera_resolution_dimensions, normalize_camera_target_fps
from semi_auto_probe.web_app import WebProbeService


class CameraSourceTest(unittest.TestCase):
    def test_numeric_and_legacy_sources_still_map_to_opencv(self) -> None:
        self.assertEqual(normalize_camera_source("0"), "opencv:0")
        self.assertEqual(normalize_camera_source("direct:2"), "opencv:2")

    def test_auto_prefers_sdk_backends_before_opencv(self) -> None:
        candidates = _camera_source_candidates(DEFAULT_CAMERA_SOURCE, default_index=3)

        self.assertEqual([candidate.kind for candidate in candidates], ["miicam", "toupcam", "opencv"])
        self.assertEqual(candidates[-1].index, 3)

    def test_miicam_source_is_available_in_choices(self) -> None:
        choices = camera_source_choices(max_opencv_index=1, max_toupcam_index=1)

        self.assertIn("miicam:0", choices)
        self.assertIn("opencv:0", choices)
        self.assertIn("toupcam:0", choices)

    def test_miicam_sdk_root_path_resolves_windows_dll(self) -> None:
        root = Path("C:/SDK/mmgr.20250415")

        candidates = _miicam_paths_from_root(root)

        self.assertTrue(any(str(path).replace("\\", "/").endswith("/win/x64/miicam.dll") for path in candidates))

    def test_normalize_frame_preserves_camera_aspect_ratio(self) -> None:
        from semi_auto_probe.camera import UsbCamera

        camera = UsbCamera(width=800, height=450, flip_vertical=False)
        camera._cv2 = cv2
        frame = np.zeros((600, 800, 3), dtype=np.uint8)

        normalized = camera._normalize_frame(frame)

        self.assertEqual(normalized.shape[:2], (450, 600))

    def test_normalize_frame_keeps_matching_widescreen_bounds(self) -> None:
        from semi_auto_probe.camera import UsbCamera

        camera = UsbCamera(width=800, height=450, flip_vertical=False)
        camera._cv2 = cv2
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

        normalized = camera._normalize_frame(frame)

        self.assertEqual(normalized.shape[:2], (450, 800))

    def test_config_accepts_camera_resolution_and_frame_rate(self) -> None:
        config = ProbeConfig(camera_resolution_width="648", camera_target_fps=30)
        config.validate()

        self.assertEqual(camera_resolution_dimensions(config.camera_resolution_width), (648, 486))
        self.assertEqual(normalize_camera_target_fps(config.camera_target_fps), 30)

    def test_opencv_backend_applies_advanced_video_properties(self) -> None:
        class FakeCapture:
            def __init__(self) -> None:
                self.set_calls: list[tuple[int, float]] = []
                self.values: dict[int, float] = {}

            def set(self, property_id: int, value: float) -> bool:
                self.set_calls.append((property_id, value))
                self.values[property_id] = value
                return True

            def get(self, property_id: int) -> float:
                return self.values.get(property_id, -1.0)

        class FakeCv2:
            CAP_PROP_AUTO_EXPOSURE = 1
            CAP_PROP_EXPOSURE = 2
            CAP_PROP_GAIN = 3
            CAP_PROP_AUTO_WB = 4
            CAP_PROP_WB_TEMPERATURE = 5
            CAP_PROP_SATURATION = 6
            CAP_PROP_BRIGHTNESS = 7
            CAP_PROP_CONTRAST = 8
            CAP_PROP_GAMMA = 9

        capture = FakeCapture()
        backend = _OpenCvCameraBackend(FakeCv2, index=0, width=640, height=480, target_fps=None, backends=("any",))
        backend._capture = capture

        backend.apply_settings(
            CameraSettings(
                exposure_mode="manual",
                exposure=-6.0,
                gain_mode="manual",
                gain=12.0,
                white_balance_mode="manual",
                white_balance_temperature=5200.0,
                saturation=140.0,
                brightness=10.0,
                contrast=-5.0,
                gamma=110.0,
            )
        )

        self.assertIn((FakeCv2.CAP_PROP_WB_TEMPERATURE, 5200.0), capture.set_calls)
        self.assertIn((FakeCv2.CAP_PROP_AUTO_WB, 0.0), capture.set_calls)
        self.assertIn((FakeCv2.CAP_PROP_SATURATION, 140.0), capture.set_calls)
        self.assertIn((FakeCv2.CAP_PROP_BRIGHTNESS, 10.0), capture.set_calls)
        self.assertIn((FakeCv2.CAP_PROP_CONTRAST, -5.0), capture.set_calls)
        self.assertIn((FakeCv2.CAP_PROP_GAMMA, 110.0), capture.set_calls)

    def test_opencv_backend_close_ignores_release_exception(self) -> None:
        class FailingCapture:
            def __init__(self) -> None:
                self.released = False

            def release(self) -> None:
                self.released = True
                raise RuntimeError("release failed")

        backend = _OpenCvCameraBackend(object(), index=0, width=640, height=480, target_fps=None, backends=("any",))
        capture = FailingCapture()
        backend._capture = capture

        backend.close()

        self.assertTrue(capture.released)
        self.assertIsNone(backend._capture)

    def test_toupcam_backend_uses_sdk_for_advanced_video_properties(self) -> None:
        class FakeHandle:
            def __init__(self) -> None:
                self.calls: list[tuple[str, tuple[int, ...]]] = []

            def get_TempTint(self) -> tuple[int, int]:
                return 6500, 1001

            def put_TempTint(self, temp: int, tint: int) -> None:
                self.calls.append(("put_TempTint", (temp, tint)))

            def put_Saturation(self, value: int) -> None:
                self.calls.append(("put_Saturation", (value,)))

            def put_Brightness(self, value: int) -> None:
                self.calls.append(("put_Brightness", (value,)))

            def put_Contrast(self, value: int) -> None:
                self.calls.append(("put_Contrast", (value,)))

            def put_Gamma(self, value: int) -> None:
                self.calls.append(("put_Gamma", (value,)))

            def AwbOnce(self) -> None:
                self.calls.append(("AwbOnce", ()))

        handle = FakeHandle()
        backend = _ToupCamBackend()
        backend._handle = handle

        backend.apply_settings(
            CameraSettings(
                white_balance_mode="manual",
                white_balance_temperature=5000.0,
                saturation=150.0,
                brightness=4.0,
                contrast=-3.0,
                gamma=120.0,
            )
        )

        self.assertIn(("put_TempTint", (5000, 1001)), handle.calls)
        self.assertIn(("put_Saturation", (150,)), handle.calls)
        self.assertIn(("put_Brightness", (4,)), handle.calls)
        self.assertIn(("put_Contrast", (-3,)), handle.calls)
        self.assertIn(("put_Gamma", (120,)), handle.calls)

        handle.calls.clear()
        backend.apply_settings(CameraSettings(white_balance_mode="auto"))

        self.assertIn(("AwbOnce", ()), handle.calls)

    def test_miicam_awb_once_uses_sdk_callback_signature(self) -> None:
        class FakeDll:
            def __init__(self) -> None:
                self.calls: list[tuple[object, object, object]] = []

            def Miicam_AwbOnce(self, handle: object, callback: object, context: object) -> int:
                self.calls.append((handle, callback, context))
                return 0

            def Miicam_put_Saturation(self, _handle: object, _value: int) -> int:
                return 0

            def Miicam_put_Brightness(self, _handle: object, _value: int) -> int:
                return 0

            def Miicam_put_Contrast(self, _handle: object, _value: int) -> int:
                return 0

            def Miicam_put_Gamma(self, _handle: object, _value: int) -> int:
                return 0

        dll = FakeDll()
        backend = _MiiCamBackend()
        backend._dll = dll
        backend._handle = object()

        backend.apply_settings(CameraSettings(white_balance_mode="auto"))

        self.assertEqual(dll.calls, [(backend._handle, None, None)])


class WebCameraSourceTest(unittest.TestCase):
    def test_web_accepts_miicam_and_legacy_direct_sources(self) -> None:
        self.assertEqual(WebProbeService._parse_camera_source("miicam:0"), ("direct", "miicam:0", None))
        self.assertEqual(WebProbeService._parse_camera_source("direct:1"), ("direct", "opencv:1", 1))

    def test_web_auto_direct_label_is_clear(self) -> None:
        self.assertEqual(WebProbeService._direct_camera_label("auto-direct"), "Auto direct camera")
        self.assertEqual(WebProbeService._direct_camera_label("miicam:0"), "MiiCam SDK 0")


if __name__ == "__main__":
    unittest.main()
