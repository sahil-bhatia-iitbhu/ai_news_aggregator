"""Run OpenAI research scraper. Primary: openai.com/research/index/; fallback: non-OpenAI search results."""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from app.ingestion import OpenAIResearchScraper


def main() -> None:
    scraper = OpenAIResearchScraper()
    out_path = scraper.run()

    if out_path and out_path.exists():
        print("")
        print("--- Verification ---")
        print(f"Markdown file location: {out_path.resolve()}")
    else:
        print("No markdown file was written (no items in last 7 days).")


if __name__ == "__main__":
    main()
