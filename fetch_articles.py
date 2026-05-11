"""
fetch_articles.py
RSS フィードを取得し、articles_raw/ に生JSONとして保存する。
スコアリングは呼ばない。まず「データが安定して取れるか」を確認する段階。
"""

import json
import uuid
import yaml
import logging
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests
from bs4 import BeautifulSoup

# ─── パス設定 ───────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
RAW_DIR     = BASE_DIR / "articles_raw"
LOGS_DIR    = BASE_DIR / "logs"
SOURCES_CFG = BASE_DIR / "rss_sources.yaml"
RAW_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# ─── ロギング ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOGS_DIR / "fetch.log"),
    ],
)
log = logging.getLogger(__name__)


# ─── ユーティリティ ──────────────────────────────────────────

def load_sources() -> dict:
    with open(SOURCES_CFG, encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_published_at(entry) -> str | None:
    """feedparserのエントリから公開日時をISO 8601文字列で取得する。"""
    try:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            return dt.isoformat()
        if hasattr(entry, "updated_parsed") and entry.updated_parsed:
            dt = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
            return dt.isoformat()
    except Exception:
        pass
    return None


def extract_body_text(entry, max_chars: int = 3000) -> str:
    """RSS エントリから本文テキストを抽出する。HTMLタグを除去する。"""
    raw_html = ""
    if hasattr(entry, "content") and entry.content:
        raw_html = entry.content[0].get("value", "")
    elif hasattr(entry, "summary"):
        raw_html = entry.summary or ""

    text = BeautifulSoup(raw_html, "html.parser").get_text(separator=" ").strip()
    return text[:max_chars] if text else ""


def detect_primary_source(entry) -> dict:
    """
    記事内のリンクから一次ソース候補を検出する。
    公式ブログ・プレスリリース・政府・学術ドメインを優先する。
    """
    primary_domains = {
        "official_blog": [
            "openai.com", "anthropic.com", "deepmind.google", "ai.google",
            "research.google", "huggingface.co", "mistral.ai", "meta.ai",
            "blogs.microsoft.com", "aws.amazon.com/blogs",
        ],
        "press_release": [
            "prnewswire.com", "businesswire.com", "globenewswire.com",
            "accesswire.com", "ir.", "investor.",
        ],
        "government": [
            ".gov", ".go.jp", "europa.eu",
        ],
        "academic_paper": [
            "arxiv.org", "openreview.net", "proceedings.mlr.press",
        ],
        "sec_filing": [
            "sec.gov", "edgar.",
        ],
    }

    links = []
    if hasattr(entry, "links"):
        links = [l.get("href", "") for l in entry.links]
    if hasattr(entry, "link"):
        links.append(entry.link or "")

    for source_type, domains in primary_domains.items():
        for link in links:
            for domain in domains:
                if domain in link:
                    return {
                        "detected": True,
                        "url": link,
                        "type": source_type,
                    }

    return {"detected": False, "url": None, "type": "none"}


def fetch_feed(source: dict, settings: dict) -> list[dict]:
    """1つのRSSソースを取得し、raw記事リストを返す。"""
    url = source["url"]
    log.info(f"Fetching: {source['name']} ({url})")

    try:
        resp = requests.get(
            url,
            timeout=settings.get("request_timeout_seconds", 15),
            headers={"User-Agent": "AINewsPipeline/1.0"},
        )
        resp.raise_for_status()
    except Exception as e:
        log.error(f"Failed to fetch {url}: {e}")
        return []

    feed = feedparser.parse(resp.content)
    max_articles = settings.get("max_articles_per_source", 20)
    max_chars    = settings.get("body_text_max_chars", 3000)

    articles = []
    for entry in feed.entries[:max_articles]:
        article_id   = str(uuid.uuid4())
        published_at = parse_published_at(entry)
        body_text    = extract_body_text(entry, max_chars)
        primary_src  = detect_primary_source(entry)

        article = {
            "article_id":  article_id,
            "fetched_at":  datetime.now(timezone.utc).isoformat(),
            "source": {
                "name":       source["name"],
                "url":        source["url"],
                "type":       source["type"],
                "trust_base": source.get("trust_base", 60),
                "language":   source.get("language", "en"),
            },
            "original": {
                "title":        entry.get("title", "").strip(),
                "url":          entry.get("link", "").strip(),
                "published_at": published_at,
                "body_text":    body_text,
                "language":     source.get("language", "en"),
            },
            "primary_source": primary_src,
            "duplicate_check": {
                "is_duplicate":  False,
                "duplicate_of":  None,
            },
        }
        articles.append(article)

    log.info(f"  → {len(articles)} articles fetched from {source['name']}")
    return articles


def save_raw(articles: list[dict], source_name: str) -> Path:
    """記事リストをJSONファイルとして articles_raw/ に保存する。"""
    timestamp   = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_name   = source_name.replace(" ", "_").lower()
    output_path = RAW_DIR / f"{timestamp}_{safe_name}.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)

    log.info(f"  → Saved: {output_path.name} ({len(articles)} articles)")
    return output_path


# ─── メイン ─────────────────────────────────────────────────

def main():
    config   = load_sources()
    sources  = config["sources"]
    settings = config["settings"]

    total = 0
    for source in sources:
        articles = fetch_feed(source, settings)
        if articles:
            save_raw(articles, source["name"])
            total += len(articles)

    log.info(f"Fetch complete. Total articles: {total}")


if __name__ == "__main__":
    main()
