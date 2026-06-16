from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler

from app.core.config import get_settings
from app.google.repository import SheetsRepository
from app.google.sheets_client import SheetsClient
from app.pipeline.maintenance import MaintenancePipeline
from app.pipeline.stage1 import Stage1Pipeline
from app.pipeline.stage2 import Stage2Pipeline
from app.pipeline.stage5_publish_sms import Stage5PublishAndSmsPipeline


def run_stage1_job() -> dict[str, int]:
    repo = SheetsRepository(SheetsClient())
    return Stage1Pipeline(repo).run_once()


def run_worker_tick() -> dict[str, dict[str, int]]:
    repo = SheetsRepository(SheetsClient())
    return {
        "stage1": Stage1Pipeline(repo).run_once(),
        "stage2": Stage2Pipeline(repo).run_once(),
        "stage5": Stage5PublishAndSmsPipeline(repo).run_once(),
        "maintenance": MaintenancePipeline(repo).run_once(),
    }


def start_scheduler() -> BackgroundScheduler:
    settings = get_settings()
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        run_worker_tick,
        "interval",
        seconds=settings.scheduler_interval_seconds,
        id="stage1_topic_script_generation",
        max_instances=1,
        replace_existing=True,
    )
    scheduler.start()
    return scheduler
