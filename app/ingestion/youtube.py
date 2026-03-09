"""
YouTube scraper: one class that fetches recent videos from a channel list,
uses video title and description (as summary), filters to last 7 days,
and writes results to date-named markdown files with metrics logging.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.core.config import get_settings

# Project root (parent of app/)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Markdown output: app/memory_files/youtube/scraped_YYYY-MM-DD.md
YOUTUBE_MARKDOWN_DIR = _PROJECT_ROOT / "app" / "memory_files" / "youtube"


@dataclass
class VideoRecord:
    channel_name: str
    channel_id: str
    video_id: str
    title: str
    summary: str
    language: str
    published_at: datetime | None


class YouTubeScraper:
    """
    Single-class YouTube scraper: channel list -> recent videos (last 7 days)
    -> title + description as summary -> markdown file per run date with metrics.
    """

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.youtube_api_key
        if not self._api_key:
            raise ValueError("YouTube Data API key required. Set YOUTUBE_API_KEY in .env")
        self._video_limit = settings.youtube_max_videos_per_channel
        self._lookback_days = settings.youtube_lookback_days
        self._cutoff: datetime | None = None

    def _client(self):
        return build("youtube", "v3", developerKey=self._api_key, cache_discovery=False)

    def _uploads_playlist_id(self, channel_id: str) -> str | None:
        try:
            resp = self._client().channels().list(
                part="contentDetails",
                id=channel_id,
                maxResults=1,
            ).execute()
            items = resp.get("items") or []
            if not items:
                print(f"[YouTubeScraper] Channel not found (no items): {channel_id}")
                return None
            pl = items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
            if not pl:
                print(f"[YouTubeScraper] No uploads playlist for channel: {channel_id}")
            return pl
        except HttpError as e:
            print(f"[YouTubeScraper] API error for channel {channel_id}: {e}")
            return None

    def _list_playlist_videos(self, playlist_id: str, max_results: int) -> list[dict[str, Any]]:
        try:
            client = self._client()
            request = client.playlistItems().list(
                part="snippet",
                playlistId=playlist_id,
                maxResults=min(max_results, 50),
            )
            items: list[dict[str, Any]] = []
            while request and len(items) < max_results:
                resp = request.execute()
                page = resp.get("items") or []
                items.extend(page)
                request = client.playlistItems().list_next(request, resp)
                if not page or len(items) >= max_results:
                    break
            return items[:max_results]
        except HttpError as e:
            print(f"[YouTubeScraper] API error listing playlist {playlist_id}: {e}")
            return []

    def _video_languages(self, video_ids: list[str]) -> dict[str, str]:
        """Fetch defaultAudioLanguage for video IDs (batch of 50). Returns video_id -> language."""
        out: dict[str, str] = {}
        if not video_ids:
            return out
        try:
            resp = self._client().videos().list(
                part="snippet",
                id=",".join(video_ids[:50]),
            ).execute()
            for item in resp.get("items") or []:
                vid = item.get("id")
                sn = item.get("snippet") or {}
                out[vid] = (sn.get("defaultAudioLanguage") or sn.get("defaultLanguage") or "").strip() or "en"
        except HttpError as e:
            print(f"[YouTubeScraper] API error fetching video languages: {e}")
        return out

    def _parse_published(self, published_str: str | None) -> datetime | None:
        if not published_str:
            return None
        try:
            return datetime.fromisoformat(published_str.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None

    def get_existing_video_ids_from_last_n_days(self, days: int = 14) -> set[str]:
        """
        Check scraped markdown files from the last `days` days and return the set of
        video_ids already present. Used to avoid re-adding videos when running the scraper again.
        """
        if not YOUTUBE_MARKDOWN_DIR.exists():
            print(f"[YouTubeScraper] No scraped dir yet: {YOUTUBE_MARKDOWN_DIR}")
            return set()
        today = date.today()
        cutoff = today - timedelta(days=days)
        existing: set[str] = set()
        prefix = "- **video_id**: "
        for path in YOUTUBE_MARKDOWN_DIR.glob("scraped_*.md"):
            try:
                # scraped_2026-03-09.md -> 2026-03-09
                stem = path.stem
                if not stem.startswith("scraped_"):
                    continue
                date_str = stem[8:]  # len("scraped_")
                file_date = date.fromisoformat(date_str)
                if file_date < cutoff:
                    continue
                text = path.read_text(encoding="utf-8")
                for line in text.splitlines():
                    line = line.strip()
                    if line.startswith(prefix):
                        vid = line[len(prefix) :].strip()
                        if vid:
                            existing.add(vid)
            except (ValueError, OSError) as e:
                print(f"[YouTubeScraper] Skip file {path}: {e}")
        print(f"[YouTubeScraper] Existing video_ids in scraped files (last {days} days): {len(existing)}")
        return existing

    def run(self, channels: list[dict[str, str]]) -> Path | None:
        """
        Run scraper for all channels. Filters to last 7 days, writes one markdown file
        named by today's date. Returns path to the written markdown file, or None if nothing written.
        """
        now = datetime.now(timezone.utc)
        self._cutoff = now - timedelta(days=self._lookback_days)
        run_date = now.date().isoformat()

        # Metrics
        print("[YouTubeScraper] === Configuration ===")
        print(f"[YouTubeScraper] Video limit from .env (youtube_max_videos_per_channel): {self._video_limit}")
        print(f"[YouTubeScraper] Lookback days: {self._lookback_days} (only videos since {self._cutoff.date()})")
        print(f"[YouTubeScraper] Run date for markdown filename: {run_date}")

        existing_video_ids = self.get_existing_video_ids_from_last_n_days(days=14)

        all_records: list[VideoRecord] = []
        channels_responded = 0
        per_channel_counts: dict[str, int] = {}
        per_channel_id_counts: dict[str, int] = {}
        skipped_already_fetched = 0

        for ch in channels:
            channel_id = ch.get("channel_id", "").strip()
            channel_name = ch.get("name", channel_id)
            if not channel_id:
                print(f"[YouTubeScraper] Skipping channel with empty channel_id: {ch}")
                continue

            print(f"[YouTubeScraper] Fetching channel: {channel_name} ({channel_id})")
            playlist_id = self._uploads_playlist_id(channel_id)
            if not playlist_id:
                continue
            channels_responded += 1

            raw_items = self._list_playlist_videos(playlist_id, self._video_limit)
            video_ids = []
            for item in raw_items:
                sn = item.get("snippet") or {}
                vid = sn.get("resourceId", {}).get("videoId")
                if not vid:
                    continue
                published_at = self._parse_published(sn.get("publishedAt"))
                if published_at and published_at < self._cutoff:
                    continue
                video_ids.append(vid)

            languages = self._video_languages(video_ids) if video_ids else {}

            count_this_channel = 0
            for item in raw_items:
                sn = item.get("snippet") or {}
                vid = sn.get("resourceId", {}).get("videoId")
                if not vid:
                    continue
                published_at = self._parse_published(sn.get("publishedAt"))
                if published_at and published_at < self._cutoff:
                    continue
                if vid in existing_video_ids:
                    skipped_already_fetched += 1
                    continue
                title = (sn.get("title") or "").strip()
                description = (sn.get("description") or "").strip()
                summary = description or title
                lang = languages.get(vid, "en")
                all_records.append(
                    VideoRecord(
                        channel_name=channel_name,
                        channel_id=channel_id,
                        video_id=vid,
                        title=title,
                        summary=summary,
                        language=lang,
                        published_at=published_at,
                    )
                )
                count_this_channel += 1
                existing_video_ids.add(vid)

            per_channel_counts[channel_name] = count_this_channel
            per_channel_id_counts[channel_id] = count_this_channel
            print(f"[YouTubeScraper]   -> Videos fetched for '{channel_name}': {count_this_channel}")

        print("[YouTubeScraper] === Metrics ===")
        print(f"[YouTubeScraper] Count of channel_ids that responded in this run: {channels_responded}")
        print("[YouTubeScraper] Videos fetched per channel name:")
        for name, n in per_channel_counts.items():
            print(f"[YouTubeScraper]   - {name}: {n}")
        print("[YouTubeScraper] Overall videos fetched per channel_id:")
        for cid, n in per_channel_id_counts.items():
            print(f"[YouTubeScraper]   - {cid}: {n}")
        print(f"[YouTubeScraper] Total videos in last {self._lookback_days} days (new only): {len(all_records)}")
        if skipped_already_fetched:
            print(f"[YouTubeScraper] Skipped (already in scraped files in last 14 days): {skipped_already_fetched}")

        if not all_records:
            print("[YouTubeScraper] No videos to write. Skipping markdown file.")
            return None

        YOUTUBE_MARKDOWN_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"scraped_{run_date}.md"
        out_path = YOUTUBE_MARKDOWN_DIR / filename
        self._write_markdown(out_path, all_records, run_date)
        print(f"[YouTubeScraper] Markdown written: {out_path}")
        return out_path

    def _write_markdown(self, path: Path, records: list[VideoRecord], run_date: str) -> None:
        lines = [
            f"# YouTube scraped data — {run_date}",
            "",
            f"Total videos: {len(records)} (last {self._lookback_days} days).",
            "",
            "---",
            "",
        ]
        for r in records:
            video_link = f"https://www.youtube.com/watch?v={r.video_id}"
            block = [
                f"## {r.title}",
                "",
                f"- **channel_name**: {r.channel_name}",
                f"- **channel_id**: {r.channel_id}",
                f"- **video_id**: {r.video_id}",
                f"- **video_link**: {video_link}",
                f"- **language**: {r.language}",
                f"- **published_at**: {r.published_at.isoformat() if r.published_at else ''}",
                "",
                "### Summary",
                "",
                r.summary[:5000] + ("..." if len(r.summary) > 5000 else ""),
                "",
                "---",
                "",
            ]
            lines.extend(block)
        path.write_text("\n".join(lines), encoding="utf-8")
