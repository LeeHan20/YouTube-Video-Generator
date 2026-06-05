from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.pipeline.models import Scene
from app.services.media_sources import MediaSourceService
from app.services.scene_planner import ScenePlanner


def _scene(scene_id: str, media_type: str = "image") -> Scene:
    return Scene(
        scene_id=scene_id,
        title=scene_id,
        narration="아침에 물 한 잔을 마시며 하루를 시작합니다.",
        caption="물 한 잔으로 시작",
        subtitle="물 한 잔으로 시작",
        visual_prompt="warm health lifestyle",
        crawl_prompt="water glass",
        generation_prompt="warm health lifestyle scene",
        media_type=media_type,
        image_keywords=["water", "morning"],
    )


class OpeningVideoAssetTest(unittest.TestCase):
    def test_ai_plan_cannot_downgrade_first_two_scenes_to_images(self) -> None:
        scenes = [_scene("scene_001"), _scene("scene_002"), _scene("scene_003")]

        enforced = ScenePlanner._enforce_required_video_opening(scenes)

        self.assertEqual(enforced[0].media_type, "video")
        self.assertEqual(enforced[1].media_type, "video")
        self.assertEqual(enforced[2].media_type, "image")
        self.assertIn("영상 클립", enforced[0].generation_prompt)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required for local video fallback")
    def test_crawl_video_falls_back_to_renderable_mp4_asset(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = MediaSourceService()
            service.root = Path(tmpdir)
            service.public_base_url = "http://localhost:8000"
            scene = _scene("scene_001", media_type="video")

            with patch.object(service, "crawl_candidates", return_value=[]), patch.object(
                service, "_improve_generation_prompt", return_value="local fallback video prompt"
            ):
                asset = service.create_asset("topic_001", scene, source_mode="crawl_video")

            self.assertEqual(asset.source, "local_generated_video")
            self.assertTrue(asset.asset_url.endswith(".mp4"))
            self.assertIsNotNone(asset.local_path)
            self.assertTrue(asset.local_path and asset.local_path.exists())
            self.assertGreater(asset.local_path.stat().st_size, 1024)


if __name__ == "__main__":
    unittest.main()
