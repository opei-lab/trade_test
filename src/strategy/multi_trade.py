"""複数回売買戦略モジュール

1つの銘柄で複数回の売買を行う戦略を立案する。
バイオの「導出で売り → 試験結果前に再度買い → 承認で売り」のような
段階的なトレードプランを生成する。

原則:
- 各山の頂点で売り、谷で再度買う
- 「山」はイベント（導出、試験結果、承認等）で定義される
- 各山の間には株価が下がる期間がある（利確売り、不確実性）
- 再エントリーは前の山の売値より安い水準
"""


# バイオ銘柄の典型的な山パターン
#
# 導出 vs 承認のインパクト:
#   導出（メガファーマへのライセンス）は「最初の山」であり、インパクトが最も大きいことが多い。
#   理由: 市場がゼロベースで評価していた銘柄に「メガファーマが価値を認めた」という事実が付く。
#   承認は「予想されていたイベント」であり、導出後は織り込みが進んでいるため、
#   株価への追加インパクトは導出より小さくなる傾向がある。
#
#   ただし、これは一般論であり、対象疾患の市場規模やデータの質で変動する。
#   小型バイオで時価総額100億未満の場合、導出のインパクトが特に大きい（3〜10倍）。
#
BIO_MOUNTAIN_PATTERN = {
    "mountains": [
        {
            "name": "山1: 導出（メガファーマへのライセンス）",
            "trigger": "大手製薬との提携/導出契約のIR",
            "typical_rise": "3〜10倍（小型バイオの場合。メガファーマの「お墨付き」が付く最初のイベント）",
            "rise_range": (3.0, 8.0),
            "duration_days": "数日〜2週間",
            "post_peak_drop": 0.45,
            "drop_reason": "利確売り。導出はスタートであり承認は先。短期筋の利確 + 時間コストを嫌った売り",
            "floor_factor": 0.6,  # 導出前の2〜3倍が新しい床（メガファーマが認めた事実は消えない）
            "action": "ピーク手前で売り。急騰初日〜2日目が勝負。出来高が減り始めたら撤退",
            "re_entry_hint": "導出後の利確売りが一巡（出来高が枯れる）+ 株価が安定したら再エントリー",
        },
        {
            "name": "山2: 臨床試験結果（Phase 2/3データ）",
            "trigger": "学会発表(ASCO等) or IRで試験結果公表",
            "typical_rise": "1.5〜3倍（導出後の調整水準から）",
            "rise_range": (1.5, 3.0),
            "duration_days": "数日〜1週間",
            "post_peak_drop": 0.30,
            "drop_reason": "データ織り込み済みの利確。承認申請→承認まで時間がかかるため時間コスト売り",
            "floor_factor": 0.75,
            "action": "学会前に仕込み、データ発表直後〜翌日に利確。良好データなら翌日も上がることが多い",
            "re_entry_hint": "データ発表後2〜4週間で落ち着く。出来高が通常水準に戻ったら再エントリー検討",
        },
        {
            "name": "山3: 承認（PMDA/FDA）",
            "trigger": "承認取得のIR",
            "typical_rise": "1.3〜2倍（データ発表後の水準から。導出時より小さい = 織り込み済み）",
            "rise_range": (1.3, 2.0),
            "duration_days": "数日",
            "post_peak_drop": 0.25,
            "drop_reason": "Sell the fact（承認は既に期待されていた）+ 上市準備の時間コスト",
            "floor_factor": 0.85,
            "action": "承認は「予想イベント」。サプライズが少ない分、導出ほどは跳ねない。利確は早めに",
            "re_entry_hint": "上市後の初回決算で売上実績が出るまで待つ選択肢もある",
        },
        {
            "name": "山4: 売上実績（ブロックバスター確認）",
            "trigger": "四半期決算で売上が市場予想上回り。年商1000億円ペース確認",
            "typical_rise": "1.5〜3倍（承認後の水準から。実績が出ると機関投資家が入る）",
            "rise_range": (1.5, 3.0),
            "duration_days": "数週間〜数ヶ月（機関投資家の組入でジワ上げ）",
            "post_peak_drop": 0.20,
            "drop_reason": "成長鈍化、競合品上市、薬価改定リスク",
            "floor_factor": 0.9,
            "action": "実績ベースなので急騰ではなくジワ上げ。長期保有向き。株式分割があれば流動性UP",
            "re_entry_hint": "ここまで来たら長期保有が合理的。分割があれば追加買いの好機",
        },
    ],
}

# テック銘柄の山パターン
TECH_MOUNTAIN_PATTERN = {
    "mountains": [
        {
            "name": "山1: 大型受注/提携発表",
            "trigger": "IRで大型契約、資本提携発表",
            "typical_rise": "1.5〜3倍",
            "rise_range": (1.5, 3.0),
            "post_peak_drop": 0.30,
            "drop_reason": "期待先行の利確。実際の売上計上は先",
            "floor_factor": 0.75,
            "action": "IR翌日〜2日で利確",
            "re_entry_hint": "利確売り一巡後、次の決算前に再エントリー",
        },
        {
            "name": "山2: 決算で実証（売上/利益に反映）",
            "trigger": "四半期決算で提携効果が数値化",
            "typical_rise": "1.3〜2倍",
            "rise_range": (1.3, 2.0),
            "post_peak_drop": 0.20,
            "drop_reason": "Sell the fact + 次四半期への不透明感",
            "floor_factor": 0.85,
            "action": "決算後の反応を見て判断。上方修正が出れば翌日も期待できる",
            "re_entry_hint": "連続増益が確認されれば長期保有に移行",
        },
    ],
}

