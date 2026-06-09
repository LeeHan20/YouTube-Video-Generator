from __future__ import annotations

import hashlib
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx
from PIL import Image, ImageDraw

from app.core.config import get_settings
from app.pipeline.models import Scene
from app.services.ai_client import get_ai_client
from app.services.image_generation import ImageGenerationService
from app.services.image_usage import (
    calculate_duplicate_penalty,
    calculate_final_score,
    calculate_phash,
    calculate_similar_image_penalty,
    calculate_used_penalty,
    load_used_images,
    make_image_id,
)
from app.services.prompt_loader import render_prompt


logger = logging.getLogger(__name__)


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
    image_id: str = ""
    thumbnail_url: str = ""
    raw_score: int = 0
    final_score: int = 0
    used_count: int = 0
    used_penalty: int = 0
    duplicate_penalty: int = 0
    similar_image_penalty: int = 0
    phash: str = ""

    def as_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "image_id": self.image_id,
            "asset_url": self.asset_url,
            "url": self.asset_url,
            "source": self.source,
            "credit": self.credit,
            "title": self.credit,
            "license": self.license,
            "thumbnail_url": self.thumbnail_url,
            "local_path": str(self.local_path) if self.local_path else None,
            "prompt": self.prompt,
            "score": self.score,
            "raw_score": self.raw_score or self.score,
            "final_score": self.final_score or self.score,
            "used_count": self.used_count,
            "used_penalty": self.used_penalty,
            "duplicate_penalty": self.duplicate_penalty,
            "similar_image_penalty": self.similar_image_penalty,
            "reason": self.reason,
            "width": self.width,
            "height": self.height,
            "image_hash": self.image_hash,
            "phash": self.phash,
        }


