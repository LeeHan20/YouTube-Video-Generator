from __future__ import annotations

import hashlib
import json
import re

from app.google.repository import Channel
from app.services.ai_client import get_ai_client
from app.services.prompt_loader import render_prompt


TOPIC_PATTERNS = [
    ("정보제공형", "{channel}에서 꼭 알아두면 좋은 생활 기준 {num}가지"),
    ("클릭유도형", "많은 분들이 놓치는 {channel}의 의외의 신호"),
    ("정보제공형", "50대 이후 {channel}을 더 편하게 이해하는 방법"),
    ("클릭유도형", "알고 나면 바로 확인하게 되는 {channel} 체크포인트"),
    ("정보제공형", "처음 보는 분도 따라 하기 쉬운 {channel} 정리"),
    ("클릭유도형", "{channel}, 이것만은 오늘 확인해 보세요"),
]


class TopicGenerator:
    """Generate real topic candidates with AI, with a deterministic local fallback."""

    def generate(self, channel: Channel, week_key: str, count: int, avoid_topics: list[str] | None = None) -> list[dict[str, str]]:
        avoid_topics = self._unique_titles(avoid_topics or [])
        ai_topics = self._generate_with_ai(channel, week_key, count, avoid_topics)
        if ai_topics:
            return ai_topics
        return self._generate_fallback(channel, week_key, count, avoid_topics)

    def _generate_with_ai(self, channel: Channel, week_key: str, count: int, avoid_topics: list[str]) -> list[dict[str, str]]:
        prompt = render_prompt(
            "topic_candidates",
            channel_name=channel.channel_name,
            week_key=week_key,
            count=count,
            avoid_topics_json=json.dumps(avoid_topics[:120], ensure_ascii=False, indent=2),
        )
        try:
            data = get_ai_client().generate_json(prompt, max_tokens=8192)
        except (json.JSONDecodeError, ValueError, KeyError):
            return []
        topics = []
        blocked_keys = {self._topic_key(title) for title in avoid_topics}
        for item in data.get("topics", []):
            title = str(item.get("topic_title", "")).strip()
            if not title or self._is_duplicate_topic(title, blocked_keys):
                continue
            index = len(topics)
            seed = f"{channel.channel_id}:{week_key}:{index}:{item.get('topic_title', '')}"
            topics.append(
                {
                    "topic_id": f"topic_{week_key}_{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:8]}",
                    "topic_title": title,
                    "topic_type": str(item.get("topic_type", "정보제공형")).strip(),
                    "planning_note": str(item.get("planning_note", "")).strip(),
                    "script_summary": str(item.get("script_summary", "")).strip(),
                }
            )
            blocked_keys.add(self._topic_key(title))
            if len(topics) >= count:
                break
        if len(topics) < count:
            existing_titles = [*avoid_topics, *(topic["topic_title"] for topic in topics)]
            topics.extend(self._generate_fallback(channel, week_key, count - len(topics), existing_titles))
        return [topic for topic in topics if topic["topic_title"] and topic["planning_note"] and topic["script_summary"]]

    def _generate_fallback(self, channel: Channel, week_key: str, count: int, avoid_topics: list[str] | None = None) -> list[dict[str, str]]:
        topics = []
        blocked_keys = {self._topic_key(title) for title in (avoid_topics or [])}
        index = 0
        attempts = 0
        while len(topics) < count and attempts < count + len(TOPIC_PATTERNS) * 2:
            attempts += 1
            topic_type, pattern = TOPIC_PATTERNS[index % len(TOPIC_PATTERNS)]
            seed = f"{channel.channel_id}:{week_key}:{index}"
            short_hash = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]
            title = pattern.format(channel=channel.channel_name, num=(index % 5) + 3)
            index += 1
            if self._is_duplicate_topic(title, blocked_keys):
                continue
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
            blocked_keys.add(self._topic_key(title))
        return topics

    def enrich_existing_topic(self, channel_name: str, title: str, topic_type: str = "정보제공형") -> dict[str, str]:
        prompt = render_prompt(
            "topic_enrichment",
            channel_name=channel_name,
            title=title,
            topic_type=topic_type,
        )
        try:
            data = get_ai_client().generate_json(prompt, max_tokens=1400)
            planning_note = str(data.get("planning_note", "")).strip()
            script_summary = str(data.get("script_summary", "")).strip()
            if planning_note and script_summary:
                return {"planning_note": planning_note, "script_summary": script_summary}
        except Exception:
            pass
        return {
            "planning_note": self._fallback_planning_for_title(channel_name, title, topic_type),
            "script_summary": self._fallback_summary_for_title(title),
        }

    @staticmethod
    def _planning_note(channel_name: str, topic_type: str) -> str:
        if topic_type == "정보제공형":
            return (
                f"{channel_name}를 처음 접하는 50대 이상 시청자가 바로 이해할 수 있도록 생활 속 예시, 확인 순서, "
                "주의할 점을 차례로 설명한다. 단정 대신 '확인해 볼 수 있습니다' 같은 표현을 사용한다."
            )
        return (
            f"{channel_name}에서 사람들이 자주 놓치는 상황을 도입부에 제시하되 불안감을 키우지 않는다. "
            "본문에서는 오해하기 쉬운 부분과 실제 확인 방법을 비교해 차분히 정리한다."
        )

    @staticmethod
    def _script_summary(title: str) -> str:
        return (
            f"'{title}'에서 다루는 상황을 인트로에서 한 가지 사례로 열고, 본문에서는 왜 중요한지, "
            "어떤 순서로 확인하면 좋은지, 흔한 오해는 무엇인지, 오늘 바로 해볼 수 있는 점검 방법을 설명한다. "
            "마무리에서는 개인 상황에 따라 전문가 확인이 필요할 수 있음을 안내한다."
        )

    @staticmethod
    def _fallback_planning_for_title(channel_name: str, title: str, topic_type: str) -> str:
        hook = "궁금증을 주는 사례" if topic_type == "클릭유도형" else "차분한 배경 설명"
        return (
            f"'{title}' 주제를 {channel_name} 채널의 고정 톤에 맞춰 전달한다. 도입부에서는 {hook}으로 시작하고, "
            "본문에서는 50대 이상 시청자가 스스로 확인할 수 있는 기준을 3~5개로 나눈다. "
            "불안감을 키우지 않고, 필요한 경우 전문가나 공식 자료 확인을 권한다."
        )

    @staticmethod
    def _fallback_summary_for_title(title: str) -> str:
        return (
            f"'{title}'의 배경을 짧게 설명한 뒤, 왜 지금 확인하면 좋은지, 어떤 순서로 살펴보면 좋은지, "
            "흔히 헷갈리는 점은 무엇인지 정리한다. 마지막에는 오늘 바로 점검할 수 있는 행동 목록과 "
            "단정하지 않는 면책 문구로 마무리한다."
        )

    @staticmethod
    def _unique_titles(titles: list[str]) -> list[str]:
        unique = []
        seen = set()
        for title in titles:
            cleaned = " ".join((title or "").split()).strip()
            key = TopicGenerator._topic_key(cleaned)
            if cleaned and key and key not in seen:
                unique.append(cleaned)
                seen.add(key)
        return unique

    @staticmethod
    def _is_duplicate_topic(title: str, blocked_keys: set[str]) -> bool:
        return TopicGenerator._topic_key(title) in blocked_keys

    @staticmethod
    def _topic_key(title: str) -> str:
        text = re.sub(r"['\"“”‘’!?！？.,，。:：()\[\]{}<>〈〉《》]", " ", title or "")
        text = re.sub(r"\b\d+\b", " ", text)
        text = re.sub(r"\s+", " ", text).strip().lower()
        stopwords = {
            "50대", "이후", "이상", "필수", "꼭", "챙겨야", "합니다", "놓치면", "후회할", "건강",
            "시작", "위한", "좋은", "방법", "가지", "체크포인트", "확인", "오늘", "바로",
        }
        words = [word for word in text.split() if word not in stopwords and len(word) > 1]
        return " ".join(words[:8])
