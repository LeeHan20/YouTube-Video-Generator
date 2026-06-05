from __future__ import annotations

import hashlib
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
        self._google_search_blocked = False

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
    ) -> list[MediaCandidate]:
        mode = "crawl_video" if source_mode == "crawl_video" else "crawl_image"
        candidates: list[MediaCandidate] = []
        excluded_asset_urls = excluded_asset_urls or set()
        excluded_hashes = excluded_hashes or set()
        pool_limit = max(self.settings.media_crawl_max_results, limit * 8, 32)
        api_url = "https://commons.wikimedia.org/w/api.php"
        try:
            headers = {"User-Agent": "Auto2YouTubeAutomation/0.1 (local review tool; contact: admin@example.com)"}
            with httpx.Client(timeout=self.settings.media_crawl_timeout_seconds, follow_redirects=True, headers=headers) as client:
                seen_urls: set[str] = set(excluded_asset_urls)
                if mode == "crawl_image":
                    candidates.extend(
                        self._crawl_google_images(
                            client,
                            topic_id,
                            scene,
                            user_instruction,
                            seen_urls,
                            limit=min(10, pool_limit),
                            excluded_hashes=excluded_hashes,
                        )
                    )
                for query in self._search_queries(scene, user_instruction):
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
                    if len(candidates) >= pool_limit:
                        break
        except Exception:
            candidates = candidates
        if mode == "crawl_image":
            candidates.extend(
                self._crawl_openverse(
                    topic_id,
                    scene,
                    user_instruction,
                    seen={item.asset_url for item in candidates},
                    limit=pool_limit,
                    excluded_hashes=excluded_hashes,
                )
            )
        return self._select_diverse_candidates(candidates, limit, excluded_hashes)

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
    ) -> list[MediaCandidate]:
        candidates: list[MediaCandidate] = []
        excluded_hashes = excluded_hashes or set()
        if self._google_search_blocked or not self.settings.google_image_search_api_key or not self.settings.google_image_search_cx:
            return candidates
        for query in self._search_queries(scene, user_instruction):
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
                score, reason = self._score_candidate(scene, page, info, "unknown", width, height, user_instruction)
                score = max(0, score - 8)
                digest = hashlib.sha1(f"{source_url}:{score}".encode("utf-8")).hexdigest()[:12]
                candidates.append(
                    MediaCandidate(
                        candidate_id=f"{scene.scene_id}_{digest}",
                        asset_url=self._url_for(local_path),
                        source="google_image_search",
                        credit=" / ".join(part for part in [item.get("title"), item.get("displayLink"), item.get("image", {}).get("contextLink")] if part),
                        license="unknown",
                        prompt=query,
                        local_path=local_path,
                        image_hash=image_hash,
                        score=score,
                        reason=f"{reason} · Google 이미지 검색 후보, 라이선스는 사용 전 확인 필요",
                        width=width,
                        height=height,
                    )
                )
            if len(candidates) >= limit:
                break
        return candidates

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
