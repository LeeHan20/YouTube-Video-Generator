from __future__ import annotations

import argparse

from app.google.sheets_client import SheetsClient
from app.google.template import SheetsTemplateBuilder
from scripts.reset_all_sheets import _delete_all_existing_sheets, _delete_tmp_sheet
from scripts.repair_sheet_ui_format import main as repair_sheet_ui_format


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or rebuild the Google Sheets template.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="기존 데이터는 유지한 채 템플릿 탭, 헤더, 드롭다운, 서식을 다시 적용합니다.",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="기존 시트 탭을 모두 삭제하고 처음부터 템플릿을 다시 생성합니다. --force를 포함합니다.",
    )
    args = parser.parse_args()

    client = SheetsClient()
    if args.test:
        _delete_all_existing_sheets(client)

    SheetsTemplateBuilder(client).ensure_template()

    if args.test:
        _delete_tmp_sheet(client)

    if args.force or args.test:
        repair_sheet_ui_format()

    print(
        {
            "status": "created",
            "force": bool(args.force or args.test),
            "test_reset": bool(args.test),
        }
    )


if __name__ == "__main__":
    main()
