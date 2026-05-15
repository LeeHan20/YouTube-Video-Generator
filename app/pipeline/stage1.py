from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime, timedelta

from app.core.constants import CHANNEL_TOPIC_COLUMNS
from app.core.config import get_settings
from app.core.time import iso_now
from app.google.repository import Channel, SheetsRepository
from app.pipeline.week import DAY_ORDER, is_last_enabled_upload_day, next_week_key
from app.services.script_generator import ScriptGenerator
from app.services.topic_generator import TopicGenerator


class Stage1Pipeline:
    def __init__(
        self,
        repository: SheetsRepository,
        topic_generator: TopicGenerator | None = None,
        script_generator: ScriptGenerator | None = None,
    ) -> None:
        self.repository = repository
        self.topic_generator = topic_generator or TopicGenerator()
        self.script_generator = script_generator or ScriptGenerator()

    def run_once(self, force: bool = False) -> dict[str, int]:
        channels = self.repository.list_enabled_channels()
        stats = {"enabled_channels": len(channels), "generated_topics": 0, "review_tasks": 0, "skipped_channels": 0}
        for channel in channels:
            if not force and not is_last_enabled_upload_day(channel.upload_days):
                stats["skipped_channels"] += 1
                continue
            generated = self.generate_for_channel(channel)
            stats["generated_topics"] += generated
            if generated:
                stats["review_tasks"] += 1
        return stats

    def generate_for_channel(self, channel: Channel, week_key: str | None = None) -> int:
        target_week = week_key or next_week_key()
        job_id = f"job_stage1_{channel.channel_id}_{target_week}"
        locked = self.repository.acquire_job_lock(
            job_id=job_id,
            job_type="GENERATE_WEEKLY_TOPICS",
            channel_id=channel.channel_id,
            topic_id="",
            locked_by=get_settings().server_instance_id,
            input_json=json.dumps({"week_key": target_week}, ensure_ascii=False),
        )
        if not locked:
            return 0
        try:
            generated = self._generate_for_channel_locked(channel, target_week)
            self.repository.complete_job(job_id, json.dumps({"generated_topics": generated}, ensure_ascii=False))
            return generated
        except Exception as exc:
            self.repository.fail_job(job_id, str(exc))
            self.repository.update_channel(channel, {"last_checked_at": iso_now(), "status": "FAILED", "error_message": str(exc)})
            raise

    def _generate_for_channel_locked(self, channel: Channel, target_week: str) -> int:
        if channel.last_topic_generated_week == target_week:
            return 0
        existing = self.repository.list_channel_topics(channel.sheet_name)
        if any(row.get("week_key") == target_week for row in existing):
            self.repository.update_channel(channel, {"last_topic_generated_week": target_week, "last_checked_at": iso_now()})
            return 0

        upload_days = channel.upload_days or ["WEDNESDAY"]
        count = len(upload_days) * 3
        topic_candidates = self.topic_generator.generate(channel, target_week, count)
        now = iso_now()
        rows = []
        day_cycle = itertools.cycle(upload_days)
        for topic in topic_candidates:
            upload_day = next(day_cycle)
            upload_datetime = self._upload_datetime_for_week(target_week, upload_day, channel.upload_time)
            full_script = self.script_generator.generate(
                topic["topic_title"],
                topic["script_summary"],
                channel.default_video_length_minutes,
            )
            row = {column: "" for column in CHANNEL_TOPIC_COLUMNS}
            row.update(
                {
                    **topic,
                    "week_key": target_week,
                    "upload_day": upload_day,
                    "upload_datetime": upload_datetime,
                    "selected": "FALSE",
                    "full_script": full_script,
                    "video_length_minutes": str(channel.default_video_length_minutes),
                    "visual_style": channel.default_visual_style,
                    "status": "WAITING_USER_SELECTION",
                    "created_at": now,
                    "updated_at": now,
                }
            )
            rows.append(row)

        self.repository.append_channel_topics(channel.sheet_name, rows)
        self.repository.append_review_tasks(
            [
                {
                    "task_id": f"select_{channel.channel_id}_{target_week}",
                    "channel_id": channel.channel_id,
                    "channel_name": channel.channel_name,
                    "topic_id": "",
                    "task_type": "소주제 선택 필요",
                    "title": f"{channel.channel_name} {target_week} 소주제 후보 선택",
                    "status": "WAITING_USER_SELECTION",
                    "deadline": self._selection_deadline(target_week),
                    "review_link": "",
                    "user_action": "",
                    "updated_at": now,
                }
            ]
        )
        self.repository.update_channel(
            channel,
            {"last_topic_generated_week": target_week, "last_checked_at": now, "status": "WAITING_USER_SELECTION", "error_message": ""},
        )
        return len(rows)

    @staticmethod
    def _upload_datetime_for_week(week_key: str, upload_day: str, upload_time: str) -> str:
        year = int(week_key[:4])
        week = int(week_key[-2:])
        monday = datetime.fromisocalendar(year, week, 1).replace(tzinfo=UTC)
        day = monday + timedelta(days=DAY_ORDER[upload_day])
        hour, minute = [int(part) for part in upload_time.split(":")[:2]]
        return day.replace(hour=hour, minute=minute).isoformat()

    @staticmethod
    def _selection_deadline(week_key: str) -> str:
        year = int(week_key[:4])
        week = int(week_key[-2:])
        monday = datetime.fromisocalendar(year, week, 1).replace(tzinfo=UTC)
        return monday.replace(hour=23, minute=59).isoformat()
