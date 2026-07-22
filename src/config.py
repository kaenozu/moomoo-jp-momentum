"""
設定管理モジュール

ファイルパス: src/config.py
何をするか: YAML設定ファイルの読み込みと管理
なぜ存在するか: アプリケーション全体の設定を一元管理するため
関連ファイル: config.example.yaml, main.py, filters/quality_risk_filter.py
"""

from pathlib import Path
from typing import Any

import yaml


class Config:
    """設定ファイルを読み込み、アクセスするためのクラス"""

    def __init__(self, config_path: str = "config.yaml"):
        """
        Args:
            config_path: 設定ファイルのパス。デフォルトはconfig.yaml
        """
        self.config_path = Path(config_path)
        self._config: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        """設定ファイルを読み込む"""
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"設定ファイルが見つかりません: {self.config_path}\n"
                f"config.example.yaml を config.yaml としてコピーしてください"
            )

        with open(self.config_path, encoding="utf-8") as file:
            loaded = yaml.safe_load(file)
        self._config = loaded if isinstance(loaded, dict) else {}

    def get(self, key_path: str, default: Any = None) -> Any:
        """
        ドット区切りのキーで設定値を取得する

        Args:
            key_path: "opend.host" のようなドット区切りのキー
            default: キーが存在しない場合のデフォルト値

        Returns:
            設定値
        """
        keys = key_path.split(".")
        value: Any = self._config

        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default

        return value

    @property
    def opend_host(self) -> str:
        """OpenDのホストアドレス"""
        return self.get("opend.host", "127.0.0.1")

    @property
    def opend_port(self) -> int:
        """OpenDのポート番号"""
        return self.get("opend.port", 11111)

    @property
    def opend_timeout(self) -> int:
        """OpenDの接続タイムアウト（秒）"""
        return self.get("opend.timeout", 10)

    @property
    def database_path(self) -> str:
        """データベースファイルのパス"""
        return self.get("database.path", "data/moomoo.db")

    @property
    def watchlist_file(self) -> str:
        """銘柄リストファイルのパス"""
        return self.get("watchlist.symbols_file", "data/symbols.json")

    @property
    def max_symbols(self) -> int:
        """最大監視銘柄数"""
        return self.get("watchlist.max_symbols", 50)

    @property
    def trading_hours(self) -> dict[str, Any]:
        """取引時間設定"""
        return self.get("trading_hours") or {}

    @property
    def signals_config(self) -> dict[str, Any]:
        """シグナル判定設定"""
        return self.get("signals") or {}

    @property
    def quality_filter_config(self) -> dict[str, Any]:
        """モメンタム戦略で使用する品質・過熱フィルター設定"""
        return self.get("signals.quality_filter") or {}

    @property
    def scoring_config(self) -> dict[str, Any]:
        """スコアリング設定"""
        return self.get("scoring") or {}

    @property
    def benchmark_config(self) -> dict[str, Any]:
        """ベンチマーク設定"""
        return self.get("benchmark") or {}


def load_config(config_path: str = "config.yaml") -> Config:
    """
    設定ファイルを読み込んでConfigオブジェクトを返す

    Args:
        config_path: 設定ファイルのパス

    Returns:
        Configオブジェクト
    """
    return Config(config_path)
