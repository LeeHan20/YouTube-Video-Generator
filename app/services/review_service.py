from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from time import time
from urllib.parse import unquote

from app.core.config import get_settings
from app.core.time import iso_now
from app.google.repository import SheetsRepository
from app.pipeline.models import RenderManifest, Scene
from app.services.media_generation import PlaceholderMediaGenerator
from app.services.media_sources import MediaSourceService
from app.services.scene_planner import ScenePlanner


class ReviewService:
    def __init__(self, repository: SheetsRepository) -> None:
        self.repository = repository
        self.settings = get_settings()
        self.media = PlaceholderMediaGenerator()
        self.media_sources = MediaSourceService()
        self.scene_planner = ScenePlanner()

    def get_session_payload(self, session_id: str) -> dict:
        session = self.repository.get_session(session_id)
        if not session:
            raise ValueError("검수 세션을 찾을 수 없습니다.")
        manifest = self._read_manifest(session["topic_id"])
        return {
            "session": session,
            "manifest": manifest.model_dump(),
            "assets": self.repository.list_assets(session["topic_id"]),
        }

    def regenerate_scene(self, session_id: str, scene_id: str, user_instruction: str, source_mode: str = "auto") -> dict:
        session = self.repository.get_session(session_id)
        if not session:
            raise ValueError("검수 세션을 찾을 수 없습니다.")
        manifest = self._read_manifest(session["topic_id"])
        scene = next((item for item in manifest.scenes if item.scene_id == scene_id), None)
        if not scene:
            raise ValueError("장면을 찾을 수 없습니다.")
        original_prompt = scene.visual_prompt
        original_subtitle = scene.subtitle
        original_narration = scene.narration
        original_start = scene.start_seconds
        original_duration = scene.duration_seconds
        asset = self.media_sources.create_asset(manifest.topic_id, scene, source_mode=source_mode, user_instruction=user_instruction)
        scene.asset_url = asset.asset_url
        scene.selected_image_url = asset.asset_url
        scene.selected_image_path = str(asset.local_path or "")
        scene.image_hash = asset.image_hash
        scene.asset_source = asset.source
        scene.asset_credit = asset.credit
        scene.asset_license = asset.license
        scene.visual_prompt = original_prompt
        scene.subtitle = original_subtitle
        scene.narration = original_narration
        scene.start_seconds = original_start
        scene.duration_seconds = original_duration
        self._write_manifest(manifest)
        self.repository.append_assets(
            [
                {
                    "asset_id": f"{manifest.topic_id}_{scene.scene_id}_{int(time())}",
                    "topic_id": manifest.topic_id,
                    "scene_id": scene.scene_id,
                    "asset_type": asset.source,
                    "asset_url": scene.asset_url,
                    "prompt": asset.prompt,
                    "status": "READY",
                    "version": str(int(session.get("current_render_version") or "1") + 1),
                    "source": asset.source,
                    "credit": asset.credit,
                    "license": asset.license,
                    "created_at": iso_now(),
                }
            ]
        )
        self._append_history(
            session,
            {
                "scene_id": scene_id,
                "action": "replace_asset",
                "dirty": True,
                "instruction": user_instruction,
                "source_mode": source_mode,
                "prompt_used_for_asset": asset.prompt,
                "preserved_prompt": scene.visual_prompt,
            },
        )
        return self.get_session_payload(session_id)

    def crawl_image_candidates(self, session_id: str, scene_ids: list[str], user_instruction: str = "") -> dict:
        session = self.repository.get_session(session_id)
        if not session:
            raise ValueError("검수 세션을 찾을 수 없습니다.")
        manifest = self._read_manifest(session["topic_id"])
        requested = set(scene_ids)
        if not requested:
            raise ValueError("이미지를 다시 가져올 장면을 선택해 주세요.")
        found = self.repository.find_topic(manifest.topic_id)
        if found:
            channel, topic = found
            topic.update({"status": "IMAGE_CRAWLING", "updated_at": iso_now(), "error_message": ""})
            self.repository.update_channel_topic(channel.sheet_name, int(topic["_row_number"]), topic)
        failed = []
        for scene in manifest.scenes:
            if scene.scene_id not in requested:
                continue
            candidates = self.media_sources.crawl_candidates(
                manifest.topic_id,
                scene,
                source_mode="crawl_image",
                user_instruction=user_instruction,
                limit=4,
                excluded_asset_urls={item.asset_url for item in manifest.scenes if item.scene_id != scene.scene_id and item.asset_url},
                excluded_hashes={item.image_hash for item in manifest.scenes if item.scene_id != scene.scene_id and item.image_hash},
            )
            scene.image_candidates = [candidate.as_dict() for candidate in candidates]
            scene.selected_image_candidate = ""
            if not candidates:
                failed.append(scene.scene_id)
        self._write_manifest(manifest)
        status = "IMAGE_CRAWLING_FAILED" if failed and len(failed) == len(requested) else "IMAGE_CANDIDATES_READY"
        if found:
            channel, topic = found
            topic.update(
                {
                    "status": status,
                    "updated_at": iso_now(),
                    "error_message": "일부 장면의 이미지 후보를 찾지 못했습니다." if failed else "",
                }
            )
            self.repository.update_channel_topic(channel.sheet_name, int(topic["_row_number"]), topic)
        self._append_history(
            session,
            {
                "scene_ids": list(requested),
                "action": "crawl_candidates",
                "dirty": False,
                "failed_scene_ids": failed,
                "user_instruction": user_instruction,
            },
            bump_version=False,
        )
        return self.get_session_payload(session_id)

    def update_scene_prompt(self, session_id: str, scene_id: str, visual_prompt: str) -> dict:
        session = self.repository.get_session(session_id)
        if not session:
            raise ValueError("검수 세션을 찾을 수 없습니다.")
        manifest = self._read_manifest(session["topic_id"])
        scene = next((item for item in manifest.scenes if item.scene_id == scene_id), None)
        if not scene:
            raise ValueError("장면을 찾을 수 없습니다.")
        cleaned = re.sub(r"\s+", " ", visual_prompt or "").strip()
        if not cleaned:
            raise ValueError("장면 프롬프트를 입력해 주세요.")
        scene.visual_prompt = cleaned
        scene.crawl_prompt = cleaned
        scene.generation_prompt = cleaned
        scene.image_keywords = self.scene_planner._representative_keywords(cleaned)
        self._write_manifest(manifest)
        self._append_history(
            session,
            {
                "scene_id": scene_id,
                "action": "update_scene_prompt",
                "dirty": False,
                "visual_prompt": cleaned,
            },
            bump_version=False,
        )
        return self.get_session_payload(session_id)

    def select_image_candidate(self, session_id: str, scene_id: str, candidate_id: str) -> dict:
        session = self.repository.get_session(session_id)
        if not session:
            raise ValueError("검수 세션을 찾을 수 없습니다.")
        manifest = self._read_manifest(session["topic_id"])
        scene = next((item for item in manifest.scenes if item.scene_id == scene_id), None)
        if not scene:
            raise ValueError("장면을 찾을 수 없습니다.")
        candidate = next((item for item in scene.image_candidates if item.get("candidate_id") == candidate_id), None)
        if not candidate:
            raise ValueError("이미지 후보를 찾을 수 없습니다.")
        scene.asset_url = candidate.get("asset_url", "")
        scene.selected_image_url = scene.asset_url
        scene.selected_image_path = ""
        scene.image_hash = candidate.get("image_hash", "")
        scene.asset_source = candidate.get("source", "crawl_image")
        scene.asset_credit = candidate.get("credit", "")
        scene.asset_license = candidate.get("license", "")
        scene.selected_image_candidate = candidate_id
        self._write_manifest(manifest)
        self.repository.append_assets(
            [
                {
                    "asset_id": f"{manifest.topic_id}_{scene.scene_id}_{int(time())}",
                    "topic_id": manifest.topic_id,
                    "scene_id": scene.scene_id,
                    "asset_type": scene.asset_source,
                    "asset_url": scene.asset_url,
                    "prompt": candidate.get("prompt", scene.visual_prompt),
                    "status": "READY",
                    "version": str(int(session.get("current_render_version") or "1") + 1),
                    "source": scene.asset_source,
                    "credit": scene.asset_credit,
                    "license": scene.asset_license,
                    "created_at": iso_now(),
                }
            ]
        )
        found = self.repository.find_topic(manifest.topic_id)
        if found:
            channel, topic = found
            topic.update({"status": "IMAGE_SELECTED", "updated_at": iso_now(), "error_message": ""})
            self.repository.update_channel_topic(channel.sheet_name, int(topic["_row_number"]), topic)
        self._append_history(
            session,
            {
                "scene_id": scene_id,
                "action": "select_image_candidate",
                "candidate_id": candidate_id,
                "dirty": True,
                "preserved_prompt": scene.visual_prompt,
            },
        )
        return self.get_session_payload(session_id)

    def upload_scene_asset(self, session_id: str, scene_id: str, filename: str, content: bytes) -> dict:
        session = self.repository.get_session(session_id)
        if not session:
            raise ValueError("검수 세션을 찾을 수 없습니다.")
        manifest = self._read_manifest(session["topic_id"])
        scene = next((item for item in manifest.scenes if item.scene_id == scene_id), None)
        if not scene:
            raise ValueError("장면을 찾을 수 없습니다.")
        if not content:
            raise ValueError("업로드된 파일이 비어 있습니다.")
        suffix = Path(filename or "upload.png").suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm", ".mov"}:
            raise ValueError("지원하지 않는 파일 형식입니다.")
        safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(filename or "upload").stem).strip("._") or "upload"
        upload_dir = self.settings.local_storage_dir / "topics" / manifest.topic_id / "assets" / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        path = upload_dir / f"{scene.scene_id}_{int(time())}_{safe_stem}{suffix}"
        path.write_bytes(content)
        scene.asset_url = self.media._url_for(path)
        scene.selected_image_url = scene.asset_url
        scene.selected_image_path = str(path)
        scene.image_hash = self.media._file_hash(path)
        scene.asset_source = "user_upload"
        scene.asset_credit = f"사용자 업로드: {filename or path.name}"
        scene.asset_license = "user provided"
        self._write_manifest(manifest)
        self.repository.append_assets(
            [
                {
                    "asset_id": f"{manifest.topic_id}_{scene.scene_id}_{int(time())}",
                    "topic_id": manifest.topic_id,
                    "scene_id": scene.scene_id,
                    "asset_type": "user_upload",
                    "asset_url": scene.asset_url,
                    "prompt": scene.visual_prompt,
                    "status": "READY",
                    "version": str(int(session.get("current_render_version") or "1") + 1),
                    "source": "user_upload",
                    "credit": scene.asset_credit,
                    "license": scene.asset_license,
                    "created_at": iso_now(),
                }
            ]
        )
        self._append_history(session, {"scene_id": scene_id, "action": "upload_asset", "dirty": True, "filename": filename})
        return self.get_session_payload(session_id)

    def rerender(self, session_id: str) -> dict:
        session = self.repository.get_session(session_id)
        if not session:
            raise ValueError("검수 세션을 찾을 수 없습니다.")
        manifest = self._read_manifest(session["topic_id"])
        found = self.repository.find_topic(manifest.topic_id)
        if found:
            channel, topic = found
            topic.update({"status": "VIDEO_RENDERING", "updated_at": iso_now(), "error_message": ""})
            self.repository.update_channel_topic(channel.sheet_name, int(topic["_row_number"]), topic)
        try:
            dirty_scene_ids = self._dirty_scene_ids(session)
            _, video_url = self.media.render_video(manifest, dirty_scene_ids=dirty_scene_ids or None)
            manifest.video_url = video_url
            self._write_manifest(manifest)
            if found:
                channel, topic = found
                topic.update({"rendered_video_url": video_url, "updated_at": iso_now(), "status": "VIDEO_RENDERED", "error_message": ""})
                self.repository.update_channel_topic(channel.sheet_name, int(topic["_row_number"]), topic)
            self._clear_dirty_history(session)
            return self.get_session_payload(session_id)
        except Exception as exc:
            if found:
                channel, topic = found
                topic.update({"status": "VIDEO_RENDER_FAILED", "error_message": str(exc), "updated_at": iso_now()})
                self.repository.update_channel_topic(channel.sheet_name, int(topic["_row_number"]), topic)
            raise

    def approve(self, session_id: str) -> dict[str, str]:
        session = self.repository.get_session(session_id)
        if not session:
            raise ValueError("검수 세션을 찾을 수 없습니다.")
        found = self.repository.find_topic(session["topic_id"])
        if not found:
            raise ValueError("소주제를 찾을 수 없습니다.")
        channel, topic = found
        topic.update({"status": "FINAL_APPROVING", "updated_at": iso_now(), "error_message": ""})
        self.repository.update_channel_topic(channel.sheet_name, int(topic["_row_number"]), topic)
        topic.update({"status": "FINAL_APPROVED", "updated_at": iso_now(), "error_message": ""})
        self.repository.update_channel_topic(channel.sheet_name, int(topic["_row_number"]), topic)
        return {"status": "FINAL_APPROVED", "topic_id": session["topic_id"]}

    def editable_package(self, session_id: str) -> Path:
        session = self.repository.get_session(session_id)
        if not session:
            raise ValueError("검수 세션을 찾을 수 없습니다.")
        manifest = self._read_manifest(session["topic_id"])
        for scene in manifest.scenes:
            if not scene.caption_segments:
                scene.caption_segments = self.media._caption_segments_for_scene(scene)
        topic_dir = self.settings.local_storage_dir / "topics" / manifest.topic_id
        export_dir = topic_dir / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        package_path = export_dir / f"{manifest.topic_id}_{manifest.render_id or 'latest'}_editable.zip"
        project = self._editable_project(manifest)
        srt_text = self.media._srt(manifest.scenes)
        vtt_text = self._vtt_from_srt(srt_text)
        with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("auto2_project.json", json.dumps(project, ensure_ascii=False, indent=2))
            archive.writestr("subtitles.srt", srt_text)
            archive.writestr("subtitles.vtt", vtt_text)
            archive.writestr("manifest.json", manifest.model_dump_json(indent=2))
            video_path = self._path_from_url(manifest.video_url)
            if video_path and video_path.exists():
                archive.write(video_path, f"media/{video_path.name}")
            for scene in manifest.scenes:
                for path_value in [scene.selected_image_path, scene.tts_audio_path]:
                    path = Path(path_value) if path_value else None
                    if path and path.exists():
                        archive.write(path, f"media/{scene.scene_id}/{path.name}")
        return package_path

    def _read_manifest(self, topic_id: str) -> RenderManifest:
        path = self._manifest_path(topic_id)
        if not path.exists():
            raise ValueError("렌더링 manifest를 찾을 수 없습니다.")
        manifest = RenderManifest.model_validate_json(path.read_text(encoding="utf-8"))
        if self._normalize_legacy_visual_prompts(manifest):
            self._write_manifest(manifest)
        return manifest

    def _write_manifest(self, manifest: RenderManifest) -> None:
        path = self._manifest_path(manifest.topic_id)
        path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    def _manifest_path(self, topic_id: str) -> Path:
        return self.settings.local_storage_dir / "topics" / topic_id / "manifest.json"

    def _normalize_legacy_visual_prompts(self, manifest: RenderManifest) -> bool:
        changed = False
        for scene in manifest.scenes:
            if self.scene_planner.is_standard_visual_prompt(scene.visual_prompt):
                continue
            scene.visual_prompt = self.scene_planner.standard_visual_prompt(
                title=manifest.title,
                narration=scene.narration or scene.subtitle or scene.caption,
                visual_style=manifest.visual_style,
                media_type=scene.media_type,
            )
            changed = True
        return changed

    def _path_from_url(self, url: str) -> Path | None:
        if not url or "/files/" not in url:
            return None
        relative = unquote(url.split("/files/", 1)[1])
        return self.settings.local_storage_dir / relative

    @staticmethod
    def _editable_project(manifest: RenderManifest) -> dict:
        return {
            "format": "auto2-editable-project-v1",
            "topic_id": manifest.topic_id,
            "title": manifest.title,
            "render_id": manifest.render_id,
            "video_url": manifest.video_url,
            "layers": {
                "video_scenes": [
                    {
                        "scene_id": scene.scene_id,
                        "title": scene.title,
                        "start_seconds": scene.start_seconds,
                        "end_seconds": scene.end_time,
                        "duration_seconds": scene.duration_seconds,
                        "asset_url": scene.asset_url,
                        "asset_path": scene.selected_image_path,
                        "asset_source": scene.asset_source,
                        "image_keywords": scene.image_keywords,
                    }
                    for scene in manifest.scenes
                ],
                "narration": [
                    {
                        "scene_id": scene.scene_id,
                        "text": scene.narration,
                        "audio_path": scene.tts_audio_path,
                        "start_seconds": scene.start_seconds,
                        "end_seconds": scene.end_time,
                    }
                    for scene in manifest.scenes
                ],
                "captions": [
                    {
                        "caption_id": segment.caption_id,
                        "scene_id": segment.scene_id,
                        "text": segment.text,
                        "start_seconds": segment.start_seconds,
                        "end_seconds": segment.end_seconds,
                        "duration_seconds": segment.duration_seconds,
                        "style": {"line_count": 1, "position": "bottom_center"},
                    }
                    for scene in manifest.scenes
                    for segment in scene.caption_segments
                ],
            },
        }

    @staticmethod
    def _vtt_from_srt(srt_text: str) -> str:
        converted = re.sub(r"(?m)^\d+\s*\n", "", srt_text)
        converted = converted.replace(",", ".")
        return "WEBVTT\n\n" + converted.strip() + "\n"

    def _append_history(self, session: dict[str, str], item: dict, bump_version: bool = True) -> None:
        history = json.loads(session.get("replacement_history_json") or "[]")
        history.append({**item, "created_at": iso_now()})
        version = str(int(session.get("current_render_version") or "1") + (1 if bump_version else 0))
        session.update({"replacement_history_json": json.dumps(history, ensure_ascii=False), "current_render_version": version, "updated_at": iso_now()})
        self.repository.append_or_update_session(session)

    @staticmethod
    def _dirty_scene_ids(session: dict[str, str]) -> set[str]:
        try:
            history = json.loads(session.get("replacement_history_json") or "[]")
        except json.JSONDecodeError:
            return set()
        return {item["scene_id"] for item in history if item.get("dirty") and item.get("scene_id")}

    def _clear_dirty_history(self, session: dict[str, str]) -> None:
        try:
            history = json.loads(session.get("replacement_history_json") or "[]")
        except json.JSONDecodeError:
            history = []
        for item in history:
            if item.get("dirty"):
                item["dirty"] = False
                item["rendered_at"] = iso_now()
        session.update({"replacement_history_json": json.dumps(history, ensure_ascii=False), "updated_at": iso_now()})
        self.repository.append_or_update_session(session)
