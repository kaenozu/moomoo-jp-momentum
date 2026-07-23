"""
相場レジーム検出モジュール

ファイルパス: src/regime_detector.py
何をするか: SMA短期・長期線からbull/bear/rangeを判定する
なぜ存在するか: idle cash ETFオーバーレイを相場環境に連動させるため
関連ファイル: src/backtest_runner.py, config.yaml
"""

from typing import Optional, TypedDict

import pandas as pd

from .config import Config


class RegimeRecord(TypedDict):
    """1営業日分の相場レジーム判定結果。"""

    date: str
    regime: str
    sma50: Optional[float]
    sma200: Optional[float]


class RegimeDetector:
    """終値と移動平均から相場レジームを判定する。"""

    VALID_REGIMES = frozenset({"bull", "bear", "range"})

    def __init__(self, config: Config):
        cfg = config.get("backtest.idle_cash_allocation.regime", {}) or {}
        if not isinstance(cfg, dict):
            raise ValueError("regime設定はマッピングで指定してください")

        self.enabled = bool(cfg.get("enabled", False))
        self.signal_code = str(cfg.get("signal_code", "JP.1306"))
        self.short_window = int(cfg.get("short_window", 50))
        self.long_window = int(cfg.get("long_window", 200))

        if self.short_window <= 0 or self.long_window <= 0:
            raise ValueError("SMAウィンドウは正の整数で指定してください")
        if self.short_window >= self.long_window:
            raise ValueError("short_windowはlong_windowより小さくしてください")

        raw_active_regimes = cfg.get("active_regimes", ["bull"])
        if isinstance(raw_active_regimes, str):
            raw_active_regimes = [raw_active_regimes]
        if not isinstance(raw_active_regimes, list):
            raise ValueError("active_regimesは文字列のリストで指定してください")

        self.active_regimes = [
            str(regime).lower() for regime in raw_active_regimes
        ]
        unknown = set(self.active_regimes) - self.VALID_REGIMES
        if unknown:
            raise ValueError(
                "未対応のレジームが指定されています: "
                + ", ".join(sorted(unknown))
            )

    def detect(self, df: pd.DataFrame) -> list[RegimeRecord]:
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

        data = df[["date", "close"]].copy()
        data["date"] = pd.to_datetime(data["date"], errors="coerce")
        data["close"] = pd.to_numeric(data["close"], errors="coerce")
        data = (
            data.dropna(subset=["date"])
            .sort_values("date")
            .drop_duplicates(subset=["date"], keep="last")
            .reset_index(drop=True)
        )
        if data.empty:
            return []

        data["sma_short"] = data["close"].rolling(
            window=self.short_window,
            min_periods=self.short_window,
        ).mean()
        data["sma_long"] = data["close"].rolling(
            window=self.long_window,
            min_periods=self.long_window,
        ).mean()

        results: list[RegimeRecord] = []
        for _, row in data.iterrows():
            close = row["close"]
            sma_short = row["sma_short"]
            sma_long = row["sma_long"]
            date = str(row["date"])[:10]

            if (
                pd.isna(close)
                or pd.isna(sma_short)
                or pd.isna(sma_long)
            ):
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
                    "sma50": round(float(sma_short), 2),
                    "sma200": round(float(sma_long), 2),
                }
            )

        return results

    def regime_on(
        self,
        date: str,
        regimes: list[RegimeRecord],
    ) -> Optional[str]:
        """指定日の1営業日前のレジームを返し、先読みを防止する。"""
        for index, result in enumerate(regimes):
            if result["date"] != date:
                continue
            if index == 0:
                return None
            return regimes[index - 1]["regime"]
        return None

    def is_overlay_active(self, regime: Optional[str]) -> bool:
        """設定上オーバーレイを適用すべきレジームか判定する。"""
        if not self.enabled or regime is None:
            return False
        return regime.lower() in self.active_regimes
