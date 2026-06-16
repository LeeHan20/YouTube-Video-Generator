from __future__ import annotations

from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.time import iso_now, parse_iso
from app.google.repository import SheetsRepository
from app.sms.provider import SmsProvider, sms_provider_from_settings
from app.youtube.oauth import EncryptedTokenStore
from app.youtube.uploader import YouTubeUploader


class Stage5PublishAndSmsPipeline:
    def __init__(self, repository: SheetsRepository, sms_provider: SmsProvider | None = None) -> None:
        self.repository = repository
        self.settings = get_settings()
        self.tokens = EncryptedTokenStore()
        self.uploader = YouTubeUploader()
        self.sms = sms_provider or sms_provider_from_settings()

    def run_once(self, force: bool = False, test: bool = False) -> dict[str, int]:
        stats = {"published": 0, "would_publish": 0, "sms_sent": 0, "would_send_sms": 0}
        publish_stats = self._publish_due_videos(force=force, test=test)
        sms_stats = self._send_selection_reminders(force=force, test=test)
        stats.update(publish_stats)
        stats.update(sms_stats)
        return stats

    def _publish_due_videos(self, force: bool = False, test: bool = False) -> dict[str, int]:
        stats = {"published": 0, "would_publish": 0}
        now = datetime.now(UTC)
        for channel in self.repository.list_enabled_channels():
            if not self.tokens.has_token(channel.channel_id):
                continue
            for topic in self.repository.list_channel_topics(channel.sheet_name):
                if topic.get("status") not in {"UPLOADED_PRIVATE", "SCHEDULED"}:
                    continue
                publish_at = parse_iso(topic.get("upload_datetime"))
                if not force and (not publish_at or publish_at > now):
                    continue
                if test:
                    stats["would_publish"] += 1
                    continue
                result = self.uploader.publish(channel.channel_id, topic["youtube_video_id"])
                topic.update({"status": "PUBLISHED", "youtube_public_url": result["youtube_public_url"], "updated_at": iso_now()})
                self.repository.update_channel_topic(channel.sheet_name, int(topic["_row_number"]), topic)
                stats["published"] += 1
        return stats

    def _send_selection_reminders(self, force: bool = False, test: bool = False) -> dict[str, int]:
        stats = {"sms_sent": 0, "would_send_sms": 0}
        today = datetime.now(UTC).weekday()
        if not force and today not in {0, 1}:
            return stats
        for channel in self.repository.list_enabled_channels():
            if not channel.raw.get("alert_phone_number"):
                continue
            waiting = [
                topic
                for topic in self.repository.list_channel_topics(channel.sheet_name)
                if topic.get("status") == "WAITING_USER_SELECTION" and topic.get("selected") != "TRUE"
            ]
            if not waiting:
                continue
            message = self._selection_reminder_message(channel.channel_name, len(waiting))
            if test:
                stats["would_send_sms"] += 1
                continue
            job_id = f"sms_selection_{channel.channel_id}_{datetime.now(UTC).date().isoformat()}"
            locked = self.repository.acquire_job_lock(
                job_id=job_id,
                job_type="SMS_SELECTION_REMINDER",
                channel_id=channel.channel_id,
                topic_id="",
                locked_by=self.settings.server_instance_id,
                lock_minutes=24 * 60,
            )
            if not locked:
                continue
            result = self.sms.send(channel.raw["alert_phone_number"], message)
            if not result.success:
                self.repository.fail_job(job_id, result.error_message or "SMS send failed")
                continue
            self.repository.complete_job(job_id)
            stats["sms_sent"] += 1
        return stats

    def _selection_reminder_message(self, channel_name: str, waiting_count: int) -> str:
        template = self._sms_template()
        message = template.format(
            channel_name=channel_name,
            waiting_count=waiting_count,
            public_base_url=self.settings.public_base_url,
        ).strip()
        message_bytes = len(message.encode("utf-8"))
        if message_bytes > self.settings.sms_max_bytes:
            raise ValueError(f"SMS template rendered to {message_bytes} bytes; max is {self.settings.sms_max_bytes} bytes")
        return message

    def _sms_template(self) -> str:
        path = self.settings.sms_format_path
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        return "[{channel_name}] 선택 필요 {waiting_count}건"
