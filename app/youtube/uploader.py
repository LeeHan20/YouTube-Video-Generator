from __future__ import annotations

from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from app.youtube.oauth import EncryptedTokenStore


class YouTubeUploader:
    def __init__(self) -> None:
        self.tokens = EncryptedTokenStore()

    def upload_private(
        self,
        channel_id: str,
        video_path: Path,
        title: str,
        description: str,
        tags: list[str] | None = None,
        thumbnail_path: Path | None = None,
    ) -> dict[str, str]:
        credentials = self.tokens.read_credentials(channel_id)
        youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
        body = {
            "snippet": {
                "title": title[:100],
                "description": description,
                "tags": tags or [],
                "categoryId": "22",
            },
            "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False},
        }
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=MediaFileUpload(str(video_path), chunksize=-1, resumable=True),
        )
        response = None
        while response is None:
            _status, response = request.next_chunk()
        video_id = response["id"]
        if thumbnail_path and thumbnail_path.exists():
            youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumbnail_path))).execute()
        return {
            "youtube_video_id": video_id,
            "youtube_private_url": f"https://www.youtube.com/watch?v={video_id}",
        }

    def publish(self, channel_id: str, youtube_video_id: str) -> dict[str, str]:
        credentials = self.tokens.read_credentials(channel_id)
        youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
        youtube.videos().update(
            part="status",
            body={"id": youtube_video_id, "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}},
        ).execute()
        return {"youtube_public_url": f"https://www.youtube.com/watch?v={youtube_video_id}"}
