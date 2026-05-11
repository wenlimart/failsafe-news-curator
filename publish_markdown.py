"""
publish_markdown.py
scored_articles/ の記事をMarkdownに変換し、publish/ に保存する。

出力ファイル：
  publish/YYYY-MM-DD.md   日付別まとめ（draft_auto のみ）
  publish/review.md       human_review 一覧（閾値調整用）
  publish/index.md        全体インデックス

目的：
  - draft_auto の記事を実際に読んで品質を確認する
  - human_review の記事を一覧で見て、閾値の妥当性を判断する
  - blocked の理由を可視化して、過剰ブロックを検出する
"""

import json
import glob
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ─── パス設定 ───────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
SCORED_DIR  = BASE_DIR / "scored_articles"
PUBLISH_DIR = BASE_DIR / "publish"
LOGS_DIR    = BASE_DIR / "logs"
PUBLISH_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOGS_DIR / "publish.log"),
    ],
)
log = logging.getLogger(__name__)


# ─── ユーティリティ ──────────────────────────────────────────

def load_scored_articles() -> list[dict]:
    """scored_articles/ の全JSONを読み込んで返す。"""
    rows = []
    for path in sorted(glob.glob(str(SCORED_DIR / "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                rows.extend(json.load(f))
        except Exception as e:
            log.error(f"読み込み失敗: {path} → {e}")
    return rows


def verdict_badge(status: str) -> str:
    return {
        "draft_auto":   "✅ Draft Auto",
        "human_review": "👁 Human Review",
        "blocked":      "🚫 Blocked",
    }.get(status, status)


def score_bar(score: int, max_score: int = 100) -> str:
    """スコアを簡易バーで表示する。"""
    filled = int(score / max_score * 10)
    return "█" * filled + "░" * (10 - filled) + f" {score}"


def format_article_block(article: dict, show_body: bool = False) -> str:
    """記事1件をMarkdownブロックとして整形する。"""
    orig    = article.get("original", {})
    src     = article.get("source", {})
    ps      = article.get("primary_source", {})
    scores  = article.get("scores", {})
    verdict = article.get("verdict", {})
    qa      = article.get("qa", {})

    title        = orig.get("title", "（タイトルなし）")
    url          = orig.get("url", "")
    published_at = orig.get("published_at", "不明")
    source_name  = src.get("name", "不明")
    source_type  = src.get("type", "")
    ps_url       = ps.get("url") or ""
    ps_type      = ps.get("type", "none")
    qa_flags     = qa.get("flags", [])

    imp   = scores.get("importance", {}).get("score", "-")
    trust = scores.get("trust", {}).get("score", "-")
    risk  = scores.get("risk", {}).get("score", "-")
    fresh = scores.get("freshness", {}).get("score", "-")
    expr  = scores.get("expression_risk", {}).get("score", "-")
    t_cat = scores.get("trust", {}).get("trust_category", "-")

    verdict_reason = verdict.get("reason", "")

    lines = [
        f"### {title}",
        f"",
        f"- **ソース：** {source_name}（`{source_type}`）",
        f"- **公開日時：** {published_at}",
        f"- **記事URL：** {url}",
    ]
    if ps_url:
        lines.append(f"- **一次ソース：** {ps_url}（`{ps_type}`）")
    if qa_flags:
        lines.append(f"- **QAフラグ：** {', '.join(qa_flags)}")
    lines += [
        f"",
        f"| スコア | 値 |",
        f"|--------|-----|",
        f"| Importance     | {score_bar(imp if isinstance(imp, int) else 0)} |",
        f"| Trust          | {score_bar(trust if isinstance(trust, int) else 0)} |",
        f"| Risk（低=安全）| {score_bar(risk if isinstance(risk, int) else 0)} |",
        f"| Freshness      | {score_bar(fresh if isinstance(fresh, int) else 0)} |",
        f"| Expression Risk| {score_bar(expr if isinstance(expr, int) else 0)} |",
        f"",
        f"- **Trust Category：** `{t_cat}`",
        f"- **判定理由：** {verdict_reason}",
    ]
    if show_body:
        body = orig.get("body_text", "").strip()
        if body:
            preview = body[:300] + "..." if len(body) > 300 else body
            lines += ["", "**本文プレビュー：**", "", f"> {preview.replace(chr(10), ' ')}"]
    lines.append("")
    return "\n".join(lines)


# ─── 出力生成 ────────────────────────────────────────────────

def write_daily_draft(articles: list[dict]) -> None:
    """draft_auto の記事を日付別Markdownに書き出す。"""
    draft = [a for a in articles if (a.get("verdict") or {}).get("status") == "draft_auto"]
    if not draft:
        log.info("draft_auto の記事がありません。")
        return

    # 日付別にグループ化
    by_date: dict[str, list[dict]] = defaultdict(list)
    for a in draft:
        pub = a.get("original", {}).get("published_at", "")
        date = pub[:10] if pub else "unknown"
        by_date[date].append(a)

    for date, items in sorted(by_date.items(), reverse=True):
        out_path = PUBLISH_DIR / f"{date}.md"
        lines = [
            f"# {date} — Draft Auto 記事",
            f"",
            f"自動生成候補 {len(items)} 件。投稿前に内容を確認してください。",
            f"",
            "---",
            "",
        ]
        for a in items:
            lines.append(format_article_block(a, show_body=True))
            lines.append("---")
            lines.append("")

        out_path.write_text("\n".join(lines), encoding="utf-8")
        log.info(f"draft: {out_path.name} ({len(items)}件)")


def write_review_list(articles: list[dict]) -> None:
    """human_review の記事を一覧Markdownに書き出す。閾値調整の参考用。"""
    review = [a for a in articles if (a.get("verdict") or {}).get("status") == "human_review"]
    if not review:
        log.info("human_review の記事がありません。")
        return

    out_path = PUBLISH_DIR / "review.md"
    lines = [
        f"# Human Review 一覧",
        f"",
        f"確認が必要な記事 {len(review)} 件。スコアと理由を見て、承認・修正・却下を判断してください。",
        f"",
        "---",
        "",
    ]
    for a in review:
        lines.append(format_article_block(a, show_body=False))
        lines.append("---")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"review: {out_path.name} ({len(review)}件)")


def write_index(articles: list[dict]) -> None:
    """全記事のインデックスをMarkdownで書き出す。"""
    from collections import Counter
    verdict_counts = Counter((a.get("verdict") or {}).get("status", "") for a in articles)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Failsafe News Curator — 記事インデックス",
        "",
        f"生成日時：{now}",
        "",
        "## サマリー",
        "",
        f"| ステータス | 件数 |",
        f"|-----------|------|",
        f"| ✅ Draft Auto   | {verdict_counts.get('draft_auto', 0)} |",
        f"| 👁 Human Review | {verdict_counts.get('human_review', 0)} |",
        f"| 🚫 Blocked      | {verdict_counts.get('blocked', 0)} |",
        f"| **合計**        | **{len(articles)}** |",
        "",
        "## ファイル一覧",
        "",
        "- [Draft Auto 記事](./YYYY-MM-DD.md) — 公開候補（日付別）",
        "- [Human Review 一覧](./review.md) — 確認待ち記事",
        "",
        "## Draft Auto 記事タイトル一覧",
        "",
    ]
    draft = [a for a in articles if (a.get("verdict") or {}).get("status") == "draft_auto"]
    for a in draft:
        title = a.get("original", {}).get("title", "（タイトルなし）")
        url   = a.get("original", {}).get("url", "")
        src   = a.get("source", {}).get("name", "")
        lines.append(f"- [{title}]({url}) — {src}")

    lines += [
        "",
        "## Human Review 記事タイトル一覧",
        "",
    ]
    review = [a for a in articles if (a.get("verdict") or {}).get("status") == "human_review"]
    for a in review:
        title  = a.get("original", {}).get("title", "（タイトルなし）")
        url    = a.get("original", {}).get("url", "")
        src    = a.get("source", {}).get("name", "")
        reason = (a.get("verdict") or {}).get("reason", "")[:50]
        lines.append(f"- [{title}]({url}) — {src} | {reason}")

    out_path = PUBLISH_DIR / "index.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"index: {out_path.name}")


# ─── メイン ─────────────────────────────────────────────────

def main():
    articles = load_scored_articles()
    if not articles:
        log.warning("scored_articles/ にファイルがありません。score_articles.py を先に実行してください。")
        return

    log.info(f"記事読み込み: {len(articles)}件")
    write_daily_draft(articles)
    write_review_list(articles)
    write_index(articles)
    log.info(f"publish/ への出力完了")


if __name__ == "__main__":
    main()
