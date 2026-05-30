from __future__ import annotations

import json
from datetime import datetime

from app.core.config import get_settings
from app.core.time import iso_now
from app.google.repository import Channel, SheetsRepository
from app.pipeline.models import RenderManifest
from app.pipeline.stage1 import Stage1Pipeline
from app.services.media_generation import PlaceholderMediaGenerator
from app.services.scene_planner import ScenePlanner


class Stage2Pipeline:
    def __init__(self, repository: SheetsRepository) -> None:
        self.repository = repository
        self.scene_planner = ScenePlanner()
        self.media = PlaceholderMediaGenerator()
        self.settings = get_settings()

    def run_once(self) -> dict[str, int]:
        stats = {"selected_topics": 0, "rendered_topics": 0, "skipped_topics": 0}
        self._progress("[stage2] 시작: 선택된 소주제 확인")
        for channel in self.repository.list_enabled_channels():
            self._progress(f"[stage2] 채널 확인: {channel.channel_name}")
            for topic in self.repository.list_channel_topics(channel.sheet_name):
                if topic.get("selected") != "TRUE":
                    continue
                stats["selected_topics"] += 1
                self._progress(f"[stage2] 선택됨: {topic.get('topic_id')} / {topic.get('topic_title')}")
                if topic.get("status") not in {"WAITING_USER_SELECTION", "SELECTED", "SCRIPT_GENERATED", "FAILED"}:
                    stats["skipped_topics"] += 1
                    self._progress(f"[stage2] 건너뜀: status={topic.get('status')}")
                    continue
                if self.render_selected_topic(channel, topic):
                    stats["rendered_topics"] += 1
        self._progress(f"[stage2] 완료: {stats}")
        return stats

    def render_selected_topic(self, channel: Channel, topic: dict[str, str]) -> bool:
        topic_id = topic["topic_id"]
        job_id = f"job_stage2_{topic_id}"
        locked = self.repository.acquire_job_lock(
            job_id=job_id,
            job_type="GENERATE_VIDEO_DRAFT",
            channel_id=channel.channel_id,
            topic_id=topic_id,
            locked_by=self.settings.server_instance_id,
            input_json=json.dumps({"sheet_name": channel.sheet_name, "row": topic.get("_row_number")}, ensure_ascii=False),
        )
        if not locked:
            self._progress(f"[stage2] lock 획득 실패 또는 이미 완료: {job_id}")
            return False
        try:
            self._render_selected_topic_locked(channel, topic)
            self.repository.complete_job(job_id, json.dumps({"status": "VIDEO_RENDERED"}, ensure_ascii=False))
            return True
        except Exception as exc:
            self.repository.fail_job(job_id, str(exc))
            self._update_topic(channel, topic, {"status": "FAILED", "error_message": str(exc), "updated_at": iso_now()})
            raise

    def _render_selected_topic_locked(self, channel: Channel, topic: dict[str, str]) -> None:
        self._progress(f"[stage2] 작업 시작: {topic['topic_id']}")
        length = self._int_or_default(topic.get("video_length_minutes"), channel.default_video_length_minutes)
        visual_style = topic.get("user_custom_style_prompt") or topic.get("visual_style") or channel.default_visual_style
        title = topic.get("topic_title", "")
        upload_day = topic.get("upload_day") or (channel.upload_days[0] if channel.upload_days else "수요일")
        upload_datetime = Stage1Pipeline._upload_datetime_for_week(topic["week_key"], upload_day, channel.upload_time)
        self._progress("[stage2] 장면 계획 생성")
        scenes = self.scene_planner.plan(
            title=title,
            script=topic.get("full_script") or topic.get("script_summary") or title,
            visual_style=visual_style,
            length_minutes=length,
        )
        self._progress(f"[stage2] 장면 {len(scenes)}개 생성됨")
        scenes = self.media.generate_scene_assets(
            topic["topic_id"],
            scenes,
            source_mode=self.settings.media_source_mode,
            progress=self._progress,
        )
        self._progress("[stage2] 자막 파일 초안 생성")
        subtitle_path, subtitle_url = self.media.write_subtitles(topic["topic_id"], scenes)
        manifest = RenderManifest(
            topic_id=topic["topic_id"],
            channel_id=channel.channel_id,
            title=title,
            video_length_minutes=length,
            visual_style=visual_style,
            scenes=scenes,
            subtitle_url=subtitle_url,
            created_at=iso_now(),
        )
        self._progress("[stage2] 영상 렌더링 시작")
        video_path, video_url = self.media.render_video(manifest, progress=self._progress)
        manifest.video_url = video_url
        self._progress("[stage2] manifest 저장")
        manifest_path = self.media.write_manifest(manifest)

        assets = [
            {
                "asset_id": f"{topic['topic_id']}_{scene.scene_id}",
                "topic_id": topic["topic_id"],
                "scene_id": scene.scene_id,
                "asset_type": "placeholder_scene",
                "asset_url": scene.asset_url,
                "prompt": scene.visual_prompt,
                "status": "READY",
                "version": "1",
                "source": scene.asset_source,
                "credit": scene.asset_credit,
                "license": scene.asset_license,
                "created_at": iso_now(),
            }
            for scene in scenes
        ]
        assets.append(
            {
                "asset_id": f"{topic['topic_id']}_video",
                "topic_id": topic["topic_id"],
                "scene_id": "",
                "asset_type": "rendered_video",
                "asset_url": video_url,
                "prompt": str(video_path),
                "status": "READY",
                "version": "1",
                "created_at": iso_now(),
            }
        )
        self.repository.append_assets(assets)
        self._progress("[stage2] _SYSTEM_ASSETS 기록 완료")

        session_id = f"session_{topic['topic_id']}"
        edit_url = f"{self.settings.public_base_url.rstrip('/')}/review/{session_id}"
        self.repository.append_or_update_session(
            {
                "session_id": session_id,
                "topic_id": topic["topic_id"],
                "edit_session_url": edit_url,
                "current_render_version": "1",
                "replacement_history_json": "[]",
                "created_at": iso_now(),
                "updated_at": iso_now(),
            }
        )
        self._update_topic(
            channel,
            topic,
            {
                "status": "WAITING_FINAL_APPROVAL",
                "upload_datetime": upload_datetime,
                "review_link": edit_url,
                "edit_session_link": edit_url,
                "rendered_video_url": video_url,
                "error_message": "",
                "updated_at": iso_now(),
            },
        )
        self.repository.append_review_tasks(
            [
                {
                    "task_id": f"approve_{topic['topic_id']}",
                    "channel_id": channel.channel_id,
                    "channel_name": channel.channel_name,
                    "topic_id": topic["topic_id"],
                    "task_type": "영상 검수 필요",
                    "title": title,
                    "status": "WAITING_FINAL_APPROVAL",
                    "deadline": "",
                    "review_link": edit_url,
                    "user_action": "",
                    "updated_at": iso_now(),
                }
            ]
        )
        self._progress(f"[stage2] 작업 완료: {topic['topic_id']} -> {video_url}")

    def _update_topic(self, channel: Channel, topic: dict[str, str], updates: dict[str, str]) -> None:
        row_number = int(topic["_row_number"])
        topic.update(updates)
        self.repository.update_channel_topic(channel.sheet_name, row_number, topic)

    @staticmethod
    def _int_or_default(value: str | None, default: int) -> int:
        try:
            return max(1, int(value or default))
        except ValueError:
            return max(1, default)

    @staticmethod
    def _progress(message: str) -> None:
        print(f"{datetime.now().strftime('%H:%M:%S')} {message}", flush=True)
