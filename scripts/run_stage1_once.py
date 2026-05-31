from __future__ import annotations

import argparse

from app.google.repository import SheetsRepository
from app.google.sheets_client import SheetsClient
from app.pipeline.stage1 import Stage1Pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Stage1 topic/script generation once.")
    parser.add_argument("--force", action="store_true", help="날짜/주차 조건과 완료 job 여부를 무시하고 자동화 ON 채널의 소주제를 생성합니다.")
    parser.add_argument("--test", action="store_true", help="기존 Stage1 산출물을 삭제한 뒤 새 소주제를 생성합니다. --force가 함께 적용됩니다.")
    args = parser.parse_args()
    stats = Stage1Pipeline(SheetsRepository(SheetsClient())).run_once(force=args.force or args.test, reset_outputs=args.test)
    print(stats)


if __name__ == "__main__":
    main()
