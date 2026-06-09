from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.pipeline.models import Scene
from app.services.caption_alignment import CaptionAlignmentService


def _scene() -> Scene:
    return Scene(
        scene_id="scene_001",
        title="장면 1",
        narration="침실은 어둡고 조용하며 시원하게 유지하는 것이 좋습니다.",
        caption="침실은 어둡고 조용하게",
        subtitle="침실은 어둡고 조용하게",
        visual_prompt="bedroom",
        start_seconds=10.0,
        duration_seconds=8.0,
    )


class CaptionAlignmentServiceTest(unittest.TestCase):
    def test_aeneas_syncmap_is_converted_to_caption_segments(self) -> None:
        service = CaptionAlignmentService()
        chunks = ["침실은 어둡고 조용하며", "좋습니다."]

        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "scene.wav"
            audio_path.write_bytes(b"fake wav")

            def fake_run(command, **kwargs):
                del kwargs
                output_path = Path(command[-2])
                output_path.write_text(
                    json.dumps(
                        {
                            "fragments": [
                                {"begin": "0.000", "end": "3.250", "lines": [chunks[0]]},
                                {"begin": "3.250", "end": "4.100", "lines": [chunks[1]]},
                            ]
                        }
                    ),
                    encoding="utf-8",
                )

            with patch.object(service, "_aeneas_python_path", return_value="/fake/python"), patch(
                "app.services.caption_alignment.subprocess.run", side_effect=fake_run
            ):
                segments = service.align(_scene(), audio_path, chunks)

        self.assertEqual(service.last_provider, "aeneas")
        self.assertEqual([segment.text for segment in segments or []], chunks)
        self.assertEqual((segments or [])[0].start_seconds, 10.0)
        self.assertEqual((segments or [])[0].end_seconds, 13.25)
        self.assertEqual((segments or [])[1].start_seconds, 13.25)
        self.assertEqual((segments or [])[1].end_seconds, 14.1)


if __name__ == "__main__":
    unittest.main()
