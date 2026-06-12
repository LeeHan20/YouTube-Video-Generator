from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from difflib import SequenceMatcher
from pathlib import Path

from app.core.config import get_settings
from app.pipeline.models import CaptionSegment, Scene


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class CaptionAlignmentService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.last_provider = ""

    def align(self, scene: Scene, audio_path: Path, chunks: list[str]) -> list[CaptionSegment] | None:
        self.last_provider = ""
        cleaned_chunks = [" ".join((chunk or "").split()).strip() for chunk in chunks]
        cleaned_chunks = [chunk for chunk in cleaned_chunks if chunk]
        if not cleaned_chunks:
            return None
        aligned = self._align_with_aeneas(scene, audio_path, cleaned_chunks)
        if aligned:
            self.last_provider = "aeneas"
            return aligned
        aligned = self._align_with_gemini(scene, audio_path, cleaned_chunks)
        if aligned:
            self.last_provider = "gemini"
            return aligned
        aligned = self._align_with_whisper(scene, audio_path, cleaned_chunks)
        if aligned:
            self.last_provider = "whisper"
            return aligned
        return None

    def _align_with_aeneas(self, scene: Scene, audio_path: Path, chunks: list[str]) -> list[CaptionSegment] | None:
        python_path = self._aeneas_python_path()
        if not python_path or not audio_path.exists():
            return None
        with tempfile.TemporaryDirectory(prefix="auto2_aeneas_") as temp_dir:
            temp_path = Path(temp_dir)
            text_path = temp_path / "captions.txt"
            output_path = temp_path / "syncmap.json"
            text_path.write_text(self._aeneas_parsed_text(chunks), encoding="utf-8")
            base_config = (
                f"task_language={self.settings.aeneas_language}"
                "|is_text_type=parsed"
                "|os_task_file_format=json"
            )
            extra = (self.settings.aeneas_task_extra_config or "").strip()
            config = f"{base_config}|{extra}" if extra else base_config
            try:
                command = [
                    python_path,
                    "-m",
                    "aeneas.tools.execute_task",
                    str(audio_path),
                    str(text_path),
                    config,
                    str(output_path),
                ]
                runtime_config = (self.settings.aeneas_runtime_config or "").strip()
                if runtime_config:
                    command.append(f"-r={runtime_config}")
                subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=self.settings.aeneas_timeout_seconds,
                    env=self._aeneas_env(),
                )
                if not output_path.exists():
                    return None
                data = json.loads(output_path.read_text(encoding="utf-8"))
                fragments = data.get("fragments", [])
                items = self._aeneas_items_from_fragments(fragments)
                return self._segments_from_items(scene, chunks, items)
            except Exception:
                return None

    @staticmethod
    def _aeneas_parsed_text(chunks: list[str]) -> str:
        lines = []
        for index, chunk in enumerate(chunks, start=1):
            text = re.sub(r"\s+", " ", chunk).replace("|", " ").strip()
            if text:
                lines.append(f"f{index:06d}|{text}")
        return "\n".join(lines)

    @staticmethod
    def _aeneas_items_from_fragments(fragments: list[dict]) -> list[dict]:
        items = []
        for fallback_index, fragment in enumerate(fragments, start=1):
            begin = fragment.get("begin")
            end = fragment.get("end")
            if begin in {"", None} or end in {"", None}:
                continue
            identifier = str(fragment.get("id") or "")
            match = re.search(r"(\d+)$", identifier)
            index = int(match.group(1)) if match else fallback_index
            items.append(
                {
                    "index": index,
                    "start_seconds": begin,
                    "end_seconds": end,
                }
            )
        return sorted(items, key=lambda item: item["index"])

    def _align_with_gemini(self, scene: Scene, audio_path: Path, chunks: list[str]) -> list[CaptionSegment] | None:
        if not self.settings.gemini_api_key or not audio_path.exists():
            return None
        try:
            from google import genai

            client = genai.Client(api_key=self.settings.gemini_api_key)
            uploaded = client.files.upload(file=audio_path)
            prompt = (
                "You are aligning Korean subtitles to a narration audio file. "
                "Listen to the audio and return only valid JSON. "
                "Do not explain anything. "
                "Use seconds relative to the beginning of this audio. "
                "Align each caption chunk to the moment when that exact meaning is spoken. "
                "Return one item per input chunk, in the same order. "
                "Schema: {\"segments\":[{\"index\":1,\"start_seconds\":0.0,\"end_seconds\":1.2}]}.\n\n"
                f"Caption chunks:\n{json.dumps(chunks, ensure_ascii=False)}"
            )
            response = client.models.generate_content(
                model=self.settings.gemini_model,
                contents=[uploaded, prompt],
            )
            parsed = self._parse_json_object(response.text or "")
            return self._segments_from_items(scene, chunks, parsed.get("segments", []))
        except Exception:
            return None

    def _align_with_whisper(self, scene: Scene, audio_path: Path, chunks: list[str]) -> list[CaptionSegment] | None:
        whisper = shutil.which("whisper")
        if not whisper or not audio_path.exists():
            return None
        with tempfile.TemporaryDirectory(prefix="auto2_whisper_") as temp_dir:
            try:
                subprocess.run(
                    [
                        whisper,
                        str(audio_path),
                        "--language",
                        "Korean",
                        "--task",
                        "transcribe",
                        "--output_format",
                        "json",
                        "--output_dir",
                        temp_dir,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                json_path = Path(temp_dir) / f"{audio_path.stem}.json"
                if not json_path.exists():
                    return None
                data = json.loads(json_path.read_text(encoding="utf-8"))
                transcript_segments = [
                    {
                        "start": float(item.get("start", 0)),
                        "end": float(item.get("end", 0)),
                        "text": str(item.get("text", "")),
                    }
                    for item in data.get("segments", [])
                    if str(item.get("text", "")).strip()
                ]
                items = self._match_chunks_to_transcript(chunks, transcript_segments)
                return self._segments_from_items(scene, chunks, items)
            except Exception:
                return None

    def _match_chunks_to_transcript(self, chunks: list[str], transcript_segments: list[dict]) -> list[dict]:
        items: list[dict] = []
        pointer = 0
        for index, chunk in enumerate(chunks, start=1):
            best: tuple[float, int, int] | None = None
            target = self._normalize_text(chunk)
            for start_index in range(pointer, min(len(transcript_segments), pointer + 4)):
                combined = ""
                for end_index in range(start_index, min(len(transcript_segments), start_index + 4)):
                    combined = f"{combined} {transcript_segments[end_index]['text']}".strip()
                    score = SequenceMatcher(None, target, self._normalize_text(combined)).ratio()
                    if best is None or score > best[0]:
                        best = (score, start_index, end_index)
            if best is None:
                continue
            _score, start_index, end_index = best
            pointer = end_index + 1
            items.append(
                {
                    "index": index,
                    "start_seconds": transcript_segments[start_index]["start"],
                    "end_seconds": transcript_segments[end_index]["end"],
                }
            )
        return items

    def _segments_from_items(self, scene: Scene, chunks: list[str], items: list[dict]) -> list[CaptionSegment] | None:
        if len(items) != len(chunks):
            return None
        scene_start = scene.start_seconds
        scene_end = scene.start_seconds + scene.duration_seconds
        segments: list[CaptionSegment] = []
        previous_end = scene_start
        for expected_index, (chunk, item) in enumerate(zip(chunks, items, strict=True), start=1):
            if int(item.get("index", expected_index)) != expected_index:
                return None
            local_start = self._float_or_none(item.get("start_seconds"))
            local_end = self._float_or_none(item.get("end_seconds"))
            if local_start is None or local_end is None:
                return None
            start = max(scene_start, min(scene_end, scene_start + local_start))
            end = max(start + 0.05, min(scene_end, scene_start + local_end))
            if start < previous_end:
                start = previous_end
            if end <= start:
                return None
            segments.append(
                CaptionSegment(
                    caption_id=f"{scene.scene_id}_cap_{expected_index:03d}",
                    scene_id=scene.scene_id,
                    text=chunk,
                    start_seconds=start,
                    end_seconds=end,
                    duration_seconds=end - start,
                )
            )
            previous_end = end
        return segments

    @staticmethod
    def _parse_json_object(text: str) -> dict:
        cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.I | re.M).strip()
        try:
            value = json.loads(cleaned)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, flags=re.S)
            if not match:
                return {}
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else {}

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"[^0-9A-Za-z가-힣]+", "", text or "").lower()

    @staticmethod
    def _float_or_none(value) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _aeneas_python_path(self) -> str:
        configured = self._resolve_project_path(self.settings.aeneas_python_path or "")
        if configured and configured.exists():
            return str(configured)
        detected = shutil.which("python")
        if detected:
            try:
                subprocess.run(
                    [detected, "-m", "aeneas.tools.execute_task", "--help"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    env=self._aeneas_env(),
                )
                return detected
            except Exception:
                return ""
        return ""

    def _aeneas_env(self) -> dict[str, str]:
        env = os.environ.copy()
        repo_path = self._resolve_project_path(self.settings.aeneas_repo_path or "")
        if repo_path and repo_path.exists():
            existing = env.get("PYTHONPATH", "")
            paths = [str(repo_path)]
            if existing:
                paths.append(existing)
            env["PYTHONPATH"] = os.pathsep.join(paths)
        return env

    @staticmethod
    def _resolve_project_path(value: str) -> Path | None:
        cleaned = (value or "").strip()
        if not cleaned:
            return None
        path = Path(cleaned).expanduser()
        if path.is_absolute():
            return path
        return PROJECT_ROOT / path
