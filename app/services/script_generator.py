from __future__ import annotations


class ScriptGenerator:
    """MVP script generator with conservative language for older audiences."""

    def generate(self, title: str, summary: str, length_minutes: int) -> str:
        length = max(1, length_minutes)
        chapter_count = 3 if length <= 5 else 5
        chapters = "\n".join(
            [
                (
                    f"챕터 {idx}. 핵심 확인 {idx}\n"
                    "짧고 쉬운 문장으로 설명합니다. 예외가 있을 수 있으니 개인 상황에 맞게 확인하는 것이 좋습니다. "
                    "무리하게 결론을 내리지 않고, 시청자가 오늘 바로 점검할 수 있는 기준을 알려드립니다."
                )
                for idx in range(1, chapter_count + 1)
            ]
        )
        return (
            f"제목: {title}\n\n"
            "인트로\n"
            "안녕하세요. 오늘은 일상에서 놓치기 쉬운 내용을 천천히 살펴보겠습니다. "
            "어렵지 않게, 꼭 필요한 부분만 차분히 정리해 드리겠습니다.\n\n"
            f"요약\n{summary}\n\n"
            f"{chapters}\n\n"
            "마무리\n"
            "오늘 내용은 일반적인 정보입니다. 건강, 금융, 법률처럼 중요한 결정은 전문가와 한 번 더 확인해 주세요. "
            "도움이 되셨다면 다음 영상에서도 차분하고 정확한 정보로 찾아뵙겠습니다."
        )
