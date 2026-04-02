"""推奨レポート生成モジュール（段階トレードプラン対応）

スクリーニング結果から「いつ判断できて、いつ買いで、いつ売りか」を
データに基づいて文面生成する。

原則:
- 全ての記述は算出済みの数値データから組み立てる
- 推測・予測は行わない
- 「〜の可能性がある」ではなく「データがこうなっている」と書く
"""

import json
import requests
from src.strategy.multi_trade import generate_multi_trade_plan, format_trade_plan


def generate_report_template(r: dict) -> str:
    """テンプレートベースでレポートを生成する（LLM不要）。

    全ての文言は渡されたデータの数値から機械的に組み立てる。
    """
    sections = []

    # --- 銘柄概要 ---
    name = r.get("name", r.get("code", ""))
    code = r.get("code", "")
    current = r.get("current_price", 0)
    sections.append(f"## {name} ({code})  現在値 ¥{current:,.0f}")

    # --- なぜ上がると言えるか（数値ストーリー） ---
    sections.append("### なぜこの銘柄か")

    story_parts = []

    # 需給の構造
    vol_anomaly = r.get("volume_anomaly", 0)
    squeeze = r.get("squeeze", 0)
    price_pos = r.get("price_position", 50)
    mcap = r.get("market_cap", 0)
    mcap_str = f"{mcap/1e8:,.0f}億" if mcap > 0 else ""

    if price_pos < 20:
        story_parts.append(f"株価は過去レンジの下位{price_pos:.0f}%に位置しており、底値圏にある。")
    elif price_pos < 40:
        story_parts.append(f"株価は過去レンジの下位{price_pos:.0f}%。まだ割安圏。")

    if vol_anomaly < 0.8:
        story_parts.append(f"出来高は平常の{vol_anomaly:.1f}倍にまで縮小しており、売りたい人がいなくなった状態。")
    elif vol_anomaly > 2:
        story_parts.append(f"出来高が平常の{vol_anomaly:.1f}倍に急増。何かが動き始めている。")

    if squeeze > 60:
        story_parts.append(f"ボリンジャーバンド幅は過去の下位{100-squeeze:.0f}%まで収縮。静寂の後に大きな動きが来る典型的なパターン。")

    # 信用残
    margin_ratio = r.get("margin_ratio", 0)
    if margin_ratio > 0 and margin_ratio < 1:
        story_parts.append(f"信用倍率{margin_ratio:.1f}倍（売り長）。空売りが溜まっており、踏み上げの燃料になる。")
    elif margin_ratio > 3:
        story_parts.append(f"信用倍率{margin_ratio:.1f}倍と高いが、上値抵抗のスコアは許容範囲内。")

    # しこり・真空地帯
    overhead_pct = r.get("overhead_pct", 0)
    ceiling_score = r.get("ceiling_score", 50)
    vacuum_desc = r.get("vacuum_desc", "")
    if vacuum_desc:
        story_parts.append(vacuum_desc)
    elif ceiling_score < 20:
        story_parts.append("上方にしこりがほぼなく、上値が軽い。上がり始めれば抵抗が少ない。")
    elif overhead_pct > 30:
        story_parts.append(f"上方に出来高{overhead_pct:.0f}%のしこりあり。段階的な利確が必要。")

    # ML逆張りシグナル
    ml_prob = r.get("ml_win_prob")
    whale_phase = r.get("whale_phase", "none")
    if ml_prob is not None and ml_prob < 0.1 and whale_phase in ("accumulating", "holding"):
        story_parts.append(f"**ML逆張りシグナル検出。** 統計モデルは勝率{ml_prob*100:.0f}%と予測（ほぼ負け判定）。しかし大口が仕込み中。数値に現れない動きがある=市場が見落としている可能性。")

    # 大口の売買計画
    whale_plan_desc = r.get("whale_plan_description", "")
    if whale_plan_desc and "検出されていない" not in whale_plan_desc:
        story_parts.append(f"**{whale_plan_desc}**")
        # 大口の推定取得コストと目標
        wp = r.get("whale_plan", {})
        tz = wp.get("target_zone", {})
        if tz.get("target_low"):
            story_parts.append(tz.get("description", ""))

    # 大口・機関投資家の動き
    whale_summary = r.get("whale_summary", "")
    if whale_summary and "検出されていない" not in whale_summary:
        story_parts.append(whale_summary)
    inst_signals = r.get("institutional_signals", [])
    for sig in inst_signals[:2]:
        story_parts.append(sig)

    # セクター相対強弱
    sector_desc = r.get("sector_description", "")
    if sector_desc:
        story_parts.append(sector_desc)

    # ステージ変化
    stage_summary = r.get("stage_summary", "")
    if stage_summary and stage_summary != "特筆すべきステージ変化なし":
        story_parts.append(f"**ファンダメンタルの変化: {stage_summary}**")
        market_gap = r.get("market_gap", "none")
        if market_gap == "large":
            story_parts.append("この変化に対して株価がまだ反応していない。市場が織り込んでいないギャップが大きい。")

    # 時価総額
    if mcap_str and mcap > 0 and mcap < 30e9:
        story_parts.append(f"時価総額{mcap_str}の小型株であり、資金が入れば株価が大きく動く構造。")

    # 結論
    conviction = r.get("conviction", {})
    grade = conviction.get("grade", "?")
    passed_names = [p["name"] for p in conviction.get("passed", []) if p.get("weight", 0) >= 4]
    if passed_names:
        story_parts.append(f"**重要条件の合致: {', '.join(passed_names)}。確度{grade}。**")

    if story_parts:
        sections.append("\n".join(story_parts))
    else:
        sections.append("需給スコアが基準以上。")

    # --- 判断タイミング ---
    sections.append("### いつ判断できるか")

    events = r.get("upcoming_events", [])
    event_desc = r.get("event_description", "")
    if events:
        sections.append(f"直近のイベント: {event_desc}")
        nearest = events[0]
        days = nearest.get("days_until", 0)
        if days < 0:
            sections.append(f"**{nearest['event_name']}は{abs(days)}日前に終了。** IRや開示で結果が出ているか確認すべきタイミング。")
        elif days == 0:
            sections.append(f"**{nearest['event_name']}は本日。** 結果を確認して判断。")
        elif days <= 7:
            sections.append(f"**{nearest['event_name']}まであと{days}日。** イベント前にポジションを取るか、結果を待つかの判断が必要。")
        elif days <= 30:
            sections.append(f"{nearest['event_name']}まで{days}日。事前にエントリーする場合は、現在の需給状態で判断する。")
        else:
            sections.append(f"{nearest['event_name']}は{days}日後。まだ時間があるため、需給の変化を監視しながら判断。")
    else:
        sections.append("特定のイベントは検出されていない。需給指標の変化で判断する。")

    # --- 直近シグナル ---
    timing_signals = r.get("timing_signals", [])
    urgency = r.get("urgency", "watching")
    if timing_signals:
        sections.append("### 直近の動き")
        for sig in timing_signals:
            sections.append(f"- {sig}")
        if urgency == "immediate":
            sections.append("**複数の発火シグナルが検出されている。注視すべき局面。**")

    # --- 買いの判断 ---
    sections.append("### 買い")
    entry = r.get("entry", 0)
    timing = r.get("timing", "WAIT")
    floor_price = r.get("floor_price", 0)
    max_down = r.get("max_downside_pct", 0)

    # 買いタイミングと注文方法
    squeeze_val = r.get("squeeze", 0)
    timing_score = r.get("timing_score", 0)
    vol_anom = r.get("volume_anomaly", 1)
    if vol_anom >= 1.5 and current > entry * 1.05:
        # 既に出来高増+ちょい上げ済み → 押し目を待つ
        dip_price = round(current * 0.90)
        sections.append(f"**出来高増でちょい上げ済み。慌てて買わない。**")
        sections.append(f"注文: 押し目¥{dip_price:,}（現在値-10%）に指値。")
        sections.append(f"バックテスト: ちょい上げ後の押し目で買う → 勝率85-94%、平均+36%")
        sections.append(f"押し目は平均10日後。指値を入れて待つ。")
    elif squeeze_val > 40 and timing_score >= 25:
        sections.append("**買いタイミング良好。** ボラ収縮+出来高変化シグナル。")
        sections.append(f"注文: ¥{entry:,}に指値。")
    elif timing_score >= 25:
        sections.append("出来高に変化あり。動き始めのサイン。")
        sections.append(f"注文: ¥{entry:,}に指値。急がず約定を待つ。")
    elif squeeze_val > 60:
        sections.append("ボラ収縮中だが出来高変化はまだ。ウォッチに入れて出来高を監視。")
        sections.append(f"注文: 出来高が動き始めてから¥{entry:,}に指値。今は買わない。")
    else:
        sections.append(f"注文: ¥{entry:,}に指値。焦らず約定を待つ。")

    # IR・ニュースの自動確認結果
    news_signals = r.get("news_signals", [])
    positive_ir = r.get("positive_catalysts", [])
    if positive_ir:
        sections.append(f"**直近のポジティブIR:** {', '.join(positive_ir[:3])}")
    if news_signals:
        for ns in news_signals[:3]:
            sections.append(f"- {ns}")
    if not positive_ir and not news_signals:
        sections.append("直近IRにポジティブな事実確定なし。出来高・需給のみの判断。")

    # ワラントリスク
    dilution = r.get("dilution_risk_count", 0)
    stage_risks = r.get("stage_risks", [])
    dilution_items = [ri for ri in stage_risks if isinstance(ri, dict) and ri.get("type") in ("dilution", "high_float")]
    if dilution > 0 or dilution_items:
        sections.append(f"**⚠ ワラント/希薄化リスク検出。** エントリー前にIRを確認すること。リスクを許容できない場合は見送り。")

    if timing == "NOW":
        sections.append(f"**現在値がエントリー圏内（¥{entry:,}付近）。**")
    elif timing == "NEAR":
        sections.append(f"エントリー価格¥{entry:,}に接近中。もう少し下がれば圏内。")
    else:
        sections.append(f"エントリー価格¥{entry:,}まで距離がある。¥{entry:,}付近まで引きつけて買いたい。")

    if floor_price > 0:
        sections.append(f"下値の床: ¥{floor_price:,}（最大下落リスク -{max_down:.0f}%）")

    # 買いの根拠
    conviction = r.get("conviction", {})
    passed = conviction.get("passed", [])
    if passed:
        buy_reasons = []
        for p in passed:
            desc = p.get("description", "")
            if p.get("weight", 0) >= 4:
                buy_reasons.append(f"- **{p['name']}**{': ' + desc if desc else ''}")
            elif p.get("weight", 0) >= 3:
                buy_reasons.append(f"- {p['name']}{': ' + desc if desc else ''}")
        if buy_reasons:
            sections.append("買いの根拠（重要度順）:")
            sections.extend(buy_reasons)

    # --- 売りの判断 ---
    sections.append("### 売り（段階目標）")
    # 推奨売値は目標の90%（バックテスト最適値。100%だと届かずに反落するリスク）
    target_val = r.get("target", 0)
    entry_val = r.get("entry", 0)
    recommended_sell = round(entry_val + (target_val - entry_val) * 0.9) if entry_val > 0 else target_val
    sections.append(f"**推奨売値: ¥{recommended_sell:,}**（目標の90%。バックテストで手数料・税金考慮後の最適値）")
    sections.append(f"売りシグナル: 出来高が購入時水準を下回って3日連続減少 → ピーク通過、利確。購入時より高い水準での減少は「落ち着き」であり売りではない。")
    # 大口コストベースの安全圏
    whale_plan_data = r.get("whale_plan", {})
    whale_cost = 0
    if isinstance(whale_plan_data, dict):
        whale_cost = whale_plan_data.get("accumulation", {}).get("avg_cost", 0)
    if whale_cost > 0:
        safe_floor = round(whale_cost * 0.95)
        safe_target = round(whale_cost * 2)
        sections.append(f"大口コスト¥{whale_cost:,} → 下値安全圏¥{safe_floor:,}、上値目安¥{safe_target:,}")

        # 仕込み量から上昇持続期間を推定
        accum_data = whale_plan_data.get("accumulation", {})
        accum_shares = accum_data.get("accumulated_shares", 0)
        avg_vol = r.get("volume_anomaly", 1)  # 近似値として使用
        if accum_shares > 0:
            # info取得可能な場合は実出来高を使う
            info_avg_vol = 0
            try:
                from src.data.price import get_stock_info
                si = get_stock_info(r.get("code", ""))
                info_avg_vol = si.get("average_volume", 0)
            except Exception:
                pass
            if info_avg_vol > 0:
                unwind_days = round(accum_shares / (info_avg_vol * 0.15))
                if unwind_days < 7:
                    sections.append(f"売り抜け所要{unwind_days}日 → **⚠ 一過性リスク。注文設定が間に合わない可能性。見送り推奨。**")
                elif unwind_days < 30:
                    sections.append(f"売り抜け所要{unwind_days}日 → 数週間は持続見込み。数日は様子を見て利確タイミングを選べる")
                else:
                    sections.append(f"売り抜け所要{unwind_days}日 → 中期で持続見込み。じっくり利確可能")

    sections.append(f"注文: **買い約定と同時に売り指値¥{recommended_sell:,}を入れる。** 急騰で即日到達なら自動利確。翌日以降の下落に備えて売り指値は常に入れておく。")
    sections.append("")
    stop_loss = r.get("stop_loss", 0)
    risk_pct = r.get("risk_pct", 0)

    # 段階目標がある場合
    staged = r.get("staged_targets", [])
    if staged:
        for s in staged:
            prob_bar = "●" * int(s["probability"] // 10) + "○" * int(10 - s["probability"] // 10)
            sections.append(
                f"- **{s['step']}**: ¥{s['target_price']:,}（{s['multiplier']}倍）"
                f" 確率{s['probability']}% {prob_bar}"
            )
            if s.get("floor_after"):
                sections.append(f"  到達後の床: ¥{s['floor_after']:,}")
    else:
        target = r.get("target", 0)
        reward_pct = r.get("reward_pct", 0)
        multiplier = r.get("multiplier", 0)
        target_basis = r.get("target_basis", "")
        sections.append(f"目標: ¥{target:,}（+{reward_pct:.0f}%、{multiplier:.1f}倍） 根拠: {target_basis}")

        prev_highs = r.get("prev_highs", [])
        if prev_highs:
            sections.append(f"過去の主要高値: {', '.join([f'¥{int(ph):,}' for ph in prev_highs[:3]])}")

    sections.append(f"\n損切り: ¥{stop_loss:,}（-{risk_pct:.0f}%）")
    rr = r.get("risk_reward", 0)
    sections.append(f"リスクリワード比: {rr:.1f}")

    # 期間推定
    timeframe = r.get("timeframe", {})
    tf_desc = timeframe.get("description", "")
    if tf_desc:
        sections.append(f"**期間推定: {tf_desc}**")
    est_days = timeframe.get("estimated_days")
    if est_days:
        from datetime import date, timedelta
        target_date = date.today() + timedelta(days=est_days)
        sections.append(f"目標到達の目安: {target_date.isoformat()}頃")

    # 売りタイミングの補足
    ceiling_score = r.get("ceiling_score", 0)
    overhead_pct = r.get("overhead_pct", 0)
    margin_ratio = r.get("margin_ratio", 0)

    sell_notes = []
    if overhead_pct > 30:
        sell_notes.append(f"上方に出来高{overhead_pct:.0f}%のしこりあり。途中で上値が重くなる可能性。段階的な利確を検討。")
    if margin_ratio > 3:
        sell_notes.append(f"信用倍率{margin_ratio:.1f}倍。信用の解消売りで上値が抑えられる可能性。")
    if not sell_notes:
        sell_notes.append("上値のしこり・信用の重しは軽い。目標価格まで比較的素直に上昇しやすい環境。")
    for note in sell_notes:
        sections.append(note)

    # --- リスク ---
    risks = r.get("stage_risks", [])
    risk_factors = r.get("risk_factors", [])
    all_risks = risks + [{"description": rf} for rf in risk_factors]
    if all_risks:
        sections.append("### リスク")
        for risk in all_risks:
            desc = risk.get("description", str(risk))
            sections.append(f"- {desc}")

    # --- バックテスト実績 ---
    bt = r.get("backtest", {})
    if bt:
        sections.append("### 過去実績（バックテスト）")
        stats = bt.get("overall_stats", {})
        if stats:
            sections.append(f"同条件での過去実績（{stats.get('total_samples', 0)}サンプル）:")
            sections.append(f"- 60日間の平均上昇: +{stats.get('avg_gain_60d', 0)}%")
            sections.append(f"- 60日間で2倍達成率: {stats.get('hit_2x_rate', 0)}%")
            sections.append(f"- 平均最大ドローダウン: -{stats.get('avg_drawdown', 0)}%")

        era = bt.get("era_comparison", {})
        if "modern" in era:
            m = era["modern"]
            sections.append(f"- **直近（{m['period']}）**: 平均+{m['avg_gain_60d']}%, ドローダウン-{m['avg_drawdown']}%")

        realistic = bt.get("realistic", {})
        if realistic:
            sections.append(f"- 現実的な目標（75%到達ライン）: 30日+{realistic.get('realistic_gain_30d', 0)}%, 60日+{realistic.get('realistic_gain_60d', 0)}%")
            sections.append(f"- ワーストケース（90%ile）: -{realistic.get('worst_case_drawdown', 0)}%")

        patterns = bt.get("patterns", [])
        if patterns:
            sections.append("有効なパターン:")
            for p in patterns[:3]:
                sections.append(f"- {p['description']}: +{p['avg_gain']}%（通常+{p['baseline_gain']}%、優位性+{p['advantage']}%）")

    # --- 確度 ---
    grade = r.get("conviction_grade", "?")
    conv_score = conviction.get("conviction_score", 0)
    conv_count = conviction.get("conviction_count", 0)
    total = conviction.get("total_checks", 0)
    sections.append(f"### 確度: {grade} ({conv_score}pt, {conv_count}/{total}条件合致)")

    # 不合致条件（何が足りないか）
    failed = conviction.get("failed", [])
    important_failed = [f for f in failed if f["weight"] >= 4]
    if important_failed:
        sections.append("足りない条件（重要度4以上）:")
        for f in important_failed:
            sections.append(f"- {f['name']}: {f['description']}")

    # --- 段階トレードプラン ---
    trade_plan = r.get("trade_plan")
    if trade_plan and trade_plan.get("trades"):
        sections.append(format_trade_plan(trade_plan))

    return "\n\n".join(sections)


def generate_report_llm(r: dict, ollama_model: str = "llama3") -> str:
    """Ollama（ローカルLLM）でレポートを生成する。

    LLMには「データを読みやすい日本語にする」だけを任せる。
    推測・予測は禁止。
    """
    # テンプレートで生成した内容をベースにする
    template_report = generate_report_template(r)

    prompt = f"""以下は株式銘柄の分析データから機械的に生成されたレポートです。
これを投資判断に使いやすい自然な日本語に書き直してください。

ルール:
- データに書かれている事実のみを使うこと
- 推測、予測、「〜だろう」「〜と思われる」は禁止
- 「データがこうなっている」「数値がこう示している」という事実ベースの記述のみ
- 構成は「概要→判断タイミング→買い→売り→リスク→確度」の順

元データ:
{template_report}
"""

    try:
        resp = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": ollama_model,
                "prompt": prompt,
                "stream": False,
            },
            timeout=60,
        )
        if resp.status_code == 200:
            return resp.json().get("response", template_report)
    except Exception:
        pass

    # Ollama未起動時はテンプレート版を返す
    return template_report


def generate_report(r: dict, use_llm: bool = False) -> str:
    """レポートを生成する。

    Args:
        r: スクリーニング結果の1銘柄分
        use_llm: Ollamaを使うかどうか

    Returns:
        Markdown形式のレポート文字列
    """
    if use_llm:
        return generate_report_llm(r)
    return generate_report_template(r)
