"""検証スクリプト: スクリーニング精度の検証

過去の有名な急騰銘柄で「スクリーニングが事前に検出できたか」を検証する。
"""

from src.data.price import fetch_price
from src.analysis.supply import calc_supply_score
from src.analysis.manipulation.detector import detect_phase
from src.strategy.screener import find_price_targets, calc_entry_exit
from src.analysis.resistance import detect_volume_vacuum
from src.strategy.conviction import calc_conviction

codes = ["4572", "3133", "6526", "7203", "3697"]
names = {"4572": "Carna Bio", "3133": "Kaiho", "6526": "Socionext", "7203": "Toyota", "3697": "SHIFT"}

for code in codes:
    print(f"\n{'=' * 60}")
    print(f"  {names.get(code, code)} ({code})")
    print(f"{'=' * 60}")

    df = fetch_price(code, period_days=730)
    if df.empty:
        print("  Data fetch failed")
        continue

    current = float(df["Close"].iloc[-1])
    high = float(df["Close"].max())
    low = float(df["Close"].min())
    pos = (current - low) / (high - low) * 100 if high > low else 50
    print(f"  Current: {current:,.0f}  High: {high:,.0f}  Low: {low:,.0f}  Position: {pos:.0f}%")

    supply = calc_supply_score(df)
    phase = detect_phase(df)
    trade = calc_entry_exit(df, supply, phase)
    vacuum = detect_volume_vacuum(df)
    levels = find_price_targets(df)

    print(f"\n  Supply score: {supply['total']:.1f}")
    print(f"  Price position: {supply.get('price_position', '?')}%")
    print(f"  Volume anomaly: {supply.get('volume_anomaly', 0):.2f}x")
    print(f"  Squeeze: {supply.get('squeeze', 0):.1f}")
    print(f"  Phase: {phase['phase']} ({phase['confidence']}%)")

    print(f"\n  Entry: {trade['entry']:,}  Target: {trade['target']:,}  Stop: {trade['stop_loss']:,}")
    print(f"  Reward: +{trade['reward_pct']:.0f}%  RR: {trade['risk_reward']:.1f}")
    print(f"  Target basis: {trade['target_basis']}")
    print(f"  Timeframe: {trade.get('timeframe', {}).get('description', 'N/A')}")
    print(f"  Vacuum: {'YES' if vacuum['has_vacuum'] else 'no'} {vacuum.get('description', '')}")
    print(f"  Past highs: {[f'{h:,.0f}' for h in levels.get('prev_highs', [])[:3]]}")

    # Sanity checks
    print(f"\n  --- Checks ---")
    ok = True
    if trade["target"] > high * 1.5:
        print(f"  [WARN] Target {trade['target']:,} > 150% of period high {high:,.0f}")
        ok = False
    if trade["entry"] > current * 1.15:
        print(f"  [WARN] Entry {trade['entry']:,} far above current {current:,.0f}")
        ok = False
    if trade["risk_reward"] < 1:
        print(f"  [WARN] RR < 1")
        ok = False
    if ok:
        print("  [OK] All checks passed")

print("\n\nDone.")
