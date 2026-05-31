from __future__ import annotations

from pydantic import BaseModel, Field


class CaptionSegment(BaseModel):
    caption_id: str
    scene_id: str
    text: str
    start_seconds: float = 0
    end_seconds: float = 0
    duration_seconds: float = 0


class Scene(BaseModel):
    scene_id: str
    title: str
    narration: str
    caption: str = ""
    subtitle: str
    visual_prompt: str
    image_keywords: list[str] = Field(default_factory=list)
    start_seconds: float = 0
    start_time: float = 0
    end_time: float = 0
    duration_seconds: float = 8
    audio_duration_seconds: float = 0
    asset_url: str = ""
    selected_image_url: str = ""
    selected_image_path: str = ""
    image_hash: str = ""
    asset_source: str = "placeholder"
    asset_credit: str = ""
    asset_license: str = ""
    tts_audio_path: str = ""
    image_candidates: list[dict] = Field(default_factory=list)
    selected_image_candidate: str = ""
    caption_segments: list[CaptionSegment] = Field(default_factory=list)


class RenderManifest(BaseModel):
    topic_id: str
    channel_id: str
    title: str
    video_length_minutes: int
    visual_style: str
    scenes: list[Scene]
    video_url: str = ""
    subtitle_url: str = ""
    render_id: str = ""
    validation: dict = Field(default_factory=dict)
    created_at: str
