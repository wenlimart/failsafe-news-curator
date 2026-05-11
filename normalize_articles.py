"""
normalize_articles.py
articles_raw/ の生JSONを読み込み、スキーマに合わせて正規化し
articles_normalized/ に保存する。

正規化でやること：
- タイトル・URL の空白除去・nullチェック
- published_at が取れない場合に警告フラグを立てる
- body_text が極端に短い場合に警告フラグを立てる
- source.type の値を既知の選択肢に限定する
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timezone

# ─── パス設定 ───────────────────────────────────────────────
BASE_DIR        = Path(__file__).parent
RAW_DIR         = BASE_DIR / "articles_raw"
NORMALIZED_DIR  = BASE_DIR / "articles_normalized"
LOGS_DIR        = BASE_DIR / "logs"
NORMALIZED_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOGS_DIR / "normalize.log"),
    ],
)
log = logging.getLogger(__name__)

# ─── 定数 ───────────────────────────────────────────────────
VALID_SOURCE_TYPES = {
    "official_blog", "press_release", "major_media",
    "specialist_media", "aggregator", "unknown",
}
VALID_PRIMARY_TYPES = {
    "official_blog", "press_release", "sec_filing",
    "academic_paper", "government", "none",
}
BODY_TEXT_MIN_CHARS = 100   # これ未満は警告


# ─── 正規化処理 ──────────────────────────────────────────────

def normalize_article(raw: dict) -> dict:
    """1件の記事を正規化し、QAフラグを付与して返す。"""
    qa_flags = []

    # タイトル
    title = (raw.get("original", {}).get("title") or "").strip()
    if not title:
        qa_flags.append("title_missing")

    # URL
    url = (raw.get("original", {}).get("url") or "").strip()
    if not url or not url.startswith("http"):
        qa_flags.append("url_invalid")

    # 公開日時
    published_at = raw.get("original", {}).get("published_at")
    if not published_at:
        qa_flags.append("published_at_missing")

    # 本文
    body_text = (raw.get("original", {}).get("body_text") or "").strip()
    if len(body_text) < BODY_TEXT_MIN_CHARS:
        qa_flags.append("body_text_too_short")

    # source.type の正規化
    source_type = raw.get("source", {}).get("type", "unknown")
    if source_type not in VALID_SOURCE_TYPES:
        log.warning(f"Unknown source type '{source_type}', falling back to 'unknown'")
        source_type = "unknown"

    # primary_source.type の正規化
    ps = raw.get("primary_source", {})
    ps_type = ps.get("type", "none")
    if ps_type not in VALID_PRIMARY_TYPES:
        ps_type = "none"

    normalized = {
        "article_id":  raw["article_id"],
        "fetched_at":  raw["fetched_at"],
        "normalized_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "name":       raw["source"]["name"],
            "url":        raw["source"]["url"],
            "type":       source_type,
            "trust_base": raw["source"].get("trust_base", 60),
            "language":   raw["source"].get("language", "en"),
        },
        "original": {
            "title":        title,
            "url":          url,
            "published_at": published_at,
            "body_text":    body_text,
            "language":     raw["original"].get("language", "en"),
        },
        "primary_source": {
            "detected": ps.get("detected", False),
            "url":      ps.get("url"),
            "type":     ps_type,
        },
        "duplicate_check": raw.get("duplicate_check", {
            "is_duplicate": False,
            "duplicate_of": None,
        }),
        "qa": {
            "flags":    qa_flags,
            "has_warnings": len(qa_flags) > 0,
        },
    }
    return normalized


def process_file(raw_path: Path) -> Path | None:
    """1ファイル分の生JSONを正規化し、normalized/ に保存する。"""
    try:
        with open(raw_path, encoding="utf-8") as f:
            raw_articles = json.load(f)
    except Exception as e:
        log.error(f"Failed to read {raw_path.name}: {e}")
        return None

    normalized = [normalize_article(a) for a in raw_articles]

    # QA警告をサマリーとして出力
    warned = [a for a in normalized if a["qa"]["has_warnings"]]
    if warned:
        log.warning(
            f"{raw_path.name}: {len(warned)}/{len(normalized)} articles have QA flags"
        )
        for a in warned:
            log.warning(f"  [{a['article_id'][:8]}] {a['original']['title'][:60]} → {a['qa']['flags']}")

    out_path = NORMALIZED_DIR / raw_path.name
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)

    log.info(f"Normalized: {raw_path.name} → {out_path.name} ({len(normalized)} articles)")
    return out_path


def main():
    raw_files = sorted(RAW_DIR.glob("*.json"))
    if not raw_files:
        log.warning("No raw JSON files found in articles_raw/")
        return

    for raw_path in raw_files:
        process_file(raw_path)

    log.info("Normalization complete.")


if __name__ == "__main__":
    main()
