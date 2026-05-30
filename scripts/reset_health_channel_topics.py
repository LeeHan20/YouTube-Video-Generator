from __future__ import annotations

from app.core.constants import ASSET_COLUMNS, CHANNEL_TOPIC_COLUMNS, REVIEW_COLUMNS, SESSION_COLUMNS, UPLOAD_COLUMNS
from app.core.time import iso_now
from app.google.repository import SheetsRepository
from app.google.sheets_client import SheetsClient
from app.pipeline.stage1 import Stage1Pipeline
from app.pipeline.week import next_week_key
from scripts.repair_sheet_ui_format import main as repair_sheet_ui_format


CHANNEL_NAME = "건강정보"
TOPIC_START_ROW = 23
CLEAR_UNTIL_ROW = 500


def main() -> None:
    repo = SheetsRepository(SheetsClient())
    channel = next((item for item in repo.list_channels() if item.channel_name == CHANNEL_NAME), None)
    if not channel:
        raise RuntimeError(f"{CHANNEL_NAME} 채널을 찾지 못했습니다.")

    old_topics = [topic for topic in repo.list_channel_topics(channel.sheet_name) if topic.get("topic_id")]
    old_topic_ids = {topic["topic_id"] for topic in old_topics}

    clear_width = len(CHANNEL_TOPIC_COLUMNS)
    blank_rows = [[""] * clear_width for _ in range(CLEAR_UNTIL_ROW - TOPIC_START_ROW + 1)]
    end_col = SheetsClient._col_letter(clear_width)
    repo.client.update_values(f"'{channel.sheet_name}'!A{TOPIC_START_ROW}:{end_col}{CLEAR_UNTIL_ROW}", blank_rows)

    repo.update_channel(
        channel,
        {
            "last_topic_generated_week": "",
            "last_checked_at": iso_now(),
            "status": "EMPTY",
            "error_message": "",
        },
    )

    cleared_review_tasks = _clear_matching_rows(repo, "검수대기", REVIEW_COLUMNS, lambda row: row.get("channel_id") == channel.channel_id)
    cleared_uploads = _clear_matching_rows(
        repo,
        "업로드현황",
        UPLOAD_COLUMNS,
        lambda row: row.get("channel_id") == channel.channel_id or row.get("topic_id") in old_topic_ids,
    )
    cleared_assets = _clear_matching_rows(repo, "_SYSTEM_ASSETS", ASSET_COLUMNS, lambda row: row.get("topic_id") in old_topic_ids)
    cleared_sessions = _clear_matching_rows(repo, "_SYSTEM_SESSIONS", SESSION_COLUMNS, lambda row: row.get("topic_id") in old_topic_ids)

    updated_channel = next((item for item in repo.list_channels() if item.channel_id == channel.channel_id), channel)
    generated = Stage1Pipeline(repo)._generate_for_channel_locked(updated_channel, next_week_key())
    repair_sheet_ui_format()
    print(
        {
            "channel": channel.channel_name,
            "deleted_topics": len(old_topics),
            "cleared_review_tasks": cleared_review_tasks,
            "cleared_uploads": cleared_uploads,
            "cleared_assets": cleared_assets,
            "cleared_sessions": cleared_sessions,
            "generated_topics": generated,
        }
    )


def _clear_matching_rows(repo: SheetsRepository, sheet_name: str, headers: list[str], predicate) -> int:
    cleared = 0
    for row in repo.client.read_records(sheet_name):
        normalized = repo._normalize_record(row)
        if not predicate(normalized):
            continue
        row_number = int(row["_row_number"])
        repo.client.update_values(f"'{sheet_name}'!A{row_number}:{SheetsClient._col_letter(len(headers))}{row_number}", [[""] * len(headers)])
        cleared += 1
    return cleared


if __name__ == "__main__":
    main()
