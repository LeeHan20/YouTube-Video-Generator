from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx
from PIL import Image

from app.core.config import get_settings
from app.pipeline.models import Scene
from app.services.ai_client import get_ai_client
from app.services.image_generation import ImageGenerationService
from app.services.prompt_loader import render_prompt


@dataclass
class MediaAsset:
    asset_url: str
    source: str
    credit: str
    license: str
    prompt: str
    local_path: Path | None = None
    image_hash: str = ""


@dataclass
class MediaCandidate(MediaAsset):
    candidate_id: str = ""
    score: int = 0
    reason: str = ""
    width: int = 0
    height: int = 0

    def as_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "asset_url": self.asset_url,
            "source": self.source,
            "credit": self.credit,
            "license": self.license,
            "prompt": self.prompt,
            "score": self.score,
            "reason": self.reason,
            "width": self.width,
            "height": self.height,
            "image_hash": self.image_hash,
        }


class MediaSourceService:
    def __init__(self) -> None:
        settings = get_settings()
        self.root = settings.local_storage_dir
        self.public_base_url = settings.public_base_url.rstrip("/")
        self.settings = settings
        self.root.mkdir(parents=True, exist_ok=True)
        self.image_generation = ImageGenerationService()

    def create_asset(
        self,
        topic_id: str,
        scene: Scene,
        source_mode: str = "auto",
        user_instruction: str = "",
        excluded_asset_urls: set[str] | None = None,
        excluded_hashes: set[str] | None = None,
    ) -> MediaAsset:
        mode = (source_mode or self.settings.media_source_mode or "crawl_image").lower()
        if mode == "auto":
            mode = self._auto_mode(scene)
        if mode in {"crawl", "crawl_image", "crawl_video"}:
            candidates = self.crawl_candidates(
                topic_id,
                scene,
                mode,
                user_instruction,
                limit=1,
                excluded_asset_urls=excluded_asset_urls,
                excluded_hashes=excluded_hashes,
            )
            if candidates:
                return candidates[0]
            return self._crawl_failed_placeholder(topic_id, scene, user_instruction)
        if mode in {"ai", "ai_image", "generate"}:
            return self._generate_ai_placeholder(topic_id, scene, user_instruction)
        return self._crawl_failed_placeholder(topic_id, scene, f"지원하지 않는 소스 모드: {source_mode}")

    def crawl_candidates(
        self,
        topic_id: str,
        scene: Scene,
        source_mode: str = "crawl_image",
        user_instruction: str = "",
        limit: int = 4,
        excluded_asset_urls: set[str] | None = None,
        excluded_hashes: set[str] | None = None,
    ) -> list[MediaCandidate]:
        mode = "crawl_video" if source_mode == "crawl_video" else "crawl_image"
        candidates: list[MediaCandidate] = []
        excluded_asset_urls = excluded_asset_urls or set()
        excluded_hashes = excluded_hashes or set()
        api_url = "https://commons.wikimedia.org/w/api.php"
        try:
            headers = {"User-Agent": "Auto2YouTubeAutomation/0.1 (local review tool; contact: admin@example.com)"}
            with httpx.Client(timeout=self.settings.media_crawl_timeout_seconds, follow_redirects=True, headers=headers) as client:
                seen_urls: set[str] = set(excluded_asset_urls)
                for query in self._search_queries(scene, user_instruction):
                    params = {
                        "action": "query",
                        "generator": "search",
                        "gsrsearch": query,
                        "gsrnamespace": "6",
                        "gsrlimit": str(max(self.settings.media_crawl_max_results, limit * 4, 12)),
                        "prop": "imageinfo",
                        "iiprop": "url|mime|extmetadata|size",
                        "format": "json",
                    }
                    response = client.get(api_url, params=params)
                    response.raise_for_status()
                    pages = response.json().get("query", {}).get("pages", {})
                    for page in pages.values():
                        if len(candidates) >= limit:
                            break
                        info = (page.get("imageinfo") or [{}])[0]
                        mime = info.get("mime", "")
                        if mode == "crawl_video" and not mime.startswith("video/"):
                            continue
                        if mode != "crawl_video" and not mime.startswith("image/"):
                            continue
                        if mode != "crawl_video" and mime not in {"image/jpeg", "image/png", "image/webp", "image/svg+xml", "image/gif"}:
                            continue
                        license_name = self._metadata_value(info, "LicenseShortName").lower()
                        if not self._allowed_license(license_name):
                            continue
                        asset_url = info.get("url") or info.get("thumburl")
                        if not asset_url or asset_url in seen_urls:
                            continue
                        seen_urls.add(asset_url)
                        local_path = self._download_asset(client, topic_id, scene.scene_id, asset_url, mime)
                        image_hash = self._file_hash(local_path)
                        if image_hash and image_hash in excluded_hashes:
                            local_path.unlink(missing_ok=True)
                            continue
                        width, height = self._dimensions(local_path, info)
                        score, reason = self._score_candidate(scene, page, info, license_name, width, height, user_instruction)
                        digest = hashlib.sha1(f"{asset_url}:{score}".encode("utf-8")).hexdigest()[:12]
                        candidates.append(
                            MediaCandidate(
                                candidate_id=f"{scene.scene_id}_{digest}",
                                asset_url=self._url_for(local_path),
                                source="crawl_video" if mime.startswith("video/") else "crawl_image",
                                credit=self._credit(page, info),
                                license=license_name or "wikimedia commons",
                                prompt=query,
                                local_path=local_path,
                                image_hash=image_hash,
                                score=score,
                                reason=reason,
                                width=width,
                                height=height,
                            )
                        )
                    if len(candidates) >= limit:
                        break
        except Exception:
            candidates = []
        candidates.sort(key=lambda item: item.score, reverse=True)
        if len(candidates) < limit and mode == "crawl_image":
            candidates.extend(
                self._crawl_openverse(
                    topic_id,
                    scene,
                    user_instruction,
                    seen={item.asset_url for item in candidates},
                    limit=limit - len(candidates),
                    excluded_hashes=excluded_hashes,
                )
            )
            candidates.sort(key=lambda item: item.score, reverse=True)
        return candidates[:limit]

    def _auto_mode(self, scene: Scene) -> str:
        del scene
        return "crawl_image"

    def _crawl_failed_placeholder(self, topic_id: str, scene: Scene, user_instruction: str = "") -> MediaAsset:
        topic_dir = self.root / "topics" / topic_id / "assets"
        topic_dir.mkdir(parents=True, exist_ok=True)
        path = topic_dir / f"{scene.scene_id}_crawl_failed.html"
        message = (
            "<!doctype html><meta charset='utf-8'>"
            "<body style='margin:0;display:grid;place-items:center;width:100vw;height:100vh;"
            "background:#eef4ea;color:#202820;font-family:sans-serif;text-align:center'>"
            f"<div><h1>{scene.title}</h1><p>크롤링 이미지 후보를 찾지 못했습니다.</p>"
            f"<p>{user_instruction}</p></div></body>"
        )
        path.write_text(message, encoding="utf-8")
        return MediaAsset(
            asset_url=self._url_for(path),
            source="crawl_image_failed",
            credit="No crawling candidate found",
            license="none",
            prompt=self._search_query(scene, user_instruction),
            local_path=path,
        )

    def _generate_ai_placeholder(self, topic_id: str, scene: Scene, user_instruction: str = "", fallback_reason: str = "") -> MediaAsset:
        improved_prompt = self._improve_generation_prompt(scene, user_instruction)
        topic_dir = self.root / "topics" / topic_id / "assets"
        topic_dir.mkdir(parents=True, exist_ok=True)
        path = topic_dir / f"{scene.scene_id}_ai.png"
        generated_path, source = self.image_generation.generate(improved_prompt, path, title=scene.title)
        return MediaAsset(
            asset_url=self._url_for(generated_path),
            source=source,
            credit="AI generated image" if source == "gemini_image" else "Local generated image",
            license="generated",
            prompt=improved_prompt,
            local_path=generated_path,
            image_hash=self._file_hash(generated_path),
        )

    def _download_asset(self, client: httpx.Client, topic_id: str, scene_id: str, url: str, mime: str) -> Path:
        suffix = self._suffix(url, mime)
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
        path = self.root / "topics" / topic_id / "assets" / f"{scene_id}_crawl_{digest}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        response = client.get(url)
        response.raise_for_status()
        path.write_bytes(response.content)
        return path

    def _crawl_openverse(
        self,
        topic_id: str,
        scene: Scene,
        user_instruction: str,
        seen: set[str],
        limit: int = 4,
        excluded_hashes: set[str] | None = None,
    ) -> list[MediaCandidate]:
        candidates: list[MediaCandidate] = []
        excluded_hashes = excluded_hashes or set()
        if limit <= 0:
            return candidates
        headers = {"User-Agent": "Auto2YouTubeAutomation/0.1 (local review tool; contact: admin@example.com)"}
        try:
            with httpx.Client(timeout=self.settings.media_crawl_timeout_seconds, follow_redirects=True, headers=headers) as client:
                for query in self._search_queries(scene, user_instruction):
                    response = client.get(
                        "https://api.openverse.org/v1/images/",
                        params={"q": query, "page_size": 12, "mature": "false"},
                    )
                    response.raise_for_status()
                    for item in response.json().get("results", []):
                        if len(candidates) >= limit:
                            break
                        source_url = item.get("url") or item.get("thumbnail")
                        if not source_url or source_url in seen:
                            continue
                        mime = self._mime_from_url(source_url)
                        if mime not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
                            continue
                        try:
                            local_path = self._download_asset(client, topic_id, scene.scene_id, source_url, mime)
                        except Exception:
                            thumb = item.get("thumbnail")
                            if not thumb or thumb in seen:
                                continue
                            try:
                                local_path = self._download_asset(client, topic_id, scene.scene_id, thumb, mime)
                            except Exception:
                                continue
                        seen.add(source_url)
                        image_hash = self._file_hash(local_path)
                        if image_hash and image_hash in excluded_hashes:
                            local_path.unlink(missing_ok=True)
                            continue
                        width, height = self._dimensions(local_path, {"width": item.get("width"), "height": item.get("height")})
                        info = {"extmetadata": {"ImageDescription": {"value": item.get("title", "")}}}
                        page = {"title": item.get("title", "")}
                        license_name = "cc-" + str(item.get("license", "")).replace("cc-", "")
                        if not self._allowed_license(license_name):
                            continue
                        score, reason = self._score_candidate(scene, page, info, license_name, width, height, user_instruction)
                        digest = hashlib.sha1(f"{source_url}:{score}".encode("utf-8")).hexdigest()[:12]
                        creator = item.get("creator") or "Openverse"
                        candidates.append(
                            MediaCandidate(
                                candidate_id=f"{scene.scene_id}_{digest}",
                                asset_url=self._url_for(local_path),
                                source="crawl_image",
                                credit=" / ".join(part for part in [item.get("title"), creator, item.get("foreign_landing_url")] if part),
                                license=f"{item.get('license', 'cc')} {item.get('license_version', '')}".strip(),
                                prompt=query,
                                local_path=local_path,
                                image_hash=image_hash,
                                score=score,
                                reason=f"{reason} · Openverse 공개 라이선스 후보",
                                width=width,
                                height=height,
                            )
                        )
                    if len(candidates) >= limit:
                        break
        except Exception:
            return candidates
        return candidates

    def _improve_generation_prompt(self, scene: Scene, user_instruction: str) -> str:
        prompt = render_prompt(
            "image_generation_prompt_improvement",
            scene_visual_prompt=scene.visual_prompt,
            user_instruction=user_instruction or "없음",
        )
        try:
            text = get_ai_client().generate_text(prompt, max_tokens=900).text.strip()
            if text and "AI API 키가 설정되지 않아" not in text:
                return text
        except Exception:
            pass
        extra = f" 사용자 요청: {user_instruction}." if user_instruction else ""
        return f"{scene.visual_prompt}{extra} 원본 생성 이미지, 큰 자막 공간, 모바일 가독성, 부드러운 조명."

    @staticmethod
    def _search_query(scene: Scene, user_instruction: str) -> str:
        keyword_text = " ".join(scene.image_keywords or [])
        text = f"{keyword_text} {scene.caption} {scene.narration} {user_instruction}".strip()
        text = re.sub(r"[^\w\s가-힣]", " ", text)
        words = [word for word in text.split() if len(word) > 1][:8]
        return " ".join(words) or "health lifestyle"

    def _search_queries(self, scene: Scene, user_instruction: str) -> list[str]:
        primary = self._search_query(scene, user_instruction)
        keyword_text = " ".join(scene.image_keywords or [])
        text = f"{keyword_text} {scene.caption} {scene.subtitle} {scene.narration} {scene.visual_prompt}"
        keyword_query = self._semantic_keyword_query(text)
        fallback = "healthy lifestyle senior people"
        if any(keyword in text for keyword in ["역사", "문화", "전쟁", "왕", "조선"]):
            fallback = "history education illustration"
        elif any(keyword in text for keyword in ["가족", "생활", "상식", "집"]):
            fallback = "family home lifestyle"
        elif any(keyword in text for keyword in ["건강", "운동", "병원", "혈압", "식사"]):
            fallback = "senior walking"
        queries = [primary, keyword_query, fallback, "healthy food vegetables", "older people walking", "public domain health illustration"]
        unique = []
        for query in queries:
            if query and query not in unique:
                unique.append(query)
        return unique

    @staticmethod
    def _semantic_keyword_query(text: str) -> str:
        lowered = text.lower()
        pairs = [
            (["혈당", "당뇨", "스파이크", "당분"], "blood sugar glucose diabetes healthy food"),
            (["간", "지방간", "해독"], "liver health medical illustration"),
            (["수면", "잠", "불면"], "senior sleep bedroom healthy lifestyle"),
            (["식습관", "식사", "채소", "음식", "영양"], "healthy meal vegetables senior"),
            (["운동", "걷기", "산책"], "older people walking park exercise"),
            (["물", "차", "커피", "음료"], "healthy drink water tea senior"),
            (["병원", "검진", "의사"], "doctor consultation senior health"),
        ]
        for keywords, query in pairs:
            if any(keyword in lowered for keyword in keywords):
                return query
        return "senior health lifestyle clear photo"

    def _allowed_license(self, license_name: str) -> bool:
        if not license_name:
            return True
        allowed = [item.strip().lower() for item in self.settings.media_crawl_allowed_licenses.split(",")]
        normalized = license_name.replace("-", " ")
        if " nc" in f" {normalized}" or " nd" in f" {normalized}":
            return False
        normalized_allowed = [item.replace("-", " ") for item in allowed]
        return any(item in license_name or item in normalized for item in allowed + normalized_allowed)

    @staticmethod
    def _metadata_value(info: dict, key: str) -> str:
        value = info.get("extmetadata", {}).get(key, {}).get("value", "")
        return re.sub(r"<[^>]+>", "", value)

    def _credit(self, page: dict, info: dict) -> str:
        artist = self._metadata_value(info, "Artist") or "Wikimedia Commons"
        title = page.get("title", "").replace("File:", "")
        source = self._metadata_value(info, "CreditLine") or self._metadata_value(info, "ObjectName")
        return " / ".join(part for part in [title, artist, source] if part)

    def _score_candidate(
        self,
        scene: Scene,
        page: dict,
        info: dict,
        license_name: str,
        width: int,
        height: int,
        user_instruction: str,
    ) -> tuple[int, str]:
        title = page.get("title", "").replace("File:", "")
        haystack = f"{title} {self._metadata_value(info, 'ObjectName')} {self._metadata_value(info, 'ImageDescription')}".lower()
        keywords = [word.lower() for word in self._search_query(scene, user_instruction).split() if len(word) > 1]
        matches = sum(1 for word in keywords if word in haystack)
        score = 45 + min(matches * 8, 24)
        reasons = []
        if matches:
            reasons.append("장면/대본 키워드와 일부 일치")
        if width >= 900 and height >= 500:
            score += 12
            reasons.append("모바일 영상에 쓰기 좋은 해상도")
        elif width and height:
            score += 4
            reasons.append("기본 해상도 확인")
        ratio = width / height if width and height else 0
        if 1.2 <= ratio <= 2.1:
            score += 8
            reasons.append("가로 영상 구도에 적합")
        elif ratio:
            reasons.append("비율 차이는 contain 방식으로 보정")
        if self._allowed_license(license_name):
            score += 10
            reasons.append("허용 라이선스 범위")
        risky_words = ["blood", "wound", "surgery", "disease", "graphic", "nude", "weapon"]
        if any(word in haystack for word in risky_words):
            score -= 25
            reasons.append("민감하거나 불편할 수 있는 표현 포함 가능")
        if any(word in haystack for word in ["family", "home", "생활", "가족", "health", "wellness"]):
            score += 6
            reasons.append("50대 이상 생활 정보 맥락에 비교적 적합")
        return max(0, min(100, score)), " · ".join(reasons or ["출처와 이미지 형식을 확인한 공개 이미지 후보"])

    @staticmethod
    def _file_hash(path: Path) -> str:
        try:
            digest = hashlib.sha1()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        except Exception:
            return ""

    @staticmethod
    def _dimensions(path: Path, info: dict) -> tuple[int, int]:
        try:
            width = int(info.get("width") or 0)
            height = int(info.get("height") or 0)
            if width and height:
                return width, height
        except (TypeError, ValueError):
            pass
        try:
            with Image.open(path) as image:
                return image.size
        except Exception:
            return 0, 0

    @staticmethod
    def _suffix(url: str, mime: str) -> str:
        if mime == "image/png":
            return ".png"
        if mime in {"image/svg+xml", "image/svg"}:
            return ".svg"
        if mime.startswith("video/"):
            return ".mp4" if ".mp4" in url.lower() else ".webm"
        return ".jpg"

    @staticmethod
    def _mime_from_url(url: str) -> str:
        lower = url.lower().split("?", 1)[0]
        if lower.endswith(".png"):
            return "image/png"
        if lower.endswith(".webp"):
            return "image/webp"
        if lower.endswith(".gif"):
            return "image/gif"
        return "image/jpeg"

    def _url_for(self, path: Path) -> str:
        relative = path.relative_to(self.root).as_posix()
        return f"{self.public_base_url}/files/{quote(relative, safe='/')}"
