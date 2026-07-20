"""
業種内相対モメンタム戦略。

return_20dを市場全体と業種内の双方でz-score化し、設定比率でブレンドする。
バックテストの既存MomentumStrategyのトレンド・流動性ゲートは維持する。
"""

from __future__ import annotations

import math
import sqlite3
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence

from ..config import Config
from ..indicators import StockIndicators, register_cross_sectional_observer
from . import StrategyRegistry, StrategyResult
from .momentum import MomentumStrategy

UNKNOWN_SECTOR = "未分類"
EPSILON = 1e-12


@dataclass(frozen=True)
class SectorMomentumScore:
    """1銘柄の日次業種相対モメンタムスコア。"""

    sector: str
    raw_zscore: float
    sector_zscore: float
    blended_score: float


@StrategyRegistry.register("sector_relative_momentum")
class SectorRelativeMomentumStrategy(MomentumStrategy):
    """
    業種内相対momentum戦略。

    1. return_20dを市場全体と各業種内でz-score化
    2. raw_weightとrelative_weightでブレンド
    3. 業種ウェイトをユニバース業種比率の指定倍率以内に近似制限
    4. 各業種の上位N銘柄を候補化
    """

    def __init__(self, config: Config):
        super().__init__(config)
        self.strategy_name = "sector_relative_momentum"

        strategy_config = config.get("strategies.sector_relative_momentum", {})
        self.enabled = bool(strategy_config.get("enabled", False))
        self.raw_weight = float(strategy_config.get("raw_weight", 0.5))
        self.relative_weight = float(strategy_config.get("relative_weight", 0.5))
        self.max_sector_active_weight = float(
            strategy_config.get("max_sector_active_weight", 1.5)
        )
        self.top_n_per_sector = int(strategy_config.get("top_n_per_sector", 3))
        self.min_sector_size = int(strategy_config.get("min_sector_size", 5))
        self.max_positions = int(config.get("backtest.max_positions", 5))
        self._validate_config()

        self._sector_by_code: dict[str, str] = {}
        self._sector_sizes: Counter[str] = Counter()
        self._selected_scores: dict[str, SectorMomentumScore] = {}
        self._load_sector_metadata()
        register_cross_sectional_observer(self.prepare_cross_section)

    def _validate_config(self) -> None:
        if self.raw_weight < 0 or self.relative_weight < 0:
            raise ValueError("raw_weightとrelative_weightは0以上で指定してください")
        if self.raw_weight + self.relative_weight <= EPSILON:
            raise ValueError("raw_weightとrelative_weightの合計は0より大きくしてください")
        if self.max_sector_active_weight <= 0:
            raise ValueError("max_sector_active_weightは0より大きくしてください")
        if self.top_n_per_sector <= 0:
            raise ValueError("top_n_per_sectorは1以上で指定してください")
        if self.min_sector_size < 2:
            raise ValueError("min_sector_sizeは2以上で指定してください")
        if self.max_positions <= 0:
            raise ValueError("backtest.max_positionsは1以上で指定してください")

    @property
    def database_path(self) -> Path:
        return Path(self.config.database_path)

    @staticmethod
    def _normalize_sector(value: object) -> str:
        if value is None:
            return UNKNOWN_SECTOR
        normalized = str(value).strip()
        return normalized or UNKNOWN_SECTOR

    def _load_sector_metadata(self) -> None:
        """有効かつ売買可能なユニバースの業種情報を読み込む。"""
        with sqlite3.connect(str(self.database_path)) as connection:
            rows = connection.execute(
                """
                SELECT code, sector
                FROM symbols
                WHERE enabled = 1
                  AND COALESCE(role, 'trade_candidate') = 'trade_candidate'
                  AND COALESCE(tradable, 1) = 1
                """
            ).fetchall()

        self._sector_by_code = {
            str(code): self._normalize_sector(sector) for code, sector in rows
        }
        self._sector_sizes = Counter(self._sector_by_code.values())

    @staticmethod
    def _z_scores(values: Mapping[str, float]) -> dict[str, float]:
        """母標準偏差を使う決定論的z-score。分散0なら全銘柄0を返す。"""
        if not values:
            return {}
        numeric_values = [float(value) for value in values.values()]
        mean = statistics.fmean(numeric_values)
        stddev = statistics.pstdev(numeric_values)
        if not math.isfinite(stddev) or stddev <= EPSILON:
            return {code: 0.0 for code in values}
        return {
            code: (float(value) - mean) / stddev
            for code, value in values.items()
        }

    def _held_sector_counts(self) -> Counter[str]:
        """現在runの保有銘柄数を業種別に取得する。"""
        with sqlite3.connect(str(self.database_path)) as connection:
            row = connection.execute(
                """
                SELECT id
                FROM backtest_runs
                WHERE strategy_name = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (self.strategy_name,),
            ).fetchone()
            if row is None:
                return Counter()
            positions = connection.execute(
                """
                SELECT code
                FROM backtest_positions
                WHERE run_id = ? AND strategy_name = ? AND quantity > 0
                """,
                (int(row[0]), self.strategy_name),
            ).fetchall()

        return Counter(
            self._sector_by_code.get(str(position[0]), UNKNOWN_SECTOR)
            for position in positions
        )

    def _sector_position_limit(self, sector: str) -> int:
        """等額ポジション前提で業種ウェイト倍率を銘柄数上限へ変換する。"""
        known_sizes = {
            name: size
            for name, size in self._sector_sizes.items()
            if name != UNKNOWN_SECTOR and size >= self.min_sector_size
        }
        universe_size = sum(known_sizes.values())
        sector_size = known_sizes.get(sector, 0)
        if universe_size <= 0 or sector_size <= 0:
            return 0
        benchmark_weight = sector_size / universe_size
        approximate_limit = (
            self.max_positions * benchmark_weight * self.max_sector_active_weight
        )
        return max(1, math.ceil(approximate_limit - EPSILON))

    def prepare_cross_section(self, indicators: Sequence[StockIndicators]) -> None:
        """日次候補集合から業種内順位と最終選択集合を作る。"""
        self._selected_scores = {}
        if not indicators or not self._sector_by_code:
            return

        usable: dict[str, StockIndicators] = {}
        raw_values: dict[str, float] = {}
        sector_values: dict[str, dict[str, float]] = defaultdict(dict)
        for indicator in indicators:
            sector = self._sector_by_code.get(indicator.code, UNKNOWN_SECTOR)
            return_20d = indicator.return_20d
            if (
                sector == UNKNOWN_SECTOR
                or self._sector_sizes.get(sector, 0) < self.min_sector_size
                or return_20d is None
                or not math.isfinite(float(return_20d))
            ):
                continue
            usable[indicator.code] = indicator
            raw_values[indicator.code] = float(return_20d)
            sector_values[sector][indicator.code] = float(return_20d)

        raw_scores = self._z_scores(raw_values)
        relative_scores: dict[str, float] = {}
        for values in sector_values.values():
            relative_scores.update(self._z_scores(values))

        total_weight = self.raw_weight + self.relative_weight
        scored_by_sector: dict[str, list[tuple[SectorMomentumScore, StockIndicators]]] = (
            defaultdict(list)
        )
        for code, indicator in usable.items():
            base_result = super().evaluate(indicator)
            if base_result.signal_type != "BUY_CANDIDATE":
                continue
            sector = self._sector_by_code[code]
            raw_zscore = raw_scores[code]
            sector_zscore = relative_scores[code]
            blended_score = (
                self.raw_weight * raw_zscore
                + self.relative_weight * sector_zscore
            ) / total_weight
            score = SectorMomentumScore(
                sector=sector,
                raw_zscore=raw_zscore,
                sector_zscore=sector_zscore,
                blended_score=blended_score,
            )
            scored_by_sector[sector].append((score, indicator))

        ranked_candidates: list[tuple[SectorMomentumScore, StockIndicators]] = []
        for sector_candidates in scored_by_sector.values():
            sector_candidates.sort(
                key=lambda item: (
                    -item[0].blended_score,
                    -(item[1].return_20d or 0.0),
                    item[1].code,
                )
            )
            ranked_candidates.extend(sector_candidates[: self.top_n_per_sector])

        ranked_candidates.sort(
            key=lambda item: (
                -item[0].blended_score,
                -item[0].sector_zscore,
                item[1].code,
            )
        )

        held_counts = self._held_sector_counts()
        held_total = sum(held_counts.values())
        slots_available = max(0, self.max_positions - held_total)
        selected_counts: Counter[str] = Counter()
        for score, indicator in ranked_candidates:
            if len(self._selected_scores) >= slots_available:
                break
            sector_limit = self._sector_position_limit(score.sector)
            if held_counts[score.sector] + selected_counts[score.sector] >= sector_limit:
                continue
            self._selected_scores[indicator.code] = score
            selected_counts[score.sector] += 1

    def evaluate(
        self,
        indicators: StockIndicators,
        benchmark_returns: Optional[dict] = None,
    ) -> StrategyResult:
        """MomentumStrategyのゲートを満たし、日次選択集合に入った銘柄だけ採用する。"""
        result = super().evaluate(indicators, benchmark_returns)
        if result.signal_type != "BUY_CANDIDATE":
            return result

        score = self._selected_scores.get(indicators.code)
        if score is None:
            result.signal_type = "EXCLUDE"
            result.reason = "除外: 業種内順位・業種ウェイト上限の選択対象外"
            return result

        result.score = score.blended_score
        result.reason = (
            "買い候補: 業種相対momentum "
            f"sector={score.sector}, raw_z={score.raw_zscore:.3f}, "
            f"sector_z={score.sector_zscore:.3f}, blend={score.blended_score:.3f}"
        )
        return result
