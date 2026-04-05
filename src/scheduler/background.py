"""バックグラウンドスキャン

アプリ起動時に裏でスキャンを実行し、結果をキャッシュに保存する。
画面は常にキャッシュから結果を表示するだけ。
処理中でも画面操作が可能。

サイクル:
  1. フルスキャン（Stage 1 + Stage 2）→ 候補抽出 + ウォッチ更新
  2. ウォッチ追跡（MFE/MAE更新、トレード完了判定、乖離チェック）
  3. 答え合わせ（過去推奨の結果確認 + 重み最適化）
"""

import threading
import time
import logging
from datetime import datetime, date
from pathlib import Path

log_dir = Path(__file__).parent.parent.parent / "data"
log_dir.mkdir(exist_ok=True)
logging.basicConfig(
    filename=str(log_dir / "daily_job.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

_thread_started = False
_scan_status = {"running": False, "last_run": None, "progress": "", "error": None}


def get_scan_status() -> dict:
    """スキャンの進行状態を返す。"""
    return dict(_scan_status)


def _run_scan():
    """フルサイクル: スキャン → ウォッチ追跡 → 答え合わせ。"""
    global _scan_status

    _scan_status["running"] = True
    _scan_status["progress"] = "開始中..."
    _scan_status["error"] = None

    try:
        from src.data.database import init_db
        init_db()

        from src.data.stocklist import get_low_price_stocks
        from src.strategy.screener import screen_stocks
        from src.strategy.deep_analysis import run_deep_analysis
        from src.strategy.cache import save_screen_results
        from src.data.watchlist import update_from_screening, refresh_watchlist, get_trade_stats
        from src.feedback.tracker import record_recommendation, check_outcomes
        from src.feedback.optimizer import update_weights

        # === 1. フルスキャン ===
        _scan_status["progress"] = "銘柄リスト取得中..."
        stocks = get_low_price_stocks()
        all_codes = stocks["code"].tolist()
        name_map = dict(zip(stocks["code"].astype(str), stocks["name"]))
        total = len(all_codes)
        logging.info(f"Scanning {total} stocks")

        def on_progress_1(c, t, code):
            _scan_status["progress"] = f"Stage 1: {code} ({c+1}/{t})"

        candidates = screen_stocks(all_codes, min_score=0, progress_callback=on_progress_1)

        for r in candidates:
            jpx = name_map.get(r["code"], "")
            if jpx and jpx.strip():
                r["name"] = jpx

        logging.info(f"Stage 1: {len(candidates)} candidates")

        results = []
        if candidates:
            def on_progress_2(c, t, code):
                _scan_status["progress"] = f"Stage 2: {code} ({c+1}/{t})"

            results = run_deep_analysis(candidates, progress_callback=on_progress_2)
            logging.info(f"Stage 2: {len(results)} results")

            save_screen_results("全市場500円以下", results)
            update_from_screening(results)

            for r in results[:10]:
                try:
                    record_recommendation(r)
                except Exception:
                    pass

        # === 2. ウォッチ追跡（スキャン対象外も含む全ウォッチ銘柄） ===
        _scan_status["progress"] = "ウォッチ追跡中..."
        try:
            wl_result = refresh_watchlist()
            logging.info(f"Watchlist: {wl_result['updated']} updated, {len(wl_result.get('auto_removed', []))} removed")
        except Exception as e:
            logging.warning(f"Watchlist refresh failed: {e}")

        # === 3. 答え合わせ + 最適化 ===
        _scan_status["progress"] = "答え合わせ中..."
        check_outcomes(days_after=30)
        update_weights()

        # 実績ログ
        try:
            stats = get_trade_stats()
            if stats["count"] > 0:
                logging.info(f"Trade stats: {stats['count']} trades, win_rate={stats['win_rate']}%, ev={stats['expected_value']}%")
        except Exception:
            pass

        # === 4. 海外情報チェック（毎回。FDA+EDGAR+ClinicalTrials） ===
        _scan_status["progress"] = "海外情報チェック中..."
        try:
            from src.data.overseas.monitor import run_overseas_check
            overseas = run_overseas_check(full=False)  # FDA+EDGAR+CT。fullは週1
            if overseas.get("high_count", 0) > 0:
                logging.warning(f"🚨 Overseas high-impact: {overseas['high_count']} alerts")
                for a in overseas.get("high_impact", [])[:5]:
                    logging.warning(f"  {a['code']} {a['company']}: {a.get('title', '')[:60]}")
        except Exception as e:
            logging.warning(f"Overseas check failed: {e}")

        # === 5. ゴールデンルール再検証（週1回） ===
        try:
            from src.analysis.backtest_validator import load_validated_rules, run_validation_backtest, find_golden_rules
            rules = load_validated_rules()
            last_gen = rules.get("generated_at", "")
            days_since = (date.today() - date.fromisoformat(last_gen)).days if last_gen else 999
            if days_since >= 7:
                # 週1: 海外フルチェック（News+PubMed含む）
                try:
                    from src.data.overseas.monitor import run_overseas_check
                    _scan_status["progress"] = "海外フルチェック中..."
                    run_overseas_check(full=True)
                except Exception:
                    pass

                _scan_status["progress"] = "ルール再検証中..."
                logging.info("Starting weekly rule recalibration")
                bt_df = run_validation_backtest(
                    all_codes,
                    progress_callback=lambda c, t, code: (
                        _scan_status.update({"progress": f"検証: {code} ({c+1}/{t})"})
                    ),
                )
                if not bt_df.empty:
                    new_rules = find_golden_rules(bt_df)
                    n_rules = len(new_rules.get("golden_rules", []))
                    logging.info(f"Recalibration complete: {n_rules} golden rules, baseline={new_rules.get('baseline_win_rate', 0)}%")
        except Exception as e:
            logging.warning(f"Rule recalibration failed: {e}")

        _scan_status["last_run"] = date.today().isoformat()
        _scan_status["progress"] = f"完了（{len(results)}件）"
        logging.info("=== Scan cycle completed ===")

    except Exception as e:
        _scan_status["error"] = str(e)
        _scan_status["progress"] = f"エラー: {e}"
        logging.error(f"Scan failed: {e}", exc_info=True)
    finally:
        _scan_status["running"] = False


def _background_loop():
    """バックグラウンドループ。起動時に即実行、以降1時間ごと。"""
    _run_scan()

    while True:
        time.sleep(3600)
        try:
            _run_scan()
        except Exception as e:
            logging.error(f"Background loop error: {e}")


def start_background_job():
    """バックグラウンドジョブを開始する。"""
    global _thread_started
    if _thread_started:
        return

    thread = threading.Thread(target=_background_loop, daemon=True)
    thread.start()
    _thread_started = True
