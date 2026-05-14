"""
publish_markdown.py
scored_articles/ の記事をMarkdownに変換し、docs/ に保存する。
GitHub Pages の公開元は /docs に設定する。

出力ファイル：
  docs/index.md                公開トップ（最新2日分の draft_auto）
  docs/YYYY-MM-DD.md           日付別 draft_auto 記事（記事公開日ベース）
  docs/archive/index.md        日別ページの一覧（過去ログ）
  docs/internal/review.md      human_review 一覧（内部確認用）
  docs/internal/blocked.md     blocked 分析（内部確認用）
"""

import json
import glob
import re
import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ─── パス設定 ───────────────────────────────────────────────
BASE_DIR     = Path(__file__).parent
SCORED_DIR   = BASE_DIR / "scored_articles"
DOCS_DIR     = BASE_DIR / "docs"
INTERNAL_DIR = DOCS_DIR / "internal"
ARCHIVE_DIR  = DOCS_DIR / "archive"
LOGS_DIR     = BASE_DIR / "logs"
DOCS_DIR.mkdir(exist_ok=True)
INTERNAL_DIR.mkdir(exist_ok=True)
ARCHIVE_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# トップページに表示する最新日数
INDEX_DAYS = 1

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


def get_published_date(article: dict) -> str:
    """article["original"]["published_at"] から YYYY-MM-DD を返す。なければ "unknown"。"""
    pub = article.get("original", {}).get("published_at", "")
    if pub and len(pub) >= 10:
        return pub[:10]
    return "unknown"


def group_draft_by_published_date(articles: list[dict]) -> dict[str, list[dict]]:
    """draft_auto の記事を published_at 日付ごとにグループ化して返す。"""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for a in articles:
        if (a.get("verdict") or {}).get("status") != "draft_auto":
            continue
        date = get_published_date(a)
        grouped[date].append(a)
    return grouped


def verdict_badge(status: str) -> str:
    return {
        "draft_auto":   "✅ Draft Auto",
        "human_review": "👁 Human Review",
        "blocked":      "🚫 Blocked",
    }.get(status, status)


def score_bar(score: int, max_score: int = 100) -> str:
    filled = int(score / max_score * 10)
    return "█" * filled + "░" * (10 - filled) + f" {score}"


def make_anchor(article: dict) -> str:
    """
    記事URLのpathから安定したアンカーIDを生成する。
    URLパスが空の場合はタイトルをfallbackにする。
    生成ルール: 英数字とハイフン・アンダースコア以外をハイフンに置換し、
                連続ハイフンを1つにまとめ、先頭40文字を使う。
    例:
      https://openai.com/news/how-finance-teams-use-codex
        → article-how-finance-teams-use-codex
      https://openai.com/news/2026/05/12/
        → article-2026-05-12  (末尾スラッシュ除去後のpath全体から生成)
    """
    from urllib.parse import urlparse
    url   = article.get("original", {}).get("url", "")
    title = article.get("original", {}).get("title", "")

    # URLのpathを使う
    raw = ""
    if url:
        path = urlparse(url).path.strip("/")
        if path:
            raw = path

    # pathが空ならtitleをfallback
    if not raw and title:
        raw = title

    slug = re.sub(r"[^a-zA-Z0-9_]", "-", raw)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return f"article-{slug[:50]}"


def format_article_list(items: list[dict]) -> list[str]:
    """
    記事の簡易リストを生成する。
    記事タイトル → 元記事URL
    ソース名    → ページ内詳細アンカー
    """
    lines = []
    for a in items:
        title  = a.get("original", {}).get("title", "（タイトルなし）")
        url    = a.get("original", {}).get("url", "")
        src    = a.get("source", {}).get("name", "不明")
        anchor = make_anchor(a)
        lines.append(f"- [{title}]({url}) — [{src}](#{anchor})")
    return lines


