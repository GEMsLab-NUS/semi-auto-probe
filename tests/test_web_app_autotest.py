from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from semi_auto_probe.web_app import AutoTestSessionRepository


class AutoTestWebRepositoryTests(unittest.TestCase):
    def make_session(self, root: Path) -> Path:
        session = root / "20260530_120000"
        (session / "images").mkdir(parents=True)
        (session / "iv").mkdir()
        (session / "wobb").mkdir()
        (session / "b1500").mkdir()
        (session / "images" / "DevA1.png").write_bytes(b"png")
        (session / "iv" / "DevA1_iv.csv").write_text("voltage_v,current_a\n0,0\n", encoding="utf-8")
        (session / "iv" / "DevA1_iv.json").write_text(
            json.dumps(
                {
                    "format": "semi_auto_probe.autotest_result_metadata",
                    "result_type": "iv_sweep",
                    "created_at": "2026-05-30T12:00:00",
                    "csv_file": "DevA1_iv.csv",
                    "device": {"name": "DevA1", "row": 0, "col": 0, "order": 1},
                    "measurement": {"resource": "GPIB0::18::INSTR", "stop": 1.0},
                    "statistics": {"sample_count": 21, "resistance_ohm": 123.4},
                }
            ),
            encoding="utf-8",
        )
        (session / "notes.txt").write_text("operator note", encoding="utf-8")
        return session

    def test_session_summary_counts_files_by_kind_and_category(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.make_session(root)
            repository = AutoTestSessionRepository(root)

            sessions = repository.list_sessions()

            self.assertEqual(len(sessions), 1)
            summary = sessions[0]
            self.assertEqual(summary["id"], "20260530_120000")
            self.assertEqual(summary["file_count"], 4)
            self.assertEqual(summary["counts"]["json"], 1)
            self.assertEqual(summary["counts"]["csv"], 1)
            self.assertEqual(summary["counts"]["images"], 1)
            self.assertEqual(summary["categories"]["iv"]["file_count"], 2)
            self.assertEqual(summary["categories"]["images"]["file_count"], 1)

    def test_session_detail_includes_json_metadata_and_device_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.make_session(root)
            repository = AutoTestSessionRepository(root)

            detail = repository.session_detail("20260530_120000")

            self.assertEqual(detail["json_total"], 1)
            self.assertEqual(detail["json_documents"][0]["result_type"], "iv_sweep")
            self.assertEqual(detail["json_documents"][0]["device"]["name"], "DevA1")
            self.assertEqual(detail["devices"]["count"], 1)
            self.assertEqual(detail["result_counts"], {"iv_sweep": 1})

    def test_json_preview_returns_parsed_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.make_session(root)
            repository = AutoTestSessionRepository(root)

            preview = repository.json_preview("20260530_120000", "iv/DevA1_iv.json")

            self.assertEqual(preview["path"], "iv/DevA1_iv.json")
            self.assertEqual(preview["content"]["device"]["name"], "DevA1")

    def test_file_resolution_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.make_session(root)
            repository = AutoTestSessionRepository(root)

            with self.assertRaises(HTTPException):
                repository.resolve_session_file("20260530_120000", "../outside.txt")


if __name__ == "__main__":
    unittest.main()
