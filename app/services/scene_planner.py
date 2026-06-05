from __future__ import annotations

import json
import re
import textwrap

from app.pipeline.models import Scene
from app.services.ai_client import get_ai_client
from app.services.prompt_loader import render_prompt


class ScenePlanner:
    def plan(self, title: str, script: str, visual_style: str, length_minutes: int) -> list[Scene]:
        chunks = self._paragraph_chunks(script)
        if not chunks:
            chunks = self._script_chunks(script, max(4, length_minutes * 2))
        scenes = []
        estimated_duration = max(6, int(length_minutes * 60 / max(1, len(chunks))))
        for idx, chunk in enumerate(chunks, start=1):
            narration = self._narration(chunk)
            caption = self._caption(narration)
            media_type = "video" if idx <= 2 else "image"
            prompt = self._visual_prompt(title, chunk, visual_style, media_type)
            crawl_prompt = self._crawl_prompt(title, chunk, visual_style, media_type)
            generation_prompt = self._generation_prompt(title, chunk, visual_style, media_type)
            scenes.append(
                Scene(
                    scene_id=f"scene_{idx:03d}",
                    title=f"장면 {idx}",
                    narration=narration,
                    caption=caption,
                    subtitle=caption,
                    visual_prompt=prompt,
                    crawl_prompt=crawl_prompt,
                    generation_prompt=generation_prompt,
                    media_type=media_type,
                    image_keywords=self._representative_keywords(narration),
                    start_seconds=(idx - 1) * estimated_duration,
                    start_time=(idx - 1) * estimated_duration,
                    end_time=idx * estimated_duration,
                    duration_seconds=estimated_duration,
                )
            )
        scenes = self._apply_ai_asset_plan(title, visual_style, scenes)
        scenes = self._enforce_required_video_opening(scenes)
        return self._standardize_visual_prompts(title, visual_style, scenes)

    def improve_prompt(self, title: str, scene: Scene, user_instruction: str) -> str:
        prompt = render_prompt(
            "scene_prompt_improvement",
            title=title,
            scene_visual_prompt=scene.visual_prompt,
            user_instruction=user_instruction,
        )
        return get_ai_client().generate_text(prompt, max_tokens=700).text.strip()

    def _apply_ai_asset_plan(self, title: str, visual_style: str, scenes: list[Scene]) -> list[Scene]:
        plan = self._ai_asset_plan(title, visual_style, scenes)
        if not plan:
            return scenes
        by_id = {item.get("scene_id"): item for item in plan if isinstance(item, dict)}
        for scene in scenes:
            item = by_id.get(scene.scene_id)
            if not item:
                continue
            media_type = str(item.get("media_type") or scene.media_type).strip().lower()
            if media_type in {"image", "video"}:
                scene.media_type = media_type
            crawl_prompt = str(item.get("crawl_prompt") or "").strip()
            generation_prompt = str(item.get("generation_prompt") or "").strip()
            if crawl_prompt:
                scene.crawl_prompt = crawl_prompt
            if generation_prompt:
                scene.generation_prompt = generation_prompt
            keywords = item.get("image_keywords")
            if isinstance(keywords, list):
                cleaned = [str(keyword).strip() for keyword in keywords if str(keyword).strip()]
                if cleaned:
                    scene.image_keywords = cleaned[:6]
        return scenes

    @staticmethod
    def _enforce_required_video_opening(scenes: list[Scene]) -> list[Scene]:
        for scene in scenes[:2]:
            scene.media_type = "video"
            if scene.generation_prompt and "영상 클립" not in scene.generation_prompt:
                scene.generation_prompt = f"{scene.generation_prompt} 나레이션 길이에 맞춰 자르거나 반복할 수 있는 영상 클립."
        return scenes

    @staticmethod
    def _standardize_visual_prompts(title: str, visual_style: str, scenes: list[Scene]) -> list[Scene]:
        for scene in scenes:
            scene.visual_prompt = ScenePlanner.standard_visual_prompt(
                title=title,
                narration=scene.narration or scene.subtitle or scene.caption,
                visual_style=visual_style,
                media_type=scene.media_type,
            )
        return scenes

    @staticmethod
    def standard_visual_prompt(title: str, narration: str, visual_style: str, media_type: str = "image") -> str:
        return ScenePlanner._visual_prompt(title, narration, visual_style, media_type)

    @staticmethod
    def is_standard_visual_prompt(prompt: str) -> bool:
        return all(marker in (prompt or "") for marker in ["영상 제목은", "장면 내용:", "대표어:", "이 장면에는"])

    def _ai_asset_plan(self, title: str, visual_style: str, scenes: list[Scene]) -> list[dict]:
        scenes_json = json.dumps(
            [
                {
                    "scene_id": scene.scene_id,
                    "narration": scene.narration,
                    "caption": scene.caption,
                    "fallback_media_type": scene.media_type,
                }
                for scene in scenes
            ],
            ensure_ascii=False,
        )
        prompt = render_prompt("scene_asset_plan", title=title, visual_style=visual_style, scenes_json=scenes_json)
        try:
            text = get_ai_client().generate_text(prompt, max_tokens=max(1400, len(scenes) * 220)).text.strip()
            parsed = self._parse_json_object(text)
            items = parsed.get("scenes", [])
            return items if isinstance(items, list) else []
        except Exception:
            return []

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
    def _paragraph_chunks(script: str) -> list[str]:
        text = ScenePlanner._clean_script_preserving_paragraphs(script)
        paragraphs = re.split(r"\n\s*\n+", text)
        chunks: list[str] = []
        for paragraph in paragraphs:
            sentences = ScenePlanner._sentences(paragraph)
            for index in range(0, len(sentences), 2):
                chunk = " ".join(sentences[index:index + 2]).strip()
                if chunk:
                    chunks.append(chunk)
        return chunks

    @staticmethod
    def _script_chunks(script: str, target_count: int) -> list[str]:
        text = ScenePlanner._clean_script(script)
        sentences = ScenePlanner._sentences(text)
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
    def _sentences(text: str) -> list[str]:
        sentences = re.split(r"(?<=[.!?。！？])\s+|(?<=다\.)\s+|(?<=요\.)\s+|(?<=죠\.)\s+", text)
        cleaned = [ScenePlanner._clean_scene_text(sentence) for sentence in sentences]
        return [sentence for sentence in cleaned if sentence]

    @staticmethod
    def _clean_script(script: str) -> str:
        text = ScenePlanner._clean_script_preserving_paragraphs(script)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _clean_script_preserving_paragraphs(script: str) -> str:
        text = script or ""
        text = re.sub(r"```.*?```", " ", text, flags=re.S)
        text = re.sub(r"^#{1,6}\s*", " ", text, flags=re.M)
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        text = re.sub(r"\[[^\]]*(인트로|챕터|아웃트로|BGM|화면|자막|장면)[^\]]*\]", " ", text, flags=re.I)
        text = re.sub(r"\([^)]*(BGM|화면|자막|효과음|전환|이미지)[^)]*\)", " ", text, flags=re.I)
        text = re.sub(r"[-=]{3,}", " ", text)
        text = re.sub(r"(유튜브\s*)?영상\s*나레이션\s*대본|전체\s*대본|대본\s*상세", " ", text)
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = "\n\n".join(re.sub(r"\s*\n\s*", " ", paragraph).strip() for paragraph in text.split("\n\n"))
        return text

    @staticmethod
    def _clean_scene_text(text: str) -> str:
        cleaned = re.sub(r"^(제목|요약|인트로|본문|챕터\s*\d*|아웃트로)\s*[:：.]?\s*", "", text.strip())
        cleaned = re.sub(r"^\d+\.\s*", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    @staticmethod
    def _narration(text: str) -> str:
        return ScenePlanner._clean_scene_text(text)

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
    def _visual_prompt(title: str, narration: str, visual_style: str, media_type: str = "image") -> str:
        clean_narration = ScenePlanner._clean_scene_text(narration)
        keywords = ", ".join(ScenePlanner._representative_keywords(clean_narration))
        media_label = "짧은 영상 클립" if media_type == "video" else "이미지"
        return (
            f"{visual_style}. 영상 제목은 '{title}'. "
            f"장면 내용: {clean_narration[:260]}. 대표어: {keywords}. "
            f"이 장면에는 {media_label}가 필요하다. 50대 이상 시청자가 바로 이해할 수 있는 따뜻하고 명확한 구도."
        )

    @staticmethod
    def _crawl_prompt(title: str, narration: str, visual_style: str, media_type: str = "image") -> str:
        clean_narration = ScenePlanner._clean_scene_text(narration)
        focused = ScenePlanner._english_query_for_text(clean_narration)
        return focused.strip()

    @staticmethod
    def _generation_prompt(title: str, narration: str, visual_style: str, media_type: str = "image") -> str:
        clean_narration = ScenePlanner._clean_scene_text(narration)
        keywords = ", ".join(ScenePlanner._representative_keywords(clean_narration, limit=6))
        if media_type == "video":
            return (
                f"{visual_style}. '{title}' 영상의 장면. 내용: {clean_narration[:260]}. "
                f"대표어: {keywords}. 나레이션보다 약간 긴 짧은 영상 클립으로 쓰기 좋은 자연스러운 움직임."
            )
        return (
            f"{visual_style}. '{title}' 영상의 장면. 내용: {clean_narration[:260]}. "
            f"대표어: {keywords}. 원본 생성 이미지, 큰 자막 공간, 모바일 가독성, 따뜻하고 명확한 구도."
        )

    @staticmethod
    def _english_query_for_text(text: str) -> str:
        lowered = text.lower()
        pairs = [
            (["수면 위생", "숙면", "수면", "잠", "불면", "뒤척"], "sleep bedroom"),
            (["스마트폰", "휴대폰", "잠들기 전"], "phone bed"),
            (["카페인", "커피"], "coffee cup"),
            (["조명", "불빛", "블루라이트"], "bedroom night"),
            (["침실", "잠자리", "침대"], "bedroom"),
            (["스트레칭", "이완", "호흡"], "relaxation stretching"),
            (["물", "수분"], "water glass"),
            (["의사", "전문가", "상담"], "doctor consultation"),
            (["아침", "햇빛", "기상"], "morning sunlight"),
            (["건강", "생활습관", "습관"], "daily routine"),
        ]
        for keywords, query in pairs:
            if any(keyword in lowered for keyword in keywords):
                return query
        keywords = " ".join(ScenePlanner._representative_keywords(text, limit=5))
        return f"{keywords} clear realistic lifestyle"

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
