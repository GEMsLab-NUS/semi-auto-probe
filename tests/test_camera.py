import unittest
from pathlib import Path

import cv2
import numpy as np

from semi_auto_probe.camera import (
    DEFAULT_CAMERA_SOURCE,
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


class WebCameraSourceTest(unittest.TestCase):
    def test_web_accepts_miicam_and_legacy_direct_sources(self) -> None:
        self.assertEqual(WebProbeService._parse_camera_source("miicam:0"), ("direct", "miicam:0", None))
        self.assertEqual(WebProbeService._parse_camera_source("direct:1"), ("direct", "opencv:1", 1))

    def test_web_auto_direct_label_is_clear(self) -> None:
        self.assertEqual(WebProbeService._direct_camera_label("auto-direct"), "Auto direct camera")
        self.assertEqual(WebProbeService._direct_camera_label("miicam:0"), "MiiCam SDK 0")


if __name__ == "__main__":
    unittest.main()
