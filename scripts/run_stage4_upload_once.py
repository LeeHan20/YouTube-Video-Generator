from __future__ import annotations

from app.google.repository import SheetsRepository
from app.google.sheets_client import SheetsClient
from app.pipeline.stage4_upload import Stage4UploadPipeline


def main() -> None:
    stats = Stage4UploadPipeline(SheetsRepository(SheetsClient())).run_once()
    print(stats)


if __name__ == "__main__":
    main()
