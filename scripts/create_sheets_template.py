from __future__ import annotations

from app.google.sheets_client import SheetsClient
from app.google.template import SheetsTemplateBuilder


def main() -> None:
    SheetsTemplateBuilder(SheetsClient()).ensure_template()
    print("Google Sheets template is ready.")


if __name__ == "__main__":
    main()
