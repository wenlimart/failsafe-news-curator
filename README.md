# Failsafe News Curator
フェイルセーフ型ニュースキュレーション基盤

A fail-safe news curation pipeline for collecting, deduplicating, scoring, reviewing, and publishing trustworthy news across domains.

---

設計書 v1.2 / 実装仕様書 v1.0 に対応する RSS 収集・正規化・重複除去・ログ書き込みの実装です（Phase 1〜3）。

## ディレクトリ構成

```
failsafe-news-curator/
├── .github/workflows/
│   └── syntax_check.yml      # GitHub Actions 構文チェック
├── rss_sources.yaml          # RSSソース定義・設定
├── utils.py                  # 共通ユーティリティ（normalize_url等）
├── fetch_articles.py         # Step 1: RSS取得 → articles_raw/
├── normalize_articles.py     # Step 2: 正規化 → articles_normalized/
├── dedupe_articles.py        # Step 3: 重複除去 → articles_ready/
├── write_log.py              # Step 4: CSVログ書き込み → logs/
├── run_pipeline.py           # 全ステップを順番に実行するランナー
├── requirements.txt
├── articles_raw/             # 生JSON（fetch出力）
├── articles_normalized/      # 正規化JSON
├── articles_ready/           # 重複除去済みJSON（スコアリング入力）
├── scored_articles/          # score_articles.py 実装後に使用
└── logs/
    ├── fetch.log
    ├── normalize.log
    ├── dedupe.log
    ├── write_log.log
    ├── pipeline.log
    └── news_review_log.csv   # 判定ログ（Human Reviewはここに手動記入）
```

## セットアップ

```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

> **最初は `rss_sources.yaml` の sources を3〜5個に絞ってテストする。**  
> 全ソースを一気に有効にすると、QAフラグや重複の傾向が掴みにくい。
> 問題がないことを確認してからソースを追加する。

## 実行方法

### パイプライン全体を動かす（推奨）

```bash
python run_pipeline.py
```

fetch → normalize → dedupe → write_log の順で実行される。
1ステップが失敗しても他のステップのログは残る。

### 個別に動かす

```bash
python fetch_articles.py      # articles_raw/ に生JSONを保存
python normalize_articles.py  # articles_normalized/ に正規化JSONを保存
python dedupe_articles.py     # articles_ready/ に重複除去済みJSONを保存
python write_log.py           # logs/news_review_log.csv に追記
```

### 定期実行（例：macOS launchd / Linux cron）

```bash
# cron の例（毎朝6時30分と毎晩18時）
30 6  * * * cd /path/to/failsafe-news-curator && venv/bin/python run_pipeline.py
0  18 * * * cd /path/to/failsafe-news-curator && venv/bin/python run_pipeline.py
```

---

## RSSソースのカスタマイズ

`rss_sources.yaml` を編集する。AIニュース以外の任意のドメインに差し替えられる。

```yaml
sources:
  - name: "あなたの業界メディア"
    url:  "https://example.com/feed"
    type: "specialist_media"   # major_media / specialist_media / official_blog など
    language: "ja"
    trust_base: 65             # Trust Scoreの基準値（0〜100）
```

`source.type` の選択肢：
`official_blog` / `press_release` / `major_media` / `specialist_media` / `aggregator` / `unknown`

**対応ドメイン例：**
AIニュース・金融・医療・法規制・競合モニタリング・地域ニュースなど、`rss_sources.yaml` を差し替えれば横展開できる。ドメインによってリスク閾値は調整が必要（医療・金融・法律は高リスク寄りに設定する）。

---

## 重複除去の仕組み（3段階）

`dedupe_articles.py` は以下の順で重複判定する：

1. **URL正規化一致** — UTMパラメータ等を除去した正規化URLが同じ
2. **primary_source_url 一致** — 同一ニュースを複数メディアが報道している場合
3. **タイトル類似度** — `SequenceMatcher` で閾値以上の類似度

`duplicate_check.method` カラムにどの方法で検出されたかが記録される。  
閾値は `rss_sources.yaml` の `dedupe_title_similarity_threshold`（デフォルト0.85）で調整できる。

---

## 判定ログ（news_review_log.csv）の見方

スコアリング前の段階では以下の列が埋まっている：

| 列 | 内容 |
|----|------|
| article_title / url | 記事の基本情報 |
| published_at | 公開日時 |
| primary_source_detected | 一次ソース検出の成否 |
| qa_flags | 正規化フェーズで検出した警告（title_missing など） |

スコアリング実装後に埋まる列：

| 列 | 内容 |
|----|------|
| importance / trust / risk / freshness / expression_risk | 5軸スコア |
| verdict | draft_auto / human_review / blocked |
| ai_verdict_reason | AIの判定理由 |

**Human Review 時に手動で記入する列：**

| 列 | 選択肢 |
|----|--------|
| human_result | `approved` / `approved_with_edit` / `rejected` |
| false_positive | AIが止めたが本来通してよかった → `TRUE` |
| false_negative | AIが通したが本来止めるべきだった → `TRUE` |
| error_type | `missed_high_risk` / `over_blocked_low_risk` / `freshness_error` / `expression_error` / `source_misclassified` |
| rule_improvement_suggestion | ルール改善のメモ（自由記述） |

---

## QAフラグの意味

`normalize_articles.py` が検出するフラグ：

| フラグ | 意味 | 対応 |
|--------|------|------|
| `title_missing` | タイトルが空 | 手動確認 |
| `url_invalid` | URLがない or httpから始まらない | 除外検討 |
| `published_at_missing` | 公開日時が取れなかった | Freshness=0として処理される |
| `body_text_too_short` | 本文が100文字未満 | 要約精度に影響する可能性 |

---

## Phase 4 以降（スコアリング・公開）への接続

`articles_ready/*.json` がスコアリングの入力になる。
次に実装する `score_articles.py` はこのフォルダを読み込み、
実装仕様書のスコアリングプロンプトを使ってスコアを付与し、
`scored_articles/*.json` に保存する。

公開形式は出力アダプターとして差し替え可能な設計にする：

```text
Core Pipeline（本リポジトリ）
  RSS取得 → 正規化 → 重複除去 → スコアリング → ログ

Publish Adapters（今後追加）
  - 静的サイト（Markdown / GitHub Pages）
  - ニュースレター
  - キュレーション済みRSSの再配信
  - Slack / Discord
```

**Phase 1〜3 が安定して動き、ログが溜まってから Phase 4 に進むこと。**
最初の1〜2週間はログを眺めて以下を確認する：

- どのソースで `qa_flags` が多く出るか
- `published_at_missing` が出るソースはないか
- 重複除去の閾値（0.85）が適切か
- `primary_source_detected: true` の比率はどのくらいか

この実態を把握してからスコアリングプロンプトの調整をする方が、机上のルールになりにくい。

---

## GitHub移行手順

```bash
git init
git add .
git commit -m "Initial commit: Failsafe News Curator MVP"

git branch -M main
git remote add origin https://github.com/<YOUR_USERNAME>/failsafe-news-curator.git
git push -u origin main
```
