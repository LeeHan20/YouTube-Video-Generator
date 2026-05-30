from __future__ import annotations

from app.core.constants import (
    CHANNEL_LIST_COLUMNS,
    CHANNEL_SETTINGS_KEYS,
    CHANNEL_TOPIC_COLUMNS,
    REVIEW_COLUMNS,
    UPLOAD_COLUMNS,
)
from app.google.sheets_client import SheetsClient
from app.google.template import (
    HEADER_RGB,
    SECTION_RGB,
    SYSTEM_RGB,
    USER_EDITABLE_RGB,
    WHITE_RGB,
    SheetsTemplateBuilder,
)


TOPIC_HEADER_ROW_NUMBER = len(CHANNEL_SETTINGS_KEYS) + 3
TOPIC_HEADER_ROW_INDEX = TOPIC_HEADER_ROW_NUMBER - 1
TOPIC_DATA_START_INDEX = TOPIC_HEADER_ROW_NUMBER


def main() -> None:
    client = SheetsClient()
    metadata = client.metadata()
    builder = SheetsTemplateBuilder(client)
    requests: list[dict] = []

    for sheet_name, meta in metadata.items():
        if sheet_name in {"채널목록", "검수대기", "업로드현황"}:
            columns = _columns_for_sheet(sheet_name)
            requests.append(_clear_unused_columns_request(meta.sheet_id, len(columns)))
            requests.append(builder._header_format_request(meta.sheet_id, 0, len(columns)))
            requests.extend(_table_body_requests(meta.sheet_id, 1, columns, builder._editable_columns_for_sheet(sheet_name)))
            requests.append(builder._row_height_request(meta.sheet_id, 1, 1000, 44))
            continue

        if sheet_name.startswith("채널_"):
            requests.append(_clear_unused_columns_request(meta.sheet_id, len(CHANNEL_TOPIC_COLUMNS)))
            requests.append(builder._header_format_request(meta.sheet_id, 0, 2))
            requests.extend(_channel_settings_requests(meta.sheet_id))
            requests.append(builder._header_format_request(meta.sheet_id, TOPIC_HEADER_ROW_INDEX, len(CHANNEL_TOPIC_COLUMNS)))
            requests.extend(
                _table_body_requests(
                    meta.sheet_id,
                    TOPIC_DATA_START_INDEX,
                    CHANNEL_TOPIC_COLUMNS,
                    {"upload_day", "selected", "visual_style", "user_custom_style_prompt"},
                )
            )
            requests.append(builder._row_height_request(meta.sheet_id, TOPIC_HEADER_ROW_INDEX + 1, 1000, 64))

    client.batch_update(requests)
    print({"formatted_sheets": len([name for name in metadata if name in {"채널목록", "검수대기", "업로드현황"} or name.startswith("채널_")])})


def _columns_for_sheet(sheet_name: str) -> list[str]:
    if sheet_name == "채널목록":
        return CHANNEL_LIST_COLUMNS
    if sheet_name == "검수대기":
        return REVIEW_COLUMNS
    if sheet_name == "업로드현황":
        return UPLOAD_COLUMNS
    return []


def _table_body_requests(sheet_id: int, start_row_index: int, columns: list[str], editable: set[str]) -> list[dict]:
    requests = []
    for index, name in enumerate(columns):
        color = USER_EDITABLE_RGB if name in editable else SYSTEM_RGB
        requests.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": start_row_index,
                        "startColumnIndex": index,
                        "endColumnIndex": index + 1,
                    },
                    "cell": {"userEnteredFormat": SheetsTemplateBuilder._body_format(color)},
                    "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,wrapStrategy,textFormat)",
                }
            }
        )
    return requests


def _clear_unused_columns_request(sheet_id: int, start_column_index: int) -> dict:
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startColumnIndex": start_column_index,
            },
            "cell": {"userEnteredFormat": SheetsTemplateBuilder._body_format(WHITE_RGB)},
            "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,wrapStrategy,textFormat)",
        }
    }


def _channel_settings_requests(sheet_id: int) -> list[dict]:
    requests = [
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 20, "startColumnIndex": 0, "endColumnIndex": 1},
                "cell": {"userEnteredFormat": {"backgroundColor": SECTION_RGB}},
                "fields": "userEnteredFormat.backgroundColor",
            }
        }
    ]
    editable = {"channel_description", "intro_format_prompt", "outro_format_prompt"}
    for row_index, key in enumerate(CHANNEL_SETTINGS_KEYS, start=1):
        color = USER_EDITABLE_RGB if key in editable else SYSTEM_RGB
        requests.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": row_index,
                        "endRowIndex": row_index + 1,
                        "startColumnIndex": 1,
                        "endColumnIndex": 2,
                    },
                    "cell": {"userEnteredFormat": SheetsTemplateBuilder._body_format(color)},
                    "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,wrapStrategy,textFormat)",
                }
            }
        )
    return requests


if __name__ == "__main__":
    main()
