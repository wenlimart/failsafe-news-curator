"""
run_pipeline.py
Phase 1〜3 を順番に実行するランナー。
fetch → normalize → dedupe → write_log の順で動く。

使い方：
    python run_pipeline.py

スコアリングはまだ含まない。
「データが安定して取れるか」を確認してから score_articles.py を追加する。
"""

import logging
import sys
from pathlib import Path
from datetime import datetime, timezone

# ─── ロギング（ランナー用）───────────────────────────────────
LOGS_DIR = Path(__file__).parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOGS_DIR / "pipeline.log"),
    ],
)
log = logging.getLogger("pipeline")


def run_step(name: str, module_path: str) -> bool:
    """1ステップを実行し、成功/失敗を返す。失敗しても次のステップを止めない。"""
    log.info(f"▶ Starting: {name}")
    try:
        import importlib.util
        spec   = importlib.util.spec_from_file_location(name, module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.main()
        log.info(f"✓ Done: {name}")
        return True
    except Exception as e:
        log.error(f"✗ Failed: {name} → {e}")
        return False


def main():
    base_dir = Path(__file__).parent
    started_at = datetime.now(timezone.utc).isoformat()
    log.info(f"Pipeline started at {started_at}")

    steps = [
        ("fetch_articles",     str(base_dir / "fetch_articles.py")),
        ("normalize_articles", str(base_dir / "normalize_articles.py")),
        ("dedupe_articles",    str(base_dir / "dedupe_articles.py")),
        ("write_log",          str(base_dir / "write_log.py")),
        ("score_articles",     str(base_dir / "score_articles.py")),
        ("publish_markdown",   str(base_dir / "publish_markdown.py")),
    ]

    results = {}
    for name, path in steps:
        results[name] = run_step(name, path)

    log.info("── Pipeline summary ──────────────────────")
    all_ok = True
    for name, ok in results.items():
        status = "✓" if ok else "✗"
        log.info(f"  {status} {name}")
        if not ok:
            all_ok = False

    if all_ok:
        log.info("All steps completed successfully.")
    else:
        log.warning("Some steps failed. Check logs/ for details.")
        sys.exit(1)


if __name__ == "__main__":
    main()
