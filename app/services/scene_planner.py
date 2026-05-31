from __future__ import annotations

import re
import textwrap

from app.pipeline.models import Scene
from app.services.ai_client import get_ai_client
from app.services.prompt_loader import render_prompt


class ScenePlanner:
    def plan(self, title: str, script: str, visual_style: str, length_minutes: int) -> list[Scene]:
        target_scene_count = max(4, min(12, length_minutes * 2))
        chunks = self._script_chunks(script, target_scene_count)
        scenes = []
        estimated_duration = max(6, int(length_minutes * 60 / max(1, len(chunks))))
        for idx, chunk in enumerate(chunks, start=1):
            narration = self._narration(chunk)
            caption = self._caption(narration)
            prompt = self._visual_prompt(title, chunk, visual_style)
            scenes.append(
                Scene(
                    scene_id=f"scene_{idx:03d}",
                    title=f"장면 {idx}",
                    narration=narration,
                    caption=caption,
                    subtitle=caption,
                    visual_prompt=prompt,
                    image_keywords=self._representative_keywords(narration),
                    start_seconds=(idx - 1) * estimated_duration,
                    start_time=(idx - 1) * estimated_duration,
                    end_time=idx * estimated_duration,
                    duration_seconds=estimated_duration,
                )
            )
        return scenes

    def improve_prompt(self, title: str, scene: Scene, user_instruction: str) -> str:
        prompt = render_prompt(
            "scene_prompt_improvement",
            title=title,
            scene_visual_prompt=scene.visual_prompt,
            user_instruction=user_instruction,
        )
        return get_ai_client().generate_text(prompt, max_tokens=700).text.strip()

    @staticmethod
    def _script_chunks(script: str, target_count: int) -> list[str]:
        text = ScenePlanner._clean_script(script)
        sentences = re.split(r"(?<=[.!?。！？])\s+|(?<=다\.)\s+|(?<=요\.)\s+|(?<=죠\.)\s+", text)
        sentences = [sentence.strip() for sentence in sentences if sentence.strip()]
        if not sentences:
            return ["대본을 확인하는 기본 장면입니다."]
        chunks: list[str] = []
        current = ""
        max_chars = 105
        for sentence in sentences:
            if len(chunks) >= target_count:
                break
            sentence = ScenePlanner._clean_scene_text(sentence)
            if not sentence:
                continue
            candidate = f"{current} {sentence}".strip()
            if current and len(candidate) > max_chars:
                chunks.append(current)
                current = sentence
            else:
                current = candidate
        if current and len(chunks) < target_count:
            chunks.append(current)
        return chunks[:target_count] or ["대본을 확인하는 기본 장면입니다."]

    @staticmethod
    def _clean_script(script: str) -> str:
        text = script or ""
        text = re.sub(r"```.*?```", " ", text, flags=re.S)
        text = re.sub(r"^#{1,6}\s*", " ", text, flags=re.M)
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        text = re.sub(r"\[[^\]]*(인트로|챕터|아웃트로|BGM|화면|자막|장면)[^\]]*\]", " ", text, flags=re.I)
        text = re.sub(r"\([^)]*(BGM|화면|자막|효과음|전환|이미지)[^)]*\)", " ", text, flags=re.I)
        text = re.sub(r"[-=]{3,}", " ", text)
        text = re.sub(r"(유튜브\s*)?영상\s*나레이션\s*대본|전체\s*대본|대본\s*상세", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _clean_scene_text(text: str) -> str:
        cleaned = re.sub(r"^(제목|요약|인트로|본문|챕터\s*\d*|아웃트로)\s*[:：.]?\s*", "", text.strip())
        cleaned = re.sub(r"^\d+\.\s*", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    @staticmethod
    def _narration(text: str) -> str:
        cleaned = ScenePlanner._clean_scene_text(text)
        if len(cleaned) <= 220:
            return cleaned
        parts = textwrap.wrap(cleaned, width=105, break_long_words=False, replace_whitespace=False)
        return " ".join(parts[:2]).strip()

    @staticmethod
    def _caption(text: str) -> str:
        cleaned = ScenePlanner._clean_scene_text(text)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if len(cleaned) <= 56:
            return cleaned
        sentences = re.split(r"(?<=[.!?。！？])\s+|(?<=다\.)\s+|(?<=요\.)\s+", cleaned)
        first = next((item.strip() for item in sentences if item.strip()), cleaned)
        if len(first) <= 64:
            return first
        return textwrap.shorten(first, width=64, placeholder="...")

    @staticmethod
    def _visual_prompt(title: str, narration: str, visual_style: str) -> str:
        clean_narration = ScenePlanner._clean_scene_text(narration)
        keywords = ", ".join(ScenePlanner._representative_keywords(clean_narration))
        return (
            f"{visual_style}. 영상 제목은 '{title}'. "
            f"장면 내용: {clean_narration[:180]}. 대표어: {keywords}. "
            "저작권 문제가 없는 원본 생성 이미지, 큰 자막 공간, 따뜻하고 명확한 구도."
        )

    @staticmethod
    def _representative_keywords(text: str, limit: int = 4) -> list[str]:
        cleaned = re.sub(r"[^\w\s가-힣]", " ", text)
        stopwords = {
            "오늘", "여러분", "그리고", "하지만", "그래서", "이것", "저것", "있습니다", "합니다", "됩니다", "좋습니다",
            "확인", "경우", "부분", "내용", "영상", "장면", "정리", "하겠습니다", "살펴보겠습니다",
        }
        words = [word.strip() for word in cleaned.split() if len(word.strip()) > 1 and word.strip() not in stopwords]
        scored: dict[str, int] = {}
        for index, word in enumerate(words):
            bonus = 8 if any(key in word for key in ["혈당", "주스", "커피", "음료", "수면", "운동", "식사", "간", "건강"]) else 0
            scored[word] = max(scored.get(word, 0), bonus + max(1, 20 - index))
        return [word for word, _ in sorted(scored.items(), key=lambda item: item[1], reverse=True)[:limit]]
