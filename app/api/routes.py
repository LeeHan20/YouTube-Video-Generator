from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.auth import require_admin
from app.core.time import iso_now
from app.google.repository import SheetsRepository
from app.google.sheets_client import SheetsClient
from app.google.template import SheetsTemplateBuilder
from app.pipeline.stage1 import Stage1Pipeline
from app.pipeline.stage2 import Stage2Pipeline
from app.pipeline.stage4_upload import Stage4UploadPipeline
from app.pipeline.stage5_publish_sms import Stage5PublishAndSmsPipeline
from app.youtube.oauth import YouTubeOAuthService


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


@router.post("/admin/pipeline/stage2/run")
def run_stage2(
    _: str = Depends(require_admin),
    repo: SheetsRepository = Depends(repository),
) -> dict[str, int]:
    return Stage2Pipeline(repo).run_once()


@router.get("/admin/youtube/oauth/start/{channel_id}")
def youtube_oauth_start(channel_id: str, _: str = Depends(require_admin)) -> dict[str, str]:
    return {"authorization_url": YouTubeOAuthService().authorization_url(channel_id)}


@router.get("/admin/youtube/oauth/callback")
def youtube_oauth_callback(
    request: Request,
    state: str,
    _: str = Depends(require_admin),
    repo: SheetsRepository = Depends(repository),
) -> dict[str, str]:
    YouTubeOAuthService().save_callback(str(request.url), state)
    for channel in repo.list_channels():
        if channel.channel_id == state:
            repo.update_channel(channel, {"oauth_connected": "연결됨", "last_checked_at": iso_now()})
            break
    return {"status": "connected", "channel_id": state}


@router.post("/admin/pipeline/stage4/upload/run")
def run_stage4_upload(
    _: str = Depends(require_admin),
    repo: SheetsRepository = Depends(repository),
) -> dict[str, int]:
    return Stage4UploadPipeline(repo).run_once()


@router.post("/admin/pipeline/stage5/run")
def run_stage5(
    _: str = Depends(require_admin),
    repo: SheetsRepository = Depends(repository),
) -> dict[str, int]:
    return Stage5PublishAndSmsPipeline(repo).run_once()
