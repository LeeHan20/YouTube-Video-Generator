from __future__ import annotations

from io import BytesIO
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw, ImageFont

from app.core.config import get_settings


class ImageGenerationService:
    def __init__(self) -> None:
        self.settings = get_settings()

    def generate(self, prompt: str, output_path: Path, title: str = "") -> tuple[Path, str]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.settings.gemini_api_key and self._generate_with_gemini(prompt, output_path):
            return output_path, "gemini_image"
        self._generate_local_placeholder(prompt, output_path, title)
        return output_path, "local_generated_image"

    def _generate_with_gemini(self, prompt: str, output_path: Path) -> bool:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=self.settings.gemini_api_key)
            response = client.models.generate_content(
                model=self.settings.gemini_image_model,
                contents=prompt,
                config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
            )
            for part in response.candidates[0].content.parts:
                inline_data = getattr(part, "inline_data", None)
                if inline_data and inline_data.data:
                    image = Image.open(BytesIO(inline_data.data))
                    image = image.convert("RGB")
                    image.save(output_path)
                    return True
        except Exception:
            return False
        return False

    @staticmethod
    def _generate_local_placeholder(prompt: str, output_path: Path, title: str) -> None:
        image = Image.new("RGB", (1280, 720), (238, 246, 231))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((60, 60, 1220, 660), radius=28, fill=(255, 250, 240), outline=(210, 222, 206), width=3)
        font_title = ImageGenerationService._font(56)
        font_body = ImageGenerationService._font(30)
        font_note = ImageGenerationService._font(22)
        draw.text((110, 130), title or "장면 이미지", fill=(31, 50, 40), font=font_title)
        y = 240
        for line in ImageGenerationService._wrap(prompt, 52)[:7]:
            draw.text((110, y), line, fill=(48, 63, 54), font=font_body)
            y += 44
        draw.text((110, 610), "로컬 생성 이미지 후보", fill=(90, 107, 98), font=font_note)
        image.save(output_path)

    @staticmethod
    def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        candidates = [
            "/System/Library/Fonts/AppleSDGothicNeo.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        ]
        for candidate in candidates:
            path = Path(candidate)
            if path.exists():
                return ImageFont.truetype(str(path), size)
        return ImageFont.load_default()

    @staticmethod
    def _wrap(text: str, width: int) -> list[str]:
        words = text.replace("\n", " ").split()
        lines: list[str] = []
        current = ""
        for word in words:
            if len(current) + len(word) + 1 > width:
                lines.append(current)
                current = word
            else:
                current = f"{current} {word}".strip()
        if current:
            lines.append(current)
        return lines
