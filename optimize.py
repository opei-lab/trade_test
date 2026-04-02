"""過去データによる重み最適化バッチ

過去2年分のグロース市場データで「どの条件が実際に効いたか」を検証し、
確度条件の重みを最適化する。結果はファイルに保存。

使い方: python optimize.py
所要時間: 10〜30分（銘柄数による）
"""

import json
import sys
import os
from datetime import datetime
from pathlib import Path

os.chdir(os.path.dirname(os.path.abspath(__file__)))

print("=" * 50)
print("  Stock Screener - Weight Optimization")
print("  Past data backtest + weight tuning")
print("=" * 50)
print()

try:
    from src.data.database import init_db
    init_db()
except Exception as e:
    print(f"[ERROR] DB init: {e}")

from src.data.stocklist import get_growth_stocks
from src.feedback.historical_optimizer import (
    run_historical_backtest,
    optimize_weights,
    apply_optimized_weights,
    find_quick_patterns,
    format_optimization_report,
)
from src.strategy.conviction import CONVICTION_CHECKS

# Step 1: 銘柄リスト取得
print("[1/4] Fetching stock list...")
try:
    stocks = get_growth_stocks()
    codes = stocks["code"].tolist()
    print(f"  Growth market: {len(codes)} stocks")
except Exception as e:
    print(f"[ERROR] {e}")
    input("Press Enter to exit...")
    sys.exit(1)

# 上限100銘柄（時間とAPI負荷のバランス）
scan_count = min(100, len(codes))
codes = codes[:scan_count]
print(f"  Scanning: {scan_count} stocks")
print()

# Step 2: 大規模バックテスト実行
print("[2/4] Running historical backtest (this takes a while)...")
print()

def on_progress(current, total, code):
    pct = (current + 1) / total * 100
    bar = "#" * int(pct // 2) + "-" * (50 - int(pct // 2))
    print(f"\r  [{bar}] {pct:.0f}% ({current+1}/{total}) {code}    ", end="", flush=True)

bt_df = run_historical_backtest(
    codes,
    period_days=730,
    sample_interval=15,  # 15日ごと（より細かくサンプリング）
    hold_days=60,
    progress_callback=on_progress,
)
print()
print()

if bt_df.empty:
    print("[ERROR] No backtest data generated")
    input("Press Enter to exit...")
    sys.exit(1)

print(f"  Total samples: {len(bt_df)}")

# パスの質の統計
if "is_clean_win" in bt_df.columns:
    clean = bt_df["is_clean_win"].sum()
    quick = bt_df["is_quick_win"].sum()
    painful = bt_df["is_painful_win"].sum()
    loss = bt_df["is_loss"].sum()
    print(f"  Clean wins (safe +30%, DD<10%, <20d): {clean} ({clean/len(bt_df)*100:.1f}%)")
    print(f"  Quick wins (fast +15%, DD<5%, <10d):  {quick} ({quick/len(bt_df)*100:.1f}%)")
    print(f"  Painful wins (DD>15% but still +):     {painful} ({painful/len(bt_df)*100:.1f}%)")
    print(f"  Losses (peak <+5%):                    {loss} ({loss/len(bt_df)*100:.1f}%)")
print()

# Step 3: 各目標で最適化
print("[3/4] Optimizing weights...")
print()

targets = {
    "is_clean_win": "Safe win (+30%, DD<10%, <20 days)",
    "is_quick_win": "Quick win (+15%, DD<5%, <10 days)",
    "path_quality": "Path quality (overall score)",
}

all_results = {}
for target_key, target_name in targets.items():
    print(f"  Target: {target_name}")
    optimized = optimize_weights(bt_df, target=target_key)
    all_results[target_key] = optimized

    # 上位条件を表示
    sorted_conds = sorted(optimized.items(), key=lambda x: x[1].get("lift", 0), reverse=True)
    for cid, data in sorted_conds[:5]:
        check_name = next((c["name"] for c in CONVICTION_CHECKS if c["id"] == cid), cid)
        lift = data.get("lift", 0)
        weight = data.get("weight", 3)
        arrow = "+" if lift > 0 else ""
        print(f"    {check_name}: weight={weight}, lift={arrow}{lift}%")
    print()

# メイン目標（安全な勝ち）で重みを適用
print("  Applying weights optimized for: Safe win")
main_optimized = all_results["is_clean_win"]
apply_optimized_weights(main_optimized)
print()

# Step 4: パターン発見
print("[4/4] Finding winning patterns...")
patterns = find_quick_patterns(bt_df)
if patterns:
    for p in patterns:
        print(f"  {p['name']}")
        print(f"    Rate: {p['occurrence_rate']}%, Avg gain: +{p['avg_gain']}%, Avg days: {p['avg_days']:.0f}, DD: -{p['avg_dd']}%")
        print(f"    Key conditions: {', '.join(p['key_conditions'].keys())}")
        print()
else:
    print("  No clear patterns found yet (need more data)")
print()

# 結果をファイルに保存
output_dir = Path("data")
output_dir.mkdir(exist_ok=True)
output_file = output_dir / "optimization_result.json"

save_data = {
    "timestamp": datetime.now().isoformat(),
    "samples": len(bt_df),
    "stocks_scanned": scan_count,
    "weights": {},
    "patterns": patterns,
}
for cid, data in main_optimized.items():
    check_name = next((c["name"] for c in CONVICTION_CHECKS if c["id"] == cid), cid)
    save_data["weights"][cid] = {
        "name": check_name,
        "weight": data.get("weight", 3),
        "lift": data.get("lift", 0),
        "samples": data.get("samples", 0),
    }

with open(output_file, "w", encoding="utf-8") as f:
    json.dump(save_data, f, ensure_ascii=False, indent=2)

print(f"Results saved to: {output_file}")
print()

# レポート出力
report = format_optimization_report(main_optimized, patterns)
report_file = output_dir / "optimization_report.md"
with open(report_file, "w", encoding="utf-8") as f:
    f.write(report)
print(f"Report saved to: {report_file}")

print()
print("=" * 50)
print("  Optimization complete!")
print(f"  Next time you run the screener,")
print(f"  optimized weights will be used.")
print("=" * 50)
print()
input("Press Enter to exit...")
