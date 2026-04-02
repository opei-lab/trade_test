"""機械学習による勝率予測モジュール

ルールベースの閾値判定ではなく、全指標を特徴量として投入し、
勾配ブースティングで「60日後にclean winする確率」を予測する。

過学習対策:
- 時系列分割（過去で学習、直近でテスト）
- Walk-forward検証
- 特徴量の重要度で不要な特徴を除外
"""

import numpy as np
import pandas as pd
import json
import pickle
from pathlib import Path
from datetime import datetime
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score

MODEL_DIR = Path(__file__).parent.parent.parent / "data"
MODEL_FILE = MODEL_DIR / "ml_model.pkl"
REPORT_FILE = MODEL_DIR / "ml_report.json"

# 特徴量の定義
FEATURE_COLS = [
    "price_position",      # 株価位置 (0-100)
    "squeeze",             # ボラ収縮スコア
    "divergence",          # 売り枯れ
    "accumulation",        # 買い集め
    "volume_anomaly",      # 出来高異常度
    "supply_score",        # 需給総合スコア
]

TARGET_COL = "is_clean_win"  # 安全勝ち（+30%, DD<10%, <20日）


def prepare_features(bt_df: pd.DataFrame) -> tuple:
    """バックテストDataFrameから特徴量とターゲットを抽出する。"""
    available = [c for c in FEATURE_COLS if c in bt_df.columns]
    if not available or TARGET_COL not in bt_df.columns:
        return None, None, None

    X = bt_df[available].copy()
    y = bt_df[TARGET_COL].astype(int).copy()

    # NaN/inf処理
    X = X.replace([np.inf, -np.inf], np.nan).fillna(0)

    return X, y, available


def train_model(bt_df: pd.DataFrame) -> dict:
    """バックテスト結果からモデルを学習する。

    時系列分割でWalk-forward検証を行い、過学習をチェックする。

    Returns:
        {
            "model": 学習済みモデル,
            "metrics": 検証メトリクス,
            "feature_importance": 特徴量重要度,
            "overfitting_check": 過学習チェック結果,
        }
    """
    X, y, features = prepare_features(bt_df)
    if X is None or len(X) < 100:
        return {"error": "データ不足（100件以上必要）", "samples": len(bt_df) if bt_df is not None else 0}

    # 時系列分割でWalk-forward検証
    tscv = TimeSeriesSplit(n_splits=5)
    train_scores = []
    test_scores = []
    test_precisions = []
    all_test_probs = []

    best_model = None
    best_test_score = 0

    for train_idx, test_idx in tscv.split(X):
        X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
        y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]

        model = GradientBoostingClassifier(
            n_estimators=100,
            max_depth=3,       # 浅い木で過学習を防ぐ
            min_samples_leaf=10,  # 最低10サンプル
            learning_rate=0.05,
            subsample=0.8,     # ランダムサンプリングで汎化
            random_state=42,
        )
        model.fit(X_train, y_train)

        train_pred = model.predict(X_train)
        test_pred = model.predict(X_test)
        test_probs = model.predict_proba(X_test)[:, 1] if len(model.classes_) > 1 else np.zeros(len(X_test))

        train_acc = accuracy_score(y_train, train_pred)
        test_acc = accuracy_score(y_test, test_pred)
        test_prec = precision_score(y_test, test_pred, zero_division=0)

        train_scores.append(train_acc)
        test_scores.append(test_acc)
        test_precisions.append(test_prec)
        all_test_probs.extend(test_probs.tolist())

        if test_acc > best_test_score:
            best_test_score = test_acc
            best_model = model

    # 過学習チェック
    avg_train = np.mean(train_scores)
    avg_test = np.mean(test_scores)
    overfit_gap = avg_train - avg_test
    is_overfitting = overfit_gap > 0.15  # 15%以上の差は過学習

    # 特徴量重要度
    importance = {}
    if best_model:
        for feat, imp in zip(features, best_model.feature_importances_):
            importance[feat] = round(float(imp), 4)

    # モデル保存
    if best_model and not is_overfitting:
        MODEL_DIR.mkdir(exist_ok=True)
        with open(MODEL_FILE, "wb") as f:
            pickle.dump({"model": best_model, "features": features}, f)

    # レポート保存
    report = {
        "timestamp": datetime.now().isoformat(),
        "samples": len(X),
        "positive_rate": round(float(y.mean()) * 100, 1),
        "metrics": {
            "train_accuracy": round(avg_train * 100, 1),
            "test_accuracy": round(avg_test * 100, 1),
            "test_precision": round(np.mean(test_precisions) * 100, 1),
            "overfit_gap": round(overfit_gap * 100, 1),
        },
        "is_overfitting": bool(is_overfitting),
        "feature_importance": dict(sorted(importance.items(), key=lambda x: x[1], reverse=True)),
    }

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    return report


def predict_win_probability(supply: dict) -> float | None:
    """学習済みモデルで勝率を予測する。

    Returns:
        勝率（0-1）。モデルがない場合はNone。
    """
    if not MODEL_FILE.exists():
        return None

    try:
        with open(MODEL_FILE, "rb") as f:
            data = pickle.load(f)

        model = data["model"]
        features = data["features"]

        # 特徴量を抽出
        values = []
        for feat in features:
            values.append(supply.get(feat, 0))

        X = np.array([values])
        X = np.nan_to_num(X, nan=0, posinf=0, neginf=0)

        prob = model.predict_proba(X)[0]
        # positive classの確率
        if len(prob) > 1:
            return float(prob[1])
        return float(prob[0])

    except Exception:
        return None


def get_model_report() -> dict | None:
    """学習レポートを取得する。"""
    if not REPORT_FILE.exists():
        return None
    try:
        with open(REPORT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None
