from __future__ import annotations

import re

from app.pipeline.models import Scene
from app.services.ai_client import get_ai_client


class ScenePlanner:
    def plan(self, title: str, script: str, visual_style: str, length_minutes: int) -> list[Scene]:
        target_scene_count = max(4, min(12, length_minutes * 2))
        chunks = self._script_chunks(script, target_scene_count)
        scenes = []
        duration = max(8, int(length_minutes * 60 / max(1, len(chunks))))
        for idx, chunk in enumerate(chunks, start=1):
            prompt = self._visual_prompt(title, chunk, visual_style)
            scenes.append(
                Scene(
                    scene_id=f"scene_{idx:03d}",
                    title=f"장면 {idx}",
                    narration=chunk,
                    subtitle=self._subtitle(chunk),
                    visual_prompt=prompt,
                    start_seconds=(idx - 1) * duration,
                    duration_seconds=duration,
                )
            )
        return scenes

    def improve_prompt(self, title: str, scene: Scene, user_instruction: str) -> str:
        prompt = (
            "아래 영상 장면을 다시 생성하기 위한 이미지/영상 프롬프트를 한국어로 개선해줘.\n"
            "50대 이상 시청자에게 편안하고 과장되지 않아야 해.\n\n"
            f"영상 제목: {title}\n"
            f"기존 장면 설명: {scene.visual_prompt}\n"
            f"사용자 요청: {user_instruction}\n"
        )
        return get_ai_client().generate_text(prompt, max_tokens=700).text.strip()

    @staticmethod
    def _script_chunks(script: str, target_count: int) -> list[str]:
        text = re.sub(r"\s+", " ", script).strip()
        sentences = re.split(r"(?<=[.!?。])\s+|(?<=다\.)\s+|(?<=요\.)\s+", text)
        sentences = [sentence.strip() for sentence in sentences if sentence.strip()]
        if not sentences:
            return ["대본을 확인하는 기본 장면입니다."]
        chunk_size = max(1, len(sentences) // target_count)
        chunks = [" ".join(sentences[index : index + chunk_size]) for index in range(0, len(sentences), chunk_size)]
        return chunks[:target_count]

    @staticmethod
    def _subtitle(text: str) -> str:
        return text[:90] + ("..." if len(text) > 90 else "")

    @staticmethod
    def _visual_prompt(title: str, narration: str, visual_style: str) -> str:
        return (
            f"{visual_style}. 영상 제목은 '{title}'. "
            f"장면 내용: {narration[:220]}. "
            "저작권 문제가 없는 원본 생성 이미지, 큰 자막 공간, 따뜻하고 명확한 구도."
        )
