from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from semi_auto_probe.app import FOCUSMAP_AUTOSAVE_FILENAME, ProbeApp
from semi_auto_probe.gds_stage_mapper import (
    GDSStageMapperPanel,
    LAYOUTBOND_AUTOSAVE_FILENAME,
)


class DefaultFileDialogTests(unittest.TestCase):
    def test_focusmap_import_selects_root_autosave_file(self) -> None:
        app = SimpleNamespace(af_plane_running=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("pathlib.Path.cwd", return_value=Path(tmpdir)):
                with patch(
                    "semi_auto_probe.app.filedialog.askopenfilename",
                    return_value="",
                ) as chooser:
                    ProbeApp.load_af_plane_results(app)

        chooser.assert_called_once_with(
            title="Import FocusMap JSON",
            initialdir=tmpdir,
            initialfile=FOCUSMAP_AUTOSAVE_FILENAME,
            filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
        )

    def test_layoutmap_load_selects_root_autosave_file(self) -> None:
        panel = SimpleNamespace()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("pathlib.Path.cwd", return_value=Path(tmpdir)):
                with patch(
                    "semi_auto_probe.gds_stage_mapper.filedialog.askopenfilename",
                    return_value="",
                ) as chooser:
                    GDSStageMapperPanel.load_calibration_dialog(panel)

        chooser.assert_called_once_with(
            title="Load GDS Calibration",
            initialdir=tmpdir,
            initialfile=LAYOUTBOND_AUTOSAVE_FILENAME,
            filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
        )
