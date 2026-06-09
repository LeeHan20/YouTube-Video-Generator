from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import imagehash
except Exception:  # pragma: no cover - optional dependency
    imagehash = None

from PIL import Image


USED_IMAGES_PATH = Path("data/used_images.json")


def make_image_id(url: str) -> str:
    return hashlib.sha256((url or "").encode("utf-8")).hexdigest()


def calculate_final_score(base_score: int | float, used_penalty: int = 0, duplicate_penalty: int = 0, similar_image_penalty: int = 0) -> int:
    final_score = int(round(base_score - used_penalty - duplicate_penalty - similar_image_penalty))
    return max(0, min(100, final_score))


def calculate_used_penalty(used_count: int) -> int:
    return min(max(0, used_count) * 15, 60)


def calculate_duplicate_penalty(image_id: str, current_video_used_image_ids: set[str] | None = None) -> int:
    return 80 if image_id and image_id in (current_video_used_image_ids or set()) else 0


def calculate_phash(image_path: str | Path) -> str:
    if imagehash is None:
        return ""
    try:
        with Image.open(image_path) as image:
            return str(imagehash.phash(image))
    except Exception:
        return ""


def phash_distance(left: str, right: str) -> int | None:
    if not left or not right:
        return None
    if imagehash is not None:
        try:
            return int(imagehash.hex_to_hash(left) - imagehash.hex_to_hash(right))
        except Exception:
            pass
    try:
        return bin(int(left, 16) ^ int(right, 16)).count("1")
    except Exception:
        return None


def calculate_similar_image_penalty(candidate_phash: str, used_images: dict[str, Any], threshold: int = 6) -> int:
    if not candidate_phash:
        return 0
    for item in used_images.get("images", {}).values():
        distance = phash_distance(candidate_phash, item.get("phash", ""))
        if distance is not None and distance < threshold:
            return 40
    return 0


def load_used_images(path: Path = USED_IMAGES_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"images": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"images": {}}
    if not isinstance(data, dict):
        return {"images": {}}
    images = data.get("images")
    if not isinstance(images, dict):
        data["images"] = {}
    return data


def update_used_images(
    selected_image: dict[str, Any],
    video_id: str,
    scene_id: str,
    topic: str = "",
    path: Path = USED_IMAGES_PATH,
) -> dict[str, Any]:
    image_url = selected_image.get("url") or selected_image.get("asset_url") or ""
    image_id = selected_image.get("image_id") or make_image_id(image_url)
    if not image_id:
        return load_used_images(path)

    data = load_used_images(path)
    now = datetime.now().isoformat(timespec="seconds")
    entry = data["images"].setdefault(
        image_id,
        {
            "url": image_url,
            "source": selected_image.get("source", "unknown"),
            "used_count": 0,
            "first_used_at": now,
            "last_used_at": now,
            "used_in": [],
        },
    )
    entry["url"] = entry.get("url") or image_url
    entry["source"] = entry.get("source") or selected_image.get("source", "unknown")
    if selected_image.get("phash"):
        entry["phash"] = selected_image["phash"]
    entry["used_count"] = int(entry.get("used_count") or 0) + 1
    entry["last_used_at"] = now
    entry.setdefault("first_used_at", now)
    entry.setdefault("used_in", []).append({"video_id": video_id, "scene_id": scene_id, "topic": topic})

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data
