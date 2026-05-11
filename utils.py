"""
utils.py
パイプライン共通ユーティリティ。
normalize_url() を dedupe_articles.py と write_log.py の両方から使う。
detect_primary_source_by_url() を fetch_articles.py から使う。
"""

from urllib.parse import urlparse, urlunparse, urlencode, parse_qsl

# クエリパラメータから除去するトラッキング系キー
_REMOVE_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "ref", "source", "fbclid", "gclid", "mc_cid", "mc_eid",
}

# ドメインベース一次ソース判定テーブル
# キー：ドメイン（部分一致）、値：source_type
# 記事URL自体がこのドメインに該当する場合、一次ソースとみなす
PRIMARY_SOURCE_DOMAINS: dict[str, str] = {
    # AI企業公式
    "openai.com":           "official_blog",
    "anthropic.com":        "official_blog",
    "deepmind.google":      "official_blog",
    "deepmind.com":         "official_blog",
    "blog.google":          "official_blog",
    "ai.google":            "official_blog",
    "research.google":      "official_blog",
    "ai.meta.com":          "official_blog",
    "llama.meta.com":       "official_blog",
    "microsoft.com":        "official_blog",
    "mistral.ai":           "official_blog",
    "huggingface.co":       "official_blog",
    "stability.ai":         "official_blog",
    "cohere.com":           "official_blog",
    "github.blog":          "official_blog",
    "github.com":           "official_blog",
    "aws.amazon.com":       "official_blog",
    "cloud.google.com":     "official_blog",
    # 学術・論文
    "arxiv.org":            "academic_paper",
    "papers.ssrn.com":      "academic_paper",
    "openreview.net":       "academic_paper",
    "proceedings.mlr.press":"academic_paper",
    "semanticscholar.org":  "academic_paper",
    # 規制・政府
    "sec.gov":              "sec_filing",
    "ftc.gov":              "government",
    "ec.europa.eu":         "government",
    "digital-strategy.ec.europa.eu": "government",
    "nist.gov":             "government",
    "go.jp":                "government",
    # プレスリリース
    "prnewswire.com":       "press_release",
    "businesswire.com":     "press_release",
    "globenewswire.com":    "press_release",
    "accesswire.com":       "press_release",
}


def normalize_url(url: str) -> str:
    """
    URLを正規化して比較しやすくする。
    - https に統一
    - ホスト名を小文字化
    - トラッキング系クエリパラメータを除去
    - 末尾スラッシュを除去
    - フラグメント（#以降）を除去

    空文字・Noneは空文字で返す。
    パース失敗時は元のURLをそのまま返す。
    """
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        filtered_query = urlencode(
            [(k, v) for k, v in parse_qsl(parsed.query)
             if k not in _REMOVE_PARAMS]
        )
        normalized = urlunparse((
            "https",
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            parsed.params,
            filtered_query,
            "",   # fragment除去
        ))
        return normalized
    except Exception:
        return url


def detect_primary_source_by_url(url: str) -> dict:
    """
    記事URL自体がPRIMARY_SOURCE_DOMAINSに該当するか判定する（ドメインベース）。
    TechCrunch・The Verge等のメディア記事はここでは検出されない（正しい挙動）。
    OpenAI Blog・arXiv等の公式系RSSを追加したときに正しく機能する。

    Returns:
        {"detected": bool, "url": str | None, "type": str}
    """
    if not url:
        return {"detected": False, "url": None, "type": "none"}

    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return {"detected": False, "url": None, "type": "none"}

    for domain, source_type in PRIMARY_SOURCE_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return {"detected": True, "url": url, "type": source_type}

    return {"detected": False, "url": None, "type": "none"}

    """
    URLを正規化して比較しやすくする。
    - https に統一
    - ホスト名を小文字化
    - トラッキング系クエリパラメータを除去
    - 末尾スラッシュを除去
    - フラグメント（#以降）を除去

    空文字・Noneは空文字で返す。
    パース失敗時は元のURLをそのまま返す。
    """
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        filtered_query = urlencode(
            [(k, v) for k, v in parse_qsl(parsed.query)
             if k not in _REMOVE_PARAMS]
        )
        normalized = urlunparse((
            "https",
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            parsed.params,
            filtered_query,
            "",   # fragment除去
        ))
        return normalized
    except Exception:
        return url
