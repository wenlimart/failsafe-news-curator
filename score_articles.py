"""
score_articles.py
articles_ready/ の記事にルールベースでスコアを付与し、
scored_articles/ に保存する。また logs/news_review_log.csv のスコア列を更新する。

Phase 4 最小実装：LLM APIは呼ばない。
目的は「収集→スコアリング→verdict→ログ更新」の流れが壊れないか確認すること。
LLMスコアリングは流れが安定してから差し込む。

verdict の定義：
  draft_auto   : 全条件クリア。下書き生成可（MVPでは投稿しない）
  human_review : 境界ケース。人間確認が必要
  blocked      : 投稿停止。ログ記録のみ
"""

import csv
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from utils import PRIMARY_SOURCE_DOMAINS

# ─── パス設定 ───────────────────────────────────────────────
BASE_DIR       = Path(__file__).parent
READY_DIR      = BASE_DIR / "articles_ready"
SCORED_DIR     = BASE_DIR / "scored_articles"
LOGS_DIR       = BASE_DIR / "logs"
LOG_CSV        = LOGS_DIR / "news_review_log.csv"
SCORED_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

PROMPT_VERSION = "rulebased-v1.0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOGS_DIR / "score.log"),
    ],
)
log = logging.getLogger(__name__)

# ─── リスク強制昇格キーワード ────────────────────────────────
ESCALATION_KEYWORDS: dict[str, list[str]] = {
    "法務・規制": [
        "lawsuit", "court", "sued", "investigation", "regulator",
        "SEC", "FTC", "EU AI Act", "訴訟", "規制", "調査", "当局",
    ],
    "企業行動": [
        "acquisition", "merger", "layoffs", "bankruptcy",
        "買収", "合併", "レイオフ", "解雇", "倒産",
    ],
    "政治・政策": [
        "election", "government", "policy", "sanctions",
        "選挙", "政府", "政策", "制裁",
    ],
    "医療": ["medical", "diagnosis", "drug", "health", "医療", "診断", "薬"],
    "金融": ["stock", "investment", "revenue", "earnings", "株", "投資", "収益", "決算"],
    "その他高リスク": ["炎上", "差別", "犯罪", "事故", "未成年"],
}

# 煽り・断定表現パターン
EXPRESSION_RISK_PATTERNS = [
    r"will (definitely|certainly|absolutely)",
    r"guaranteed",
    r"破壊的",
    r"革命的",
    r"完全に",
    r"絶対に",
    r"[！!]{2,}",   # 感嘆符の連続
]


# ─── ルールベーススコアリング ────────────────────────────────

def check_escalation(text: str) -> dict:
    """ルールベースでリスク強制昇格キーワードを検出する。"""
    matched = []
    category = None
    for cat, keywords in ESCALATION_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text.lower():
                matched.append(kw)
                category = cat
    return {
        "escalated": len(matched) > 0,
        "keywords": matched,
        "category": category,
    }


def score_importance(article: dict) -> dict:
    """重要度スコア（0〜100）をルールベースで算出する。"""
    src_type = article.get("source", {}).get("type", "unknown")
    trust_base = article.get("source", {}).get("trust_base", 60)
    ps_detected = article.get("primary_source", {}).get("detected", False)

    # ソース権威（20点）
    authority = {
        "official_blog": 20,
        "academic_paper": 18,
        "press_release": 15,
        "major_media": 14,
        "specialist_media": 12,
    }.get(src_type, 8)

    # 一次ソース検出ボーナス（20点）
    primary_bonus = 20 if ps_detected else 5

    # trust_base を業務インパクト・実用性の代替とする（各10点）
    normalized = min(trust_base / 100, 1.0)
    impact = int(normalized * 10)
    relevance = int(normalized * 10)

    # 緊急性・競合優位性はルールベースでは一律中間値
    urgency = 10
    competitive = 10

    total = authority + primary_bonus + impact + relevance + urgency + competitive
    return {
        "score": min(total, 100),
        "breakdown": {
            "source_authority": authority,
            "primary_bonus": primary_bonus,
            "business_impact": impact,
            "japan_relevance": relevance,
            "urgency": urgency,
            "competitive_edge": competitive,
        },
        "reason": f"ルールベース: src_type={src_type}, primary={ps_detected}",
    }


