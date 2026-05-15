from __future__ import annotations

import time

from app.core.config import get_settings
from app.scheduler.runner import run_stage1_job


def main() -> None:
    settings = get_settings()
    while True:
        stats = run_stage1_job()
        print(f"stage1 stats={stats}", flush=True)
        time.sleep(settings.scheduler_interval_seconds)


if __name__ == "__main__":
    main()
