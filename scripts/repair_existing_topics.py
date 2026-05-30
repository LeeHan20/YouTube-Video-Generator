from __future__ import annotations

from app.google.repository import SheetsRepository
from app.google.sheets_client import SheetsClient
from app.pipeline.week import DAY_LABELS
from app.services.topic_generator import TopicGenerator


def main() -> None:
    repo = SheetsRepository(SheetsClient())
    topic_generator = TopicGenerator()
    repaired = 0
    for channel in repo.list_channels():
        for topic in repo.list_channel_topics(channel.sheet_name):
            if not topic.get("topic_id"):
                continue
            changed = False
            title = topic.get("topic_title", "")
            topic_type = topic.get("topic_type", "정보제공형")
            if _looks_generic(topic.get("planning_note", "")) or _looks_generic(topic.get("script_summary", "")):
                enriched = {
                    "planning_note": topic_generator._fallback_planning_for_title(channel.channel_name, title, topic_type),
                    "script_summary": topic_generator._fallback_summary_for_title(title),
                }
                topic.update(enriched)
                changed = True
            else:
                topic["planning_note"] = _clean_fallback_text(topic.get("planning_note", ""))
                topic["script_summary"] = _clean_fallback_text(topic.get("script_summary", ""))
                changed = True
            if _looks_generic(topic.get("full_script", "")):
                topic["full_script"] = _fallback_script(
                    title,
                    topic.get("script_summary", ""),
                    _int_or_default(topic.get("video_length_minutes"), channel.default_video_length_minutes),
                )
                changed = True
            if topic.get("upload_day") in DAY_LABELS:
                topic["upload_day"] = DAY_LABELS[topic["upload_day"]]
                changed = True
            if changed:
                repo.update_channel_topic(channel.sheet_name, int(topic["_row_number"]), topic)
                repaired += 1
    print({"repaired_topics": repaired})


def _looks_generic(value: str) -> bool:
    markers = [
        "핵심 확인",
        "짧고 쉬운 문장으로 설명합니다",
        "50대 이상 시청자가",
        "본문에서 3~5개 챕터",
        "차분한 정보 전달",
        "궁금증을 주되",
    ]
    return not value or any(marker in value for marker in markers)


def _clean_fallback_text(value: str) -> str:
    return value.replace("'를 ", "' 주제를 ").replace(" 설명로 ", " 설명으로 ")


def _int_or_default(value: str | None, default: int) -> int:
    try:
        return max(1, int(value or default))
    except ValueError:
        return default


def _fallback_script(title: str, summary: str, length_minutes: int) -> str:
    chapter_count = 3 if length_minutes <= 5 else 5
    chapters = []
    points = [
        "왜 이 주제를 확인해야 하는지",
        "가장 먼저 살펴볼 기준",
        "자주 헷갈리는 부분",
        "오늘 바로 점검할 수 있는 방법",
        "무리하지 않고 마무리하는 기준",
    ][:chapter_count]
    for index, point in enumerate(points, start=1):
        chapters.append(
            f"챕터 {index}. {point}\n"
            f"이 장에서는 '{title}'을 이해할 때 필요한 {point}을 차분히 설명합니다. "
            "한 가지 사례만 보고 단정하지 않고, 내 상황에 맞는지 확인하는 순서로 안내합니다. "
            "필요한 경우 전문가나 공식 안내를 함께 확인하는 것이 좋습니다."
        )
    return (
        f"제목: {title}\n\n"
        "인트로\n"
        f"안녕하세요. 오늘은 '{title}'에 대해 천천히 살펴보겠습니다. "
        "어려운 용어보다 실제로 확인할 수 있는 기준을 중심으로 정리하겠습니다.\n\n"
        f"요약\n{summary}\n\n"
        + "\n\n".join(chapters)
        + "\n\n마무리\n오늘 내용은 일반적인 정보입니다. 중요한 결정은 개인 상황에 맞게 한 번 더 확인해 주세요. "
        "다음 영상에서도 차분하고 정확한 정보로 찾아뵙겠습니다."
    )


if __name__ == "__main__":
    main()
