from __future__ import annotations

from app.google.repository import SheetsRepository
from app.google.sheets_client import SheetsClient
from app.pipeline.stage2 import Stage2Pipeline


def main() -> None:
    stats = Stage2Pipeline(SheetsRepository(SheetsClient())).run_once()
    print(stats)


if __name__ == "__main__":
    main()
