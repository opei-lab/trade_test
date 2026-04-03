"""テーマ自動検出モジュール

みんかぶのテーマランキングをスクレイピングし、
旬のテーマとその関連銘柄を自動取得する。

themes.yaml を自動更新して、コード変更なしでトレンド追従。
手動で書いたテーマ（manual: true）は上書きしない。
"""

import time
import yaml
import requests
from bs4 import BeautifulSoup
from pathlib import Path
from datetime import datetime

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

THEMES_PATH = Path(__file__).parent.parent / "config" / "themes.yaml"


def fetch_theme_ranking(ranking_type: str = "rise") -> list[dict]:
    """みんかぶからテーマランキングを取得する。

    Args:
        ranking_type: "rise"（急上昇）or "popular"（人気）

    Returns:
        [{"name": str, "url": str, "rank": int}]
    """
    url = f"https://minkabu.jp/theme/{ranking_type}_ranking"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        themes = []
        seen = set()
        skip = {"/theme/", "/theme/rise_ranking", "/theme/popular_ranking"}
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if "/theme/" not in href or href in skip:
                continue
            name = a.get_text(strip=True)
            if not name or len(name) <= 1 or "ランキング" in name or "プレミアム" in name:
                continue
            if name in seen:
                continue
            seen.add(name)
            themes.append({
                "name": name,
                "url": f"https://minkabu.jp{href}" if not href.startswith("http") else href,
                "rank": len(themes) + 1,
            })
            if len(themes) >= 30:
                break
        return themes
    except Exception:
        return []


def fetch_theme_stocks(theme_url: str, max_stocks: int = 20) -> list[str]:
    """テーマページから関連銘柄コードを取得する。

    Returns:
        ["4572", "3778", ...]
    """
    try:
        resp = requests.get(theme_url, headers=HEADERS, timeout=10)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        codes = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if "/stock/" not in href:
                continue
            code = href.split("/stock/")[-1].split("/")[0].split("?")[0]
            if code.isdigit() and len(code) == 4 and code not in seen:
                seen.add(code)
                codes.append(code)
                if len(codes) >= max_stocks:
                    break
        return codes
    except Exception:
        return []


def fetch_trending_themes(top_n: int = 15) -> list[dict]:
    """急上昇 + 人気テーマを統合して返す。

    Returns:
        [{
            "name": str,
            "source": "rise" | "popular",
            "rank": int,
            "codes": [str],
        }]
    """
    results = []
    seen_names = set()

    # 急上昇を優先（ニュースより先に動く）
    for ranking_type in ["rise", "popular"]:
        themes = fetch_theme_ranking(ranking_type)
        for t in themes:
            if t["name"] in seen_names:
                continue
            seen_names.add(t["name"])
            results.append({
                "name": t["name"],
                "source": ranking_type,
                "rank": t["rank"],
                "url": t["url"],
                "codes": [],
            })
            if len(results) >= top_n:
                break
        if len(results) >= top_n:
            break
        time.sleep(0.5)

    # 上位テーマの関連銘柄を取得（上位5テーマのみ。速度対策）
    for i, t in enumerate(results[:5]):
        t["codes"] = fetch_theme_stocks(t["url"], max_stocks=15)
        if i < 4:
            time.sleep(0.5)

    return results


