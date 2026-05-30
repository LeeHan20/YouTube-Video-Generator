from __future__ import annotations

import json
import shlex
import subprocess
import textwrap
from pathlib import Path
from urllib.parse import unquote, urlparse

from PIL import Image, ImageDraw, ImageFont

from app.core.config import get_settings
from app.pipeline.models import RenderManifest, Scene
from app.services.media_sources import MediaSourceService
from app.services.narration import NarrationService
from app.services.image_generation import ImageGenerationService


class PlaceholderMediaGenerator:
    def __init__(self) -> None:
        settings = get_settings()
        self.root = settings.local_storage_dir
        self.public_base_url = settings.public_base_url.rstrip("/")
        self.root.mkdir(parents=True, exist_ok=True)
        self.sources = MediaSourceService()
        self.narration = NarrationService()
        self.image_generation = ImageGenerationService()

    def generate_scene_assets(self, topic_id: str, scenes: list[Scene], source_mode: str = "auto") -> list[Scene]:
        for scene in scenes:
            original_prompt = scene.visual_prompt
            asset = self.sources.create_asset(topic_id, scene, source_mode=source_mode)
            scene.asset_url = asset.asset_url
            scene.asset_source = asset.source
            scene.asset_credit = asset.credit
            scene.asset_license = asset.license
            scene.visual_prompt = original_prompt if asset.source.startswith("crawl_") else asset.prompt
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

    def render_video(self, manifest: RenderManifest, dirty_scene_ids: set[str] | None = None) -> tuple[Path, str]:
        topic_dir = self.root / "topics" / manifest.topic_id
        topic_dir.mkdir(parents=True, exist_ok=True)
        clean_video_path = topic_dir / "rendered_no_subtitles.mp4"
        video_path = topic_dir / "rendered.mp4"
        try:
            segment_paths = []
            cursor = 0.0
            for index, scene in enumerate(manifest.scenes, start=1):
                scene.start_seconds = cursor
                audio_path = topic_dir / "audio" / f"{scene.scene_id}.wav"
                segment_path = topic_dir / "segments" / f"{index:03d}_{scene.scene_id}.mp4"
                should_render = dirty_scene_ids is None or scene.scene_id in dirty_scene_ids or not segment_path.exists()
                if should_render or not audio_path.exists():
                    self.narration.synthesize(scene.narration, audio_path)
                existing_segment_duration = self._probe_duration(segment_path) if not should_render else None
                duration = max(scene.duration_seconds, existing_segment_duration or self._probe_duration(audio_path) or scene.duration_seconds)
                scene.duration_seconds = duration
                if should_render:
                    self._render_scene_segment(scene, audio_path, segment_path, duration)
                segment_paths.append(segment_path)
                cursor += duration
            subtitle_path, subtitle_url = self.write_subtitles(manifest.topic_id, manifest.scenes)
            manifest.subtitle_url = subtitle_url
            self._concat_segments(segment_paths, clean_video_path)
            self._mux_subtitles(clean_video_path, subtitle_path, video_path)
        except (FileNotFoundError, subprocess.CalledProcessError):
            fallback = topic_dir / "rendered-placeholder.json"
            fallback.write_text(json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
            return fallback, self._url_for(fallback)
        return video_path, self._url_for(video_path)

    def _render_scene_segment(self, scene: Scene, audio_path: Path, output_path: Path, duration: float) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        asset_path = self._path_from_url(scene.asset_url)
        subtitle_overlay_path = output_path.parent.parent / "subtitles" / f"{scene.scene_id}.png"
        self._write_subtitle_overlay(scene, subtitle_overlay_path)
        if not self._is_video(asset_path) and not self._is_image(asset_path):
            generated_path = output_path.parent.parent / "assets" / f"{scene.scene_id}_render_fallback.png"
            self.image_generation.generate(scene.visual_prompt or scene.subtitle, generated_path, title=scene.title)
            asset_path = generated_path
            scene.asset_url = self._url_for(generated_path)
            scene.asset_source = "render_fallback_image"
            scene.asset_credit = "Local generated render image"
            scene.asset_license = "generated"
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
                "-loop",
                "1",
                "-i",
                str(subtitle_overlay_path),
                "-t",
                f"{duration:.3f}",
                "-filter_complex",
                f"[0:v]{self._video_filter(scene)}[base];[base][2:v]overlay=0:0:format=auto[v]",
                "-map",
                "[v]",
                "-map",
                "1:a:0",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-shortest",
                "-pix_fmt",
                "yuv420p",
                str(output_path),
            ]
        else:
            command = [
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-t",
                f"{duration:.3f}",
                "-i",
                str(asset_path),
                "-i",
                str(audio_path),
                "-loop",
                "1",
                "-i",
                str(subtitle_overlay_path),
                "-filter_complex",
                f"[0:v]{self._video_filter(scene)}[base];[base][2:v]overlay=0:0:format=auto[v]",
                "-map",
                "[v]",
                "-map",
                "1:a:0",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-shortest",
                "-pix_fmt",
                "yuv420p",
                str(output_path),
            ]
        subprocess.run(command, check=True, capture_output=True, text=True)

    def _concat_segments(self, segment_paths: list[Path], output_path: Path) -> None:
        concat_file = output_path.parent / "concat.txt"
        concat_file.write_text("".join(f"file {shlex.quote(str(path.resolve()))}\n" for path in segment_paths), encoding="utf-8")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(output_path)],
            check=True,
            capture_output=True,
            text=True,
        )

    def _mux_subtitles(self, video_path: Path, subtitle_path: Path, output_path: Path) -> None:
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(video_path),
                    "-i",
                    str(subtitle_path),
                    "-map",
                    "0",
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
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            output_path.write_bytes(video_path.read_bytes())

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

    @staticmethod
    def _is_video(path: Path) -> bool:
        return path.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv"}

    @staticmethod
    def _is_image(path: Path) -> bool:
        return path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

    def _video_filter(self, scene: Scene) -> str:
        del scene
        return "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black"

    def _write_subtitle_overlay(self, scene: Scene, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGBA", (1280, 720), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        font = self._subtitle_font(54)
        text = self._wrap_subtitle(scene.subtitle or scene.narration, font, 1040)
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
        cleaned = " ".join((text or "").split())
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
        return "\n".join(lines[:3])

    def _url_for(self, path: Path) -> str:
        relative = path.relative_to(self.root).as_posix()
        return f"{self.public_base_url}/files/{relative}"

    @staticmethod
    def _srt(scenes: list[Scene]) -> str:
        blocks = []
        for index, scene in enumerate(scenes, start=1):
            start = PlaceholderMediaGenerator._timestamp(scene.start_seconds)
            end = PlaceholderMediaGenerator._timestamp(scene.start_seconds + scene.duration_seconds)
            blocks.append(f"{index}\n{start} --> {end}\n{scene.subtitle}\n")
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
