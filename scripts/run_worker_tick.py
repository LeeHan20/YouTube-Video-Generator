from __future__ import annotations

from app.scheduler.runner import run_worker_tick


def main() -> None:
    print(run_worker_tick())


if __name__ == "__main__":
    main()
