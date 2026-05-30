from __future__ import annotations

import argparse

from app.google.repository import SheetsRepository
from app.google.sheets_client import SheetsClient
from app.google.template import SheetsTemplateBuilder
from scripts.repair_sheet_ui_format import main as repair_sheet_ui_format


RESET_TMP_SHEET = "__RESET_TMP__"


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete all tabs in the configured spreadsheet and rebuild the default template.")
    parser.add_argument(
        "--enable-channel",
        default="건강정보",
        help="Template reset 후 Stage1 테스트를 위해 자동화 ON으로 바꿀 채널명. 빈 문자열이면 아무 채널도 켜지 않습니다.",
    )
    args = parser.parse_args()

    client = SheetsClient()
    _delete_all_existing_sheets(client)
    SheetsTemplateBuilder(client).ensure_template()
    _delete_tmp_sheet(client)

    enabled_channel = ""
    if args.enable_channel:
        enabled_channel = _enable_channel(args.enable_channel)

    repair_sheet_ui_format()
    print({"reset": "done", "enabled_channel": enabled_channel or None})


def _delete_all_existing_sheets(client: SheetsClient) -> None:
    metadata = client.metadata()
    requests = []
    if RESET_TMP_SHEET not in metadata:
        requests.append({"addSheet": {"properties": {"title": RESET_TMP_SHEET}}})
        client.batch_update(requests)
        metadata = client.metadata()

    tmp_sheet_id = metadata[RESET_TMP_SHEET].sheet_id
    delete_requests = [
        {"deleteSheet": {"sheetId": meta.sheet_id}}
        for name, meta in metadata.items()
        if meta.sheet_id != tmp_sheet_id
    ]
    client.batch_update(delete_requests)


def _delete_tmp_sheet(client: SheetsClient) -> None:
    metadata = client.metadata()
    tmp = metadata.get(RESET_TMP_SHEET)
    if tmp and len(metadata) > 1:
        client.batch_update([{"deleteSheet": {"sheetId": tmp.sheet_id}}])


def _enable_channel(channel_name: str) -> str:
    repo = SheetsRepository(SheetsClient())
    for channel in repo.list_channels():
        if channel.channel_name == channel_name:
            repo.update_channel(channel, {"automation_enabled": "ON", "status": "EMPTY", "error_message": ""})
            return channel.channel_name
    raise RuntimeError(f"채널을 찾지 못했습니다: {channel_name}")


if __name__ == "__main__":
    main()
