from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from app.pipeline.models import RenderManifest


FULL_SCRIPT_MARKERS = [
    "##",
    "**",
    "[인트로",
    "[챕터",
    "[아웃트로",
    "유튜브 영상 나레이션 대본",
    "전체 대본",
    "---",
]


def probe_duration(path: Path) -> float | None:
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


def probe_streams(path: Path) -> list[dict]:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-print_format", "json", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout).get("streams", [])
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return []


def count_srt_segments(path: Path) -> int:
    if not path.exists():
        return 0
    text = path.read_text(encoding="utf-8")
    return len(re.findall(r"(?m)^\d+\s*$", text))


def validate_render(manifest: RenderManifest, video_path: Path, subtitle_path: Path) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    scene_count = len(manifest.scenes)
    metrics: dict[str, float | int] = {"scene_count": scene_count}

    if not video_path.exists():
        errors.append(f"최종 mp4가 없습니다: {video_path}")
    video_duration = probe_duration(video_path) if video_path.exists() else None
    if video_duration is None:
        errors.append("최종 mp4 duration을 확인할 수 없습니다.")
    else:
        metrics["video_duration"] = round(video_duration, 3)

    streams = probe_streams(video_path) if video_path.exists() else []
    audio_stream_count = sum(1 for stream in streams if stream.get("codec_type") == "audio")
    video_stream_count = sum(1 for stream in streams if stream.get("codec_type") == "video")
    metrics["audio_stream_count"] = audio_stream_count
    metrics["video_stream_count"] = video_stream_count
    if video_stream_count != 1:
        errors.append(f"최종 mp4의 비디오 스트림 수가 1이 아닙니다: {video_stream_count}")
    if audio_stream_count != 1:
        errors.append(f"최종 mp4의 오디오 스트림 수가 1이 아닙니다: {audio_stream_count}")

    expected_caption_count = sum(len(scene.caption_segments) for scene in manifest.scenes) or scene_count
    subtitle_segment_count = count_srt_segments(subtitle_path)
    metrics["subtitle_segment_count"] = subtitle_segment_count
    metrics["expected_caption_count"] = expected_caption_count
    if subtitle_segment_count != expected_caption_count:
        errors.append(f"자막 segment 수({subtitle_segment_count})와 예상 자막 수({expected_caption_count})가 다릅니다.")

    missing_audio = [scene.scene_id for scene in manifest.scenes if not scene.tts_audio_path or not Path(scene.tts_audio_path).exists()]
    if missing_audio:
        errors.append(f"TTS 파일이 없는 scene이 있습니다: {', '.join(missing_audio)}")

    missing_image = [scene.scene_id for scene in manifest.scenes if not scene.asset_url]
    if missing_image:
        errors.append(f"이미지/영상 asset이 없는 scene이 있습니다: {', '.join(missing_image)}")

    bad_subtitles = []
    for scene in manifest.scenes:
        texts = [segment.text for segment in scene.caption_segments] or [scene.caption or scene.subtitle or scene.narration]
        for text in texts:
            if "\n" in text or len(text) > 32 or any(marker in text for marker in FULL_SCRIPT_MARKERS):
                bad_subtitles.append(scene.scene_id)
                break
    if bad_subtitles:
        errors.append(f"전체 대본 또는 너무 긴 문장이 자막에 들어간 scene이 있습니다: {', '.join(bad_subtitles)}")

    used_assets: dict[str, list[str]] = {}
    for scene in manifest.scenes:
        key = scene.image_hash or scene.asset_url
        if key:
            used_assets.setdefault(key, []).append(scene.scene_id)
    repeated = {key: ids for key, ids in used_assets.items() if len(ids) > 1}
    metrics["repeated_image_groups"] = len(repeated)
    if repeated and scene_count >= 3:
        warnings.append("동일 이미지가 여러 scene에 반복되었습니다.")
        if len(repeated) / max(1, scene_count) > 0.25:
            errors.append("동일 이미지 반복 비율이 높습니다.")

    total_scene_duration = sum(scene.duration_seconds for scene in manifest.scenes)
    total_audio_duration = sum(scene.audio_duration_seconds or scene.duration_seconds for scene in manifest.scenes)
    metrics["total_scene_duration"] = round(total_scene_duration, 3)
    metrics["total_audio_duration"] = round(total_audio_duration, 3)
    if video_duration is not None and abs(video_duration - total_audio_duration) > max(2.0, scene_count * 0.35):
        errors.append(
            f"영상 길이({video_duration:.2f}s)와 scene audio 총합({total_audio_duration:.2f}s)의 차이가 큽니다."
        )

    return {"ok": not errors, "errors": errors, "warnings": warnings, "metrics": metrics}
