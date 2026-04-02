"""SQLiteデータベース管理モジュール

株価履歴、スクリーニング結果、推奨記録、スコア重みを管理する。
"""

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    Float,
    String,
    Date,
    DateTime,
    Text,
    Index,
)
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "db" / "screener.db"
Base = declarative_base()


class PriceHistory(Base):
    """日次株価履歴"""

    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False)
    date = Column(Date, nullable=False)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Integer)

    __table_args__ = (
        Index("idx_price_code_date", "code", "date", unique=True),
    )


class ScreenResult(Base):
    """スクリーニング結果"""

    __tablename__ = "screen_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False)
    name = Column(String(200))
    screened_at = Column(DateTime, default=datetime.now)
    supply_score = Column(Float, default=0)  # 需給スコア
    theme_score = Column(Float, default=0)  # テーマスコア
    catalyst_score = Column(Float, default=0)  # カタリストスコア
    manipulation_score = Column(Float, default=0)  # 仕手パターンスコア
    total_score = Column(Float, default=0)  # 総合スコア
    phase = Column(String(10))  # 仕手フェーズ (A/B/C/D/E)
    entry_price = Column(Float)  # 推奨エントリー価格
    target_price = Column(Float)  # 目標売却価格
    stop_loss = Column(Float)  # 損切りライン
    reason = Column(Text)  # 推奨理由

    __table_args__ = (
        Index("idx_screen_code_date", "code", "screened_at"),
    )


class Recommendation(Base):
    """推奨記録と結果追跡（フィードバックループ用）"""

    __tablename__ = "recommendations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False)
    name = Column(String(200))
    recommended_at = Column(DateTime, default=datetime.now)
    entry_price = Column(Float)  # 推奨時の株価
    target_price = Column(Float)  # 目標価格
    strategy_type = Column(String(50))  # 戦略タイプ（仕手/テーマ/カタリスト等）
    # 結果追跡
    actual_high = Column(Float)  # 推奨後の実際の高値
    actual_low = Column(Float)  # 推奨後の実際の安値
    result_date = Column(Date)  # 結果確定日
    profit_pct = Column(Float)  # 損益率 (%)
    hit = Column(Integer)  # 的中: 1, 外れ: 0, 未確定: None


class ScoreWeights(Base):
    """スコアリングの重み（自動最適化対象）"""

    __tablename__ = "score_weights"

    id = Column(Integer, primary_key=True, autoincrement=True)
    updated_at = Column(DateTime, default=datetime.now)
    supply_weight = Column(Float, default=0.3)
    theme_weight = Column(Float, default=0.3)
    catalyst_weight = Column(Float, default=0.2)
    manipulation_weight = Column(Float, default=0.2)
    hit_rate = Column(Float)  # その時点での的中率


def get_engine():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{DB_PATH}", echo=False)


def get_session():
    engine = get_engine()
    Session = sessionmaker(bind=engine)
    return Session()


def init_db():
    """データベースとテーブルを初期化する。"""
    engine = get_engine()
    Base.metadata.create_all(engine)

    # デフォルトの重みを挿入（存在しない場合）
    session = get_session()
    if session.query(ScoreWeights).count() == 0:
        session.add(ScoreWeights())
        session.commit()
    session.close()


def save_price_history(code: str, df):
    """DataFrameの株価データをDBに保存する（UPSERT）。"""
    session = get_session()
    for date, row in df.iterrows():
        existing = (
            session.query(PriceHistory)
            .filter_by(code=code, date=date.date() if hasattr(date, "date") else date)
            .first()
        )
        if existing:
            existing.open = float(row.get("Open", 0))
            existing.high = float(row.get("High", 0))
            existing.low = float(row.get("Low", 0))
            existing.close = float(row.get("Close", 0))
            existing.volume = int(row.get("Volume", 0))
        else:
            session.add(
                PriceHistory(
                    code=code,
                    date=date.date() if hasattr(date, "date") else date,
                    open=float(row.get("Open", 0)),
                    high=float(row.get("High", 0)),
                    low=float(row.get("Low", 0)),
                    close=float(row.get("Close", 0)),
                    volume=int(row.get("Volume", 0)),
                )
            )
    session.commit()
    session.close()
