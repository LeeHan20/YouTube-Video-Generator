from __future__ import annotations

import hashlib

from app.google.repository import Channel


TOPIC_PATTERNS = [
    ("정보제공형", "{channel}에서 꼭 알아두면 좋은 생활 기준 {num}가지"),
    ("클릭유도형", "많은 분들이 놓치는 {channel}의 의외의 신호"),
    ("정보제공형", "50대 이후 {channel}을 더 편하게 이해하는 방법"),
    ("클릭유도형", "알고 나면 바로 확인하게 되는 {channel} 체크포인트"),
    ("정보제공형", "처음 보는 분도 따라 하기 쉬운 {channel} 정리"),
    ("클릭유도형", "{channel}, 이것만은 오늘 확인해 보세요"),
]


class TopicGenerator:
    """Deterministic MVP generator. Replace this with an LLM adapter later."""

    def generate(self, channel: Channel, week_key: str, count: int) -> list[dict[str, str]]:
        topics = []
        for index in range(count):
            topic_type, pattern = TOPIC_PATTERNS[index % len(TOPIC_PATTERNS)]
            seed = f"{channel.channel_id}:{week_key}:{index}"
            short_hash = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]
            title = pattern.format(channel=channel.channel_name, num=(index % 5) + 3)
            planning_note = self._planning_note(channel.channel_name, topic_type)
            script_summary = self._script_summary(title)
            topics.append(
                {
                    "topic_id": f"topic_{week_key}_{short_hash}",
                    "topic_title": title,
                    "topic_type": topic_type,
                    "planning_note": planning_note,
                    "script_summary": script_summary,
                }
            )
        return topics

    @staticmethod
    def _planning_note(channel_name: str, topic_type: str) -> str:
        tone = "차분한 정보 전달" if topic_type == "정보제공형" else "궁금증을 주되 과장하지 않는 도입"
        return (
            f"50대 이상 시청자가 {channel_name} 주제를 쉽게 이해하도록 {tone}을 사용한다. "
            "건강, 금융, 법률처럼 민감한 내용은 단정 표현을 피하고 확인이 필요한 부분을 안내한다."
        )

    @staticmethod
    def _script_summary(title: str) -> str:
        return (
            f"'{title}'의 핵심 배경을 짧게 소개하고, 본문에서 3~5개 챕터로 나누어 설명한 뒤 "
            "마지막에 실천 가능한 확인 목록과 고정 아웃트로 문구로 마무리한다."
        )
