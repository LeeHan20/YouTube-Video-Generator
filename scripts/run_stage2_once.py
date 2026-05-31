from __future__ import annotations

import argparse

from app.google.repository import SheetsRepository
from app.google.sheets_client import SheetsClient
from app.pipeline.stage2 import Stage2Pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Stage2 video draft generation once.")
    parser.add_argument("--force", action="store_true", help="상태/날짜 조건과 완료 job 여부를 무시하고 선택된 소주제를 실행합니다.")
    parser.add_argument("--test", action="store_true", help="기존 로컬 산출물을 삭제한 뒤 새 산출물을 생성합니다. --force가 함께 적용됩니다.")
    args = parser.parse_args()
    stats = Stage2Pipeline(SheetsRepository(SheetsClient())).run_once(force=args.force or args.test, reset_outputs=args.test)
    print(stats)


if __name__ == "__main__":
    main()
