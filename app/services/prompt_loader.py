from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from pathlib import Path


PROMPT_MARKER = "---PROMPT---"


def render_prompt(name: str, **values: object) -> str:
    template = load_prompt(name)
    safe_values = defaultdict(str, {key: str(value) for key, value in values.items()})
    return template.format_map(safe_values).strip()


@lru_cache
def load_prompt(name: str) -> str:
    root = Path(__file__).resolve().parents[2] / "sys_prompts"
    path = root / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    text = path.read_text(encoding="utf-8")
    if PROMPT_MARKER in text:
        text = text.split(PROMPT_MARKER, 1)[1]
    return text.strip()