class MediaSourceService:
    ASSET_DOWNLOAD_TIMEOUT_SECONDS = 8
    MAX_ASSET_DOWNLOAD_BYTES = 30 * 1024 * 1024

    def __init__(self) -> None:
        settings = get_settings()
        self.root = settings.local_storage_dir
        self.public_base_url = settings.public_base_url.rstrip("/")
        self.settings = settings
        self.root.mkdir(parents=True, exist_ok=True)
        self.image_generation = ImageGenerationService()
        self._google_search_blocked = False

    def create_asset(
        self,
        topic_id: str,
        scene: Scene,
        source_mode: str = "auto",
        user_instruction: str = "",
        excluded_asset_urls: set[str] | None = None,
        excluded_hashes: set[str] | None = None,
        current_video_used_image_ids: set[str] | None = None,
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
                current_video_used_image_ids=current_video_used_image_ids,
            )
            if candidates:
                return candidates[0]
            if mode == "crawl_video":
                return self._generate_video_placeholder(topic_id, scene, user_instruction)
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
        current_video_used_image_ids: set[str] | None = None,
    ) -> list[MediaCandidate]:
        mode = "crawl_video" if source_mode == "crawl_video" else "crawl_image"
        candidates: list[MediaCandidate] = []
        excluded_asset_urls = excluded_asset_urls or set()
        excluded_hashes = excluded_hashes or set()
        current_video_used_image_ids = current_video_used_image_ids or set()
        pool_limit = max(self.settings.media_crawl_max_results, limit * 8, 32)
        api_url = "https://commons.wikimedia.org/w/api.php"
        seen_urls: set[str] = set(excluded_asset_urls)
        used_images = load_used_images()
        source_counts: dict[str, int] = {}
        download_success = 0
        download_failed = 0
        fallback_used = False
        try:
            headers = {"User-Agent": "Auto2YouTubeAutomation/0.1 (local review tool; contact: admin@example.com)"}
            with httpx.Client(timeout=self.settings.media_crawl_timeout_seconds, follow_redirects=True, headers=headers) as client:
                if mode == "crawl_image":
                    source_calls = [
                        ("google_image_search", self._crawl_google_images),
                        ("unsplash", self._crawl_unsplash_images),
                        ("pexels", self._crawl_pexels_images),
                        ("pixabay", self._crawl_pixabay_images),
                    ]
                    for source_name, source in source_calls:
                        try:
                            results = source(
                                client,
                                topic_id,
                                scene,
                                user_instruction,
                                seen_urls,
                                limit=min(10, pool_limit),
                                excluded_hashes=excluded_hashes,
                                used_images=used_images,
                                current_video_used_image_ids=current_video_used_image_ids,
                            )
                        except Exception as exc:
                            logger.warning({"event": "image_source_failed", "source": source_name, "error": str(exc)})
                            results = []
                        source_counts[source_name] = len(results)
                        candidates.extend(results)
                        if len(candidates) >= limit:
                            break
                should_try_wikimedia = mode == "crawl_video" or len(candidates) < limit
                if not should_try_wikimedia:
                    logger.info(
                        {
                            "event": "image_source_skipped",
                            "source": "wikimedia",
                            "reason": "enough_external_candidates",
                            "candidate_count": len(candidates),
                            "limit": limit,
                        }
                    )
                for query in ([] if not should_try_wikimedia else self._fallback_queries(scene, user_instruction)):
                    logger.info({"event": "image_search", "query": query, "source": "wikimedia"})
                    params = {
                        "action": "query",
                        "generator": "search",
                        "gsrsearch": query,
                        "gsrnamespace": "6",
                        "gsrlimit": str(pool_limit),
                        "prop": "imageinfo",
                        "iiprop": "url|mime|extmetadata|size",
                        "format": "json",
                    }
                    response = client.get(api_url, params=params)
                    response.raise_for_status()
                    pages = response.json().get("query", {}).get("pages", {})
                    source_counts["wikimedia"] = source_counts.get("wikimedia", 0) + len(pages)
                    for page in pages.values():
                        if len(candidates) >= pool_limit:
                            break
                        info = (page.get("imageinfo") or [{}])[0]
                        mime = info.get("mime", "")
                        if mode == "crawl_video" and not mime.startswith("video/"):
                            continue
                        if mode != "crawl_video" and not mime.startswith("image/"):
                            continue
                        if mode != "crawl_video" and mime not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
                            continue
                        license_name = self._metadata_value(info, "LicenseShortName").lower()
                        if not self._allowed_license(license_name):
                            continue
                        asset_url = info.get("url") or info.get("thumburl")
                        if not asset_url or asset_url in seen_urls:
                            continue
                        seen_urls.add(asset_url)
                        try:
                            local_path = self._download_asset(client, topic_id, scene.scene_id, asset_url, mime)
                            download_success += 1
                        except Exception as exc:
                            download_failed += 1
                            logger.warning({"event": "image_download_failed", "source": "wikimedia", "url": asset_url, "error": str(exc)})
                            continue
                        image_hash = self._file_hash(local_path)
                        if image_hash and image_hash in excluded_hashes:
                            local_path.unlink(missing_ok=True)
                            continue
                        width, height = self._dimensions(local_path, info)
                        candidates.append(
                            self._build_candidate(
                                scene=scene,
                                source_url=asset_url,
                                source="crawl_video" if mime.startswith("video/") else "wikimedia",
                                title=page.get("title", "").replace("File:", ""),
                                credit=self._credit(page, info),
                                license_name=license_name or "wikimedia commons",
                                prompt=query,
                                local_path=local_path,
                                image_hash=image_hash,
                                width=width,
                                height=height,
                                page=page,
                                info=info,
                                user_instruction=user_instruction,
                                used_images=used_images,
                                current_video_used_image_ids=current_video_used_image_ids,
                            )
                        )
                    if len(candidates) >= pool_limit:
                        break
        except Exception as exc:
            logger.warning({"event": "image_source_failed", "source": "wikimedia", "error": str(exc)})
        if mode == "crawl_image":
            openverse_candidates = self._crawl_openverse(
                    topic_id,
                    scene,
                    user_instruction,
                    seen={item.asset_url for item in candidates} | seen_urls,
                    limit=pool_limit,
                    excluded_hashes=excluded_hashes,
                    used_images=used_images,
                    current_video_used_image_ids=current_video_used_image_ids,
            )
            source_counts["openverse"] = len(openverse_candidates)
            candidates.extend(openverse_candidates)
        selected = self._select_diverse_candidates(candidates, limit, excluded_hashes)
        if mode == "crawl_image" and len(selected) < limit:
            fallback_used = True
            selected = self._fill_fallback_candidates(
                topic_id,
                scene,
                user_instruction,
                selected,
                limit,
                used_images,
                current_video_used_image_ids,
            )
        logger.info(
            {
                "event": "image_candidates_selected",
                "query": self._search_query(scene, user_instruction),
                "sources": source_counts,
                "download_success": download_success,
                "download_failed": download_failed,
                "selected": [item.as_dict() for item in selected],
                "fallback_used": fallback_used,
            }
        )
        return selected

    def _auto_mode(self, scene: Scene) -> str:
        return "crawl_video" if scene.media_type == "video" else "crawl_image"

    def _crawl_failed_placeholder(self, topic_id: str, scene: Scene, user_instruction: str = "") -> MediaAsset:
        del topic_id
        return MediaAsset(
            asset_url="",
            source="asset_required",
            credit="No crawling candidate found",
            license="none",
            prompt=self._search_query(scene, user_instruction),
            local_path=None,
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

    def _generate_video_placeholder(self, topic_id: str, scene: Scene, user_instruction: str = "") -> MediaAsset:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return self._crawl_failed_placeholder(topic_id, scene, "ffmpeg가 없어 로컬 영상 fallback을 만들 수 없습니다.")
        topic_dir = self.root / "topics" / topic_id / "assets"
        topic_dir.mkdir(parents=True, exist_ok=True)
        prompt = self._improve_generation_prompt(scene, user_instruction)
        poster_path = topic_dir / f"{scene.scene_id}_video_fallback.png"
        video_path = topic_dir / f"{scene.scene_id}_video_fallback.mp4"
        self._write_video_poster(scene, prompt, poster_path)
        command = [
            ffmpeg,
            "-y",
            "-loop",
            "1",
            "-framerate",
            "30",
            "-i",
            str(poster_path),
            "-t",
            "12",
            "-vf",
            "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black,format=yuv420p",
            "-c:v",
            "libx264",
            "-movflags",
            "+faststart",
            str(video_path),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError:
            return self._crawl_failed_placeholder(topic_id, scene, "로컬 영상 fallback 생성에 실패했습니다.")
        return MediaAsset(
            asset_url=self._url_for(video_path),
            source="local_generated_video",
            credit="Local generated video fallback",
            license="generated",
            prompt=prompt,
            local_path=video_path,
            image_hash=self._file_hash(video_path),
        )

    @staticmethod
    def _write_video_poster(scene: Scene, prompt: str, output_path: Path) -> None:
        ImageGenerationService._generate_local_placeholder(prompt, output_path, scene.title)
        with Image.open(output_path) as image:
            image = image.convert("RGB")
            draw = ImageDraw.Draw(image)
            font = ImageGenerationService._font(24)
            draw.rounded_rectangle((985, 34, 1220, 84), radius=16, fill=(18, 42, 48), outline=(119, 206, 197), width=2)
            draw.text((1010, 47), "VIDEO CLIP", fill=(230, 255, 251), font=font)
            image.save(output_path)

    def _download_asset(self, client: httpx.Client, topic_id: str, scene_id: str, url: str, mime: str) -> Path:
        suffix = self._suffix(url, mime)
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
        path = self.root / "topics" / topic_id / "assets" / f"{scene_id}_crawl_{digest}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        timeout = httpx.Timeout(
            connect=min(5, self.settings.media_crawl_timeout_seconds),
            read=min(self.ASSET_DOWNLOAD_TIMEOUT_SECONDS, self.settings.media_crawl_timeout_seconds),
            write=5,
            pool=5,
        )
        try:
            with client.stream("GET", url, timeout=timeout, headers=self._asset_download_headers(url)) as response:
                response.raise_for_status()
                total = 0
                with path.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > self.MAX_ASSET_DOWNLOAD_BYTES:
                            raise RuntimeError(f"asset download too large: {total} bytes")
                        handle.write(chunk)
            if path.stat().st_size < 1024:
                raise RuntimeError(f"asset download too small: {path.stat().st_size} bytes")
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return path

    @staticmethod
    def _asset_download_headers(url: str) -> dict[str, str]:
        headers = {
            "User-Agent": "Auto2YouTubeAutomation/0.1 (local review tool; contact: admin@example.com)",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,video/*,*/*;q=0.8",
        }
        if "wikimedia.org" in url or "wikipedia.org" in url:
            headers["Referer"] = "https://commons.wikimedia.org/"
        return headers

    def _crawl_openverse(
        self,
        topic_id: str,
        scene: Scene,
        user_instruction: str,
        seen: set[str],
        limit: int = 4,
        excluded_hashes: set[str] | None = None,
        used_images: dict | None = None,
        current_video_used_image_ids: set[str] | None = None,
    ) -> list[MediaCandidate]:
        candidates: list[MediaCandidate] = []
        excluded_hashes = excluded_hashes or set()
        used_images = used_images or load_used_images()
        current_video_used_image_ids = current_video_used_image_ids or set()
        if limit <= 0:
            return candidates
        headers = {"User-Agent": "Auto2YouTubeAutomation/0.1 (local review tool; contact: admin@example.com)"}
        try:
            with httpx.Client(timeout=self.settings.media_crawl_timeout_seconds, follow_redirects=True, headers=headers) as client:
                for query in self._fallback_queries(scene, user_instruction):
                    logger.info({"event": "image_search", "query": query, "source": "openverse"})
                    response = client.get(
                        "https://api.openverse.org/v1/images/",
                        params={"q": query, "page_size": min(20, max(min(limit, 20), 12)), "mature": "false"},
                    )
                    response.raise_for_status()
                    for item in response.json().get("results", []):
                        try:
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
                            creator = item.get("creator") or "Openverse"
                            provider = item.get("provider") or item.get("source") or "openverse"
                            candidates.append(
                                self._build_candidate(
                                    scene=scene,
                                    source_url=source_url,
                                    source=f"openverse:{provider}",
                                    title=item.get("title", ""),
                                    credit=" / ".join(part for part in [item.get("title"), creator, item.get("foreign_landing_url")] if part),
                                    license_name=f"{item.get('license', 'cc')} {item.get('license_version', '')}".strip(),
                                    prompt=query,
                                    local_path=local_path,
                                    image_hash=image_hash,
                                    width=width,
                                    height=height,
                                    page=page,
                                    info=info,
                                    user_instruction=user_instruction,
                                    used_images=used_images,
                                    current_video_used_image_ids=current_video_used_image_ids,
                                    reason_suffix="Openverse 공개 라이선스 후보",
                                )
                            )
                        except Exception:
                            continue
                    if len(candidates) >= limit:
                        break
        except Exception:
            return candidates
        return candidates

    def _crawl_google_images(
        self,
        client: httpx.Client,
        topic_id: str,
        scene: Scene,
        user_instruction: str,
        seen: set[str],
        limit: int = 4,
        excluded_hashes: set[str] | None = None,
        used_images: dict | None = None,
        current_video_used_image_ids: set[str] | None = None,
    ) -> list[MediaCandidate]:
        candidates: list[MediaCandidate] = []
        excluded_hashes = excluded_hashes or set()
        used_images = used_images or load_used_images()
        current_video_used_image_ids = current_video_used_image_ids or set()
        if self._google_search_blocked or not self.settings.google_image_search_api_key or not self.settings.google_image_search_cx:
            return candidates
        for query in self._fallback_queries(scene, user_instruction):
            logger.info({"event": "image_search", "query": query, "source": "google_image_search"})
            params = {
                "key": self.settings.google_image_search_api_key,
                "cx": self.settings.google_image_search_cx,
                "searchType": "image",
                "q": query,
                "num": min(10, max(limit * 2, 4)),
                "safe": "active",
            }
            if self.settings.google_image_search_rights:
                params["rights"] = self.settings.google_image_search_rights
            try:
                response = client.get("https://www.googleapis.com/customsearch/v1", params=params)
                if response.status_code == 403:
                    self._google_search_blocked = True
                    return candidates
                response.raise_for_status()
            except Exception:
                continue
            for item in response.json().get("items", []):
                if len(candidates) >= limit:
                    break
                source_url = item.get("link", "")
                if not source_url or source_url in seen:
                    continue
                mime = item.get("mime") or self._mime_from_url(source_url)
                if mime not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
                    continue
                try:
                    local_path = self._download_asset(client, topic_id, scene.scene_id, source_url, mime)
                except Exception:
                    continue
                seen.add(source_url)
                image_hash = self._file_hash(local_path)
                if image_hash and image_hash in excluded_hashes:
                    local_path.unlink(missing_ok=True)
                    continue
                width, height = self._dimensions(local_path, {"width": "", "height": ""})
                page = {"title": item.get("title", "")}
                info = {"extmetadata": {"ImageDescription": {"value": item.get("snippet", "")}}}
                candidates.append(
                    self._build_candidate(
                        scene=scene,
                        source_url=source_url,
                        source="google_image_search",
                        title=item.get("title", ""),
                        credit=" / ".join(part for part in [item.get("title"), item.get("displayLink"), item.get("image", {}).get("contextLink")] if part),
                        license_name="unknown",
                        prompt=query,
                        local_path=local_path,
                        image_hash=image_hash,
                        width=width,
                        height=height,
                        page=page,
                        info=info,
                        user_instruction=user_instruction,
                        used_images=used_images,
                        current_video_used_image_ids=current_video_used_image_ids,
                        source_score_adjustment=-8,
                        reason_suffix="Google 이미지 검색 후보, 라이선스는 사용 전 확인 필요",
                    )
                )
            if len(candidates) >= limit:
                break
        return candidates

    def _crawl_unsplash_images(
        self,
        client: httpx.Client,
        topic_id: str,
        scene: Scene,
        user_instruction: str,
        seen: set[str],
        limit: int = 4,
        excluded_hashes: set[str] | None = None,
        used_images: dict | None = None,
        current_video_used_image_ids: set[str] | None = None,
    ) -> list[MediaCandidate]:
        if not self.settings.unsplash_access_key:
            return []
        candidates: list[MediaCandidate] = []
        for query in self._fallback_queries(scene, user_instruction):
            logger.info({"event": "image_search", "query": query, "source": "unsplash"})
            response = client.get(
                "https://api.unsplash.com/search/photos",
                params={"query": query, "per_page": min(30, max(limit * 2, 4)), "client_id": self.settings.unsplash_access_key},
            )
            response.raise_for_status()
            for item in response.json().get("results", []):
                source_url = item.get("urls", {}).get("regular") or item.get("urls", {}).get("full")
                if not source_url or source_url in seen or len(candidates) >= limit:
                    continue
                candidate = self._download_external_candidate(
                    client,
                    topic_id,
                    scene,
                    source_url,
                    "unsplash",
                    item.get("description") or item.get("alt_description") or "",
                    item.get("user", {}).get("name", "Unsplash"),
                    "unsplash license",
                    query,
                    item.get("width") or 0,
                    item.get("height") or 0,
                    seen,
                    excluded_hashes or set(),
                    used_images or load_used_images(),
                    current_video_used_image_ids or set(),
                    reason_suffix="Unsplash 후보",
                )
                if candidate:
                    candidates.append(candidate)
            if len(candidates) >= limit:
                break
        return candidates

    def _crawl_pexels_images(
        self,
        client: httpx.Client,
        topic_id: str,
        scene: Scene,
        user_instruction: str,
        seen: set[str],
        limit: int = 4,
        excluded_hashes: set[str] | None = None,
        used_images: dict | None = None,
        current_video_used_image_ids: set[str] | None = None,
    ) -> list[MediaCandidate]:
        if not self.settings.pexels_api_key:
            return []
        candidates: list[MediaCandidate] = []
        headers = {"Authorization": self.settings.pexels_api_key}
        for query in self._fallback_queries(scene, user_instruction):
            logger.info({"event": "image_search", "query": query, "source": "pexels"})
            response = client.get("https://api.pexels.com/v1/search", params={"query": query, "per_page": min(30, max(limit * 2, 4))}, headers=headers)
            response.raise_for_status()
            for item in response.json().get("photos", []):
                source_url = item.get("src", {}).get("large2x") or item.get("src", {}).get("large")
                if not source_url or source_url in seen or len(candidates) >= limit:
                    continue
                candidate = self._download_external_candidate(
                    client,
                    topic_id,
                    scene,
                    source_url,
                    "pexels",
                    item.get("alt", ""),
                    item.get("photographer", "Pexels"),
                    "pexels license",
                    query,
                    item.get("width") or 0,
                    item.get("height") or 0,
                    seen,
                    excluded_hashes or set(),
                    used_images or load_used_images(),
                    current_video_used_image_ids or set(),
                    reason_suffix="Pexels 후보",
                )
                if candidate:
                    candidates.append(candidate)
            if len(candidates) >= limit:
                break
        return candidates

    def _crawl_pixabay_images(
        self,
        client: httpx.Client,
        topic_id: str,
        scene: Scene,
        user_instruction: str,
        seen: set[str],
        limit: int = 4,
        excluded_hashes: set[str] | None = None,
        used_images: dict | None = None,
        current_video_used_image_ids: set[str] | None = None,
    ) -> list[MediaCandidate]:
        if not self.settings.pixabay_api_key:
            return []
        candidates: list[MediaCandidate] = []
        for query in self._fallback_queries(scene, user_instruction):
            logger.info({"event": "image_search", "query": query, "source": "pixabay"})
            response = client.get(
                "https://pixabay.com/api/",
                params={"key": self.settings.pixabay_api_key, "q": query, "image_type": "photo", "safesearch": "true", "per_page": min(50, max(limit * 2, 4))},
            )
            response.raise_for_status()
            for item in response.json().get("hits", []):
                source_url = item.get("webformatURL") or item.get("largeImageURL")
                if not source_url or source_url in seen or len(candidates) >= limit:
                    continue
                candidate = self._download_external_candidate(
                    client,
                    topic_id,
                    scene,
                    source_url,
                    "pixabay",
                    item.get("tags", ""),
                    item.get("user", "Pixabay"),
                    "pixabay license",
                    query,
                    item.get("imageWidth") or 0,
                    item.get("imageHeight") or 0,
                    seen,
                    excluded_hashes or set(),
                    used_images or load_used_images(),
                    current_video_used_image_ids or set(),
                    reason_suffix="Pixabay 후보",
                )
                if candidate:
                    candidates.append(candidate)
            if len(candidates) >= limit:
                break
        return candidates

    def _download_external_candidate(
        self,
        client: httpx.Client,
        topic_id: str,
        scene: Scene,
        source_url: str,
        source: str,
        title: str,
        credit: str,
        license_name: str,
        prompt: str,
        width: int,
        height: int,
        seen: set[str],
        excluded_hashes: set[str],
        used_images: dict,
        current_video_used_image_ids: set[str],
        reason_suffix: str,
    ) -> MediaCandidate | None:
        seen.add(source_url)
        try:
            local_path = self._download_asset(client, topic_id, scene.scene_id, source_url, self._mime_from_url(source_url))
        except Exception as exc:
            logger.warning({"event": "image_download_failed", "source": source, "url": source_url, "error": str(exc)})
            return None
        image_hash = self._file_hash(local_path)
        if image_hash and image_hash in excluded_hashes:
            local_path.unlink(missing_ok=True)
            return None
        actual_width, actual_height = self._dimensions(local_path, {"width": width, "height": height})
        page = {"title": title}
        info = {"extmetadata": {"ImageDescription": {"value": title}}}
        return self._build_candidate(
            scene=scene,
            source_url=source_url,
            source=source,
            title=title,
            credit=credit,
            license_name=license_name,
            prompt=prompt,
            local_path=local_path,
            image_hash=image_hash,
            width=actual_width,
            height=actual_height,
            page=page,
            info=info,
            user_instruction="",
            used_images=used_images,
            current_video_used_image_ids=current_video_used_image_ids,
            reason_suffix=reason_suffix,
        )

    def _build_candidate(
        self,
        scene: Scene,
        source_url: str,
        source: str,
        title: str,
        credit: str,
        license_name: str,
        prompt: str,
        local_path: Path,
        image_hash: str,
        width: int,
        height: int,
        page: dict,
        info: dict,
        user_instruction: str,
        used_images: dict,
        current_video_used_image_ids: set[str],
        source_score_adjustment: int = 0,
        reason_suffix: str = "",
    ) -> MediaCandidate:
        image_id = make_image_id(source_url)
        base_score, reason = self._score_candidate(scene, page, info, license_name, width, height, user_instruction)
        base_score = max(0, min(100, base_score + source_score_adjustment))
        used_count = int(used_images.get("images", {}).get(image_id, {}).get("used_count", 0))
        used_penalty = calculate_used_penalty(used_count)
        duplicate_penalty = calculate_duplicate_penalty(image_id, current_video_used_image_ids)
        phash = calculate_phash(local_path)
        similar_image_penalty = calculate_similar_image_penalty(phash, used_images)
        final_score = calculate_final_score(base_score, used_penalty, duplicate_penalty, similar_image_penalty)
        digest = hashlib.sha1(f"{source_url}:{final_score}".encode("utf-8")).hexdigest()[:12]
        full_reason = " · ".join(part for part in [reason, reason_suffix] if part)
        logger.info(
            {
                "event": "image_candidate_score",
                "query": prompt,
                "source": source,
                "url": source_url,
                "base_score": base_score,
                "used_penalty": used_penalty,
                "duplicate_penalty": duplicate_penalty,
                "similar_image_penalty": similar_image_penalty,
                "final_score": final_score,
            }
        )
        return MediaCandidate(
            candidate_id=f"{scene.scene_id}_{digest}",
            image_id=image_id,
            asset_url=self._url_for(local_path),
            source=source,
            credit=credit or title or source,
            license=license_name or "unknown",
            thumbnail_url=source_url,
            prompt=prompt,
            local_path=local_path,
            image_hash=image_hash,
            score=final_score,
            raw_score=base_score,
            final_score=final_score,
            used_count=used_count,
            used_penalty=used_penalty,
            duplicate_penalty=duplicate_penalty,
            similar_image_penalty=similar_image_penalty,
            phash=phash,
            reason=full_reason,
            width=width,
            height=height,
        )

    def _fill_fallback_candidates(
        self,
        topic_id: str,
        scene: Scene,
        user_instruction: str,
        selected: list[MediaCandidate],
        limit: int,
        used_images: dict,
        current_video_used_image_ids: set[str],
    ) -> list[MediaCandidate]:
        existing_ids = {item.image_id for item in selected if item.image_id}
        fallback_queries = self._fallback_queries(scene, user_instruction)
        topic_dir = self.root / "topics" / topic_id / "assets"
        topic_dir.mkdir(parents=True, exist_ok=True)
        index = 0
        while len(selected) < limit:
            prompt = fallback_queries[index % len(fallback_queries)]
            suffix = "ai" if index == 0 and self.settings.gemini_api_key else "placeholder"
            path = topic_dir / f"{scene.scene_id}_fallback_{index + 1}.png"
            generated_path, source = self.image_generation.generate(
                f"{prompt}. {scene.visual_prompt}. safe editorial video still, 16:9, no text.",
                path,
                title=scene.title,
            )
            source_url = self._url_for(generated_path)
            image_id = make_image_id(f"{source_url}:{index}")
            if image_id in existing_ids:
                index += 1
                continue
            image_hash = self._file_hash(generated_path)
            width, height = self._dimensions(generated_path, {"width": 1280, "height": 720})
            page = {"title": prompt}
            info = {"extmetadata": {"ImageDescription": {"value": prompt}}}
            candidate = self._build_candidate(
                scene=scene,
                source_url=f"{source_url}:{index}",
                source=source if suffix == "ai" else "local_placeholder",
                title=prompt,
                credit="Fallback generated image",
                license_name="generated",
                prompt=prompt,
                local_path=generated_path,
                image_hash=image_hash,
                width=width,
                height=height,
                page=page,
                info=info,
                user_instruction=user_instruction,
                used_images=used_images,
                current_video_used_image_ids=current_video_used_image_ids,
                reason_suffix="검색 실패 시 영상 생성을 유지하기 위한 fallback 후보",
            )
            selected.append(candidate)
            existing_ids.add(candidate.image_id)
            index += 1
        return selected[:limit]

    def _improve_generation_prompt(self, scene: Scene, user_instruction: str) -> str:
        prompt = render_prompt(
            "image_generation_prompt_improvement",
            scene_visual_prompt=scene.generation_prompt or scene.visual_prompt,
            user_instruction=user_instruction or "없음",
        )
        try:
            text = get_ai_client().generate_text(prompt, max_tokens=900).text.strip()
            if text and "AI API 키가 설정되지 않아" not in text:
                return text
        except Exception:
            pass
        extra = f" 사용자 요청: {user_instruction}." if user_instruction else ""
        return f"{scene.generation_prompt or scene.visual_prompt}{extra} 원본 생성 이미지, 큰 자막 공간, 모바일 가독성, 부드러운 조명."

    @staticmethod
    def _search_query(scene: Scene, user_instruction: str) -> str:
        keyword_text = " ".join(scene.image_keywords or [])
        text = f"{scene.crawl_prompt} {keyword_text} {user_instruction}".strip()
        text = re.sub(r"[^\w\s가-힣]", " ", text)
        words = [word for word in text.split() if len(word) > 1][:5]
        return " ".join(words) or "health lifestyle"

    def _search_queries(self, scene: Scene, user_instruction: str) -> list[str]:
        primary = self._compact_query(scene.crawl_prompt) or self._search_query(scene, user_instruction)
        keyword_text = " ".join(scene.image_keywords or [])
        text = f"{scene.crawl_prompt} {keyword_text} {scene.caption} {scene.subtitle} {scene.narration} {scene.visual_prompt}"
        keyword_query = self._semantic_keyword_query(text)
        focused = self._focused_queries(scene)
        if scene.media_type == "video":
            queries = [primary, *focused, keyword_query, "bedroom night", "morning routine"]
        else:
            queries = [primary, *focused, keyword_query]
        unique = []
        for query in queries:
            if query and query not in unique:
                unique.append(query)
        return unique

    def _fallback_queries(self, scene: Scene, user_instruction: str) -> list[str]:
        original = self._search_query(scene, user_instruction)
        simplified = self._compact_query(original)
        semantic = self._semantic_keyword_query(
            f"{scene.crawl_prompt} {' '.join(scene.image_keywords or [])} {scene.narration} {scene.visual_prompt}"
        )
        topic_keyword = " ".join((scene.image_keywords or [])[:3]) or "healthy lifestyle"
        queries = [
            *self._search_queries(scene, user_instruction),
            simplified,
            semantic,
            topic_keyword,
            "healthy lifestyle",
            "abstract background",
            "nature background",
        ]
        unique: list[str] = []
        for query in queries:
            cleaned = self._compact_query(query) or query
            if cleaned and cleaned not in unique:
                unique.append(cleaned)
        return unique or ["healthy lifestyle", "nature background"]

    @staticmethod
    def _compact_query(query: str) -> str:
        text = re.sub(r"\b(public domain|copyright free|creative commons|photo|image|stock|video clip|clip)\b", " ", query or "", flags=re.I)
        text = re.sub(r"[^\w\s가-힣]", " ", text)
        words = [word for word in text.split() if len(word) > 1]
        return " ".join(words[:4])

    @staticmethod
    def _focused_queries(scene: Scene) -> list[str]:
        text = f"{scene.narration} {scene.caption} {scene.visual_prompt}".lower()
        pairs = [
            (["미지근한 물", "물 한 잔", "수분", "마시"], ["water glass", "drinking water"]),
            (["스트레칭", "목을", "어깨", "허리", "팔다리"], ["morning stretching", "gentle stretching"]),
            (["스마트폰", "눈 뜨자마자"], ["phone bed", "checking phone"]),
            (["수면", "잠", "불면", "무호흡"], ["sleep bedroom", "sleep hygiene", "bedroom night"]),
            (["근육", "관절", "뻣뻣"], ["stretching", "joint mobility"]),
            (["혈액순환", "림프", "노폐물"], ["blood circulation", "wellness"]),
            (["장 건강", "변비", "소화"], ["digestive health", "digestion"]),
            (["아침", "활력", "신진대사"], ["morning routine", "morning sunlight"]),
            (["의사", "전문가", "상담"], ["doctor consultation", "medical consultation"]),
        ]
        queries: list[str] = []
        for keywords, values in pairs:
            if any(keyword in text for keyword in keywords):
                queries.extend(values)
        return queries

    @staticmethod
    def _semantic_keyword_query(text: str) -> str:
        lowered = text.lower()
        pairs = [
            (["혈당", "당뇨", "스파이크", "당분"], "blood sugar glucose diabetes healthy food"),
            (["간", "지방간", "해독"], "liver health medical illustration"),
            (["수면", "잠", "불면"], "sleep bedroom"),
            (["식습관", "식사", "채소", "음식", "영양"], "healthy meal vegetables senior"),
            (["운동", "걷기", "산책"], "walking park"),
            (["물", "차", "커피", "음료"], "water glass"),
            (["병원", "검진", "의사"], "doctor consultation"),
        ]
        for keywords, query in pairs:
            if any(keyword in lowered for keyword in keywords):
                return query
        return "senior health lifestyle clear photo"

    def _select_diverse_candidates(
        self,
        candidates: list[MediaCandidate],
        limit: int,
        excluded_hashes: set[str] | None = None,
    ) -> list[MediaCandidate]:
        excluded_hashes = excluded_hashes or set()
        selected: list[MediaCandidate] = []
        seen_hosts: set[str] = set()
        seen_hashes: set[str] = set(excluded_hashes)
        candidates.sort(key=lambda item: item.score, reverse=True)
        for candidate in candidates:
            if len(selected) >= limit:
                break
            if candidate.image_hash and candidate.image_hash in seen_hashes:
                continue
            host_key = self._candidate_host_key(candidate)
            if host_key in seen_hosts and len(candidates) > limit:
                continue
            selected.append(candidate)
            if candidate.image_hash:
                seen_hashes.add(candidate.image_hash)
            if host_key:
                seen_hosts.add(host_key)
        if len(selected) < limit:
            for candidate in candidates:
                if len(selected) >= limit:
                    break
                if any(candidate.asset_url == item.asset_url for item in selected):
                    continue
                if candidate.image_hash and candidate.image_hash in seen_hashes:
                    continue
                selected.append(candidate)
                if candidate.image_hash:
                    seen_hashes.add(candidate.image_hash)
        return selected

    @staticmethod
    def _candidate_host_key(candidate: MediaCandidate) -> str:
        source = candidate.source or ""
        credit = candidate.credit or ""
        if "openverse" in source.lower() or "Openverse" in credit:
            parts = [part.strip() for part in credit.split("/") if part.strip()]
            return parts[-1] if parts else source
        return source

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
            lower = url.lower()
            if ".mp4" in lower or mime == "video/mp4":
                return ".mp4"
            if ".ogv" in lower or "ogg" in mime:
                return ".ogv"
            if ".mov" in lower or "quicktime" in mime:
                return ".mov"
            return ".webm"
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
