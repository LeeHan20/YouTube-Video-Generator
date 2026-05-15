from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.auth import require_admin
from app.google.repository import SheetsRepository
from app.google.sheets_client import SheetsClient
from app.google.template import SheetsTemplateBuilder
from app.pipeline.stage1 import Stage1Pipeline


router = APIRouter()


def repository() -> SheetsRepository:
    return SheetsRepository(SheetsClient())


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/admin/sheets/template")
def create_template(_: str = Depends(require_admin)) -> dict[str, str]:
    SheetsTemplateBuilder(SheetsClient()).ensure_template()
    return {"status": "created"}


@router.get("/admin/channels")
def list_channels(_: str = Depends(require_admin), repo: SheetsRepository = Depends(repository)) -> list[dict[str, str]]:
    return [channel.raw for channel in repo.list_channels()]


@router.post("/admin/pipeline/stage1/run")
def run_stage1(
    force: bool = False,
    _: str = Depends(require_admin),
    repo: SheetsRepository = Depends(repository),
) -> dict[str, int]:
    return Stage1Pipeline(repo).run_once(force=force)
