from __future__ import annotations

from app.services.ai_client import get_ai_client
from app.services.prompt_loader import render_prompt


class ScriptGenerator:
    """Generate a real Korean narration script with an AI-first, safe fallback."""

    CHARS_PER_MINUTE = 380
    MIN_TARGET_RATIO = 0.9
    MAX_EXPANSION_ATTEMPTS = 2

    def generate(self, title: str, summary: str, length_minutes: int) -> str:
        ai_script = self._generate_with_ai(title, summary, length_minutes)
        if ai_script:
            return ai_script
        return self._generate_fallback(title, summary, length_minutes)

    def _generate_with_ai(self, title: str, summary: str, length_minutes: int) -> str:
        length = max(1, length_minutes)
        target_seconds = length * 60
        target_chars = self._target_chars(length)
        min_chars = self._min_target_chars(length)
        prompt = render_prompt(
            "script_generation",
            title=title,
            summary=summary,
            length_minutes=length,
            target_seconds=target_seconds,
            target_chars=target_chars,
            min_chars=min_chars,
        )
        try:
            client = get_ai_client()
            text = client.generate_text(prompt, max_tokens=8000).text.strip()
        except Exception:
            return ""
        if "AI API 키가 설정되지 않아" in text:
            return ""
        script = self._clean_ai_script(text)
        for _ in range(self.MAX_EXPANSION_ATTEMPTS):
            if self._spoken_char_count(script) >= min_chars:
                break
            expansion_prompt = render_prompt(
                "script_expansion",
                title=title,
                summary=summary,
                length_minutes=length,
                target_seconds=target_seconds,
                target_chars=target_chars,
                min_chars=min_chars,
                current_chars=self._spoken_char_count(script),
                estimated_seconds=self._estimated_seconds(script),
                current_script=script,
            )
            try:
                expanded = client.generate_text(expansion_prompt, max_tokens=9000).text.strip()
            except Exception:
                break
            if "AI API 키가 설정되지 않아" in expanded:
                break
            expanded_script = self._clean_ai_script(expanded)
            if self._spoken_char_count(expanded_script) <= self._spoken_char_count(script):
                break
            script = expanded_script
        return script

    def _generate_fallback(self, title: str, summary: str, length_minutes: int) -> str:
        length = max(1, length_minutes)
        chapter_count = 3 if length <= 5 else 5
        focus_points = self._focus_points(title, chapter_count)
        chapters = "\n".join(
            [
                (
                    f"챕터 {idx}. {point}\n"
                    f"먼저 {point}을 살펴보겠습니다. 이 부분은 '{title}'을 이해할 때 기준점이 됩니다. "
                    "한 번에 결론을 내리기보다, 내 상황에 해당하는지 차분히 확인하는 것이 좋습니다. "
                    "비슷해 보이는 사례라도 개인마다 조건이 다를 수 있으니, 필요한 경우 전문가나 공식 안내를 함께 확인해 주세요."
                )
                for idx, point in enumerate(focus_points, start=1)
            ]
        )
        return (
            f"제목: {title}\n\n"
            "인트로\n"
            f"안녕하세요. 오늘은 '{title}'에 대해 천천히 살펴보겠습니다. "
            "처음 들으면 조금 어렵게 느껴질 수 있지만, 핵심만 나누어 보면 이해하기 훨씬 쉽습니다. "
            "오늘 영상에서는 불안감을 주기보다, 실제로 확인해 볼 수 있는 기준을 중심으로 정리하겠습니다.\n\n"
            f"요약\n{summary}\n\n"
            f"{chapters}\n\n"
            "마무리\n"
            f"오늘은 '{title}'을 볼 때 기억하면 좋은 기준을 정리했습니다. "
            "오늘 내용은 일반적인 정보입니다. 건강, 금융, 법률처럼 중요한 결정은 전문가와 한 번 더 확인해 주세요. "
            "도움이 되셨다면 다음 영상에서도 차분하고 정확한 정보로 찾아뵙겠습니다."
        )

    @staticmethod
    def _focus_points(title: str, chapter_count: int) -> list[str]:
        base = [
            "왜 이 주제를 확인해야 하는지",
            "가장 먼저 살펴볼 기준",
            "자주 헷갈리는 부분",
            "오늘 바로 점검할 수 있는 방법",
            "무리하지 않고 확인하는 마무리 기준",
        ]
        if "건강" in title:
            base = [
                "몸 상태를 단정하지 않고 살피는 법",
                "생활 습관에서 먼저 확인할 부분",
                "병원 상담이 필요한 신호와 일반 정보의 차이",
                "오늘부터 무리 없이 기록할 수 있는 항목",
                "불안하지 않게 관리하는 기준",
            ]
        return base[:chapter_count]

    @staticmethod
    def _clean_ai_script(text: str) -> str:
        prefixes = [
            "네, 요청하신 내용을 바탕으로 YouTube 영상 나레이션 대본을 작성했습니다.",
            "요청하신 내용을 바탕으로 YouTube 영상 나레이션 대본을 작성했습니다.",
        ]
        cleaned = text.strip()
        for prefix in prefixes:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix) :].strip()
        return cleaned.strip("- \n")

    @classmethod
    def _target_chars(cls, length_minutes: int) -> int:
        return max(1, length_minutes) * cls.CHARS_PER_MINUTE

    @classmethod
    def _min_target_chars(cls, length_minutes: int) -> int:
        return int(cls._target_chars(length_minutes) * cls.MIN_TARGET_RATIO)

    @classmethod
    def _estimated_seconds(cls, script: str) -> int:
        return int(round(cls._spoken_char_count(script) / cls.CHARS_PER_MINUTE * 60))

    @staticmethod
    def _spoken_char_count(script: str) -> int:
        return len(" ".join((script or "").split()))
