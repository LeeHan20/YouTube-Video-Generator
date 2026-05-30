from __future__ import annotations

from app.core.constants import (
    ASSET_COLUMNS,
    CHANNEL_LIST_COLUMNS,
    CHANNEL_LIST_LABELS,
    CHANNEL_SETTINGS_KEYS,
    CHANNEL_SETTINGS_LABELS,
    CHANNEL_TOPIC_COLUMNS,
    CHANNEL_TOPIC_LABELS,
    JOB_COLUMNS,
    LOG_COLUMNS,
    ON_OFF_VALUES,
    REVIEW_COLUMNS,
    REVIEW_LABELS,
    SESSION_COLUMNS,
    SHEET_NAMES,
    STATUS_VALUES,
    TRUE_FALSE_VALUES,
    UPLOAD_COLUMNS,
    UPLOAD_LABELS,
    VISUAL_STYLE_VALUES,
)
from app.google.sheets_client import SheetsClient


USER_EDITABLE_RGB = {"red": 0.88, "green": 0.95, "blue": 0.82}
SYSTEM_RGB = {"red": 0.96, "green": 0.96, "blue": 0.96}
WHITE_RGB = {"red": 1, "green": 1, "blue": 1}
HEADER_RGB = {"red": 0.11, "green": 0.27, "blue": 0.46}
SECTION_RGB = {"red": 0.82, "green": 0.89, "blue": 0.96}


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

        self._seed_channel_list(channel_names)

        metadata = self.client.metadata()
        self._ensure_min_columns(metadata, 33)
        metadata = self.client.metadata()
        requests: list[dict] = []
        for hidden_sheet in [name for name in SHEET_NAMES if name.startswith("_SYSTEM")]:
            requests.append(self._hide_sheet_request(metadata[hidden_sheet].sheet_id))
        requests.extend(self._format_sheet_requests(metadata))
        self.client.batch_update(requests)

    def _ensure_sheets(self, sheet_names: list[str]) -> None:
        existing = self.client.metadata()
        requests = [{"addSheet": {"properties": {"title": name}}} for name in sheet_names if name not in existing]
        self.client.batch_update(requests)

    def _ensure_min_columns(self, metadata: dict, min_columns: int) -> None:
        requests = []
        for sheet_name, meta in metadata.items():
            if not sheet_name.startswith("채널_") or meta.column_count >= min_columns:
                continue
            requests.append(
                {
                    "appendDimension": {
                        "sheetId": meta.sheet_id,
                        "dimension": "COLUMNS",
                        "length": min_columns - meta.column_count,
                    }
                }
            )
        self.client.batch_update(requests)

    def _write_header(self, sheet_name: str, columns: list[str]) -> None:
        self.client.update_values(f"'{sheet_name}'!A1", [self._display_columns(sheet_name, columns)])

    def _write_channel_sheet(self, sheet_name: str, channel_name: str) -> None:
        setting_rows = [
            ["설정", "값"],
            *[
                [
                    CHANNEL_SETTINGS_LABELS[key],
                    self._default_setting_value(key, channel_name, sheet_name),
                ]
                for key in CHANNEL_SETTINGS_KEYS
            ],
        ]
        topic_header_row = len(setting_rows) + 2
        self.client.update_values(f"'{sheet_name}'!A1:B{len(setting_rows)}", setting_rows)
        self.client.update_values(f"'{sheet_name}'!A{topic_header_row}", [[CHANNEL_TOPIC_LABELS[column] for column in CHANNEL_TOPIC_COLUMNS]])

    def _seed_channel_list(self, channel_names: list[str]) -> None:
        existing = self.client.get_values("'채널목록'!A2:A")
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
            requests.append(self._default_cell_style_request(meta.sheet_id))
            requests.extend(self._default_dimension_requests(meta.sheet_id))
            if sheet_name in {"채널목록", "검수대기", "업로드현황"}:
                columns = self._columns_for_sheet(sheet_name)
                requests.append(self._header_format_request(meta.sheet_id, 0, len(columns)))
                requests.extend(
                    self._table_data_format_requests(
                        meta.sheet_id,
                        1,
                        columns,
                        editable_columns=self._editable_columns_for_sheet(sheet_name),
                    )
                )
                requests.append(self._row_height_request(meta.sheet_id, 0, 1, 44))
                requests.append(self._row_height_request(meta.sheet_id, 1, 1000, 44))
            if sheet_name == "채널목록":
                requests.extend(self._channel_list_validation_requests(meta.sheet_id))
                requests.extend(self._channel_list_width_requests(meta.sheet_id))
            if sheet_name.startswith("채널_"):
                requests.append(self._header_format_request(meta.sheet_id, 0, 2))
                topic_header_row = len(CHANNEL_SETTINGS_KEYS) + 3
                topic_header_index = topic_header_row - 1
                requests.extend(self._channel_upload_day_helper_requests(meta.sheet_id, sheet_name))
                requests.extend(self._channel_settings_format_requests(meta.sheet_id))
                requests.append(self._header_format_request(meta.sheet_id, topic_header_index, len(CHANNEL_TOPIC_COLUMNS)))
                requests.extend(self._channel_topic_validation_requests(meta.sheet_id, sheet_name, topic_header_row))
                requests.extend(self._channel_sheet_width_requests(meta.sheet_id))
                requests.append(self._row_height_request(meta.sheet_id, 0, 1, 44))
                requests.append(self._row_height_request(meta.sheet_id, 1, 20, 36))
                requests.append(self._row_height_request(meta.sheet_id, topic_header_index, topic_header_index + 1, 48))
                requests.append(self._row_height_request(meta.sheet_id, topic_header_index + 1, 1000, 64))
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

    def _channel_topic_validation_requests(self, sheet_id: int, sheet_name: str, header_row_number: int) -> list[dict]:
        editable_columns = {"upload_day", "selected", "visual_style", "user_custom_style_prompt"}
        data_start_idx = header_row_number
        return [
            *self._table_data_format_requests(sheet_id, data_start_idx, CHANNEL_TOPIC_COLUMNS, editable_columns),
            *self._protect_system_column_requests(sheet_id, CHANNEL_TOPIC_COLUMNS, editable_columns, data_start_idx),
            self._dropdown_from_range_request(
                sheet_id,
                CHANNEL_TOPIC_COLUMNS.index("upload_day"),
                data_start_idx,
                f"='{sheet_name}'!$AA$2:$AG$2",
            ),
            self._dropdown_request(sheet_id, CHANNEL_TOPIC_COLUMNS.index("selected"), TRUE_FALSE_VALUES, data_start_idx),
            self._dropdown_request(sheet_id, CHANNEL_TOPIC_COLUMNS.index("status"), STATUS_VALUES, data_start_idx),
            self._dropdown_request(sheet_id, CHANNEL_TOPIC_COLUMNS.index("visual_style"), VISUAL_STYLE_VALUES, data_start_idx),
            self._hide_columns_request(sheet_id, 26, 33),
        ]

    def _channel_settings_format_requests(self, sheet_id: int) -> list[dict]:
        editable_keys = {"channel_description", "intro_format_prompt", "outro_format_prompt"}
        requests: list[dict] = []
        requests.append(
            {
                "repeatCell": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 20, "startColumnIndex": 0, "endColumnIndex": 1},
                    "cell": {"userEnteredFormat": {"backgroundColor": SECTION_RGB}},
                    "fields": "userEnteredFormat.backgroundColor",
                }
            }
        )
        for row_index, key in enumerate(CHANNEL_SETTINGS_KEYS, start=1):
            color = USER_EDITABLE_RGB if key in editable_keys else SYSTEM_RGB
            cell_format = self._body_format(color)
            fields = "userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,wrapStrategy,textFormat)"
            if key == "upload_time":
                cell_format = {**cell_format, "numberFormat": {"type": "TIME", "pattern": "HH:MM"}}
                fields += ",numberFormat"
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
                        "cell": {"userEnteredFormat": cell_format},
                        "fields": fields,
                    }
                }
            )
        return requests

    def _table_data_format_requests(
        self, sheet_id: int, start_row: int, columns: list[str], editable_columns: set[str]
    ) -> list[dict]:
        requests = []
        for idx, column_name in enumerate(columns):
            color = USER_EDITABLE_RGB if column_name in editable_columns else SYSTEM_RGB
            requests.append(
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": start_row,
                            "startColumnIndex": idx,
                            "endColumnIndex": idx + 1,
                        },
                        "cell": {"userEnteredFormat": self._body_format(color)},
                        "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,wrapStrategy,textFormat)",
                    }
                }
            )
        return requests

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

    def _dropdown_from_range_request(self, sheet_id: int, column_index: int, start_row: int, range_formula: str) -> dict:
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
                        "type": "ONE_OF_RANGE",
                        "values": [{"userEnteredValue": range_formula}],
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
    def _header_format_request(sheet_id: int, row_index: int, end_column_index: int | None = None) -> dict:
        range_dict: dict = {"sheetId": sheet_id, "startRowIndex": row_index, "endRowIndex": row_index + 1}
        if end_column_index is not None:
            range_dict["endColumnIndex"] = end_column_index
        return {
            "repeatCell": {
                "range": range_dict,
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": HEADER_RGB,
                        "horizontalAlignment": "LEFT",
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "WRAP",
                        "textFormat": {
                            "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                            "bold": True,
                            "fontSize": 12,
                            "fontFamily": "Arial",
                        },
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,wrapStrategy,textFormat)",
            }
        }

    @staticmethod
    def _body_format(background_color: dict) -> dict:
        return {
            "backgroundColor": background_color,
            "horizontalAlignment": "LEFT",
            "verticalAlignment": "MIDDLE",
            "wrapStrategy": "WRAP",
            "textFormat": {
                "foregroundColor": {"red": 0.09, "green": 0.11, "blue": 0.13},
                "bold": False,
                "fontSize": 12,
                "fontFamily": "Arial",
            },
        }

    @staticmethod
    def _default_cell_style_request(sheet_id: int) -> dict:
        return {
            "repeatCell": {
                "range": {"sheetId": sheet_id},
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": WHITE_RGB,
                        "horizontalAlignment": "LEFT",
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "WRAP",
                        "textFormat": {
                            "foregroundColor": {"red": 0.09, "green": 0.11, "blue": 0.13},
                            "bold": False,
                            "fontSize": 12,
                            "fontFamily": "Arial",
                        },
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,wrapStrategy,textFormat)",
            }
        }

    @staticmethod
    def _row_height_request(sheet_id: int, start_row: int, end_row: int, pixel_size: int) -> dict:
        return {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": start_row,
                    "endIndex": end_row,
                },
                "properties": {"pixelSize": pixel_size},
                "fields": "pixelSize",
            }
        }

    @staticmethod
    def _column_width_request(sheet_id: int, start_col: int, end_col: int, pixel_size: int) -> dict:
        return {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": start_col,
                    "endIndex": end_col,
                },
                "properties": {"pixelSize": pixel_size},
                "fields": "pixelSize",
            }
        }

    def _default_dimension_requests(self, sheet_id: int) -> list[dict]:
        return [
            self._column_width_request(sheet_id, 0, 26, 150),
            self._row_height_request(sheet_id, 0, 1000, 38),
        ]

    def _channel_list_width_requests(self, sheet_id: int) -> list[dict]:
        widths = {
            0: 120,
            1: 150,
            2: 170,
            3: 120,
            4: 220,
            5: 190,
            6: 150,
            14: 120,
            15: 160,
            16: 240,
            17: 160,
            18: 190,
            19: 180,
            20: 130,
            21: 260,
        }
        return [self._column_width_request(sheet_id, index, index + 1, width) for index, width in widths.items()]

    def _channel_sheet_width_requests(self, sheet_id: int) -> list[dict]:
        widths = {
            0: 170,
            1: 260,
            2: 130,
            3: 180,
            4: 100,
            5: 260,
            6: 130,
            7: 320,
            8: 320,
            9: 420,
            10: 140,
            11: 230,
            12: 280,
            13: 150,
            14: 180,
            15: 180,
            16: 220,
            17: 220,
            21: 260,
        }
        return [self._column_width_request(sheet_id, index, index + 1, width) for index, width in widths.items()]

    def _channel_upload_day_helper_requests(self, sheet_id: int, sheet_name: str) -> list[dict]:
        formulas = [
            self._enabled_day_formula(sheet_name, "H", "월요일"),
            self._enabled_day_formula(sheet_name, "I", "화요일"),
            self._enabled_day_formula(sheet_name, "J", "수요일"),
            self._enabled_day_formula(sheet_name, "K", "목요일"),
            self._enabled_day_formula(sheet_name, "L", "금요일"),
            self._enabled_day_formula(sheet_name, "M", "토요일"),
            self._enabled_day_formula(sheet_name, "N", "일요일"),
        ]
        self.client.update_values(f"'{sheet_name}'!AA2:AG2", [formulas])
        return [self._hide_columns_request(sheet_id, 26, 33)]

    @staticmethod
    def _hide_columns_request(sheet_id: int, start_col: int, end_col: int) -> dict:
        return {
            "updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": start_col, "endIndex": end_col},
                "properties": {"hiddenByUser": True},
                "fields": "hiddenByUser",
            }
        }

    @staticmethod
    def _editable_columns_for_sheet(sheet_name: str) -> set[str]:
        if sheet_name == "채널목록":
            return {
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
        if sheet_name == "검수대기":
            return {"user_action"}
        return set()

    @staticmethod
    def _columns_for_sheet(sheet_name: str) -> list[str]:
        if sheet_name == "채널목록":
            return CHANNEL_LIST_COLUMNS
        if sheet_name == "검수대기":
            return REVIEW_COLUMNS
        if sheet_name == "업로드현황":
            return UPLOAD_COLUMNS
        return []

    @staticmethod
    def _display_columns(sheet_name: str, columns: list[str]) -> list[str]:
        if sheet_name == "채널목록":
            labels = CHANNEL_LIST_LABELS
        elif sheet_name == "검수대기":
            labels = REVIEW_LABELS
        elif sheet_name == "업로드현황":
            labels = UPLOAD_LABELS
        elif sheet_name.startswith("채널_"):
            labels = CHANNEL_TOPIC_LABELS
        else:
            labels = {}
        return [labels.get(column, column) for column in columns]

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
    def _default_setting_value(key: str, channel_name: str, sheet_name: str) -> str:
        lookup_columns = {
            "channel_name": ("B", channel_name),
            "automation_enabled": ("D", "OFF"),
            "upload_time": ("O", "20:00"),
            "default_video_length_minutes": ("P", "5"),
            "visual_style_default": ("Q", "따뜻한 수채화 애니메이션풍"),
            "alert_phone_number": ("R", ""),
        }
        if key in lookup_columns:
            column, fallback = lookup_columns[key]
            return SheetsTemplateBuilder._channel_list_lookup_formula(sheet_name, column, fallback)
        if key == "upload_days":
            return SheetsTemplateBuilder._upload_days_formula(sheet_name)
        defaults = {
            "target_age_group": "50대 이상",
            "narration_style": "느리고 친절한 아나운서톤",
            "narration_speed": "느림",
            "subtitle_font": "궁서체",
            "subtitle_color": "검은색",
            "subtitle_outline_color": "노란색",
            "subtitle_position": "하단 중앙",
            "thumbnail_style_prompt": "큰 글씨, 모바일 가독성 우선, 채널별 일관된 레이아웃",
            "caution_prompt": "과장, 허위, 공포 마케팅을 피하고 건강/금융/법률은 단정하지 않는다.",
        }
        return defaults.get(key, "")

    @staticmethod
    def _channel_list_lookup_formula(sheet_name: str, column: str, fallback: str) -> str:
        return f'=IFERROR(INDEX(\'채널목록\'!${column}:${column},MATCH("{sheet_name}",\'채널목록\'!$C:$C,0)),"{fallback}")'

    @staticmethod
    def _upload_days_formula(sheet_name: str) -> str:
        parts = [
            ("H", "월"),
            ("I", "화"),
            ("J", "수"),
            ("K", "목"),
            ("L", "금"),
            ("M", "토"),
            ("N", "일"),
        ]
        day_checks = [
            f'IF(IFERROR(INDEX(\'채널목록\'!${column}:${column},MATCH("{sheet_name}",\'채널목록\'!$C:$C,0)),"OFF")="ON","{label}","")'
            for column, label in parts
        ]
        return f"=TEXTJOIN(\", \",TRUE,{','.join(day_checks)})"

    @staticmethod
    def _enabled_day_formula(sheet_name: str, column: str, day_value: str) -> str:
        return f'=IF(IFERROR(INDEX(\'채널목록\'!${column}:${column},MATCH("{sheet_name}",\'채널목록\'!$C:$C,0)),"OFF")="ON","{day_value}","")'