def format_article_block(article: dict, show_body: bool = True) -> str:
    """記事1件の詳細ブロックを生成する。アンカーID付き。"""
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
    anchor = make_anchor(article)

    lines = [
        f'<a id="{anchor}"></a>',
        f"",
        f"### [{title}]({url})",
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
    """draft_auto の記事を published_at 日付別Markdownに書き出す。unknown はスキップ。"""
    by_date = group_draft_by_published_date(articles)
    if not by_date:
        log.info("draft_auto の記事がありません。")
        return

    for date, items in sorted(by_date.items(), reverse=True):
        if date == "unknown":
            continue
        out_path = DOCS_DIR / f"{date}.md"
        lines = [
            f"# {date} — 掲載記事",
            f"",
            f"一次情報・低リスク・新鮮な記事 {len(items)} 件を掲載しています。",
            f"",
        ]
        # 簡易リスト（タイトル → 元記事URL、ソース名 → 詳細アンカー）
        lines += format_article_list(items)
        lines += [
            "",
            "[← トップに戻る](index.md) | [過去ログ](archive/)",
            "",
            "---",
            "",
        ]
        # 記事詳細（アンカー付き）
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

    out_path = INTERNAL_DIR / "review.md"
    lines = [
        f"# Human Review 一覧",
        f"",
        f"確認が必要な記事 {len(review)} 件。",
        f"",
        "## 確認観点",
        "",
        "各記事について以下を確認してください：",
        "",
        "1. **一次ソース** — 公式発表・プレスリリース・論文か、または二次報道か",
        "2. **日付** — 本当に新鮮か、古いニュースの再掲ではないか",
        "3. **リスク** — 法務・金融・医療・個人名など高リスク要素がないか",
        "4. **タイトル表現** — 断定的・煽り表現がないか、内容と一致しているか",
        "5. **承認判断** — `approved` / `approved_with_edit` / `rejected` をCSVに記入",
        "",
        "---",
        "",
    ]
    for a in review:
        lines.append(format_article_block(a, show_body=False))
        lines.append("---")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"review: {out_path.name} ({len(review)}件)")


def write_blocked_list(articles: list[dict]) -> None:
    """blocked の記事を理由別にまとめる。過剰ブロック検出・ルール改善用。"""
    blocked = [a for a in articles if (a.get("verdict") or {}).get("status") == "blocked"]
    if not blocked:
        return

    from collections import Counter
    reason_counts = Counter((a.get("verdict") or {}).get("reason", "")[:60] for a in blocked)

    out_path = INTERNAL_DIR / "blocked.md"
    lines = [
        "# Blocked 記事一覧",
        "",
        f"停止された記事 {len(blocked)} 件。過剰ブロックがないか確認し、ルール改善に活用してください。",
        "",
        "## 停止理由の内訳",
        "",
        "| 理由 | 件数 |",
        "|------|------|",
    ]
    for reason, count in reason_counts.most_common():
        lines.append(f"| {reason} | {count} |")

    lines += ["", "---", ""]

    for a in blocked:
        orig   = a.get("original", {})
        src    = a.get("source", {})
        scores = a.get("scores", {})
        reason = (a.get("verdict") or {}).get("reason", "")
        lines += [
            f"- **{orig.get('title','（タイトルなし）')}**",
            f"  - ソース: {src.get('name','')} | "
            f"trust: {scores.get('trust',{}).get('score','-')} | "
            f"risk: {scores.get('risk',{}).get('score','-')} | "
            f"freshness: {scores.get('freshness',{}).get('score','-')}",
            f"  - 理由: {reason}",
            f"  - URL: {orig.get('url','')}",
            "",
        ]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"blocked: {out_path.name} ({len(blocked)}件)")


