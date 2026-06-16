from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from app.core.time import iso_now, utc_now
from app.pipeline.maintenance import MaintenancePipeline


class FakeChannel:
    def __init__(self, sheet_name: str) -> None:
        self.sheet_name = sheet_name


class FakeRepository:
    def __init__(self, topics: list[dict[str, str]]) -> None:
        self.topics = topics

    def list_channels(self):
        return [FakeChannel("channel")]

    def list_channel_topics(self, sheet_name: str):
        del sheet_name
        return self.topics


class FakeSettings:
    def __init__(self, local_storage_dir: Path, video_source_retention_days: int = 14) -> None:
        self.local_storage_dir = local_storage_dir
        self.video_source_retention_days = video_source_retention_days


class MaintenancePipelineTest(unittest.TestCase):
    def test_prunes_old_source_files_but_keeps_final_render_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            topic_dir = root / "topics" / "topic_old"
            (topic_dir / "assets").mkdir(parents=True)
            (topic_dir / "audio").mkdir()
            (topic_dir / "assets" / "scene.mp4").write_bytes(b"source")
            (topic_dir / "audio" / "scene.wav").write_bytes(b"audio")
            (topic_dir / "rendered_no_subtitles.mp4").write_bytes(b"draft")
            (topic_dir / "rendered.mp4").write_bytes(b"final")
            (topic_dir / "manifest.json").write_text("{}", encoding="utf-8")
            old_iso = (utc_now() - timedelta(days=15)).isoformat()
            repo = FakeRepository([{"topic_id": "topic_old", "status": "WAITING_FINAL_APPROVAL", "updated_at": old_iso}])

            with patch("app.pipeline.maintenance.get_settings", return_value=FakeSettings(root)):
                stats = MaintenancePipeline(repo).run_once()

            self.assertEqual(stats["pruned_topic_dirs"], 1)
            self.assertFalse((topic_dir / "assets").exists())
            self.assertFalse((topic_dir / "audio").exists())
            self.assertFalse((topic_dir / "rendered_no_subtitles.mp4").exists())
            self.assertTrue((topic_dir / "rendered.mp4").exists())
            self.assertTrue((topic_dir / "manifest.json").exists())

    def test_recent_topic_is_not_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            topic_dir = root / "topics" / "topic_recent"
            (topic_dir / "assets").mkdir(parents=True)
            (topic_dir / "assets" / "scene.mp4").write_bytes(b"source")
            repo = FakeRepository([{"topic_id": "topic_recent", "status": "WAITING_FINAL_APPROVAL", "updated_at": iso_now()}])

            with patch("app.pipeline.maintenance.get_settings", return_value=FakeSettings(root)):
                stats = MaintenancePipeline(repo).run_once()

            self.assertEqual(stats["pruned_topic_dirs"], 0)
            self.assertTrue((topic_dir / "assets").exists())


if __name__ == "__main__":
    unittest.main()