def score_trust(article: dict) -> dict:
    """信頼度スコア（0〜100）をルールベースで算出する。"""
    src_type    = article.get("source", {}).get("type", "unknown")
    trust_base  = article.get("source", {}).get("trust_base", 60)
    ps_detected = article.get("primary_source", {}).get("detected", False)
    ps_type     = article.get("primary_source", {}).get("type", "none")
    qa_flags    = article.get("qa", {}).get("flags", [])

    score = trust_base

    # 一次ソース加点
    if ps_detected:
        bonus = {
            "official_blog": 20,
            "academic_paper": 18,
            "press_release": 15,
            "sec_filing": 15,
            "government": 15,
        }.get(ps_type, 10)
        score += bonus
        trust_category = "primary"
    elif src_type in ("major_media", "specialist_media"):
        trust_category = "credible_reporting"
    else:
        trust_category = "aggregator_only"
        score -= 20

    # QAフラグ減点
    if "published_at_missing" in qa_flags:
        score -= 20
    if "body_text_too_short" in qa_flags:
        score -= 10
    if "url_invalid" in qa_flags:
        score -= 30

    score = max(0, min(score, 100))
    return {
        "score": score,
        "trust_category": trust_category,
        "reason": f"ルールベース: trust_base={trust_base}, ps={ps_detected}/{ps_type}",
    }


def score_risk(article: dict, escalation: dict) -> dict:
    """リスクスコア（0〜100、低いほど安全）をルールベースで算出する。"""
    text = " ".join([
        article.get("original", {}).get("title", ""),
        article.get("original", {}).get("body_text", ""),
    ])

    # 強制昇格
    if escalation["escalated"]:
        return {
            "score": 65,
            "ai_classification": "high",
            "reason": f"強制昇格: {escalation['category']} / {escalation['keywords'][:3]}",
        }

    # ソースタイプ別ベースリスク
    src_type = article.get("source", {}).get("type", "unknown")
    base = {
        "official_blog": 15,
        "academic_paper": 20,
        "press_release": 25,
        "major_media": 30,
        "specialist_media": 30,
        "aggregator": 50,
    }.get(src_type, 40)

    return {
        "score": min(base, 100),
        "ai_classification": "low" if base <= 25 else "medium",
        "reason": f"ルールベース: src_type={src_type}",
    }


def score_freshness(article: dict) -> dict:
    """鮮度スコア（0・40・70・100）を算出する。"""
    published_at = article.get("original", {}).get("published_at")
    if not published_at:
        return {"score": 0, "hours_since_published": None, "reason": "公開日時不明"}

    try:
        pub = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        hours = (now - pub).total_seconds() / 3600

        if hours <= 24:
            score = 100
        elif hours <= 48:
            score = 70
        elif hours <= 72:
            score = 40
        elif hours <= 168:   # 7日以内
            score = 20
        else:
            score = 0        # 7日超は本当に古い

        return {
            "score": score,
            "hours_since_published": round(hours, 1),
            "reason": f"{round(hours,1)}時間経過",
        }
    except Exception:
        return {"score": 0, "hours_since_published": None, "reason": "日時パース失敗"}


def score_expression_risk(article: dict) -> dict:
    """表現リスクスコア（0〜100、低いほど安全）を算出する。"""
    title = article.get("original", {}).get("title", "")
    flags = []
    score = 0
    for pattern in EXPRESSION_RISK_PATTERNS:
        if re.search(pattern, title, re.IGNORECASE):
            flags.append(pattern)
            score += 20
    return {
        "score": min(score, 100),
        "flags": flags,
        "reason": f"パターン検出: {len(flags)}件" if flags else "問題なし",
    }


# ─── 判定ロジック ─────────────────────────────────────────────

def determine_verdict(scores: dict, escalation: dict) -> dict:
    """スコアからverdict（draft_auto / human_review / blocked）を決定する。"""
    importance   = scores["importance"]["score"]
    trust        = scores["trust"]["score"]
    risk         = scores["risk"]["score"]
    freshness    = scores["freshness"]["score"]
    expression   = scores["expression_risk"]["score"]
    trust_cat    = scores["trust"]["trust_category"]

    # Blocked条件（OR: 1つでも該当すれば停止）
    if any([
        risk >= 61,
        trust < 50,
        freshness == 0,          # 7日超は停止
        trust_cat == "aggregator_only" and trust < 60,
        escalation["escalated"] and trust < 70,
    ]):
        return {
            "status": "blocked",
            "reason": f"停止条件該当: risk={risk}, trust={trust}, freshness={freshness}",
            "requires_human_review": False,
        }

    # Human Review条件
    if any([
        risk >= 31,
        trust < 80,
        trust_cat in ("credible_reporting", "aggregator_only"),
        escalation["escalated"],
        freshness < 70,          # 48時間超は人間確認
    ]):
        return {
            "status": "human_review",
            "reason": f"確認推奨: risk={risk}, trust={trust}, trust_cat={trust_cat}",
            "requires_human_review": True,
        }

    # Draft Auto条件（AND: 全条件クリア）
    if all([
        importance >= 70,
        trust >= 80,
        risk <= 30,
        freshness >= 70,
        expression <= 20,
        trust_cat == "primary",
        not escalation["escalated"],
    ]):
        return {
            "status": "draft_auto",
            "reason": "全条件クリア",
            "requires_human_review": False,
        }

    # フォールバック（保守的に human_review）
    return {
        "status": "human_review",
        "reason": "条件未達のためデフォルト確認",
        "requires_human_review": True,
    }


