from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

from app.core.config import get_settings


class NarrationService:
    def __init__(self) -> None:
        self.settings = get_settings()

    def synthesize(self, text: str, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        provider = (self.settings.narration_provider or "gemini").lower()
        if provider == "gemini" and self.settings.gemini_api_key and self._gemini_tts(text, output_path):
            return output_path
        if self.settings.narration_allow_system_fallback and self._system_say(text, output_path):
            return output_path
        self._silent_audio(output_path, duration=max(3, min(30, len(text) // 9)))
        return output_path

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
