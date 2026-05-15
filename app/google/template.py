from __future__ import annotations

from app.core.constants import (
    ASSET_COLUMNS,
    CHANNEL_LIST_COLUMNS,
    CHANNEL_SETTINGS_KEYS,
    CHANNEL_TOPIC_COLUMNS,
    JOB_COLUMNS,
    LOG_COLUMNS,
    ON_OFF_VALUES,
    REVIEW_COLUMNS,
    SESSION_COLUMNS,
    SHEET_NAMES,
    STATUS_VALUES,
    TRUE_FALSE_VALUES,
    UPLOAD_COLUMNS,
    VISUAL_STYLE_VALUES,
)
from app.google.sheets_client import SheetsClient


USER_EDITABLE_RGB = {"red": 0.89, "green": 0.96, "blue": 0.84}
SYSTEM_RGB = {"red": 0.93, "green": 0.93, "blue": 0.93}
HEADER_RGB = {"red": 0.14, "green": 0.31, "blue": 0.51}


class SheetsTemplateBuilder:
    def __init__(self, client: SheetsClient) -> None:
        self.client = client

    def ensure_template(self, default_channel_names: list[str] | None = None) -> None:
        channel_names = default_channel_names or ["건강정보", "생활상식", "역사이야기"]
        self._ensure_sheets([*SHEET_NAMES, *[f"채널_{name}" for name in channel_names]])

        self.client.update_values("'사용안내'!A1:B12", self._guide_rows())
        self._write_header("채널목록", CHANNEL_LIST_COLUMNS)
        self._write_header("검수대기", REVIEW_COLUMNS)
        self._write_header("업로드현황", UPLOAD_COLUMNS)
        self._write_header("_SYSTEM_JOBS", JOB_COLUMNS)
        self._write_header("_SYSTEM_LOGS", LOG_COLUMNS)
        self._write_header("_SYSTEM_ASSETS", ASSET_COLUMNS)
        self._write_header("_SYSTEM_SESSIONS", SESSION_COLUMNS)
        self.client.update_values("'_SYSTEM_SETTINGS'!A1:B3", [["key", "value"], ["schema_version", "1"], ["db", "none"]])

        for channel_name in channel_names:
            sheet_name = f"채널_{channel_name}"
            self._write_channel_sheet(sheet_name, channel_name)

        metadata = self.client.metadata()
        requests: list[dict] = []
        for hidden_sheet in [name for name in SHEET_NAMES if name.startswith("_SYSTEM")]:
            requests.append(self._hide_sheet_request(metadata[hidden_sheet].sheet_id))
        requests.extend(self._format_sheet_requests(metadata))
        self.client.batch_update(requests)
        self._seed_channel_list(channel_names)

    def _ensure_sheets(self, sheet_names: list[str]) -> None:
        existing = self.client.metadata()
        requests = [{"addSheet": {"properties": {"title": name}}} for name in sheet_names if name not in existing]
        self.client.batch_update(requests)

    def _write_header(self, sheet_name: str, columns: list[str]) -> None:
        self.client.update_values(f"'{sheet_name}'!A1", [columns])

    def _write_channel_sheet(self, sheet_name: str, channel_name: str) -> None:
        setting_rows = [["설정", "값"], *[[key, self._default_setting_value(key, channel_name)] for key in CHANNEL_SETTINGS_KEYS]]
        topic_header_row = len(setting_rows) + 2
        self.client.update_values(f"'{sheet_name}'!A1:B{len(setting_rows)}", setting_rows)
        self.client.update_values(f"'{sheet_name}'!A{topic_header_row}", [CHANNEL_TOPIC_COLUMNS])

    def _seed_channel_list(self, channel_names: list[str]) -> None:
        existing = self.client.read_records("채널목록")
        if existing:
            return
        rows = []
        for index, channel_name in enumerate(channel_names, start=1):
            rows.append(
                {
                    "channel_id": f"channel_{index:03d}",
                    "channel_name": channel_name,
                    "sheet_name": f"채널_{channel_name}",
                    "automation_enabled": "OFF",
                    "oauth_connected": "미연결",
                    "monday_enabled": "OFF",
                    "tuesday_enabled": "OFF",
                    "wednesday_enabled": "ON",
                    "thursday_enabled": "OFF",
                    "friday_enabled": "ON",
                    "saturday_enabled": "OFF",
                    "sunday_enabled": "OFF",
                    "upload_time": "20:00",
                    "default_video_length_minutes": "5",
                    "default_visual_style": "따뜻한 수채화 애니메이션풍",
                    "status": "EMPTY",
                }
            )
        self.client.append_records("채널목록", CHANNEL_LIST_COLUMNS, rows)

    def _format_sheet_requests(self, metadata: dict) -> list[dict]:
        requests: list[dict] = []
        for sheet_name, meta in metadata.items():
            requests.append(self._freeze_request(meta.sheet_id, 1))
            requests.append(self._header_format_request(meta.sheet_id, 0))
            requests.append(self._auto_resize_request(meta.sheet_id))
            if sheet_name == "채널목록":
                requests.extend(self._channel_list_validation_requests(meta.sheet_id))
            if sheet_name.startswith("채널_"):
                topic_header_idx = len(CHANNEL_SETTINGS_KEYS) + 2
                requests.append(self._header_format_request(meta.sheet_id, topic_header_idx - 1))
                requests.extend(self._channel_topic_validation_requests(meta.sheet_id, topic_header_idx))
        return requests

    def _channel_list_validation_requests(self, sheet_id: int) -> list[dict]:
        editable_columns = {
            "automation_enabled",
            "monday_enabled",
            "tuesday_enabled",
            "wednesday_enabled",
            "thursday_enabled",
            "friday_enabled",
            "saturday_enabled",
            "sunday_enabled",
            "upload_time",
            "default_video_length_minutes",
            "default_visual_style",
        }
        requests = self._editable_color_requests(sheet_id, CHANNEL_LIST_COLUMNS, editable_columns, 1)
        requests.extend(self._protect_system_column_requests(sheet_id, CHANNEL_LIST_COLUMNS, editable_columns, 1))
        for name in editable_columns & set(CHANNEL_LIST_COLUMNS):
            if name.endswith("_enabled") or name == "automation_enabled":
                requests.append(self._dropdown_request(sheet_id, CHANNEL_LIST_COLUMNS.index(name), ON_OFF_VALUES, 1))
        requests.append(self._dropdown_request(sheet_id, CHANNEL_LIST_COLUMNS.index("default_visual_style"), VISUAL_STYLE_VALUES, 1))
        requests.append(self._number_validation_request(sheet_id, CHANNEL_LIST_COLUMNS.index("default_video_length_minutes"), 1, 1))
        return requests

    def _channel_topic_validation_requests(self, sheet_id: int, header_row_number: int) -> list[dict]:
        editable_columns = {"selected", "video_length_minutes", "visual_style", "user_custom_style_prompt"}
        data_start_idx = header_row_number
        return [
            *self._editable_color_requests(sheet_id, CHANNEL_TOPIC_COLUMNS, editable_columns, data_start_idx),
            *self._protect_system_column_requests(sheet_id, CHANNEL_TOPIC_COLUMNS, editable_columns, data_start_idx),
            self._dropdown_request(sheet_id, CHANNEL_TOPIC_COLUMNS.index("selected"), TRUE_FALSE_VALUES, data_start_idx),
            self._dropdown_request(sheet_id, CHANNEL_TOPIC_COLUMNS.index("status"), STATUS_VALUES, data_start_idx),
            self._dropdown_request(sheet_id, CHANNEL_TOPIC_COLUMNS.index("visual_style"), VISUAL_STYLE_VALUES, data_start_idx),
            self._number_validation_request(sheet_id, CHANNEL_TOPIC_COLUMNS.index("video_length_minutes"), data_start_idx, 1),
        ]

    def _editable_color_requests(self, sheet_id: int, columns: list[str], editable: set[str], start_row: int) -> list[dict]:
        requests = []
        for idx, name in enumerate(columns):
            color = USER_EDITABLE_RGB if name in editable else SYSTEM_RGB
            requests.append(
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": start_row,
                            "startColumnIndex": idx,
                            "endColumnIndex": idx + 1,
                        },
                        "cell": {"userEnteredFormat": {"backgroundColor": color}},
                        "fields": "userEnteredFormat.backgroundColor",
                    }
                }
            )
        return requests

    def _protect_system_column_requests(
        self, sheet_id: int, columns: list[str], editable: set[str], start_row: int
    ) -> list[dict]:
        requests = []
        for idx, name in enumerate(columns):
            if name in editable:
                continue
            requests.append(
                {
                    "addProtectedRange": {
                        "protectedRange": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": start_row,
                                "startColumnIndex": idx,
                                "endColumnIndex": idx + 1,
                            },
                            "description": f"System managed column: {name}",
                            "warningOnly": True,
                        }
                    }
                }
            )
        return requests

    def _dropdown_request(self, sheet_id: int, column_index: int, values: list[str], start_row: int) -> dict:
        return {
            "setDataValidation": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": start_row,
                    "startColumnIndex": column_index,
                    "endColumnIndex": column_index + 1,
                },
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [{"userEnteredValue": value} for value in values],
                    },
                    "showCustomUi": True,
                    "strict": True,
                },
            }
        }

    def _number_validation_request(self, sheet_id: int, column_index: int, start_row: int, min_value: int) -> dict:
        return {
            "setDataValidation": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": start_row,
                    "startColumnIndex": column_index,
                    "endColumnIndex": column_index + 1,
                },
                "rule": {
                    "condition": {
                        "type": "NUMBER_GREATER_THAN_EQ",
                        "values": [{"userEnteredValue": str(min_value)}],
                    },
                    "strict": True,
                },
            }
        }

    @staticmethod
    def _hide_sheet_request(sheet_id: int) -> dict:
        return {
            "updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "hidden": True},
                "fields": "hidden",
            }
        }

    @staticmethod
    def _freeze_request(sheet_id: int, row_count: int) -> dict:
        return {
            "updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": row_count}},
                "fields": "gridProperties.frozenRowCount",
            }
        }

    @staticmethod
    def _header_format_request(sheet_id: int, row_index: int) -> dict:
        return {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": row_index, "endRowIndex": row_index + 1},
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": HEADER_RGB,
                        "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True},
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat)",
            }
        }

    @staticmethod
    def _auto_resize_request(sheet_id: int) -> dict:
        return {"autoResizeDimensions": {"dimensions": {"sheetId": sheet_id, "dimension": "COLUMNS"}}}

    @staticmethod
    def _guide_rows() -> list[list[str]]:
        return [
            ["YouTube 자동화 운영 시트", "Google Sheets가 운영 상태 저장소입니다. 별도 DB는 사용하지 않습니다."],
            ["1", "채널목록에서 자동화 여부와 업로드 요일을 설정합니다."],
            ["2", "채널별 시트에서 소주제 후보를 확인하고 selected를 TRUE로 바꿉니다."],
            ["3", "검수대기 시트의 링크로 웹 검수 UI에 들어갑니다."],
            ["4", "최종 승인 후 서버가 YouTube에 비공개 업로드합니다."],
            ["주의", "Google 계정 비밀번호, 2단계 인증 코드, OAuth 토큰은 이 시트에 저장하지 않습니다."],
            ["초록색", "사용자가 수정할 수 있는 칸입니다."],
            ["회색", "서버가 관리하는 시스템 칸입니다."],
        ]

    @staticmethod
    def _default_setting_value(key: str, channel_name: str) -> str:
        defaults = {
            "channel_name": channel_name,
            "automation_enabled": "OFF",
            "upload_days": "수,금",
            "upload_time": "20:00",
            "default_video_length_minutes": "5",
            "target_age_group": "50대 이상",
            "narration_style": "느리고 친절한 아나운서톤",
            "narration_speed": "slow",
            "subtitle_font": "궁서체",
            "subtitle_color": "black",
            "subtitle_outline_color": "yellow",
            "subtitle_position": "하단 중앙",
            "visual_style_default": "따뜻한 수채화 애니메이션풍",
            "thumbnail_style_prompt": "큰 글씨, 모바일 가독성 우선, 채널별 일관된 레이아웃",
            "caution_prompt": "과장, 허위, 공포 마케팅을 피하고 건강/금융/법률은 단정하지 않는다.",
        }
        return defaults.get(key, "")
