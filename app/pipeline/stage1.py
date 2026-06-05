from __future__ import annotations

import itertools
import json
import shutil
from datetime import datetime, timedelta

from app.core.constants import ASSET_COLUMNS, CHANNEL_TOPIC_COLUMNS, JOB_COLUMNS, REVIEW_COLUMNS, SESSION_COLUMNS, UPLOAD_COLUMNS
from app.core.config import get_settings
from app.core.time import iso_now
from app.core.time import KST
from app.google.repository import Channel, SheetsRepository
from app.pipeline.week import DAY_LABELS, DAY_ORDER, is_last_enabled_upload_day, next_week_key
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

    def run_once(self, force: bool = False, reset_outputs: bool = False) -> dict[str, int]:
        self._progress("[stage1] 시작: 채널목록에서 자동화 ON 채널을 읽는 중")
        channels = self.repository.list_enabled_channels()
        stats = {"enabled_channels": len(channels), "generated_topics": 0, "review_tasks": 0, "skipped_channels": 0}
        mode = []
        if force:
            mode.append("force")
        if reset_outputs:
            mode.append("test/reset_outputs")
        if mode:
            self._progress(f"[stage1] 실행 옵션: {', '.join(mode)}")
        self._progress(f"[stage1] 자동화 ON 채널 {len(channels)}개 발견")
        for index, channel in enumerate(channels, start=1):
            self._progress(f"[stage1] 채널 확인 {index}/{len(channels)}: {channel.channel_name} ({channel.channel_id})")
            if not force and not is_last_enabled_upload_day(channel.upload_days):
                stats["skipped_channels"] += 1
                self._progress(
                    f"[stage1] 건너뜀: 오늘은 {channel.channel_name}의 마지막 업로드 요일이 아닙니다. "
                    f"업로드 요일={', '.join(channel.upload_days) or '없음'}"
                )
                continue
            generated = self.generate_for_channel(channel, force=force, reset_outputs=reset_outputs)
            stats["generated_topics"] += generated
            if generated:
                stats["review_tasks"] += 1
                self._progress(f"[stage1] 채널 완료: {channel.channel_name}, 소주제 {generated}개 생성")
            else:
                self._progress(f"[stage1] 채널 완료: {channel.channel_name}, 새로 생성할 소주제 없음")
        self._progress(f"[stage1] 전체 완료: {stats}")
        return stats

    def generate_for_channel(self, channel: Channel, week_key: str | None = None, force: bool = False, reset_outputs: bool = False) -> int:
        target_week = week_key or next_week_key()
        job_id = f"job_stage1_{channel.channel_id}_{target_week}"
        self._progress(f"[stage1] 작업 준비: channel={channel.channel_name}, week={target_week}, job={job_id}")
        if reset_outputs:
            self._progress(f"[stage1] test: {channel.channel_name} 기존 Stage1 산출물 초기화 시작")
            self._reset_channel_stage1_outputs(channel)
            channel = next((item for item in self.repository.list_channels() if item.channel_id == channel.channel_id), channel)
        if force:
            self._reset_stage1_job(job_id)
        self._progress(f"[stage1] lock 요청: {job_id}")
        locked = self.repository.acquire_job_lock(
            job_id=job_id,
            job_type="GENERATE_WEEKLY_TOPICS",
            channel_id=channel.channel_id,
            topic_id="",
            locked_by=get_settings().server_instance_id,
            input_json=json.dumps({"week_key": target_week}, ensure_ascii=False),
        )
        if not locked:
            self._progress(f"[stage1] lock 획득 실패 또는 이미 완료: {job_id}")
            return 0
        try:
            self._progress(f"[stage1] lock 획득: {job_id}")
            generated = self._generate_for_channel_locked(channel, target_week, force=force)
            self.repository.complete_job(job_id, json.dumps({"generated_topics": generated}, ensure_ascii=False))
            self._progress(f"[stage1] job 완료: {job_id}, generated_topics={generated}")
            return generated
        except Exception as exc:
            self._progress(f"[stage1] job 실패: {job_id}, error={exc}")
            self.repository.fail_job(job_id, str(exc))
            self.repository.update_channel(channel, {"last_checked_at": iso_now(), "status": "FAILED", "error_message": str(exc)})
            raise

    def _generate_for_channel_locked(self, channel: Channel, target_week: str, force: bool = False) -> int:
        if not force and channel.last_topic_generated_week == target_week:
            self._progress(f"[stage1] 건너뜀: {channel.channel_name} {target_week}는 이미 생성됨")
            return 0
        self._progress(f"[stage1] 기존 소주제 확인: {channel.sheet_name}")
        existing = self.repository.list_channel_topics(channel.sheet_name)
        if not force and any(row.get("week_key") == target_week for row in existing):
            self.repository.update_channel(channel, {"last_topic_generated_week": target_week, "last_checked_at": iso_now()})
            self._progress(f"[stage1] 건너뜀: {channel.sheet_name}에 {target_week} 소주제가 이미 있음")
            return 0

        upload_days = channel.upload_days or ["WEDNESDAY"]
        count = len(upload_days) * max(1, get_settings().topic_generation_multiplier)
        self._progress(
            f"[stage1] 소주제 생성 시작: {channel.channel_name}, 업로드 요일 {len(upload_days)}개, "
            f"생성 개수 {count}개"
        )
        topic_candidates = self.topic_generator.generate(channel, target_week, count)
        self._progress(f"[stage1] 소주제 후보 생성 완료: {len(topic_candidates)}개")
        now = iso_now()
        rows = []
        day_cycle = itertools.cycle(upload_days)
        for index, topic in enumerate(topic_candidates, start=1):
            upload_day = next(day_cycle)
            upload_datetime = self._upload_datetime_for_week(target_week, upload_day, channel.upload_time)
            self._progress(
                f"[stage1] 대본 생성 {index}/{len(topic_candidates)}: "
                f"{topic.get('topic_title', '')} / 업로드 요일={DAY_LABELS.get(upload_day, upload_day)}"
            )
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
                    "upload_day": DAY_LABELS.get(upload_day, upload_day),
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

        self._progress(f"[stage1] 채널 시트 기록 시작: {channel.sheet_name}, {len(rows)}행")
        self.repository.prepend_channel_topics(channel.sheet_name, rows)
        self._progress("[stage1] 검수대기 할 일 기록 시작")
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
        self._progress("[stage1] 채널목록 상태 갱신 시작")
        self.repository.update_channel(
            channel,
            {"last_topic_generated_week": target_week, "last_checked_at": now, "status": "WAITING_USER_SELECTION", "error_message": ""},
        )
        self._progress(f"[stage1] 기록 완료: {channel.channel_name} {target_week}")
        return len(rows)

    def _reset_stage1_job(self, job_id: str) -> None:
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
        self._progress(f"[stage1] force: job 재실행 가능 상태로 변경: {job_id}")

    def _reset_channel_stage1_outputs(self, channel: Channel) -> None:
        old_topics = [topic for topic in self.repository.list_channel_topics(channel.sheet_name) if topic.get("topic_id")]
        old_topic_ids = {topic["topic_id"] for topic in old_topics}
        clear_width = len(CHANNEL_TOPIC_COLUMNS)
        blank_rows = [[""] * clear_width for _ in range(500 - 23 + 1)]
        end_col = self.repository.client._col_letter(clear_width)
        self.repository.client.update_values(f"'{channel.sheet_name}'!A23:{end_col}500", blank_rows)
        self.repository.update_channel(
            channel,
            {
                "last_topic_generated_week": "",
                "last_checked_at": iso_now(),
                "status": "EMPTY",
                "error_message": "",
            },
        )
        cleared = {
            "review_tasks": self._clear_matching_rows("검수대기", REVIEW_COLUMNS, lambda row: row.get("channel_id") == channel.channel_id),
            "uploads": self._clear_matching_rows(
                "업로드현황",
                UPLOAD_COLUMNS,
                lambda row: row.get("channel_id") == channel.channel_id or row.get("topic_id") in old_topic_ids,
            ),
            "assets": self._clear_matching_rows("_SYSTEM_ASSETS", ASSET_COLUMNS, lambda row: row.get("topic_id") in old_topic_ids),
            "sessions": self._clear_matching_rows("_SYSTEM_SESSIONS", SESSION_COLUMNS, lambda row: row.get("topic_id") in old_topic_ids),
            "jobs": self._clear_matching_rows(
                "_SYSTEM_JOBS",
                JOB_COLUMNS,
                lambda row: row.get("channel_id") == channel.channel_id
                and (row.get("job_type") in {"GENERATE_WEEKLY_TOPICS", "GENERATE_VIDEO_DRAFT"} or row.get("topic_id") in old_topic_ids),
            ),
        }
        for topic_id in old_topic_ids:
            topic_dir = get_settings().local_storage_dir / "topics" / topic_id
            if topic_dir.exists():
                shutil.rmtree(topic_dir)
        self._progress(f"[stage1] test: {channel.channel_name} 기존 소주제 {len(old_topic_ids)}개 및 관련 산출물 삭제: {cleared}")

    def _clear_matching_rows(self, sheet_name: str, headers: list[str], predicate) -> int:
        cleared = 0
        for row in self.repository.client.read_records(sheet_name):
            normalized = self.repository._normalize_record(row)
            if not predicate(normalized):
                continue
            row_number = int(row["_row_number"])
            end_col = self.repository.client._col_letter(len(headers))
            self.repository.client.update_values(f"'{sheet_name}'!A{row_number}:{end_col}{row_number}", [[""] * len(headers)])
            cleared += 1
        return cleared

    @staticmethod
    def _upload_datetime_for_week(week_key: str, upload_day: str, upload_time: str) -> str:
        year = int(week_key[:4])
        week = int(week_key[-2:])
        monday = datetime.fromisocalendar(year, week, 1).replace(tzinfo=KST)
        day = monday + timedelta(days=DAY_ORDER[upload_day])
        hour, minute = [int(part) for part in upload_time.split(":")[:2]]
        dt = day.replace(hour=hour, minute=minute)
        return f"{dt.strftime('%Y-%m-%d')}\n{dt.strftime('%H:%M')}"

    @staticmethod
    def _selection_deadline(week_key: str) -> str:
        year = int(week_key[:4])
        week = int(week_key[-2:])
        monday = datetime.fromisocalendar(year, week, 1).replace(tzinfo=KST)
        dt = monday.replace(hour=23, minute=59)
        return f"{dt.strftime('%Y-%m-%d')}\n{dt.strftime('%H:%M')}"

    @staticmethod
    def _progress(message: str) -> None:
        print(f"{datetime.now().strftime('%H:%M:%S')} {message}", flush=True)
