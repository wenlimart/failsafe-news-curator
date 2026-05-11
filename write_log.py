"""
write_log.py
articles_ready/ の記事を読み込み、news_review_log.csv に追記する。
この時点ではスコアリング前なので、スコア列は空欄で記録する。
Human Review 結果は後から手動で記入する。
"""

import csv
import json
import logging
import uuid
from pathlib import Path
from datetime import datetime, timezone

from utils import normalize_url

# ─── パス設定 ───────────────────────────────────────────────
BASE_DIR  = Path(__file__).parent
READY_DIR = BASE_DIR / "articles_ready"
LOGS_DIR  = BASE_DIR / "logs"
LOG_CSV   = LOGS_DIR / "news_review_log.csv"
LOGS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOGS_DIR / "write_log.log"),
    ],
)
log = logging.getLogger(__name__)

# ─── CSVカラム定義 ───────────────────────────────────────────
# 設計書v1.2 / 実装仕様書 v1.0 のログスキーマに準拠
COLUMNS = [
    # 記事基本情報
    "log_id",
    "article_id",
    "logged_at",
    "article_title",
    "article_url",
    "source_name",
    "source_type",
    "language",
    "published_at",
    "primary_source_detected",
    "primary_source_url",
    "primary_source_type",

    # QAフラグ（正規化フェーズ）
    "qa_flags",

    # スコアリング結果（score_articles.py 実行後に記入）
    "importance",
    "trust",
    "risk",
    "freshness",
    "expression_risk",
    "rule_based_escalated",
    "escalation_keywords",
    "trust_category",
    "content_qc_passed",
    "content_qc_failed_checks",

    # 判定結果
    "verdict",
    "ai_verdict_reason",
    "prompt_version",

    # 人間レビュー（手動記入）
    "human_reviewed",
    "reviewed_at",
    "reviewer",
    "human_result",          # approved / approved_with_edit / rejected
    "correction_reason",
    "false_positive",        # AIが止めたが本来通してよかった
    "false_negative",        # AIが通したが本来止めるべきだった
    "error_type",            # missed_high_risk / over_blocked / etc.
    "rule_improvement_suggestion",
]


def load_existing_ids_and_urls() -> tuple[set[str], set[str]]:
    """
    既存ログに記録済みの article_id と正規化済み article_url を返す。
    article_id はUUID新規生成のため定期実行で変わる。
    normalize_url() を通した article_url で判定することで
    UTMパラメータの有無によるログ重複を防ぐ。
    """
    if not LOG_CSV.exists():
        return set(), set()
    existing_ids  = set()
    existing_urls = set()
    with open(LOG_CSV, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("article_id"):
                existing_ids.add(row["article_id"])
            if row.get("article_url"):
                existing_urls.add(normalize_url(row["article_url"]))
    return existing_ids, existing_urls


def article_to_row(article: dict) -> dict:
    """記事dictをCSV1行分のdictに変換する。スコア列は空欄。"""
    now    = datetime.now(timezone.utc).isoformat()
    orig   = article.get("original", {})
    src    = article.get("source", {})
    ps     = article.get("primary_source", {})
    qa     = article.get("qa", {})

    return {
        "log_id":               str(uuid.uuid4()),
        "article_id":           article["article_id"],
        "logged_at":            now,
        "article_title":        orig.get("title", ""),
        "article_url":          orig.get("url", ""),
        "source_name":          src.get("name", ""),
        "source_type":          src.get("type", ""),
        "language":             orig.get("language", ""),
        "published_at":         orig.get("published_at", ""),
        "primary_source_detected": ps.get("detected", False),
        "primary_source_url":   ps.get("url", ""),
        "primary_source_type":  ps.get("type", "none"),
        "qa_flags":             "|".join(qa.get("flags", [])),

        # スコアリング列（空欄 → score_articles.py 実行後に書き込む）
        "importance":           "",
        "trust":                "",
        "risk":                 "",
        "freshness":            "",
        "expression_risk":      "",
        "rule_based_escalated": "",
        "escalation_keywords":  "",
        "trust_category":       "",
        "content_qc_passed":    "",
        "content_qc_failed_checks": "",

        # 判定列（空欄）
        "verdict":              "",
        "ai_verdict_reason":    "",
        "prompt_version":       "",

        # 人間レビュー列（手動記入用）
        "human_reviewed":       "FALSE",
        "reviewed_at":          "",
        "reviewer":             "",
        "human_result":         "",
        "correction_reason":    "",
        "false_positive":       "",
        "false_negative":       "",
        "error_type":           "",
        "rule_improvement_suggestion": "",
    }


def write_log(articles: list[dict], existing_ids: set[str], existing_urls: set[str]) -> int:
    """新規記事をCSVに追記し、追記件数を返す。
    article_id と normalize_url(article_url) の両方で既出判定する。
    dedupe_articles.py と同じ正規化基準を使うことでログ重複を防ぐ。
    """
    new_articles = [
        a for a in articles
        if a["article_id"] not in existing_ids
        and normalize_url(a.get("original", {}).get("url", "")) not in existing_urls
        and not a.get("duplicate_check", {}).get("is_duplicate", False)
    ]

    if not new_articles:
        return 0

    write_header = not LOG_CSV.exists()
    with open(LOG_CSV, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        if write_header:
            writer.writeheader()
        for article in new_articles:
            writer.writerow(article_to_row(article))

    return len(new_articles)


def main():
    existing_ids, existing_urls = load_existing_ids_and_urls()
    log.info(f"Existing log entries: {len(existing_ids)} (tracked URLs: {len(existing_urls)})")

    ready_files = sorted(READY_DIR.glob("*.json"))
    if not ready_files:
        log.warning("No ready JSON files found.")
        return

    total_written = 0
    for path in ready_files:
        try:
            with open(path, encoding="utf-8") as f:
                articles = json.load(f)
        except Exception as e:
            log.error(f"Failed to read {path.name}: {e}")
            continue

        written = write_log(articles, existing_ids, existing_urls)
        # 実際に書いた記事のID/URLだけを既出セットに追加（同一実行内の重複防止）
        for a in articles:
            url = normalize_url(a.get("original", {}).get("url", ""))
            if a["article_id"] not in existing_ids and url not in existing_urls \
                    and not a.get("duplicate_check", {}).get("is_duplicate", False):
                existing_ids.add(a["article_id"])
                existing_urls.add(url)
        log.info(f"{path.name}: {written} new articles written to log")
        total_written += written

    log.info(f"write_log complete. Total new entries: {total_written}")
    log.info(f"Log file: {LOG_CSV}")


if __name__ == "__main__":
    main()
