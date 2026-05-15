from __future__ import annotations

import argparse

from app.google.repository import SheetsRepository
from app.google.sheets_client import SheetsClient
from app.pipeline.stage1 import Stage1Pipeline


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Generate even when today is not the last upload day.")
    args = parser.parse_args()
    stats = Stage1Pipeline(SheetsRepository(SheetsClient())).run_once(force=args.force)
    print(stats)


if __name__ == "__main__":
    main()
