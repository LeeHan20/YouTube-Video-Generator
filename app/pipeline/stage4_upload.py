from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from app.core.constants import JOB_COLUMNS
from app.core.config import get_settings
from app.core.time import iso_now
from app.google.repository import Channel, SheetsRepository
from app.youtube.oauth import EncryptedTokenStore
from app.youtube.uploader import YouTubeUploader


class Stage4UploadPipeline:
    def __init__(self, repository: SheetsRepository) -> None:
        self.repository = repository
        self.settings = get_settings()
        self.tokens = EncryptedTokenStore()
        self.uploader = YouTubeUploader()

    def run_once(self, force: bool = False, test: bool = False) -> dict[str, int]:
        stats = {"approved_topics": 0, "uploaded": 0, "would_upload": 0, "skipped": 0}
        eligible_statuses = {"APPROVED", "FINAL_APPROVED", "VIDEO_RENDERED"}
        if force:
            eligible_statuses.update({"UPLOADING_PRIVATE", "FAILED"})
        for channel in self.repository.list_enabled_channels():
            for topic in self.repository.list_channel_topics(channel.sheet_name):
                if topic.get("status") not in eligible_statuses:
                    continue
                stats["approved_topics"] += 1
                if not self.tokens.has_token(channel.channel_id):
                    stats["skipped"] += 1
                    continue
                if test:
                    self._validate_upload_topic(topic)
                    stats["would_upload"] += 1
                    continue
                if self.upload_topic(channel, topic, force=force):
                    stats["uploaded"] += 1
        return stats

    def upload_topic(self, channel: Channel, topic: dict[str, str], force: bool = False) -> bool:
        job_id = f"job_stage4_upload_{topic['topic_id']}"
        if force:
            self._reset_stage4_job(job_id)
        locked = self.repository.acquire_job_lock(
            job_id=job_id,
            job_type="UPLOAD_YOUTUBE_PRIVATE",
            channel_id=channel.channel_id,
            topic_id=topic["topic_id"],
            locked_by=self.settings.server_instance_id,
        )
        if not locked:
            return False
        try:
            self.repository.update_channel_topic(channel.sheet_name, int(topic["_row_number"]), {**topic, "status": "UPLOADING_PRIVATE", "updated_at": iso_now()})
            video_path = self._local_path_from_url(topic["rendered_video_url"])
            description = self._description(topic)
            result = self.uploader.upload_private(
                channel_id=channel.channel_id,
                video_path=video_path,
                title=topic["topic_title"],
                description=description,
                tags=[channel.channel_name, topic.get("topic_type", ""), "정보"],
            )
            updates = {
                "status": "UPLOADED_PRIVATE",
                "youtube_video_id": result["youtube_video_id"],
                "youtube_private_url": result["youtube_private_url"],
                "updated_at": iso_now(),
                "error_message": "",
            }
            topic.update(updates)
            self.repository.update_channel_topic(channel.sheet_name, int(topic["_row_number"]), topic)
            self.repository.append_or_update_upload(
                {
                    "upload_id": f"upload_{topic['topic_id']}",
                    "channel_id": channel.channel_id,
                    "channel_name": channel.channel_name,
                    "topic_id": topic["topic_id"],
                    "title": topic["topic_title"],
                    "upload_datetime": iso_now(),
                    "publish_datetime": topic.get("upload_datetime", ""),
                    **updates,
                }
            )
            self.repository.complete_job(job_id, json.dumps(result, ensure_ascii=False))
            return True
        except Exception as exc:
            topic.update({"status": "FAILED", "error_message": str(exc), "updated_at": iso_now()})
            self.repository.update_channel_topic(channel.sheet_name, int(topic["_row_number"]), topic)
            self.repository.fail_job(job_id, str(exc))
            raise

    def _validate_upload_topic(self, topic: dict[str, str]) -> Path:
        video_path = self._local_path_from_url(topic.get("rendered_video_url", ""))
        if not video_path.exists():
            raise FileNotFoundError(f"업로드할 영상 파일이 없습니다: {video_path}")
        return video_path

    def _reset_stage4_job(self, job_id: str) -> None:
        jobs = self.repository.client.read_records("_SYSTEM_JOBS")
        job = next((row for row in jobs if row.get("job_id") == job_id), None)
        if not job:
            return
        row_number = int(job.pop("_row_number"))
        job.update(
            {
                "status": "PENDING",
                "locked_by": "",
                "locked_until": "",
                "output_json": "",
                "error_message": "",
                "updated_at": iso_now(),
            }
        )
        self.repository.client.update_row("_SYSTEM_JOBS", row_number, JOB_COLUMNS, job)

    def _local_path_from_url(self, url: str) -> Path:
        parsed = urlparse(url)
        marker = "/files/"
        if marker not in parsed.path:
            raise ValueError("로컬 파일 URL이 아닙니다. S3 업로드 구현이 필요합니다.")
        relative = parsed.path.split(marker, 1)[1]
        return self.settings.local_storage_dir / relative

    @staticmethod
    def _description(topic: dict[str, str]) -> str:
        return (
            f"{topic.get('script_summary', '')}\n\n"
            "이 영상은 일반적인 정보 제공을 목적으로 합니다. 건강, 금융, 법률 관련 결정은 전문가와 확인해 주세요.\n\n"
            "시청해 주셔서 감사합니다."
        )
