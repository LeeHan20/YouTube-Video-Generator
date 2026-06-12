from __future__ import annotations

import argparse

from app.google.repository import SheetsRepository
from app.google.sheets_client import SheetsClient
from app.pipeline.stage4_upload import Stage4UploadPipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Stage4 YouTube private upload once.")
    parser.add_argument("--force", action="store_true", help="완료된 upload job lock을 초기화하고 실패/업로드중 항목도 다시 시도합니다.")
    parser.add_argument("--test", action="store_true", help="YouTube 업로드나 Sheets 변경 없이 업로드 대상과 로컬 영상 파일만 확인합니다.")
    args = parser.parse_args()
    stats = Stage4UploadPipeline(SheetsRepository(SheetsClient())).run_once(force=args.force, test=args.test)
    print(stats)


if __name__ == "__main__":
    main()
