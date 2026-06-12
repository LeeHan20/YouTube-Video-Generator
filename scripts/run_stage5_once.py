from __future__ import annotations

import argparse

from app.google.repository import SheetsRepository
from app.google.sheets_client import SheetsClient
from app.pipeline.stage5_publish_sms import Stage5PublishAndSmsPipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Stage5 publish/SMS once.")
    parser.add_argument("--force", action="store_true", help="공개 예약시간과 SMS 요일 제한을 무시하고 대상 항목을 실행합니다.")
    parser.add_argument("--test", action="store_true", help="YouTube 공개 전환, SMS 발송, Sheets 변경 없이 대상만 확인합니다.")
    args = parser.parse_args()
    stats = Stage5PublishAndSmsPipeline(SheetsRepository(SheetsClient())).run_once(force=args.force, test=args.test)
    print(stats)


if __name__ == "__main__":
    main()
