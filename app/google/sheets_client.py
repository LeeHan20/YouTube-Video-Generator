from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import get_settings


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


@dataclass(frozen=True)
class SheetMeta:
    title: str
    sheet_id: int
    hidden: bool


class SheetsClient:
    def __init__(self, spreadsheet_id: str | None = None) -> None:
        settings = get_settings()
        credentials = Credentials.from_service_account_file(
            settings.google_application_credentials,
            scopes=SCOPES,
        )
        self.spreadsheet_id = spreadsheet_id or settings.google_sheets_spreadsheet_id
        if not self.spreadsheet_id:
            raise ValueError("GOOGLE_SHEETS_SPREADSHEET_ID is required")
        self.service = build("sheets", "v4", credentials=credentials, cache_discovery=False)

    @retry(
        retry=retry_if_exception_type(HttpError),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
    )
    def metadata(self) -> dict[str, SheetMeta]:
        response = self.service.spreadsheets().get(spreadsheetId=self.spreadsheet_id).execute()
        return {
            sheet["properties"]["title"]: SheetMeta(
                title=sheet["properties"]["title"],
                sheet_id=sheet["properties"]["sheetId"],
                hidden=sheet["properties"].get("hidden", False),
            )
            for sheet in response.get("sheets", [])
        }

    @retry(
        retry=retry_if_exception_type(HttpError),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
    )
    def batch_update(self, requests: list[dict[str, Any]]) -> dict[str, Any]:
        if not requests:
            return {}
        return (
            self.service.spreadsheets()
            .batchUpdate(spreadsheetId=self.spreadsheet_id, body={"requests": requests})
            .execute()
        )

    @retry(
        retry=retry_if_exception_type(HttpError),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
    )
    def get_values(self, range_name: str) -> list[list[str]]:
        response = (
            self.service.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet_id, range=range_name)
            .execute()
        )
        return response.get("values", [])

    @retry(
        retry=retry_if_exception_type(HttpError),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
    )
    def update_values(self, range_name: str, values: list[list[Any]]) -> None:
        self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=range_name,
            valueInputOption="USER_ENTERED",
            body={"values": values},
        ).execute()

    @retry(
        retry=retry_if_exception_type(HttpError),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
    )
    def append_values(self, range_name: str, values: Iterable[list[Any]]) -> None:
        self.service.spreadsheets().values().append(
            spreadsheetId=self.spreadsheet_id,
            range=range_name,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": list(values)},
        ).execute()

    def read_records(self, sheet_name: str, header_row: int = 1) -> list[dict[str, str]]:
        values = self.get_values(f"'{sheet_name}'!A{header_row}:ZZ")
        if not values:
            return []
        headers = values[0]
        records: list[dict[str, str]] = []
        for offset, row in enumerate(values[1:], start=header_row + 1):
            padded = row + [""] * (len(headers) - len(row))
            record = {header: padded[index] for index, header in enumerate(headers)}
            record["_row_number"] = str(offset)
            records.append(record)
        return records

    def append_records(self, sheet_name: str, headers: list[str], records: list[dict[str, Any]]) -> None:
        rows = [[record.get(header, "") for header in headers] for record in records]
        if rows:
            self.append_values(f"'{sheet_name}'!A:ZZ", rows)

    def update_row(self, sheet_name: str, row_number: int, headers: list[str], record: dict[str, Any]) -> None:
        row = [[record.get(header, "") for header in headers]]
        self.update_values(f"'{sheet_name}'!A{row_number}:{self._col_letter(len(headers))}{row_number}", row)

    @staticmethod
    def _col_letter(index: int) -> str:
        result = ""
        while index:
            index, remainder = divmod(index - 1, 26)
            result = chr(65 + remainder) + result
        return result
