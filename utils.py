"""
utils.py
パイプライン共通ユーティリティ。
normalize_url() を dedupe_articles.py と write_log.py の両方から使う。
"""

from urllib.parse import urlparse, urlunparse, urlencode, parse_qsl

# クエリパラメータから除去するトラッキング系キー
_REMOVE_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "ref", "source", "fbclid", "gclid", "mc_cid", "mc_eid",
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
