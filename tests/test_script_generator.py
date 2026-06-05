from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services.ai_client import AiResponse
from app.services.script_generator import ScriptGenerator


class FakeAiClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.prompts: list[str] = []

    def generate_text(self, prompt: str, system: str = "", max_tokens: int = 2048) -> AiResponse:
        del system, max_tokens
        self.prompts.append(prompt)
        return AiResponse(text=self.responses.pop(0), provider="fake", model="fake")


class ScriptGeneratorTest(unittest.TestCase):
    def test_short_ai_script_is_expanded_to_minimum_target_size(self) -> None:
        short_script = "짧은 문장입니다. " * 20
        expanded_script = "충분히 확장된 나레이션 문장입니다. " * 100
        client = FakeAiClient([short_script, expanded_script])

        with patch("app.services.script_generator.get_ai_client", return_value=client):
            script = ScriptGenerator().generate("수면 위생", "숙면을 돕는 생활 습관", 5)

        self.assertEqual(script, expanded_script.strip())
        self.assertEqual(len(client.prompts), 2)
        self.assertIn("현재 대본 길이", client.prompts[1])
        self.assertGreaterEqual(
            ScriptGenerator._spoken_char_count(script),
            ScriptGenerator._min_target_chars(5),
        )

    def test_full_size_ai_script_does_not_request_expansion(self) -> None:
        full_script = "충분한 길이의 나레이션 문장입니다. " * 100
        client = FakeAiClient([full_script])

        with patch("app.services.script_generator.get_ai_client", return_value=client):
            script = ScriptGenerator().generate("수분 섭취", "갈증 해소법", 5)

        self.assertEqual(script, full_script.strip())
        self.assertEqual(len(client.prompts), 1)


if __name__ == "__main__":
    unittest.main()
