from __future__ import annotations

from pydantic import BaseModel, Field


class Scene(BaseModel):
    scene_id: str
    title: str
    narration: str
    subtitle: str
    visual_prompt: str
    start_seconds: float = 0
    duration_seconds: float = 8
    asset_url: str = ""
    asset_source: str = "placeholder"
    asset_credit: str = ""
    asset_license: str = ""
    image_candidates: list[dict] = Field(default_factory=list)
    selected_image_candidate: str = ""


class RenderManifest(BaseModel):
    topic_id: str
    channel_id: str
    title: str
    video_length_minutes: int
    visual_style: str
    scenes: list[Scene]
    video_url: str = ""
    subtitle_url: str = ""
    created_at: str
