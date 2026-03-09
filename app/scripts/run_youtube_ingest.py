"""Run YouTube scraper for sample AI channels. Writes markdown by date and prints its path."""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from app.core.config import get_settings
from app.ingestion import SAMPLE_AI_CHANNELS, YouTubeScraper


def main() -> None:
    api_key = get_settings().youtube_api_key
    if not api_key:
        print("ERROR: Set YOUTUBE_API_KEY in .env to run YouTube scraper.")
        sys.exit(1)

    scraper = YouTubeScraper(api_key=api_key)
    out_path = scraper.run(SAMPLE_AI_CHANNELS)

    if out_path and out_path.exists():
        print("")
        print("--- Verification ---")
        print(f"Markdown file location: {out_path.resolve()}")
    else:
        print("No markdown file was written (no videos in last 7 days or no channels responded).")


if __name__ == "__main__":
    main()
