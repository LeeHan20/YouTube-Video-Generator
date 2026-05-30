from __future__ import annotations

import shutil
import subprocess
import re
import wave
from pathlib import Path

import httpx

from app.core.config import get_settings


class NarrationService:
    def __init__(self) -> None:
        self.settings = get_settings()

    def synthesize(self, text: str, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        provider = (self.settings.narration_provider or "gemini").lower()
        if provider == "supertone" and self._supertone_tts(text, output_path):
            return output_path
        if provider == "gemini" and self.settings.gemini_api_key and self._gemini_tts(text, output_path):
            return output_path
        if self.settings.narration_allow_system_fallback and self._system_say(text, output_path):
            return output_path
        self._silent_audio(output_path, duration=max(3, min(30, len(text) // 9)))
        return output_path

    def _supertone_tts(self, text: str, output_path: Path) -> bool:
        if not self.settings.supertone_api_key or not self.settings.supertone_voice_id:
            return False
        chunks = self._chunk_for_supertone(text)
        if not chunks:
            return False
        temp_dir = output_path.parent / f".{output_path.stem}_supertone_parts"
        temp_dir.mkdir(parents=True, exist_ok=True)
        part_paths: list[Path] = []
        try:
            with httpx.Client(timeout=self.settings.supertone_timeout_seconds) as client:
                for index, chunk in enumerate(chunks, start=1):
                    part_path = temp_dir / f"{index:03d}.{self.settings.supertone_output_format}"
                    response = client.post(
                        f"https://supertoneapi.com/v1/text-to-speech/{self.settings.supertone_voice_id}",
                        headers={
                            "x-sup-api-key": self.settings.supertone_api_key,
                            "Content-Type": "application/json",
                        },
                        json={
                            "text": chunk,
                            "language": self.settings.supertone_language,
                            "model": self.settings.supertone_model,
                            "output_format": self.settings.supertone_output_format,
                            "voice_settings": {"speed": self.settings.supertone_speed},
                            "include_phonemes": False,
                            **({"style": self.settings.supertone_style} if self.settings.supertone_style else {}),
                        },
                    )
                    response.raise_for_status()
                    part_path.write_bytes(response.content)
                    part_paths.append(part_path)
            self._merge_audio_parts(part_paths, output_path)
            return True
        except Exception:
            return False
        finally:
            for part_path in part_paths:
                part_path.unlink(missing_ok=True)
            temp_dir.rmdir() if temp_dir.exists() and not any(temp_dir.iterdir()) else None

    def _gemini_tts(self, text: str, output_path: Path) -> bool:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(
                api_key=self.settings.gemini_api_key,
                http_options=types.HttpOptions(timeout=self.settings.gemini_tts_timeout_seconds * 1000),
            )
            prompt = f"[{self.settings.narration_speaking_style}] {text}"
            response = client.models.generate_content(
                model=self.settings.gemini_tts_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=self.settings.gemini_tts_voice_name,
                            )
                        )
                    ),
                ),
            )
            inline_data = response.candidates[0].content.parts[0].inline_data
            self._write_wave(output_path, inline_data.data)
            return True
        except Exception:
            return False

    @staticmethod
    def _chunk_for_supertone(text: str, max_length: int = 280) -> list[str]:
        cleaned = " ".join((text or "").split())
        if not cleaned:
            return []
        sentences = [item.strip() for item in re.split(r"(?<=[.!?。！？다요죠요])\s+", cleaned) if item.strip()]
        chunks: list[str] = []
        current = ""
        for sentence in sentences:
            if len(sentence) > max_length:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.extend([sentence[index : index + max_length] for index in range(0, len(sentence), max_length)])
                continue
            candidate = f"{current} {sentence}".strip()
            if len(candidate) <= max_length:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = sentence
        if current:
            chunks.append(current)
        return chunks

    def _merge_audio_parts(self, part_paths: list[Path], output_path: Path) -> None:
        if not part_paths:
            raise ValueError("No audio parts to merge")
        ffmpeg = shutil.which("ffmpeg")
        if len(part_paths) == 1:
            if self.settings.supertone_output_format == "wav" or not ffmpeg:
                output_path.write_bytes(part_paths[0].read_bytes())
            else:
                subprocess.run(
                    [ffmpeg, "-y", "-i", str(part_paths[0]), "-ar", "44100", "-ac", "2", str(output_path)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            return
        if not ffmpeg:
            output_path.write_bytes(part_paths[0].read_bytes())
            return
        concat_file = output_path.parent / f".{output_path.stem}_concat.txt"
        concat_file.write_text("".join(f"file '{path.resolve().as_posix()}'\n" for path in part_paths), encoding="utf-8")
        try:
            subprocess.run(
                [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-ar", "44100", "-ac", "2", str(output_path)],
                check=True,
                capture_output=True,
                text=True,
            )
        finally:
            concat_file.unlink(missing_ok=True)

    def _system_say(self, text: str, output_path: Path) -> bool:
        say = shutil.which("say")
        ffmpeg = shutil.which("ffmpeg")
        if not say or not ffmpeg:
            return False
        aiff_path = output_path.with_suffix(".aiff")
        try:
            subprocess.run([say, "-v", "Yuna", "-r", "145", "-o", str(aiff_path), text], check=True, capture_output=True, text=True)
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(aiff_path), "-ar", "44100", "-ac", "2", str(output_path)],
                check=True,
                capture_output=True,
                text=True,
            )
            aiff_path.unlink(missing_ok=True)
            return True
        except subprocess.CalledProcessError:
            return False

    @staticmethod
    def _write_wave(output_path: Path, pcm: bytes, channels: int = 1, rate: int = 24000, sample_width: int = 2) -> None:
        with wave.open(str(output_path), "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(sample_width)
            wf.setframerate(rate)
            wf.writeframes(pcm)

    @staticmethod
    def _silent_audio(output_path: Path, duration: int) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-t",
                str(duration),
                str(output_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
