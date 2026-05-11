"""
dedupe_articles.py
articles_normalized/ の正規化JSONを読み込み、重複を除去して
articles_ready/ に保存する。

重複判定の方式（優先順）：
1. URL正規化後の完全一致（utm_*等を除去した後で比較）
2. primary_source_url 一致（同一ニュースを複数メディアが報道）
3. タイトルの類似度（difflib）→ 閾値以上で重複とみなす

設計上の注意：
- 重複と判定された記事は捨てずに duplicate_check フラグを立てる
- 重複元のarticle_idを記録し、ログで追跡できるようにする
- normalize_url() は utils.py に集約し、write_log.py と基準を共有する
"""

import json
import logging
from difflib import SequenceMatcher
from pathlib import Path
from datetime import datetime, timezone

import yaml

from utils import normalize_url

# ─── パス設定 ───────────────────────────────────────────────
BASE_DIR       = Path(__file__).parent
NORMALIZED_DIR = BASE_DIR / "articles_normalized"
READY_DIR      = BASE_DIR / "articles_ready"
SOURCES_CFG    = BASE_DIR / "rss_sources.yaml"
LOGS_DIR       = BASE_DIR / "logs"
READY_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOGS_DIR / "dedupe.log"),
    ],
)
log = logging.getLogger(__name__)


def load_similarity_threshold() -> float:
    try:
        with open(SOURCES_CFG, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return cfg["settings"].get("dedupe_title_similarity_threshold", 0.85)
    except Exception:
        return 0.85



def title_similarity(a: str, b: str) -> float:
    """2つのタイトル文字列の類似度を0〜1で返す。"""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def deduplicate(articles: list[dict], threshold: float) -> list[dict]:
    """
    記事リストを受け取り、重複フラグを付与して返す。
    重複判定（優先順）：
    1. URL正規化後の完全一致
    2. primary_source_url が同じ（同一ニュースを複数メディアが報道）
    3. タイトル類似度 >= threshold
    """
    seen_norm_urls:   dict[str, str] = {}   # normalized_url → article_id
    seen_primary_urls: dict[str, str] = {}  # primary_source_url → article_id
    seen_titles:      list[tuple[str, str]] = []  # [(title, article_id)]

    result = []
    for article in articles:
        url         = article["original"]["url"]
        title       = article["original"]["title"]
        aid         = article["article_id"]
        norm_url    = normalize_url(url)
        primary_url      = normalize_url(article.get("primary_source", {}).get("url") or "")
        primary_detected = article.get("primary_source", {}).get("detected", False)

        # ① 正規化URL重複チェック
        if norm_url and norm_url in seen_norm_urls:
            original_id = seen_norm_urls[norm_url]
            log.info(f"  Duplicate (URL normalized): {title[:60]!r} → original={original_id[:8]}")
            article["duplicate_check"] = {
                "is_duplicate": True,
                "duplicate_of": original_id,
                "method": "url_normalized",
            }
            result.append(article)
            continue

        # ② primary_source_url 重複チェック（一次ソースが同じ＝同じニュース）
        if primary_detected and primary_url and primary_url in seen_primary_urls:
            original_id = seen_primary_urls[primary_url]
            log.info(f"  Duplicate (primary_source_url): {title[:60]!r} → original={original_id[:8]}")
            article["duplicate_check"] = {
                "is_duplicate": True,
                "duplicate_of": original_id,
                "method": "primary_source_url",
            }
            result.append(article)
            continue

        # ③ タイトル類似度チェック
        duplicate_found = False
        for seen_title, seen_id in seen_titles:
            sim = title_similarity(title, seen_title)
            if sim >= threshold:
                log.info(
                    f"  Duplicate (title sim={sim:.2f}): {title[:60]!r} "
                    f"≈ {seen_title[:60]!r} → original={seen_id[:8]}"
                )
                article["duplicate_check"] = {
                    "is_duplicate": True,
                    "duplicate_of": seen_id,
                    "method": f"title_similarity_{sim:.2f}",
                }
                duplicate_found = True
                break

        if duplicate_found:
            result.append(article)
            continue

        # 重複なし → 登録
        if norm_url:
            seen_norm_urls[norm_url] = aid
        if primary_detected and primary_url:
            seen_primary_urls[primary_url] = aid
        if title:
            seen_titles.append((title, aid))

        article["duplicate_check"] = {
            "is_duplicate": False,
            "duplicate_of": None,
            "method": None,
        }
        article["deduped_at"] = datetime.now(timezone.utc).isoformat()
        result.append(article)

    return result


def save_ready(articles: list[dict], source_filename: str) -> Path:
    """重複除去済み記事（is_duplicate=False のみ）を articles_ready/ に保存する。"""
    ready = [a for a in articles if not a["duplicate_check"]["is_duplicate"]]
    out_path = READY_DIR / source_filename

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(ready, f, ensure_ascii=False, indent=2)

    total = len(articles)
    dupes = total - len(ready)
    log.info(f"  Ready: {len(ready)}/{total} articles saved (dropped {dupes} duplicates)")
    return out_path


def main():
    threshold = load_similarity_threshold()
    log.info(f"Deduplication threshold: {threshold}")

    normalized_files = sorted(NORMALIZED_DIR.glob("*.json"))
    if not normalized_files:
        log.warning("No normalized JSON files found.")
        return

    # 全ファイルをまとめて読み込み、クロスファイルで重複除去する
    all_articles = []
    file_map: dict[str, list[dict]] = {}

    for path in normalized_files:
        try:
            with open(path, encoding="utf-8") as f:
                articles = json.load(f)
            file_map[path.name] = articles
            all_articles.extend(articles)
        except Exception as e:
            log.error(f"Failed to read {path.name}: {e}")

    log.info(f"Total articles before dedup: {len(all_articles)}")
    deduped_all = deduplicate(all_articles, threshold)

    # 重複フラグをファイルごとに書き戻す
    deduped_by_id = {a["article_id"]: a for a in deduped_all}

    for filename, articles in file_map.items():
        updated = [deduped_by_id[a["article_id"]] for a in articles if a["article_id"] in deduped_by_id]
        save_ready(updated, filename)

    total_ready = sum(
        1 for a in deduped_all if not a["duplicate_check"]["is_duplicate"]
    )
    log.info(f"Deduplication complete. Ready articles: {total_ready}/{len(all_articles)}")


if __name__ == "__main__":
    main()