# ─── メイン処理 ──────────────────────────────────────────────

def score_article(article: dict) -> dict:
    """1件の記事をスコアリングし、scored記事dictを返す。"""
    text = " ".join([
        article.get("original", {}).get("title", ""),
        article.get("original", {}).get("body_text", ""),
    ])
    escalation   = check_escalation(text)
    imp          = score_importance(article)
    trust        = score_trust(article)
    risk         = score_risk(article, escalation)
    freshness    = score_freshness(article)
    expression   = score_expression_risk(article)

    scores = {
        "importance":    imp,
        "trust":         trust,
        "risk":          risk,
        "freshness":     freshness,
        "expression_risk": expression,
    }
    verdict = determine_verdict(scores, escalation)

    return {
        **article,
        "scored_at":     datetime.now(timezone.utc).isoformat(),
        "prompt_version": PROMPT_VERSION,
        "rule_based":    escalation,
        "scores":        scores,
        "verdict":       verdict,
    }


def update_log(scored_articles: list[dict]) -> None:
    """news_review_log.csv のスコア列・verdict列を更新する。"""
    if not LOG_CSV.exists():
        log.warning("news_review_log.csv が見つかりません。write_log.py を先に実行してください。")
        return

    # 既存CSVを読み込む
    with open(LOG_CSV, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    # article_id → scored_article のマップを作る
    scored_map = {a["article_id"]: a for a in scored_articles}

    updated = 0
    for row in rows:
        aid = row.get("article_id", "")
        if aid not in scored_map:
            continue
        sa = scored_map[aid]
        row["importance"]        = sa["scores"]["importance"]["score"]
        row["trust"]             = sa["scores"]["trust"]["score"]
        row["risk"]              = sa["scores"]["risk"]["score"]
        row["freshness"]         = sa["scores"]["freshness"]["score"]
        row["expression_risk"]   = sa["scores"]["expression_risk"]["score"]
        row["rule_based_escalated"]  = sa["rule_based"]["escalated"]
        row["escalation_keywords"]   = "|".join(sa["rule_based"]["keywords"])
        row["trust_category"]        = sa["scores"]["trust"]["trust_category"]
        row["verdict"]               = sa["verdict"]["status"]
        row["ai_verdict_reason"]     = sa["verdict"]["reason"]
        row["prompt_version"]        = sa["prompt_version"]
        updated += 1

    with open(LOG_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    log.info(f"ログ更新完了: {updated}件")


def main():
    ready_files = sorted(READY_DIR.glob("*.json"))
    if not ready_files:
        log.warning("articles_ready/ にファイルがありません。")
        return

    all_scored = []
    for path in ready_files:
        try:
            with open(path, encoding="utf-8") as f:
                articles = json.load(f)
        except Exception as e:
            log.error(f"読み込み失敗: {path.name} → {e}")
            continue

        scored = [score_article(a) for a in articles]

        out_path = SCORED_DIR / path.name
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(scored, f, ensure_ascii=False, indent=2)

        # verdict サマリーをログに出力
        from collections import Counter
        verdicts = Counter(a["verdict"]["status"] for a in scored)
        log.info(f"{path.name}: {len(scored)}件 → {dict(verdicts)}")
        all_scored.extend(scored)

    update_log(all_scored)

    # 全体サマリー
    from collections import Counter
    all_verdicts = Counter(a["verdict"]["status"] for a in all_scored)
    log.info(f"スコアリング完了: 総計 {len(all_scored)}件 → {dict(all_verdicts)}")


if __name__ == "__main__":
    main()
