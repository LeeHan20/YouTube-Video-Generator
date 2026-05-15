from __future__ import annotations

from dataclasses import dataclass

from app.core.constants import CHANNEL_LIST_COLUMNS, CHANNEL_TOPIC_COLUMNS, JOB_COLUMNS, REVIEW_COLUMNS
from app.core.time import iso_now, lock_expiry, parse_iso, utc_now
from app.google.sheets_client import SheetsClient


@dataclass
class Channel:
    channel_id: str
    channel_name: str
    sheet_name: str
    automation_enabled: str
    upload_days: list[str]
    upload_time: str
    default_video_length_minutes: int
    default_visual_style: str
    last_topic_generated_week: str
    row_number: int
    raw: dict[str, str]


class SheetsRepository:
    def __init__(self, client: SheetsClient) -> None:
        self.client = client

    def list_channels(self) -> list[Channel]:
        records = self.client.read_records("채널목록")
        return [self._channel_from_record(record) for record in records if record.get("channel_id")]

    def list_enabled_channels(self) -> list[Channel]:
        return [channel for channel in self.list_channels() if channel.automation_enabled == "ON"]

    def update_channel(self, channel: Channel, updates: dict[str, str]) -> None:
        record = {**channel.raw, **updates}
        record.pop("_row_number", None)
        self.client.update_row("채널목록", channel.row_number, CHANNEL_LIST_COLUMNS, record)

    def list_channel_topics(self, sheet_name: str) -> list[dict[str, str]]:
        header_row = self._topic_header_row()
        return self.client.read_records(sheet_name, header_row=header_row)

    def append_channel_topics(self, sheet_name: str, topics: list[dict[str, str]]) -> None:
        self.client.append_records(sheet_name, CHANNEL_TOPIC_COLUMNS, topics)

    def append_review_tasks(self, tasks: list[dict[str, str]]) -> None:
        self.client.append_records("검수대기", REVIEW_COLUMNS, tasks)

    def acquire_job_lock(
        self,
        job_id: str,
        job_type: str,
        channel_id: str,
        topic_id: str,
        locked_by: str,
        input_json: str = "",
        lock_minutes: int = 30,
    ) -> bool:
        jobs = self.client.read_records("_SYSTEM_JOBS")
        job = next((row for row in jobs if row.get("job_id") == job_id), None)
        if not job:
            now = iso_now()
            self.client.append_records(
                "_SYSTEM_JOBS",
                JOB_COLUMNS,
                [
                    {
                        "job_id": job_id,
                        "job_type": job_type,
                        "channel_id": channel_id,
                        "topic_id": topic_id,
                        "status": "PENDING",
                        "locked_by": "",
                        "locked_until": "",
                        "retry_count": "0",
                        "max_retries": "3",
                        "input_json": input_json,
                        "output_json": "",
                        "error_message": "",
                        "created_at": now,
                        "updated_at": now,
                    }
                ],
            )
            jobs = self.client.read_records("_SYSTEM_JOBS")
            job = next((row for row in jobs if row.get("job_id") == job_id), None)
        if not job:
            return False

        locked_until = parse_iso(job.get("locked_until"))
        if job.get("status") == "DONE":
            return False
        if locked_until and locked_until > utc_now() and job.get("locked_by") != locked_by:
            return False

        job.update(
            {
                "status": "RUNNING",
                "locked_by": locked_by,
                "locked_until": lock_expiry(lock_minutes),
                "updated_at": iso_now(),
            }
        )
        row_number = int(job.pop("_row_number"))
        self.client.update_row("_SYSTEM_JOBS", row_number, JOB_COLUMNS, job)
        return True

    def complete_job(self, job_id: str, output_json: str = "") -> None:
        self._finish_job(job_id, "DONE", output_json=output_json)

    def fail_job(self, job_id: str, error_message: str) -> None:
        self._finish_job(job_id, "FAILED", error_message=error_message)

    def _finish_job(self, job_id: str, status: str, output_json: str = "", error_message: str = "") -> None:
        jobs = self.client.read_records("_SYSTEM_JOBS")
        job = next((row for row in jobs if row.get("job_id") == job_id), None)
        if not job:
            return
        row_number = int(job.pop("_row_number"))
        job.update(
            {
                "status": status,
                "locked_by": "",
                "locked_until": "",
                "output_json": output_json or job.get("output_json", ""),
                "error_message": error_message,
                "updated_at": iso_now(),
            }
        )
        self.client.update_row("_SYSTEM_JOBS", row_number, JOB_COLUMNS, job)

    @staticmethod
    def _topic_header_row() -> int:
        return 22

    @staticmethod
    def _channel_from_record(record: dict[str, str]) -> Channel:
        day_columns = {
            "monday_enabled": "MONDAY",
            "tuesday_enabled": "TUESDAY",
            "wednesday_enabled": "WEDNESDAY",
            "thursday_enabled": "THURSDAY",
            "friday_enabled": "FRIDAY",
            "saturday_enabled": "SATURDAY",
            "sunday_enabled": "SUNDAY",
        }
        upload_days = [day for column, day in day_columns.items() if record.get(column) == "ON"]
        try:
            length = int(record.get("default_video_length_minutes") or "5")
        except ValueError:
            length = 5
        return Channel(
            channel_id=record.get("channel_id", ""),
            channel_name=record.get("channel_name", ""),
            sheet_name=record.get("sheet_name", ""),
            automation_enabled=record.get("automation_enabled", "OFF"),
            upload_days=upload_days,
            upload_time=record.get("upload_time", "20:00") or "20:00",
            default_video_length_minutes=max(1, length),
            default_visual_style=record.get("default_visual_style", "따뜻한 수채화 애니메이션풍"),
            last_topic_generated_week=record.get("last_topic_generated_week", ""),
            row_number=int(record["_row_number"]),
            raw=record,
        )
