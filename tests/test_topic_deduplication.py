from __future__ import annotations

import unittest

from app.pipeline.stage1 import Stage1Pipeline
from app.services.topic_generator import TopicGenerator


class TopicDeduplicationTest(unittest.TestCase):
    def test_similar_health_topic_is_duplicate(self) -> None:
        blocked = ["혈압 관리에 도움 되는 아침 생활 습관 3가지"]
        blocked_keys = {TopicGenerator._topic_key(title) for title in blocked}
        blocked_fingerprints = [TopicGenerator._topic_fingerprint(title) for title in blocked]

        self.assertTrue(
            TopicGenerator._is_duplicate_topic(
                "아침 혈압 관리 습관, 이것만은 확인하세요",
                blocked_keys,
                blocked_fingerprints,
            )
        )

    def test_different_topic_is_not_duplicate(self) -> None:
        blocked = ["혈압 관리에 도움 되는 아침 생활 습관 3가지"]
        blocked_keys = {TopicGenerator._topic_key(title) for title in blocked}
        blocked_fingerprints = [TopicGenerator._topic_fingerprint(title) for title in blocked]

        self.assertFalse(
            TopicGenerator._is_duplicate_topic(
                "무릎 통증을 줄이는 계단 오르기 주의점",
                blocked_keys,
                blocked_fingerprints,
            )
        )

    def test_stage1_filters_duplicate_candidates_before_writing(self) -> None:
        candidates = [
            {"topic_title": "혈압 관리에 도움 되는 아침 생활 습관", "planning_note": "a", "script_summary": "b"},
            {"topic_title": "아침 혈압 관리 습관, 이것만은 확인하세요", "planning_note": "c", "script_summary": "d"},
            {"topic_title": "무릎 통증을 줄이는 계단 오르기 주의점", "planning_note": "e", "script_summary": "f"},
        ]

        deduped = Stage1Pipeline._dedupe_topic_candidates(candidates)

        self.assertEqual([item["topic_title"] for item in deduped], [candidates[0]["topic_title"], candidates[2]["topic_title"]])


if __name__ == "__main__":
    unittest.main()
