from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"patch marker not found: {path}: {old[:80]!r}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


Path("src/regime_detector.py").write_text(
    '''"""
相場レジーム検出モジュール

ファイルパス: src/regime_detector.py
何をするか: SMA短期・長期線からbull/bear/rangeを判定する
なぜ存在するか: idle cash ETFオーバーレイを相場環境に連動させるため
関連ファイル: src/backtest_runner.py, config.yaml
"""

import math
from typing import Any, Optional, cast

import pandas as pd

from .config import Config


class RegimeDetector:
    """終値と移動平均から相場レジームを判定する。"""

    def __init__(self, config: Config):
        cfg = config.get("backtest.idle_cash_allocation.regime", {}) or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.signal_code = str(cfg.get("signal_code", "JP.1306"))
        self.short_window = int(cfg.get("short_window", 50))
        self.long_window = int(cfg.get("long_window", 200))

        if self.short_window <= 0 or self.long_window <= 0:
            raise ValueError("SMAウィンドウは正の整数で指定してください")

        active_regimes = cfg.get("active_regimes", ["bull"])
        if isinstance(active_regimes, str):
            active_regimes = [active_regimes]
        self.active_regimes = [str(regime) for regime in active_regimes]

    @staticmethod
    def _optional_float(value: Any) -> Optional[float]:
        """pandas由来のスカラーを有限判定可能なfloatへ変換する。"""
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return None if math.isnan(result) else result

    def detect(self, df: pd.DataFrame) -> list[dict]:
        """日付順のレジーム系列を返す。"""
        if df.empty:
            return []

        required_columns = {"date", "close"}
        missing = required_columns.difference(df.columns)
        if missing:
            raise ValueError(
                "レジーム判定に必要な列がありません: "
                + ", ".join(sorted(missing))
            )

        data = df.copy().sort_values("date").reset_index(drop=True)
        data["sma_short"] = data["close"].rolling(
            window=self.short_window,
            min_periods=self.short_window,
        ).mean()
        data["sma_long"] = data["close"].rolling(
            window=self.long_window,
            min_periods=self.long_window,
        ).mean()

        records = cast(list[dict[str, Any]], data.to_dict(orient="records"))
        results = []
        for row in records:
            close = self._optional_float(row["close"])
            sma_short = self._optional_float(row["sma_short"])
            sma_long = self._optional_float(row["sma_long"])
            date = str(row["date"])[:10]

            if close is None or sma_short is None or sma_long is None:
                results.append(
                    {
                        "date": date,
                        "regime": "unknown",
                        "sma50": None,
                        "sma200": None,
                    }
                )
                continue

            if close > sma_long and sma_short > sma_long:
                regime = "bull"
            elif close < sma_long and sma_short < sma_long:
                regime = "bear"
            else:
                regime = "range"

            results.append(
                {
                    "date": date,
                    "regime": regime,
                    "sma50": round(sma_short, 2),
                    "sma200": round(sma_long, 2),
                }
            )

        return results

    def regime_on(
        self,
        date: str,
        regimes: list[dict],
    ) -> Optional[str]:
        """指定日の1営業日前のレジームを返し、先読みを防止する。"""
        for index, result in enumerate(regimes):
            if result["date"] != date:
                continue
            if index == 0:
                return None
            return str(regimes[index - 1]["regime"])
        return None

    def is_overlay_active(self, regime: Optional[str]) -> bool:
        """設定上オーバーレイを適用すべきレジームか判定する。"""
        if not self.enabled or regime is None:
            return False
        return regime in self.active_regimes
''',
    encoding="utf-8",
)

replace_once(
    "src/backtest_runner.py",
    "from typing import Optional, Protocol\n",
    "from typing import Optional, Protocol, cast\n",
)
replace_once(
    "src/backtest_runner.py",
    '''                regimes = regime_detector.detect(regime_df[["date", "close"]])
''',
    '''                regime_input = cast(
                    pd.DataFrame,
                    regime_df.loc[:, ["date", "close"]].copy(),
                )
                regimes = regime_detector.detect(regime_input)
''',
)

Path(".github/fix_regime_overlay_types.py").unlink()
