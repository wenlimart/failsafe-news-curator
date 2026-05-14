"""
publish_markdown.py
scored_articles/ の記事をMarkdownに変換し、docs/ に保存する。
GitHub Pages の公開元は /docs に設定する。

通常実行（run_pipeline.py から呼ばれる）:
  python publish_markdown.py
  → docs/index.md, docs/YYYY-MM-DD.md, docs/archive/index.md,
     docs/internal/review.md, docs/internal/blocked.md を生成

再生成モード（手動実行のみ）:
  python publish_markdown.py --regenerate --date YYYY-MM-DD
  → data/published.jsonl から指定日の記事を読み込み、
    docs/YYYY-MM-DD.md を新形式で再生成する
  → 指定日のデータがない場合は既存ファイルを変更しない
"""

import json
import glob
import re
import argparse
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
DATA_DIR     = BASE_DIR / "data"
LOGS_DIR     = BASE_DIR / "logs"
DOCS_DIR.mkdir(exist_ok=True)
INTERNAL_DIR.mkdir(exist_ok=True)
ARCHIVE_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

PUBLISHED_JSONL = DATA_DIR / "published.jsonl"

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


def _load_published_urls() -> set[str]:
    """published.jsonl に記録済みのURLセットを返す（重複防止用）。"""
    urls: set[str] = set()
    if not PUBLISHED_JSONL.exists():
        return urls
    with open(PUBLISHED_JSONL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("url"):
                    urls.add(rec["url"])
            except json.JSONDecodeError:
                pass
    return urls


def append_to_published_jsonl(articles: list[dict]) -> int:
    """
    draft_auto 記事を data/published.jsonl に追記する。
    重複防止キー: article URL（article_id は実行ごとに変わるため使わない）
    本文プレビューは最大200文字に限定し、外部記事本文を過剰蓄積しない。
    戻り値: 新規追記件数
    """
    existing_urls = _load_published_urls()
    new_records = []

    for a in articles:
        if (a.get("verdict") or {}).get("status") != "draft_auto":
            continue

        orig   = a.get("original", {})
        src    = a.get("source", {})
        ps     = a.get("primary_source", {})
        scores = a.get("scores", {})

        url = orig.get("url", "")
        if not url or url in existing_urls:
            continue

        # 本文プレビューは最大200文字
        body_text = orig.get("body_text", "").strip()
        preview   = body_text[:200] + "..." if len(body_text) > 200 else body_text

        record = {
            "url":               url,
            "title":             orig.get("title", ""),
            "published_at":      orig.get("published_at", ""),
            "published_date":    get_published_date(a),
            "source_name":       src.get("name", ""),
            "source_type":       src.get("type", ""),
            "primary_source_url": ps.get("url") or "",
            "scores": {
                "importance":    scores.get("importance",    {}).get("score"),
                "trust":         scores.get("trust",         {}).get("score"),
                "risk":          scores.get("risk",          {}).get("score"),
                "freshness":     scores.get("freshness",     {}).get("score"),
                "expression_risk": scores.get("expression_risk", {}).get("score"),
            },
            "trust_category":   scores.get("trust", {}).get("trust_category", ""),
            "decision_reasons":  (a.get("verdict") or {}).get("reason", ""),
            "body_preview":     preview,
            "recorded_at":      datetime.now(timezone.utc).isoformat(),
        }
        new_records.append(record)
        existing_urls.add(url)

    if new_records:
        DATA_DIR.mkdir(exist_ok=True)
        with open(PUBLISHED_JSONL, "a", encoding="utf-8") as f:
            for rec in new_records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    log.info(f"published.jsonl: {len(new_records)}件追記（累計行数確認は別途）")
    return len(new_records)


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


# ─── 再生成モード ─────────────────────────────────────────────

def load_published_for_date(date: str) -> list[dict]:
    """
    data/published.jsonl から指定日（YYYY-MM-DD）の記事だけを返す。
    published_date フィールドで絞り込む。
    """
    if not PUBLISHED_JSONL.exists():
        log.warning(f"data/published.jsonl が存在しません。Phase 1 を先に実行してください。")
        return []

    records = []
    with open(PUBLISHED_JSONL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("published_date") == date:
                    records.append(rec)
            except json.JSONDecodeError:
                pass

    log.info(f"published.jsonl から {date} の記事: {len(records)}件")
    return records


def _record_to_article_dict(rec: dict) -> dict:
    """
    published.jsonl の1レコードを、format_article_block / format_article_list
    が期待する article dict 形式に変換する。
    """
    scores_raw = rec.get("scores", {})
    return {
        "original": {
            "title":        rec.get("title", ""),
            "url":          rec.get("url", ""),
            "published_at": rec.get("published_at", ""),
            "body_text":    rec.get("body_preview", ""),
        },
        "source": {
            "name": rec.get("source_name", ""),
            "type": rec.get("source_type", ""),
        },
        "primary_source": {
            "url":      rec.get("primary_source_url") or "",
            "type":     rec.get("primary_source_type") or rec.get("source_type") or ("official_blog" if rec.get("primary_source_url") else "none"),
            "detected": bool(rec.get("primary_source_url")),
        },
        "scores": {
            "importance":     {"score": scores_raw.get("importance")},
            "trust":          {
                "score":          scores_raw.get("trust"),
                "trust_category": rec.get("trust_category", ""),
            },
            "risk":           {"score": scores_raw.get("risk")},
            "freshness":      {"score": scores_raw.get("freshness")},
            "expression_risk":{"score": scores_raw.get("expression_risk")},
        },
        "verdict": {
            "status": "draft_auto",
            "reason": rec.get("decision_reasons", ""),
        },
        "qa": {"flags": []},
    }


def regenerate_daily_page(date: str) -> None:
    """
    data/published.jsonl の指定日データから docs/YYYY-MM-DD.md を再生成する。

    安全手順:
      1. docs/YYYY-MM-DD.md.new に書き出す
      2. 問題なければ docs/YYYY-MM-DD.md に置き換える
      3. 指定日のデータがない場合は既存ファイルを変更しない
    """
    # 日付形式の検証
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date):
        log.error(f"日付形式が不正です: {date}（YYYY-MM-DD で指定してください）")
        return

    records = load_published_for_date(date)
    if not records:
        log.warning(f"{date} のデータが published.jsonl に見つかりません。既存ファイルを変更しません。")
        return

    # published.jsonl レコードを article dict に変換
    items = [_record_to_article_dict(r) for r in records]

    out_path     = DOCS_DIR / f"{date}.md"
    tmp_path     = DOCS_DIR / f"{date}.md.new"

    lines = [
        f"# {date} — 掲載記事",
        f"",
        f"一次情報・低リスク・新鮮な記事 {len(items)} 件を掲載しています。",
        f"",
    ]
    # 簡易リスト
    lines += format_article_list(items)
    lines += [
        "",
        "[← トップに戻る](index.md) | [過去ログ](archive/)",
        "",
        "---",
        "",
    ]
    # 記事詳細
    for a in items:
        lines.append(format_article_block(a, show_body=True))
        lines.append("---")
        lines.append("")

    # .new に書き出してから置き換え
    tmp_path.write_text("\n".join(lines), encoding="utf-8")
    tmp_path.replace(out_path)
    log.info(f"再生成完了: {out_path.name} ({len(items)}件)")


# ─── メイン ─────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="publish_markdown.py")
    parser.add_argument(
        "--regenerate", action="store_true",
        help="再生成モード（data/published.jsonl から日別ページを再生成する）"
    )
    parser.add_argument(
        "--date",
        help="再生成対象の日付（YYYY-MM-DD）。--regenerate と一緒に使う"
    )
    args = parser.parse_args()

    # 再生成モード
    if args.regenerate:
        if not args.date:
            log.error("--regenerate には --date YYYY-MM-DD が必要です。")
            return
        regenerate_daily_page(args.date)
        return

    # 通常モード（run_pipeline.py から呼ばれる）
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
    append_to_published_jsonl(articles)  # draft_auto 記事を永続データに追記
    log.info(f"docs/ への出力完了")


if __name__ == "__main__":
    main()

