from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path

from app.core.config import get_settings
from app.core.time import parse_iso, utc_now
from app.google.repository import SheetsRepository


DISPOSABLE_SOURCE_DIRS = {"assets", "audio", "segments", "subtitles", "render_logs"}
DISPOSABLE_SOURCE_FILES = {"rendered_no_subtitles.mp4", "concat.txt", "subtitles.srt"}
ACTIVE_STATUSES = {
    "ASSET_GENERATING",
    "IMAGE_CRAWLING",
    "IMAGE_CANDIDATES_READY",
    "IMAGE_SELECTED",
    "VIDEO_RENDERING",
    "FINAL_APPROVING",
    "UPLOADING_PRIVATE",
}


class MaintenancePipeline:
    def __init__(self, repository: SheetsRepository) -> None:
        self.repository = repository
        self.settings = get_settings()

    def run_once(self) -> dict[str, int]:
        retention_days = max(0, self.settings.video_source_retention_days)
        stats = {
            "scanned_topic_dirs": 0,
            "pruned_topic_dirs": 0,
            "deleted_dirs": 0,
            "deleted_files": 0,
            "deleted_orphan_topic_dirs": 0,
            "bytes_freed": 0,
        }
        if retention_days <= 0:
            return stats

        topics_root = self.settings.local_storage_dir / "topics"
        if not topics_root.exists():
            return stats

        cutoff = utc_now() - timedelta(days=retention_days)
        known_topics = self._topics_by_id()
        for topic_dir in topics_root.iterdir():
            if not topic_dir.is_dir():
                continue
            stats["scanned_topic_dirs"] += 1
            topic = known_topics.get(topic_dir.name)
            if topic is None:
                if self._path_is_older_than(topic_dir, cutoff):
                    stats["bytes_freed"] += self._path_size(topic_dir)
                    shutil.rmtree(topic_dir)
                    stats["deleted_orphan_topic_dirs"] += 1
                continue
            if not self._topic_is_prunable(topic, cutoff):
                continue
            pruned = self._prune_topic_sources(topic_dir)
            if pruned["deleted_dirs"] or pruned["deleted_files"]:
                stats["pruned_topic_dirs"] += 1
                stats["deleted_dirs"] += pruned["deleted_dirs"]
                stats["deleted_files"] += pruned["deleted_files"]
                stats["bytes_freed"] += pruned["bytes_freed"]
        return stats

    def _topics_by_id(self) -> dict[str, dict[str, str]]:
        topics: dict[str, dict[str, str]] = {}
        for channel in self.repository.list_channels():
            for topic in self.repository.list_channel_topics(channel.sheet_name):
                topic_id = topic.get("topic_id")
                if topic_id:
                    topics[topic_id] = topic
        return topics

    def _topic_is_prunable(self, topic: dict[str, str], cutoff: datetime) -> bool:
        if topic.get("status") in ACTIVE_STATUSES:
            return False
        reference = parse_iso(topic.get("updated_at")) or parse_iso(topic.get("created_at")) or parse_iso(topic.get("upload_datetime"))
        return bool(reference and reference <= cutoff)

    def _prune_topic_sources(self, topic_dir: Path) -> dict[str, int]:
        stats = {"deleted_dirs": 0, "deleted_files": 0, "bytes_freed": 0}
        for dirname in DISPOSABLE_SOURCE_DIRS:
            path = topic_dir / dirname
            if not path.exists():
                continue
            stats["bytes_freed"] += self._path_size(path)
            shutil.rmtree(path)
            stats["deleted_dirs"] += 1
        for filename in DISPOSABLE_SOURCE_FILES:
            path = topic_dir / filename
            if not path.exists() or not path.is_file():
                continue
            stats["bytes_freed"] += path.stat().st_size
            path.unlink()
            stats["deleted_files"] += 1
        return stats

    @staticmethod
    def _path_is_older_than(path: Path, cutoff: datetime) -> bool:
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=cutoff.tzinfo)
        return modified <= cutoff

    @staticmethod
    def _path_size(path: Path) -> int:
        if path.is_file():
            return path.stat().st_size
        return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())
