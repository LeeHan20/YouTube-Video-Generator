from __future__ import annotations

from dataclasses import dataclass

from app.core.constants import (
    ASSET_COLUMNS,
    CHANNEL_LIST_COLUMNS,
    CHANNEL_TOPIC_COLUMNS,
    HEADER_ALIASES,
    JOB_COLUMNS,
    REVIEW_COLUMNS,
    SESSION_COLUMNS,
    UPLOAD_COLUMNS,
)
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
        normalized = [self._normalize_record(record) for record in records]
        return [self._channel_from_record(record) for record in normalized if record.get("channel_id")]

    def list_enabled_channels(self) -> list[Channel]:
        return [channel for channel in self.list_channels() if channel.automation_enabled == "ON"]

    def update_channel(self, channel: Channel, updates: dict[str, str]) -> None:
        record = {**channel.raw, **updates}
        record.pop("_row_number", None)
        self.client.update_row("채널목록", channel.row_number, CHANNEL_LIST_COLUMNS, record)

    def list_channel_topics(self, sheet_name: str) -> list[dict[str, str]]:
        header_row = self._topic_header_row()
        return [self._normalize_record(record) for record in self.client.read_records(sheet_name, header_row=header_row)]

    def find_topic(self, topic_id: str) -> tuple[Channel, dict[str, str]] | None:
        for channel in self.list_channels():
            for topic in self.list_channel_topics(channel.sheet_name):
                if topic.get("topic_id") == topic_id:
                    return channel, topic
        return None

    def append_channel_topics(self, sheet_name: str, topics: list[dict[str, str]]) -> None:
        self.client.append_records(sheet_name, CHANNEL_TOPIC_COLUMNS, topics)

    def prepend_channel_topics(self, sheet_name: str, topics: list[dict[str, str]]) -> None:
        """Insert topics at the top of the channel sheet (just below the topic header row)."""
        header_row = self._topic_header_row()  # 1-indexed
        meta = self.client.metadata()
        sheet_meta = meta.get(sheet_name)
        if sheet_meta is None:
            self.append_channel_topics(sheet_name, topics)
            return
        count = len(topics)
        # Insert topic rows + 1 separator row right after the header (0-indexed position = header_row)
        self.client.insert_rows_before(sheet_meta.sheet_id, header_row, count + 1)
        rows = [[topic.get(col, "") for col in CHANNEL_TOPIC_COLUMNS] for topic in topics]
        from app.google.sheets_client import SheetsClient
        end_col = SheetsClient._col_letter(len(CHANNEL_TOPIC_COLUMNS))
        range_name = f"'{sheet_name}'!A{header_row + 1}:{end_col}{header_row + count}"
        self.client.update_values(range_name, rows)
        # header_row + count + 1 is left empty as a week separator

    def update_channel_topic(self, sheet_name: str, row_number: int, topic: dict[str, str]) -> None:
        topic.pop("_row_number", None)
        self.client.update_row(sheet_name, row_number, CHANNEL_TOPIC_COLUMNS, topic)

    def append_review_tasks(self, tasks: list[dict[str, str]]) -> None:
        self.client.append_records("검수대기", REVIEW_COLUMNS, tasks)

    def list_review_tasks(self) -> list[dict[str, str]]:
        return [self._normalize_record(record) for record in self.client.read_records("검수대기")]

    def update_review_task(self, row_number: int, task: dict[str, str]) -> None:
        task.pop("_row_number", None)
        self.client.update_row("검수대기", row_number, REVIEW_COLUMNS, task)

    def append_assets(self, assets: list[dict[str, str]]) -> None:
        self.client.append_records("_SYSTEM_ASSETS", ASSET_COLUMNS, assets)

    def list_assets(self, topic_id: str) -> list[dict[str, str]]:
        return [
            self._normalize_record(record)
            for record in self.client.read_records("_SYSTEM_ASSETS")
            if self._normalize_record(record).get("topic_id") == topic_id
        ]

    def append_or_update_session(self, session: dict[str, str]) -> None:
        sessions = self.client.read_records("_SYSTEM_SESSIONS")
        existing = next((row for row in sessions if row.get("session_id") == session.get("session_id")), None)
        if existing:
            row_number = int(existing.pop("_row_number"))
            existing.update(session)
            self.client.update_row("_SYSTEM_SESSIONS", row_number, SESSION_COLUMNS, existing)
            return
        self.client.append_records("_SYSTEM_SESSIONS", SESSION_COLUMNS, [session])

    def get_session(self, session_id: str) -> dict[str, str] | None:
        for record in self.client.read_records("_SYSTEM_SESSIONS"):
            if record.get("session_id") == session_id:
                return record
        return None

    def append_or_update_upload(self, upload: dict[str, str]) -> None:
        uploads = self.client.read_records("업로드현황")
        existing = next((row for row in uploads if row.get("upload_id") == upload.get("upload_id")), None)
        if existing:
            row_number = int(existing.pop("_row_number"))
            existing.update(upload)
            self.client.update_row("업로드현황", row_number, UPLOAD_COLUMNS, existing)
            return
        self.client.append_records("업로드현황", UPLOAD_COLUMNS, [upload])

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
    def _normalize_record(record: dict[str, str]) -> dict[str, str]:
        normalized = {}
        for key, value in record.items():
            normalized[HEADER_ALIASES.get(key, key)] = value
        return normalized

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
