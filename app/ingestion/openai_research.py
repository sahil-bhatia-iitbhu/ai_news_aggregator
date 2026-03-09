"""
OpenAI research scraper: primary = openai.com/research/index/ (last 7 days).
1st fallback = OpenAI RSS feed; 2nd fallback = DuckDuckGo search (non-OpenAI only).
Output: markdown (scraped_YYYY-MM-DD.md) with blog title, link, source, date, main text only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS

from app.core.config import get_settings

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
OPENAI_RESEARCH_INDEX_URL = "https://openai.com/research/index/"
OPENAI_RSS_URL = "https://openai.com/news/rss.xml"
OPENAI_MARKDOWN_DIR = _PROJECT_ROOT / "app" / "memory_files" / "openai_research"
FALLBACK_SEARCH_KEYWORD = "OpenAI research blog last 7 days"
FALLBACK_MAX_NON_OPENAI_LINKS = 3
OPENAI_DOMAIN = "openai.com"
# Browser-like User-Agent to reduce 403 from some sites
DEFAULT_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; rv:130.0) Gecko/20100101 Firefox/130.0"}

# Selectors to try for main article content (in order)
MAIN_CONTENT_SELECTORS = [
    "article",
    "[role='main']",
    "main",
    "[itemprop='articleBody']",
    ".article-body",
    ".post-content",
    ".entry-content",
    ".content-area",
    ".article-content",
    ".post-body",
    ".article__body",
    ".post__body",
    ".story-body",
    ".c-entry-content",
    "#article-body",
    "#content",
]

# Short UI phrases to drop from extracted text (noise)
NOISE_PHRASES = frozenset([
    "skip to main content", "the homepage", "follow", "subscribe", "sign up",
    "sign in", "log in", "login", "comments", "loading comments", "most popular",
    "related", "advertiser content", "by submitting your email", "privacy policy",
    "terms of service", "terms of use", "cookie policy", "newsletter", "daily digest",
    "contact sales", "keep reading", "view all", "join the new era", "more than 1 million",
])


@dataclass
class BlogRecord:
    title: str
    link: str
    source: str
    blog_date: str
    text_content: str


def _parse_date_from_meta(soup: BeautifulSoup) -> str | None:
    """Try to get article date from common meta tags or time elements."""
    for selector, attr in [
        ('meta[property="article:published_time"]', "content"),
        ('meta[name="published"]', "content"),
        ('meta[name="date"]', "content"),
        ('time[datetime]', "datetime"),
    ]:
        el = soup.select_one(selector)
        if el and el.get(attr):
            return el.get(attr)
    return None


def _get_main_content_node(soup: BeautifulSoup) -> BeautifulSoup | None:
    """Find the node that contains the main article body (not nav/sidebar/footer)."""
    for sel in MAIN_CONTENT_SELECTORS:
        node = soup.select_one(sel)
        if node and len((node.get_text() or "").strip()) > 100:
            return node
    # Fallback: container that holds the first h1
    h1 = soup.find("h1")
    if h1:
        parent = h1.find_parent(["article", "main", "div"])
        if parent and len((parent.get_text() or "").strip()) > 100:
            return parent
    return None


def _is_noise_line(line: str) -> bool:
    """True if line looks like UI chrome rather than article content."""
    s = line.strip().lower()
    if len(s) < 3:
        return True
    for phrase in NOISE_PHRASES:
        if phrase in s and len(s) < 120:
            return True
    return False


def _extract_main_content(soup: BeautifulSoup, max_chars: int = 50000) -> str:
    """Extract only the core main article content; omit nav, sidebar, footer, and UI noise."""
    main_node = _get_main_content_node(soup)
    if main_node:
        for tag in main_node.find_all(["script", "style", "nav", "aside"]):
            tag.decompose()
        text = main_node.get_text(separator="\n", strip=True)
    else:
        for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        body = soup.find("body") or soup
        text = (body.get_text(separator="\n", strip=True) if body else "") or ""

    lines = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or _is_noise_line(ln):
            continue
        lines.append(ln)
    combined = "\n".join(lines)
    if len(combined) > max_chars:
        return combined[:max_chars] + "\n..."
    return combined


class OpenAIResearchScraper:
    """
    Scrape OpenAI research: primary = openai.com/research/index/ (last 7 days);
    1st fallback = OpenAI RSS feed; 2nd fallback = DuckDuckGo (non-OpenAI only).
    Extracts only main article content to reduce noise in markdown.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._lookback_days = settings.openai_research_lookback_days
        self._cutoff: datetime | None = None

    def run(self) -> Path | None:
        """
        Run scraper. Primary: OpenAI research index; fallback: non-OpenAI search results.
        Returns path to written markdown file or None.
        """
        now = datetime.now(timezone.utc)
        self._cutoff = now - timedelta(days=self._lookback_days)
        run_date = now.date().isoformat()

        print("[OpenAIResearchScraper] === Configuration ===")
        print(f"[OpenAIResearchScraper] Lookback days: {self._lookback_days} (since {self._cutoff.date()})")
        print(f"[OpenAIResearchScraper] Run date for markdown: {run_date}")

        records: list[BlogRecord] = []

        # Primary: scrape OpenAI research index
        print("[OpenAIResearchScraper] Primary: fetching OpenAI research index...")
        primary_records = self._scrape_primary()
        if primary_records:
            records.extend(primary_records)
            print(f"[OpenAIResearchScraper] Primary succeeded: {len(primary_records)} item(s)")

        # 1st fallback: OpenAI RSS feed (only if primary yielded nothing)
        if not records:
            print("[OpenAIResearchScraper] 1st fallback: fetching OpenAI RSS feed...")
            rss_records = self._scrape_rss_openai()
            if rss_records:
                records.extend(rss_records)
                print(f"[OpenAIResearchScraper] RSS succeeded: {len(rss_records)} item(s)")

        # 2nd fallback: non-OpenAI search results (only if still no records)
        if not records:
            print("[OpenAIResearchScraper] 2nd fallback: DuckDuckGo search (non-OpenAI links only)...")
            search_records = self._scrape_fallback_non_openai()
            records.extend(search_records)
            print(f"[OpenAIResearchScraper] Search fallback: {len(search_records)} non-OpenAI blog(s)")

        if not records:
            print("[OpenAIResearchScraper] No items to write. Skipping markdown.")
            return None

        OPENAI_MARKDOWN_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"scraped_{run_date}.md"
        out_path = OPENAI_MARKDOWN_DIR / filename
        self._write_markdown(out_path, records, run_date)
        print(f"[OpenAIResearchScraper] Markdown written: {out_path}")
        return out_path

    def _scrape_primary(self) -> list[BlogRecord]:
        """Fetch openai.com/research/index/ and parse articles from last 7 days."""
        try:
            with httpx.Client(follow_redirects=True, timeout=30, headers=DEFAULT_HEADERS) as client:
                resp = client.get(OPENAI_RESEARCH_INDEX_URL)
                resp.raise_for_status()
                html = resp.text
        except Exception as e:
            print(f"[OpenAIResearchScraper] Primary fetch error: {e}")
            return []

        soup = BeautifulSoup(html, "html.parser")
        records: list[BlogRecord] = []

        # Try to find links to research articles (e.g. /research/...)
        base = "https://openai.com"
        seen_links: set[str] = set()

        for a in soup.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            if not href or not href.startswith("/research/") or href == "/research/index/":
                continue
            if "?" in href:
                href = href.split("?")[0]
            full_url = href if href.startswith("http") else (base + href)
            if full_url in seen_links:
                continue
            seen_links.add(full_url)

            # Fetch article page for title, date, content
            rec = self._fetch_openai_article(full_url)
            if rec and self._is_in_lookback(rec.blog_date):
                records.append(rec)
                if len(records) >= 20:
                    break

        return records

    def _is_in_lookback(self, blog_date_str: str) -> bool:
        """Return True if blog_date_str is within lookback window."""
        if not blog_date_str or not self._cutoff:
            return True
        try:
            # Support ISO and YYYY-MM-DD
            s = blog_date_str.strip()[:10]
            d = date.fromisoformat(s)
            return datetime(d.year, d.month, d.day, tzinfo=timezone.utc) >= self._cutoff
        except (ValueError, TypeError):
            return True

    def _fetch_openai_article(self, url: str) -> BlogRecord | None:
        """Fetch one OpenAI article page and return BlogRecord or None."""
        try:
            with httpx.Client(follow_redirects=True, timeout=25, headers=DEFAULT_HEADERS) as client:
                resp = client.get(url)
                resp.raise_for_status()
                html = resp.text
        except Exception as e:
            print(f"[OpenAIResearchScraper] Skip article {url}: {e}")
            return None

        soup = BeautifulSoup(html, "html.parser")
        title = ""
        h1 = soup.find("h1")
        if h1:
            title = (h1.get_text() or "").strip()
        if not title:
            og = soup.select_one('meta[property="og:title"]')
            if og and og.get("content"):
                title = og.get("content").strip()
        if not title:
            title = url.split("/")[-1] or "OpenAI Research"

        blog_date = ""
        raw = _parse_date_from_meta(soup)
        if raw:
            blog_date = raw[:10] if len(raw) >= 10 else raw
        if not blog_date:
            blog_date = date.today().isoformat()

        text_content = _extract_main_content(soup)
        return BlogRecord(
            title=title,
            link=url,
            source=OPENAI_DOMAIN,
            blog_date=blog_date,
            text_content=text_content or "(no text extracted)",
        )

    def _scrape_rss_openai(self) -> list[BlogRecord]:
        """1st fallback: parse OpenAI RSS, filter last 7 days, fetch each link and extract main content."""
        records: list[BlogRecord] = []
        try:
            with httpx.Client(follow_redirects=True, timeout=25, headers=DEFAULT_HEADERS) as client:
                resp = client.get(OPENAI_RSS_URL)
                resp.raise_for_status()
                feed = feedparser.parse(resp.text)
        except Exception as e:
            print(f"[OpenAIResearchScraper] RSS fetch error: {e}")
            return records

        for entry in feed.get("entries") or []:
            link = (entry.get("link") or "").strip()
            if not link or OPENAI_DOMAIN not in link:
                continue
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            if published:
                try:
                    pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
                    if pub_dt < self._cutoff:
                        continue
                    blog_date = pub_dt.date().isoformat()
                except (TypeError, ValueError):
                    blog_date = entry.get("published", "")[:10] or date.today().isoformat()
            else:
                blog_date = (entry.get("published") or entry.get("updated") or "")[:10] or date.today().isoformat()

            title = (entry.get("title") or "").strip() or link
            rec = self._fetch_openai_article(link)
            if rec:
                rec = BlogRecord(
                    title=title,
                    link=link,
                    source=OPENAI_DOMAIN,
                    blog_date=blog_date,
                    text_content=rec.text_content,
                )
                records.append(rec)
            if len(records) >= 15:
                break
        return records

    def _scrape_fallback_non_openai(self) -> list[BlogRecord]:
        """Search DuckDuckGo for keyword, exclude openai.com, scrape up to 2-3 links."""
        records: list[BlogRecord] = []
        try:
            with DDGS() as ddgs:
                # Iterate search results; duckduckgo_search returns dicts with 'href', 'title', etc.
                results = list(ddgs.text(FALLBACK_SEARCH_KEYWORD, max_results=15))
        except Exception as e:
            print(f"[OpenAIResearchScraper] Fallback search error: {e}")
            return records

        # Filter out openai.com and take first N non-OpenAI
        candidates: list[tuple[str, str]] = []
        for r in results:
            if not isinstance(r, dict):
                continue
            href = (r.get("href") or r.get("url") or "").strip()
            title = (r.get("title") or "").strip() or href
            if not href or OPENAI_DOMAIN in href.lower():
                continue
            candidates.append((href, title))
            if len(candidates) >= FALLBACK_MAX_NON_OPENAI_LINKS:
                break

        for url, default_title in candidates:
            rec = self._fetch_third_party_article(url, default_title)
            if rec:
                records.append(rec)

        return records

    def _fetch_third_party_article(self, url: str, default_title: str) -> BlogRecord | None:
        """Fetch a third-party page and return BlogRecord. Source = domain."""
        try:
            with httpx.Client(follow_redirects=True, timeout=25, headers=DEFAULT_HEADERS) as client:
                resp = client.get(url)
                resp.raise_for_status()
                html = resp.text
        except Exception as e:
            print(f"[OpenAIResearchScraper] Skip fallback URL {url}: {e}")
            return None

        soup = BeautifulSoup(html, "html.parser")
        title = default_title
        h1 = soup.find("h1")
        if h1:
            t = (h1.get_text() or "").strip()
            if t:
                title = t
        if not title:
            og = soup.select_one('meta[property="og:title"]')
            if og and og.get("content"):
                title = og.get("content").strip()
        if not title:
            title = url

        blog_date = ""
        raw = _parse_date_from_meta(soup)
        if raw:
            blog_date = raw[:10] if len(raw) >= 10 else raw
        if not blog_date:
            blog_date = date.today().isoformat()

        text_content = _extract_main_content(soup)
        try:
            netloc = urlparse(url).netloc or "unknown"
            source = netloc.replace("www.", "")
        except Exception:
            source = "unknown"

        return BlogRecord(
            title=title,
            link=url,
            source=source,
            blog_date=blog_date,
            text_content=text_content or "(no text extracted)",
        )

    def _write_markdown(self, path: Path, records: list[BlogRecord], run_date: str) -> None:
        lines = [
            f"# OpenAI research scraped data — {run_date}",
            "",
            f"Total items: {len(records)} (last {self._lookback_days} days).",
            "",
            "---",
            "",
        ]
        for r in records:
            block = [
                f"## {r.title}",
                "",
                f"- **blog_title**: {r.title}",
                f"- **blog_link**: {r.link}",
                f"- **blog_source**: {r.source}",
                f"- **blog_date**: {r.blog_date}",
                "",
                "### Text content",
                "",
                r.text_content,
                "",
                "---",
                "",
            ]
            lines.extend(block)
        path.write_text("\n".join(lines), encoding="utf-8")
