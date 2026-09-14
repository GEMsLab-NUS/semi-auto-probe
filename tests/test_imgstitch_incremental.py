import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from semi_auto_probe.app import ProbeApp
from semi_auto_probe.config import ProbeConfig
from semi_auto_probe.img_stitch import IncrementalStitchPreview, StitchSession, StitchSettings, TileRecord, compose_mosaic, recompose_session
from semi_auto_probe.protocol import Axis


class IncrementalPreviewTests(unittest.TestCase):
    def test_incremental_blending_handles_negative_positions_and_snapshots(self):
        records = tuple(TileRecord(0, i, i, '', 0, 0, 0, 8.*i, 0.) for i in range(3))
        session = StitchSession(1, 3, 10, 10, 1., 10, 2., 'Array', 8., 8., 0, 0, 0,
                                StitchSettings(2, 2), records)
        preview = IncrementalStitchPreview(session)
        tiles = {t.key: np.full((10, 10, 3), 40+40*t.col, np.uint8) for t in records}
        first = preview.add(records[0], tiles[records[0].key])
        for t in records[1:]:
            actual = preview.add(t, tiles[t.key])
        expected = compose_mosaic(tiles, {t.key: (-8.*t.col, 0.) for t in records})
        np.testing.assert_array_equal(actual, expected)
        np.testing.assert_array_equal(first, tiles[records[0].key])

    def test_acquisition_composes_full_resolution_once(self):
        for stop_after in (None, 3):
            with self.subTest(stop_after=stop_after), tempfile.TemporaryDirectory() as directory:
                app = ProbeApp.__new__(ProbeApp)
                app.probe_config = ProbeConfig()
                app.imgstitch_session_dir = Path(directory)
                app.imgstitch_stop_event = threading.Event()
                app.result_queue = queue.Queue()
                from unittest.mock import Mock
                app.serial_client = Mock()
                app.serial_client.read_stable_xyz_positions.return_value = {Axis.X: 0, Axis.Y: 0, Axis.Z: 0}
                app._axis_from_position_entries = lambda entries, axis: entries[axis]
                app._move_absolute_stage = lambda x, y, z: {Axis.X: x, Axis.Y: y, Axis.Z: z}
                app._wait_after_imgstitch_motion = lambda: None
                app._camera_fov_rotation_deg = lambda: 0.
                rng = np.random.default_rng(123)
                images = [rng.integers(0, 256, (32, 32, 3), dtype=np.uint8) for _ in range(6)]
                count = 0
                def acquire(*args, **kwargs):
                    nonlocal count
                    image = images[count]
                    count += 1
                    if count == stop_after:
                        app.imgstitch_stop_event.set()
                    return image
                app.acquire_tile = acquire
                settings = StitchSettings(12, 12, white_balance_correction=False, show_seams=False)
                with patch('semi_auto_probe.app.recompose_session', wraps=recompose_session) as compose, patch('cv2.imwrite', return_value=True):
                    app._imgstitch_worker(2, 3, settings, 20, 20, False, False, 20., 20., 1., 'Array', None, 'Single Frame', {})
                events = list(app.result_queue.queue)
                self.assertFalse([e for e in events if e[0] == 'imgstitch_error'])
                self.assertEqual(count, stop_after or 6)
                self.assertEqual(compose.call_count, 1)
                self.assertEqual(sum(e[0] == 'imgstitch_acquisition_preview' for e in events), count)
                final = [e for e in events if e[0] == 'imgstitch_preview']
                self.assertEqual(len(final), 1)
                expected, _, _ = recompose_session(final[0][2], tile_images=final[0][3])
                np.testing.assert_array_equal(final[0][6], expected)