# テック銘柄の山パターン
TECH_MOUNTAIN_PATTERN = {
    "mountains": [
        {
            "name": "大型受注/提携発表",
            "trigger": "IR",
            "typical_rise": "1.5〜3倍",
            "rise_range": (1.5, 3.0),
            "post_peak_drop": 0.30,
            "drop_reason": "期待先行の利確",
            "floor_factor": 0.75,
        },
        {
            "name": "売上/利益で実証",
            "trigger": "決算",
            "typical_rise": "1.3〜2倍",
            "rise_range": (1.3, 2.0),
            "post_peak_drop": 0.20,
            "drop_reason": "Sell the fact",
            "floor_factor": 0.85,
        },
    ],
}


def generate_multi_trade_plan(
    current_price: float,
    market_cap: float,
    sector: str,
    industry: str,
    target_market_size: float = 0,
) -> dict:
    """複数回売買のトレードプランを生成する。

    Returns:
        {
            "pattern": パターン名,
            "trades": [各トレードの詳細],
            "total_return": 全トレード合計のリターン,
            "total_return_pct": 合計リターン率,
            "summary": サマリー,
        }
    """
    combined = f"{sector} {industry}".lower()
    is_bio = any(kw in combined for kw in ["healthcare", "biotech", "医薬品", "drug"])

    pattern = BIO_MOUNTAIN_PATTERN if is_bio else TECH_MOUNTAIN_PATTERN
    mountains = pattern["mountains"]

    trades = []
    running_price = current_price
    running_capital = 1.0  # 正規化（1.0 = 元本100%）

    for i, mountain in enumerate(mountains):
        low_mult, high_mult = mountain["rise_range"]
        mid_mult = (low_mult + high_mult) / 2

        # 買い（前の山の下落後 or 初回は現在値）
        buy_price = running_price
        if i > 0:
            # 前の山のピークからの下落後に買う
            buy_price = running_price  # 既にpost_peak_dropが反映された水準

        # 売り（山の頂点の控えめ側で売る = 確実に利確）
        # ピークの80%で利確（欲張らない）
        conservative_mult = low_mult + (high_mult - low_mult) * 0.3
        sell_price = round(buy_price * conservative_mult)

        # この山のリターン
        trade_return = conservative_mult - 1
        running_capital *= conservative_mult

        # 山の後の下落 → 次の買い水準
        post_drop = mountain["post_peak_drop"]
        next_buy = round(sell_price * (1 - post_drop))
        floor = round(buy_price * mountain["floor_factor"])

        trades.append({
            "mountain": mountain["name"],
            "trigger": mountain["trigger"],
            "buy_price": round(buy_price),
            "sell_price": sell_price,
            "return_pct": round(trade_return * 100),
            "cumulative_capital": round(running_capital, 2),
            "post_peak_drop_pct": round(post_drop * 100),
            "re_entry_price": next_buy,
            "floor_price": floor,
            "typical_rise": mountain["typical_rise"],
            "drop_reason": mountain["drop_reason"],
            "action": mountain.get("action", ""),
            "re_entry_hint": mountain.get("re_entry_hint", ""),
        })

        # 次の山の開始水準
        running_price = next_buy

    total_return_pct = round((running_capital - 1) * 100)

    return {
        "pattern": "バイオ段階トレード" if is_bio else "テック段階トレード",
        "trades": trades,
        "total_return": round(running_capital, 2),
        "total_return_pct": total_return_pct,
        "summary": f"全{len(trades)}回のトレードで元本の{round(running_capital, 1)}倍（+{total_return_pct}%）",
    }


def format_trade_plan(plan: dict) -> str:
    """トレードプランをMarkdownで表示する。"""
    lines = [f"## 段階トレードプラン（{plan['pattern']}）\n"]
    lines.append(f"**{plan['summary']}**\n")

    for i, t in enumerate(plan["trades"]):
        lines.append(f"### 山{i+1}: {t['mountain']}")
        lines.append(f"トリガー: {t['trigger']}")
        lines.append(f"- 買い: ¥{t['buy_price']:,}")
        lines.append(f"- 売り: ¥{t['sell_price']:,}（+{t['return_pct']}%）")
        lines.append(f"- 想定上昇幅: {t['typical_rise']}")
        lines.append(f"- 累積リターン: 元本の{t['cumulative_capital']}倍")
        lines.append(f"")
        if t.get("action"):
            lines.append(f"**アクション: {t['action']}**")
        lines.append(f"")
        lines.append(f"売り後の展開:")
        lines.append(f"- ピークから-{t['post_peak_drop_pct']}%下落を想定")
        lines.append(f"- 理由: {t['drop_reason']}")
        lines.append(f"- 再エントリー目安: ¥{t['re_entry_price']:,}")
        if t.get("re_entry_hint"):
            lines.append(f"- 再エントリー判断: {t['re_entry_hint']}")
        lines.append(f"- 下値の床: ¥{t['floor_price']:,}（前ステージの事実確定で切り上がり）")
        lines.append("")

    return "\n".join(lines)