def write_index(articles: list[dict]) -> None:
    """公開トップページ（index.md）を生成する。最新掲載日1日分の draft_auto を掲載。"""
    from collections import Counter
    verdict_counts = Counter((a.get("verdict") or {}).get("status", "") for a in articles)

    now_utc = datetime.now(timezone.utc)
    now_jst = now_utc + timedelta(hours=9)
    now_str = f"{now_jst.strftime('%Y-%m-%d %H:%M')} JST ({now_utc.strftime('%H:%M')} UTC)"

    by_date = group_draft_by_published_date(articles)
    # unknown を除いて新しい順にソートし、最新 INDEX_DAYS(=1) 日分だけ使う
    sorted_dates = sorted(
        [d for d in by_date if d != "unknown"],
        reverse=True
    )[:INDEX_DAYS]

    # 表示対象の記事をまとめる
    latest_items: list[dict] = []
    latest_date = sorted_dates[0] if sorted_dates else None
    if latest_date:
        latest_items = by_date[latest_date]

    lines = [
        "# Failsafe News Curator",
        "フェイルセーフ型ニュースキュレーション基盤",
        "",
        f"更新日時：{now_str}",
        "",
        "---",
        "",
        "## 今日の掲載記事",
        "",
        f"自動選別により、低リスク・一次情報・新鮮と判定された記事 {len(latest_items)} 件を掲載しています。",
        "",
    ]

    if latest_items:
        lines.append(f"### {latest_date}")
        lines.append("")
        # 簡易リスト（タイトル → 元記事URL、ソース名 → 詳細アンカー）
        lines += format_article_list(latest_items)
        lines += [
            "",
            "[過去ログを見る](archive/)",
            "",
            "---",
            "",
        ]
        # 記事詳細（アンカー付き）
        for a in latest_items:
            lines.append(format_article_block(a, show_body=True))
            lines.append("---")
            lines.append("")
    else:
        lines += [
            "*本日の掲載記事はありません。*",
            "",
            "[過去ログを見る](archive/)",
            "",
            "---",
            "",
        ]

    lines += [
        "## このサイトについて",
        "",
        "収集したニュースを自動スコアリングし、信頼度・リスク・鮮度を評価した上で掲載しています。",
        "",
        "- **一次情報優先** — 公式ブログ・プレスリリースを優先して取得",
        "- **フェイルセーフ設計** — 法務・規制・医療・金融系は人間が確認",
        "- **出典を必ず表示** — 元記事URLを全件掲載",
        "",
        f"*Powered by [failsafe-news-curator](https://github.com/wenlimart/failsafe-news-curator)*",
    ]

    out_path = DOCS_DIR / "index.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"index: {out_path.name}")


def write_archive_index() -> None:
    """docs/ 配下の YYYY-MM-DD.md を新しい順に並べた archive/index.md を生成する。"""
    date_pattern = re.compile(r'^\d{4}-\d{2}-\d{2}\.md$')
    date_files = sorted(
        [p for p in DOCS_DIR.glob("*.md") if date_pattern.match(p.name)],
        key=lambda p: p.name,
        reverse=True,
    )

    out_path = ARCHIVE_DIR / "index.md"
    lines = [
        "# 過去ログ",
        "",
        "掲載記事の日別アーカイブです。",
        "",
        "[← トップに戻る](../)",
        "",
    ]

    if date_files:
        for p in date_files:
            date = p.stem  # "2026-05-11"
            lines.append(f"- [{date}](../{p.name})")
    else:
        lines.append("過去ログはまだありません。")

    lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"archive: {out_path.name} ({len(date_files)}件)")


# ─── メイン ─────────────────────────────────────────────────

def main():
    articles = load_scored_articles()
    if not articles:
        log.warning("scored_articles/ にファイルがありません。score_articles.py を先に実行してください。")
        return

    log.info(f"記事読み込み: {len(articles)}件")
    write_daily_draft(articles)    # 日別ページを先に生成
    write_review_list(articles)
    write_blocked_list(articles)
    write_index(articles)
    write_archive_index()          # 日別ページ生成後にアーカイブ一覧を作る
    log.info(f"docs/ への出力完了")


if __name__ == "__main__":
    main()