def update_themes_yaml(trending: list[dict] = None):
    """themes.yamlを最新テーマで自動更新する。

    - manual: true のテーマは上書きしない
    - 自動テーマは auto: true で追加
    - 古い自動テーマでランキングから消えたものは削除
    """
    if trending is None:
        trending = fetch_trending_themes()

    # 現在のthemes.yaml読み込み
    existing = {}
    if THEMES_PATH.exists():
        try:
            with open(THEMES_PATH, "r", encoding="utf-8") as f:
                existing = yaml.safe_load(f) or {}
        except Exception:
            existing = {}

    # manual テーマを保護
    manual_themes = {}
    auto_themes = {}
    for key, data in existing.items():
        if not isinstance(data, dict):
            continue
        if data.get("manual"):
            manual_themes[key] = data
        else:
            auto_themes[key] = data

    # 新しい自動テーマを構築
    new_auto = {}
    for t in trending:
        # テーマ名からYAMLキーを生成
        key = _theme_name_to_key(t["name"])

        # 手動テーマと重複する場合はスキップ（手動が優先）
        if key in manual_themes:
            continue

        # 既存の自動テーマがあればcodesを更新
        if key in auto_themes:
            old = auto_themes[key]
            old["known_codes"] = t["codes"] if t["codes"] else old.get("known_codes", [])
            old["rank"] = t["rank"]
            old["source"] = t["source"]
            old["updated"] = datetime.now().strftime("%Y-%m-%d")
            new_auto[key] = old
        else:
            new_auto[key] = {
                "name": t["name"],
                "keywords": [t["name"]],  # テーマ名自体がキーワード
                "related_sectors": [],
                "catalysts": [],
                "known_codes": t["codes"],
                "auto": True,
                "source": t["source"],
                "rank": t["rank"],
                "updated": datetime.now().strftime("%Y-%m-%d"),
            }

    # 統合: manual + new_auto
    merged = {}
    merged.update(manual_themes)
    merged.update(new_auto)

    # YAML書き出し
    THEMES_PATH.parent.mkdir(exist_ok=True)
    with open(THEMES_PATH, "w", encoding="utf-8") as f:
        f.write("# テーマ辞書（自動更新 + 手動定義）\n")
        f.write(f"# 最終更新: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write("# manual: true のテーマは自動更新で上書きされない\n")
        f.write("# auto: true のテーマはランキングから自動生成\n\n")
        yaml.dump(merged, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    return {
        "manual_count": len(manual_themes),
        "auto_count": len(new_auto),
        "total": len(merged),
        "trending_names": [t["name"] for t in trending[:10]],
    }


def _theme_name_to_key(name: str) -> str:
    """テーマ名をYAMLキーに変換する。"""
    # 日本語はそのまま、スペースをアンダースコアに
    return name.replace(" ", "_").replace("・", "_").replace("（", "").replace("）", "").replace("/", "_").lower()


def check_stock_themes(code: str) -> list[str]:
    """特定の銘柄コードが属するテーマを返す。"""
    if not THEMES_PATH.exists():
        return []
    try:
        with open(THEMES_PATH, "r", encoding="utf-8") as f:
            themes = yaml.safe_load(f) or {}
    except Exception:
        return []

    matched = []
    for key, data in themes.items():
        if not isinstance(data, dict):
            continue
        codes = [str(c) for c in data.get("known_codes", [])]
        if code in codes:
            matched.append(data.get("name", key))
    return matched


def detect_emerging_themes(ir_titles: list[str], min_count: int = 3) -> list[dict]:
    """IR適時開示タイトルの頻出キーワードから新興テーマを検出する。

    みんかぶのランキングに載る前のテーマ候補を検出。
    複数企業が同時期に同じキーワードのIRを出していたら、テーマ化の前兆。

    Args:
        ir_titles: 直近のIRタイトル群（複数銘柄分）
        min_count: 最低出現回数（デフォルト3社以上）

    Returns:
        [{"keyword": str, "count": int, "titles": [str]}]
    """
    # テーマ候補キーワード（一般的すぎるものは除外）
    exclude = {"決算", "業績", "株主総会", "配当", "取締役", "監査", "四半期",
               "定時", "招集", "通知", "訂正", "短信", "連結", "非連結",
               "お知らせ", "に関する", "について", "における"}

    # N-gramベースでキーワードを抽出
    # 2-4文字の漢字N-gram + カタカナ語 + 英字語
    import re
    keyword_count: dict[str, list[str]] = {}

    for title in ir_titles:
        # カタカナ語（3文字以上の連続）
        katakana = re.findall(r'[ァ-ヴー]{3,}', title)
        # 英字（3文字以上）
        english = re.findall(r'[A-Za-z]{3,}', title)

        # 漢字N-gram（2-4文字）: 「核融合実験装置」→「核融合」「融合実」「実験装」...
        kanji_seq = re.findall(r'[一-龥]+', title)
        kanji_ngrams = []
        for seq in kanji_seq:
            for n in [3, 2, 4]:  # 3文字優先
                for i in range(len(seq) - n + 1):
                    gram = seq[i:i+n]
                    if gram not in exclude:
                        kanji_ngrams.append(gram)

        for word in katakana + kanji_ngrams + english:
            if word in exclude or len(word) < 2:
                continue
            if word not in keyword_count:
                keyword_count[word] = []
            # 同一タイトルからの重複カウントを防止
            if not keyword_count[word] or keyword_count[word][-1] != title[:50]:
                keyword_count[word].append(title[:50])

    # min_count回以上出現したキーワード = テーマ候補
    emerging = []
    for kw, titles in keyword_count.items():
        if len(titles) >= min_count:
            emerging.append({
                "keyword": kw,
                "count": len(titles),
                "titles": titles[:5],
            })

    emerging.sort(key=lambda x: (-x["count"], -len(x["keyword"])))

    # 上位語に包含される短い語を除外（「核融合」があれば「核融」「融合」は不要）
    filtered = []
    used_keywords = set()
    for e in emerging:
        kw = e["keyword"]
        # 既に採用したキーワードの部分文字列なら除外
        if any(kw in used for used in used_keywords):
            continue
        filtered.append(e)
        used_keywords.add(kw)

    return filtered[:20]
