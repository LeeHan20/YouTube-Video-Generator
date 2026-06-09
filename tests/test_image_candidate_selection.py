from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.pipeline.models import Scene
from app.services.image_usage import (
    calculate_duplicate_penalty,
    calculate_final_score,
    calculate_used_penalty,
    load_used_images,
    update_used_images,
)
from app.services.media_sources import MediaSourceService


class FakeStreamResponse:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self):
        yield from self.chunks


class FakeDownloadClient:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.calls: list[dict] = []

    def stream(self, method: str, url: str, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return FakeStreamResponse(self.chunks)


def _scene() -> Scene:
    return Scene(
        scene_id="scene_001",
        title="healthy food",
        narration="건강한 식사를 준비합니다.",
        caption="건강한 식사",
        subtitle="건강한 식사",
        visual_prompt="healthy food on a table",
        crawl_prompt="healthy food",
        generation_prompt="healthy food on a table",
        image_keywords=["healthy", "food"],
    )


class ImageCandidateSelectionTest(unittest.TestCase):
    def test_final_score_never_negative(self) -> None:
        final_score = calculate_final_score(base_score=20, used_penalty=60, duplicate_penalty=80)
        self.assertEqual(final_score, 0)

    def test_used_image_gets_penalty(self) -> None:
        penalty = calculate_used_penalty(3)
        self.assertGreater(penalty, 0)

    def test_same_video_duplicate_gets_large_penalty(self) -> None:
        penalty = calculate_duplicate_penalty("abc", {"abc"})
        self.assertGreaterEqual(penalty, 80)

    def test_candidates_return_at_least_four(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = MediaSourceService()
            service.root = Path(tmpdir)
            service.public_base_url = "http://localhost:8000"
            service.image_generation.settings.gemini_api_key = ""

            with (
                patch.object(service, "_crawl_openverse", return_value=[]),
                patch.object(service, "_crawl_google_images", return_value=[]),
                patch.object(service, "_crawl_unsplash_images", return_value=[]),
                patch.object(service, "_crawl_pexels_images", return_value=[]),
                patch.object(service, "_crawl_pixabay_images", return_value=[]),
                patch("httpx.Client.get", side_effect=Exception("offline")),
            ):
                candidates = service.crawl_candidates("topic_001", _scene(), source_mode="crawl_image", limit=4)

            self.assertGreaterEqual(len(candidates), 4)
            self.assertTrue(all(0 <= candidate.final_score <= 100 for candidate in candidates))

    def test_used_images_json_is_updated_after_final_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "used_images.json"
            selected_image = {"image_id": "abc", "url": "https://example.com/a.jpg", "source": "flickr"}

            update_used_images(selected_image, video_id="video_001", scene_id="scene_01", topic="test", path=path)

            data = load_used_images(path)
            self.assertGreaterEqual(data["images"]["abc"]["used_count"], 1)

    def test_asset_download_uses_bounded_stream_with_download_headers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            service = MediaSourceService()
            service.root = Path(tmpdir)
            client = FakeDownloadClient([b"x" * 2048])

            path = service._download_asset(
                client,
                "topic_001",
                "scene_001",
                "https://upload.wikimedia.org/wikipedia/commons/example.jpg",
                "image/jpeg",
            )

            self.assertTrue(path.exists())
            self.assertEqual(path.stat().st_size, 2048)
            self.assertEqual(client.calls[0]["method"], "GET")
            self.assertIn("timeout", client.calls[0])
            self.assertEqual(client.calls[0]["headers"]["Referer"], "https://commons.wikimedia.org/")


if __name__ == "__main__":
    unittest.main()
