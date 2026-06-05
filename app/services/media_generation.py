from __future__ import annotations

import json
import hashlib
import re
import shlex
import subprocess
import textwrap
from datetime import datetime
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote, urlparse

from PIL import Image, ImageDraw, ImageFont

from app.core.config import get_settings
from app.pipeline.models import CaptionSegment, RenderManifest, Scene
from app.services.media_sources import MediaSourceService
from app.services.narration import NarrationService
from app.services.image_generation import ImageGenerationService
from app.services.render_validation import validate_render


class PlaceholderMediaGenerator:
    def __init__(self) -> None:
        settings = get_settings()
        self.root = settings.local_storage_dir
        self.public_base_url = settings.public_base_url.rstrip("/")
        self.root.mkdir(parents=True, exist_ok=True)
        self.sources = MediaSourceService()
        self.narration = NarrationService()
        self.image_generation = ImageGenerationService()

    def generate_scene_assets(
        self,
        topic_id: str,
        scenes: list[Scene],
        source_mode: str = "auto",
        progress: Callable[[str], None] | None = None,
    ) -> list[Scene]:
        used_urls = {scene.asset_url for scene in scenes if scene.asset_url}
        used_hashes = {scene.image_hash for scene in scenes if scene.image_hash}
        for index, scene in enumerate(scenes, start=1):
            if progress:
                progress(f"[assets] {index}/{len(scenes)} {scene.scene_id} 에셋 수집 시작 ({scene.media_type})")
            original_prompt = scene.visual_prompt
            asset = self.sources.create_asset(
                topic_id,
                scene,
                source_mode=source_mode,
                excluded_asset_urls=used_urls,
                excluded_hashes=used_hashes,
            )
            scene.asset_url = asset.asset_url
            scene.selected_image_url = asset.asset_url
            scene.selected_image_path = str(asset.local_path or "")
            scene.image_hash = asset.image_hash or (self._file_hash(asset.local_path) if asset.local_path else "")
            scene.asset_source = asset.source
            scene.asset_credit = asset.credit
            scene.asset_license = asset.license
            scene.visual_prompt = original_prompt
            if scene.asset_url:
                used_urls.add(scene.asset_url)
            if scene.image_hash:
                used_hashes.add(scene.image_hash)
            if progress:
                progress(f"[assets] {index}/{len(scenes)} {scene.scene_id} 완료: {scene.asset_source} / {scene.media_type}")
        return scenes

    def write_manifest(self, manifest: RenderManifest) -> Path:
        path = self.root / "topics" / manifest.topic_id / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        return path

    def write_subtitles(self, topic_id: str, scenes: list[Scene]) -> tuple[Path, str]:
        path = self.root / "topics" / topic_id / "subtitles.srt"
        path.write_text(self._srt(scenes), encoding="utf-8")
        return path, self._url_for(path)

    def render_video(
        self,
        manifest: RenderManifest,
        dirty_scene_ids: set[str] | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> tuple[Path, str]:
        topic_dir = self.root / "topics" / manifest.topic_id
        topic_dir.mkdir(parents=True, exist_ok=True)
        render_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        manifest.render_id = render_id
        log_path = topic_dir / "render_logs" / f"{render_id}.jsonl"
        clean_video_path = topic_dir / "rendered_no_subtitles.mp4"
        video_path = topic_dir / "rendered.mp4"
        try:
            self._log_render(
                log_path,
                {
                    "event": "render_start",
                    "video_id": manifest.topic_id,
                    "render_id": render_id,
                    "scene_count": len(manifest.scenes),
                },
            )
            segment_paths = []
            cursor = 0.0
            for index, scene in enumerate(manifest.scenes, start=1):
                scene.start_seconds = cursor
                scene.start_time = cursor
                audio_path = topic_dir / "audio" / f"{scene.scene_id}.wav"
                segment_path = topic_dir / "segments" / f"{index:03d}_{scene.scene_id}.mp4"
                should_render = dirty_scene_ids is None or scene.scene_id in dirty_scene_ids or not segment_path.exists()
                if should_render or not audio_path.exists():
                    if progress:
                        progress(f"[render] {index}/{len(manifest.scenes)} {scene.scene_id} 나레이션 생성")
                    if audio_path.exists():
                        audio_path.unlink()
                    self.narration.synthesize(self._narration_for_tts(scene), audio_path)
                audio_duration = self._validate_audio(scene, audio_path)
                existing_segment_duration = self._probe_duration(segment_path) if not should_render else None
                duration = max(0.8, existing_segment_duration or audio_duration)
                scene.duration_seconds = duration
                scene.audio_duration_seconds = duration if existing_segment_duration else audio_duration
                scene.end_time = cursor + duration
                scene.tts_audio_path = str(audio_path)
                scene.caption_segments = self._caption_segments_for_scene(scene)
                if should_render:
                    if progress:
                        progress(f"[render] {index}/{len(manifest.scenes)} {scene.scene_id} 장면 렌더링")
                    command = self._render_scene_segment(scene, audio_path, segment_path, duration)
                    segment_duration = self._probe_duration(segment_path)
                    if segment_duration and abs(segment_duration - duration) > 0.4:
                        raise RuntimeError(
                            f"{scene.scene_id} segment 길이({segment_duration:.2f}s)가 TTS 길이({duration:.2f}s)와 맞지 않습니다."
                        )
                    self._log_render(log_path, {"event": "ffmpeg_scene", "scene_id": scene.scene_id, "command": command})
                elif progress:
                    progress(f"[render] {index}/{len(manifest.scenes)} {scene.scene_id} 기존 segment 재사용")
                self._log_render(
                    log_path,
                    {
                        "event": "scene_ready",
                        "scene_id": scene.scene_id,
                        "narration": scene.narration,
                        "subtitle": self._subtitle_text(scene),
                        "caption_segments": [segment.model_dump() for segment in scene.caption_segments],
                        "selected_image": scene.selected_image_path or scene.asset_url,
                        "tts_audio_path": scene.tts_audio_path,
                        "audio_duration": audio_duration,
                        "start_time": scene.start_time,
                        "end_time": scene.end_time,
                    },
                )
                segment_paths.append(segment_path)
                cursor += duration
            if progress:
                progress("[render] 자막 파일 생성")
            subtitle_path, subtitle_url = self.write_subtitles(manifest.topic_id, manifest.scenes)
            manifest.subtitle_url = subtitle_url
            if progress:
                progress("[render] 장면 segment 합치기")
            concat_command = self._concat_segments(segment_paths, clean_video_path)
            self._log_render(log_path, {"event": "ffmpeg_concat", "command": concat_command})
            if progress:
                progress("[render] 자막 트랙 mux")
            mux_command = self._mux_subtitles(clean_video_path, subtitle_path, video_path)
            self._log_render(log_path, {"event": "ffmpeg_mux", "command": mux_command})
            validation = validate_render(manifest, video_path, subtitle_path)
            manifest.validation = validation
            self._log_render(log_path, {"event": "validation", "result": validation, "final_output_path": str(video_path)})
            if not validation["ok"]:
                raise RuntimeError("렌더링 검증 실패: " + " / ".join(validation["errors"]))
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            self._log_render(log_path, {"event": "render_failed", "error": str(exc)})
            raise RuntimeError(f"렌더링 실패: {exc}") from exc
        return video_path, self._url_for(video_path)

    def _render_scene_segment(self, scene: Scene, audio_path: Path, output_path: Path, duration: float) -> list[str]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not scene.asset_url:
            raise RuntimeError(
                f"{scene.scene_id}에 렌더링 가능한 이미지/영상이 없습니다. "
                "검수 화면에서 후보를 다시 크롤링하거나 직접 업로드 또는 AI 이미지 생성을 선택해 주세요."
            )
        asset_path = self._path_from_url(scene.asset_url)
        overlay_paths = self._write_caption_overlays(scene, output_path.parent.parent / "subtitles")
        overlay_inputs: list[str] = []
        for overlay_path in overlay_paths:
            overlay_inputs.extend(["-loop", "1", "-i", str(overlay_path)])
        filter_complex = self._caption_overlay_filter(scene, overlay_count=len(overlay_paths))
        if not self._is_video(asset_path) and not self._is_image(asset_path):
            raise RuntimeError(
                f"{scene.scene_id}에 렌더링 가능한 이미지/영상이 없습니다. "
                "검수 화면에서 후보를 다시 크롤링하거나 직접 업로드 또는 AI 이미지 생성을 선택해 주세요."
            )
        if self._is_video(asset_path):
            command = [
                "ffmpeg",
                "-y",
                "-stream_loop",
                "-1",
                "-i",
                str(asset_path),
                "-i",
                str(audio_path),
                *overlay_inputs,
                "-filter_complex",
                filter_complex,
                "-map",
                "[v]",
                "-map",
                "1:a:0",
                "-t",
                f"{duration:.3f}",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-shortest",
                "-pix_fmt",
                "yuv420p",
                "-map_metadata",
                "-1",
                str(output_path),
            ]
        else:
            command = [
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-i",
                str(asset_path),
                "-i",
                str(audio_path),
                *overlay_inputs,
                "-filter_complex",
                filter_complex,
                "-map",
                "[v]",
                "-map",
                "1:a:0",
                "-t",
                f"{duration:.3f}",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-shortest",
                "-pix_fmt",
                "yuv420p",
                "-map_metadata",
                "-1",
                str(output_path),
            ]
        subprocess.run(command, check=True, capture_output=True, text=True)
        return command

    def _write_caption_overlays(self, scene: Scene, output_dir: Path) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        segments = scene.caption_segments or self._caption_segments_for_scene(scene)
        for segment in segments:
            path = output_dir / f"{segment.caption_id}.png"
            self._write_subtitle_overlay_text(segment.text, path)
            paths.append(path)
        return paths

    def _caption_overlay_filter(self, scene: Scene, overlay_count: int) -> str:
        filters = [f"[0:v]{self._video_filter(scene)}[v0]"]
        segments = scene.caption_segments or self._caption_segments_for_scene(scene)
        for index, segment in enumerate(segments[:overlay_count], start=1):
            input_index = index + 1
            previous = f"v{index - 1}"
            current = f"v{index}"
            start = max(0.0, segment.start_seconds - scene.start_seconds)
            end = max(start + 0.05, segment.end_seconds - scene.start_seconds)
            filters.append(
                f"[{previous}][{input_index}:v]overlay=0:0:format=auto:enable='between(t,{start:.3f},{end:.3f})'[{current}]"
            )
        if overlay_count:
            filters.append(f"[v{overlay_count}]format=yuv420p[v]")
        else:
            filters.append("[v0]format=yuv420p[v]")
        return ";".join(filters)

    def _burn_subtitles(self, video_path: Path, subtitle_path: Path, output_path: Path) -> list[str]:
        escaped = str(subtitle_path.resolve()).replace("\\", "\\\\").replace(":", "\\:")
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            (
                f"subtitles='{escaped}':force_style="
                "'FontName=AppleGothic,FontSize=26,PrimaryColour=&H00000000,"
                "OutlineColour=&H0000DCFF,BorderStyle=3,BackColour=&HC8C2F8FF,"
                "Outline=2,Shadow=0,Alignment=2,MarginV=48'"
            ),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-c:v",
            "libx264",
            "-c:a",
            "copy",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
            return command
        except subprocess.CalledProcessError:
            output_path.write_bytes(video_path.read_bytes())
            return ["copy_without_burned_subtitles", str(video_path), str(output_path)]

    def _concat_segments(self, segment_paths: list[Path], output_path: Path) -> list[str]:
        concat_file = output_path.parent / "concat.txt"
        concat_file.write_text("".join(f"file {shlex.quote(str(path.resolve()))}\n" for path in segment_paths), encoding="utf-8")
        command = [
            "ffmpeg",
            "-y",
            "-fflags",
            "+genpts",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-avoid_negative_ts",
            "make_zero",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        return command

    def _mux_subtitles(self, video_path: Path, subtitle_path: Path, output_path: Path) -> list[str]:
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(subtitle_path),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-map",
            "1:0",
            "-c:v",
            "copy",
            "-c:a",
            "copy",
            "-c:s",
            "mov_text",
            "-metadata:s:s:0",
            "language=kor",
            str(output_path),
        ]
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        return command

    def _path_from_url(self, url: str) -> Path:
        parsed = urlparse(url)
        marker = "/files/"
        if marker not in parsed.path:
            raise ValueError(f"Unsupported asset URL: {url}")
        relative = unquote(parsed.path.split(marker, 1)[1])
        return self.root / relative

    def _probe_duration(self, path: Path) -> float | None:
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                check=True,
                capture_output=True,
                text=True,
            )
            return float(result.stdout.strip())
        except (subprocess.CalledProcessError, ValueError):
            return None

    def _validate_audio(self, scene: Scene, audio_path: Path) -> float:
        if not audio_path.exists():
            raise RuntimeError(f"{scene.scene_id} TTS 파일이 생성되지 않았습니다: {audio_path}")
        if audio_path.stat().st_size < 1024:
            raise RuntimeError(f"{scene.scene_id} TTS 파일이 비정상적으로 작습니다: {audio_path}")
        duration = self._probe_duration(audio_path)
        if duration is None or duration < 0.5:
            raise RuntimeError(f"{scene.scene_id} TTS duration이 비정상입니다: {duration}")
        if duration > 180:
            raise RuntimeError(f"{scene.scene_id} TTS duration이 너무 깁니다: {duration:.2f}s")
        return duration

    @staticmethod
    def _is_video(path: Path) -> bool:
        return path.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv", ".ogv"}

    @staticmethod
    def _is_image(path: Path) -> bool:
        return path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

    def _video_filter(self, scene: Scene) -> str:
        del scene
        return "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black"

    def _write_subtitle_overlay(self, scene: Scene, output_path: Path) -> None:
        self._write_subtitle_overlay_text(self._subtitle_text(scene), output_path)

    def _write_subtitle_overlay_text(self, text: str, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGBA", (1280, 720), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        cleaned = self._clean_subtitle_text(text).replace("\n", " ")
        font_size = 54
        font = self._subtitle_font(font_size)
        while font_size > 30:
            try:
                text_width = font.getlength(cleaned)
            except AttributeError:
                text_width = len(cleaned) * font_size
            if text_width <= 1080:
                break
            font_size -= 4
            font = self._subtitle_font(font_size)
        text = cleaned
        if not text:
            image.save(output_path)
            return
        bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=12, stroke_width=4)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = max(64, (1280 - text_width) // 2)
        y = 720 - text_height - 74
        padding_x = 28
        padding_y = 18
        draw.rounded_rectangle(
            (x - padding_x, y - padding_y, x + text_width + padding_x, y + text_height + padding_y),
            radius=22,
            fill=(255, 248, 194, 210),
        )
        draw.multiline_text(
            (x, y),
            text,
            font=font,
            fill=(0, 0, 0, 255),
            spacing=12,
            align="center",
            stroke_width=4,
            stroke_fill=(255, 220, 64, 255),
        )
        image.save(output_path)

    @staticmethod
    def _subtitle_font(size: int) -> ImageFont.ImageFont:
        for candidate in [
            "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
            "/Library/Fonts/Arial Unicode.ttf",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        ]:
            if Path(candidate).exists():
                return ImageFont.truetype(candidate, size=size)
        return ImageFont.load_default()

    @staticmethod
    def _wrap_subtitle(text: str, font: ImageFont.ImageFont, max_width: int) -> str:
        cleaned = PlaceholderMediaGenerator._clean_subtitle_text(text)
        if not cleaned:
            return ""
        lines: list[str] = []
        current = ""
        for token in cleaned.split():
            candidate = f"{current} {token}".strip()
            try:
                width = font.getlength(candidate)
            except AttributeError:
                width = len(candidate) * 30
            if width <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
            if len(token) > 18:
                lines.extend(textwrap.wrap(token, width=18))
                current = ""
            else:
                current = token
        if current:
            lines.append(current)
        return "\n".join(lines[:2])

    @staticmethod
    def _narration_for_tts(scene: Scene) -> str:
        return " ".join((scene.narration or scene.caption or scene.subtitle or "").split()).strip()

    @staticmethod
    def _subtitle_text(scene: Scene) -> str:
        return PlaceholderMediaGenerator._clean_subtitle_text(scene.caption or scene.subtitle or scene.narration)

    @staticmethod
    def _clean_subtitle_text(text: str) -> str:
        cleaned = " ".join((text or "").split()).strip()
        cleaned = re.sub(r"^#{1,6}\s*", "", cleaned)
        cleaned = cleaned.replace("**", "")
        cleaned = re.sub(r"\[[^\]]*(인트로|챕터|아웃트로|BGM|화면|자막|장면)[^\]]*\]", "", cleaned, flags=re.I)
        cleaned = re.sub(r"^(제목|요약|전체 대본|대본 상세)\s*[:：.]?\s*", "", cleaned)
        if len(cleaned) > 30:
            shortened = textwrap.shorten(cleaned, width=30, placeholder="...")
            return shortened
        return cleaned

    def _caption_segments_for_scene(self, scene: Scene) -> list[CaptionSegment]:
        chunks = self._caption_chunks(scene.narration or scene.caption or scene.subtitle)
        if not chunks:
            chunks = [self._subtitle_text(scene)]
        total_weight = sum(max(1, len(chunk)) for chunk in chunks)
        cursor = scene.start_seconds
        segments: list[CaptionSegment] = []
        remaining_duration = scene.duration_seconds
        for index, chunk in enumerate(chunks, start=1):
            if index == len(chunks):
                duration = max(0.2, scene.start_seconds + scene.duration_seconds - cursor)
            else:
                duration = max(0.8, scene.duration_seconds * (max(1, len(chunk)) / total_weight))
                duration = min(duration, max(0.2, remaining_duration - 0.4 * (len(chunks) - index)))
            end = min(scene.start_seconds + scene.duration_seconds, cursor + duration)
            segments.append(
                CaptionSegment(
                    caption_id=f"{scene.scene_id}_cap_{index:03d}",
                    scene_id=scene.scene_id,
                    text=chunk,
                    start_seconds=cursor,
                    end_seconds=end,
                    duration_seconds=end - cursor,
                )
            )
            remaining_duration -= end - cursor
            cursor = end
        return segments

    @staticmethod
    def _caption_chunks(text: str, max_chars: int = 30) -> list[str]:
        cleaned = " ".join((text or "").split())
        cleaned = re.sub(r"^#{1,6}\s*", "", cleaned).replace("**", "")
        cleaned = re.sub(r"\[[^\]]+\]", "", cleaned)
        if not cleaned:
            return []
        parts = [part.strip() for part in re.split(r"(?<=[.!?。！？])\s+|(?<=다\.)\s+|(?<=요\.)\s+|[,，]\s*", cleaned) if part.strip()]
        chunks: list[str] = []
        for part in parts:
            if len(part) <= max_chars:
                chunks.append(part)
                continue
            chunks.extend(textwrap.wrap(part, width=max_chars, break_long_words=False, replace_whitespace=False))
        merged: list[str] = []
        for chunk in [item.strip() for item in chunks if item.strip()]:
            if merged and len(chunk) <= 5 and len(f"{merged[-1]} {chunk}") <= max_chars:
                merged[-1] = f"{merged[-1]} {chunk}"
            else:
                merged.append(chunk)
        return merged

    @staticmethod
    def _file_hash(path: Path | None) -> str:
        if not path:
            return ""
        try:
            digest = hashlib.sha1()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        except Exception:
            return ""

    @staticmethod
    def _log_render(log_path: Path, payload: dict) -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        event = {"created_at": datetime.now().isoformat(timespec="seconds"), **payload}
        log_path.open("a", encoding="utf-8").write(json.dumps(event, ensure_ascii=False) + "\n")

    def _url_for(self, path: Path) -> str:
        relative = path.relative_to(self.root).as_posix()
        return f"{self.public_base_url}/files/{relative}"

    @staticmethod
    def _srt(scenes: list[Scene]) -> str:
        blocks = []
        segments = [segment for scene in scenes for segment in scene.caption_segments]
        if not segments:
            for scene in scenes:
                segments.extend(PlaceholderMediaGenerator()._caption_segments_for_scene(scene))
        for index, segment in enumerate(segments, start=1):
            start = PlaceholderMediaGenerator._timestamp(segment.start_seconds)
            end = PlaceholderMediaGenerator._timestamp(segment.end_seconds)
            blocks.append(f"{index}\n{start} --> {end}\n{segment.text}\n")
        return "\n".join(blocks)

    @staticmethod
    def _timestamp(seconds: float) -> str:
        total = int(seconds)
        millis = int((seconds - total) * 1000)
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    @staticmethod
    def _escape_drawtext(value: str) -> str:
        return value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
