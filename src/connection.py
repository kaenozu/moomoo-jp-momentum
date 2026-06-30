"""
OpenD接続確認モジュール

ファイルパス: src/connection.py
何をするか: moomoo OpenDへの接続を確認し、接続状態を管理する
なぜ存在するか: アプリケーションの動作前提となる接続確認を担当するため
関連ファイル: config.py, quote_service.py
"""

import logging
import socket
from dataclasses import dataclass
from typing import Optional

from futu import (
    OpenQuoteContext,
    RET_OK,
)

from .config import Config

logger = logging.getLogger(__name__)


@dataclass
class ConnectionStatus:
    """接続状態"""
    connected: bool
    message: str
    hint: Optional[str] = None
    quote_context: Optional[OpenQuoteContext] = None


def _check_port_open(host: str, port: int, timeout: float = 3.0) -> bool:
    """ポートが開いているかを確認する"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


class OpenDConnection:
    """OpenDへの接続を管理するクラス"""

    def __init__(self, config: Config):
        """
        Args:
            config: 設定オブジェクト
        """
        self.host = config.opend_host
        self.port = config.opend_port
        self.timeout = config.opend_timeout
        self._quote_context: Optional[OpenQuoteContext] = None

    def connect(self) -> ConnectionStatus:
        """
        OpenDへの接続を試行する

        Returns:
            ConnectionStatus: 接続結果（エラーメッセージと対処法を含む）
        """
        logger.info(
            f"OpenDへの接続を試行します: {self.host}:{self.port}"
        )

        # ステップ1: ポートが開いているか確認
        if not _check_port_open(self.host, self.port, timeout=3.0):
            msg = (
                f"OpenDのポート {self.port} に接続できません"
            )
            hint = (
                "対処法:\n"
                "  1. moomoo OpenDが起動しているか確認してください\n"
                "  2. OpenDの設定でポート番号が 11111 になっているか確認してください\n"
                "  3. ファイアウォールでポートがブロックされていないか確認してください"
            )
            logger.error(msg)
            return ConnectionStatus(
                connected=False,
                message=msg,
                hint=hint,
            )

        # ステップ2: OpenQuoteContextの作成
        try:
            self._quote_context = OpenQuoteContext(
                host=self.host,
                port=self.port,
            )
        except Exception as e:
            msg = f"OpenQuoteContextの作成に失敗しました: {e}"
            hint = (
                "対処法:\n"
                "  1. OpenDを再起動してください\n"
                "  2. OpenDのバージョンを確認してください"
            )
            logger.error(msg)
            return ConnectionStatus(
                connected=False,
                message=msg,
                hint=hint,
            )

        # ステップ3: 接続テスト（スナップショット取得）
        ret, data = self._quote_context.get_market_snapshot(
            ["JP.7203"]  # トヨタ自動車でテスト
        )

        if ret == RET_OK:
            logger.info("OpenDへの接続に成功しました")
            return ConnectionStatus(
                connected=True,
                message="接続成功",
                quote_context=self._quote_context,
            )

        # 接続失敗時のエラーメッセージ分析
        error_str = str(data).lower()
        msg = f"接続テスト失敗: {data}"

        if "not login" in error_str or "login" in error_str:
            hint = (
                "対処法:\n"
                "  1. OpenDでmoomooアカウントにログインしてください\n"
                "  2. アカウント登録が完了しているか確認してください"
            )
        elif "permission" in error_str or "auth" in error_str:
            hint = (
                "対処法:\n"
                "  1. 行情カード（LV2）を購入しているか確認してください\n"
                "  2. moomoo証券で日本株の相場権限が有効になっているか確認してください\n"
                "  3. [相場ストア](https://qtcard.moomoo.com/index/cards-mall)で確認してください"
            )
        elif "subscribe" in error_str:
            hint = (
                "対処法:\n"
                "  1. 購読枠の上限に達している可能性があります\n"
                "  2. 資産額に応じた購読枠を確認してください"
            )
        else:
            hint = (
                "対処法:\n"
                "  1. OpenDを再起動してください\n"
                "  2. OpenDのログを確認してください\n"
                "  3. ネットワーク接続を確認してください"
            )

        logger.error(msg)
        return ConnectionStatus(
            connected=False,
            message=msg,
            hint=hint,
        )

    def disconnect(self) -> None:
        """接続を閉じる"""
        if self._quote_context:
            self._quote_context.close()
            self._quote_context = None
            logger.info("接続を閉じました")

    def get_quote_context(self) -> Optional[OpenQuoteContext]:
        """
        行情用のコンテキストを取得する

        Returns:
            OpenQuoteContext: 行情コンテキスト。未接続の場合はNone
        """
        return self._quote_context

    def __enter__(self):
        """コンテキストマネージャー対応"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """コンテキストマネージャー対応"""
        self.disconnect()
        return False


def test_connection(config: Config) -> ConnectionStatus:
    """
    接続テストを実行する便利関数

    Args:
        config: 設定オブジェクト

    Returns:
        ConnectionStatus: 接続結果
    """
    with OpenDConnection(config) as conn:
        return conn.connect()
