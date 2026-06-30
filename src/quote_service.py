"""
相場データ取得モジュール

ファイルパス: src/quote_service.py
何をするか: moomoo OpenDから日本株の相場データを取得する
なぜ存在するか: 行情データの取得ロジックを一元管理するため
関連ファイル: connection.py, models.py, data_store.py

利用可否メモ（2026-06-30確認）:
- get_market_snapshot: ✓ 取得成功（行情カード不要）
- request_history_kline: ✓ 取得成功（行情カード不要）
- get_stock_quote: 未確認（要配信登録）
- get_order_book: 未使用（行情カード必要の可能性）
- get_rt_ticker: 未使用（行情カード必要の可能性）
- get_cur_kline: 未使用（要配信登録）

MVPでは行情カード不要のAPIのみ使用する。
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from futu import (
    KLType,
    Market,
    OpenQuoteContext,
    RET_OK,
    SecurityType,
    SubType,
)

from .config import Config
from .models import DailyBar, Quote

logger = logging.getLogger(__name__)

# futu-apiの過去ローソク足取得は1回最大1000件
MAX_KLINE_PER_REQUEST = 1000


class QuoteService:
    """相場データ取得サービス"""

    def __init__(self, config: Config, quote_context: OpenQuoteContext):
        """
        Args:
            config: 設定オブジェクト
            quote_context: OpenDの行情コンテキスト
        """
        self.config = config
        self.ctx = quote_context

    def get_stock_snapshot(self, codes: list[str]) -> pd.DataFrame:
        """
        複数銘柄のマーケットスナップショットを取得する

        Args:
            codes: 銘柄コードのリスト（例: ["JP.7203", "JP.6758"]）

        Returns:
            pd.DataFrame: スナップショットデータ
        """
        if not codes:
            return pd.DataFrame()

        logger.info(f"スナップショット取得: {len(codes)}銘柄")

        ret, data = self.ctx.get_market_snapshot(codes)

        if ret != RET_OK:
            logger.error(f"スナップショット取得失敗: {data}")
            return pd.DataFrame()

        return data

    def get_stock_quote(self, codes: list[str]) -> pd.DataFrame:
        """
        複数銘柄のリアルタイム株価を取得する

        Args:
            codes: 銘柄コードのリスト

        Returns:
            pd.DataFrame: 株価データ
        """
        if not codes:
            return pd.DataFrame()

        # 配信登録が必要
        for code in codes:
            ret, _ = self.ctx.subscribe(
                code,
                [SubType.QUOTE],
                subscribe_push=False,
            )
            if ret != RET_OK:
                logger.warning(f"配信登録失敗: {code}")

        ret, data = self.ctx.get_stock_quote(codes)

        if ret != RET_OK:
            logger.error(f"株価取得失敗: {data}")
            return pd.DataFrame()

        return data

    def get_daily_klines(
        self,
        code: str,
        num: int = 120,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        日足ローソク足を取得する（ページング対応）

        futu-apiの制限: 1回のリクエストで最大1000件まで。
        1000件を超える場合は、ページングして複数回取得する。

        Args:
            code: 銘柄コード
            num: 取得本数（デフォルト120: 約6営業月）
            start: 開始日（YYYY-MM-DD形式）
            end: 終了日（YYYY-MM-DD形式）

        Returns:
            pd.DataFrame: 日足データ
        """
        logger.info(f"日足取得: {code} (num={num})")

        if num <= MAX_KLINE_PER_REQUEST:
            # 1回で取得可能な場合
            ret, data, _ = self.ctx.request_history_kline(
                code,
                ktype=KLType.K_DAY,
                max_count=num,
                start=start,
                end=end,
            )

            if ret != RET_OK:
                logger.error(f"日足取得失敗: {code} - {data}")
                return pd.DataFrame()

            return data

        # ページングが必要な場合
        all_data = pd.DataFrame()
        remaining = num
        current_end = end

        while remaining > 0:
            batch_size = min(remaining, MAX_KLINE_PER_REQUEST)

            ret, data, page_req_key = self.ctx.request_history_kline(
                code,
                ktype=KLType.K_DAY,
                max_count=batch_size,
                start=start,
                end=current_end,
            )

            if ret != RET_OK:
                logger.error(f"日足取得失敗: {code} - {data}")
                break

            if data.empty:
                break

            all_data = pd.concat([all_data, data], ignore_index=True)
            remaining -= len(data)

            # 次のページの開始位置を設定
            if page_req_key is None or len(data) < batch_size:
                # これ以上データがない
                break

            # 最古の日付の前日を次のendにする
            oldest_date = data["time_key"].min()[:10]
            current_end = oldest_date

        logger.info(f"日足取得完了: {code} - {len(all_data)}件")
        return all_data

    def get_cur_daily_klines(
        self,
        code: str,
        num: int = 30,
    ) -> pd.DataFrame:
        """
        get_cur_klineで直近の日足を取得する（最新データを優先）

        Args:
            code: 銘柄コード
            num: 取得本数（デフォルト30）

        Returns:
            pd.DataFrame: 日足データ
        """
        logger.info(f"直近日足取得(get_cur_kline): {code} (num={num})")

        # 事前にサブスクライブ
        ret, _ = self.ctx.subscribe(
            code,
            [SubType.K_DAY],
            subscribe_push=False,
        )
        if ret != RET_OK:
            logger.warning(f"サブスクライブ失敗: {code}")

        ret, data = self.ctx.get_cur_kline(
            code,
            num=num,
            ktype=KLType.K_DAY,
        )

        if ret != RET_OK:
            logger.error(f"直近日足取得失敗: {code} - {data}")
            return pd.DataFrame()

        logger.info(f"直近日足取得完了: {code} - {len(data)}件")
        return data

    def get_daily_klines_with_fallback(
        self,
        code: str,
        num: int = 120,
        start: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        日足を取得する（フォールバック付き）

        優先順位:
        1. get_cur_kline（直近30日）
        2. request_history_kline（start指定付き）

        Args:
            code: 銘柄コード
            num: 取得本数
            start: 開始日（YYYY-MM-DD）。Noneなら6営業月前

        Returns:
            pd.DataFrame: 日足データ
        """
        from datetime import timedelta

        # 1. get_cur_klineで直近日足を取得
        cur_df = self.get_cur_daily_klines(code, num=min(num, 30))

        # 2. request_history_klineで過去分を取得
        if start is None:
            start_date = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
        else:
            start_date = start

        hist_df = self.get_daily_klines(code, num=num, start=start_date)

        # 3. 結合（重複除去）
        if cur_df.empty and hist_df.empty:
            return pd.DataFrame()

        if cur_df.empty:
            return hist_df
        if hist_df.empty:
            return cur_df

        # 両方ある場合は結合
        combined = pd.concat([cur_df, hist_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["time_key"], keep="first")
        combined = combined.sort_values("time_key", ascending=False).reset_index(drop=True)

        logger.info(f"日足取得完了(フォールバック): {code} - {len(combined)}件")
        return combined

    def get_intraday_klines(
        self,
        code: str,
        ktype: KLType = KLType.K_1M,
        num: int = 100,
    ) -> pd.DataFrame:
        """
        分足ローソク足を取得する

        Args:
            code: 銘柄コード
            ktype: 足種別（KLType.K_1M or KLType.K_5M）
            num: 取得本数

        Returns:
            pd.DataFrame: 分足データ
        """
        logger.info(f"分足取得: {code} (ktype={ktype}, num={num})")

        ret, data, _ = self.ctx.request_history_kline(
            code,
            ktype=ktype,
            num=num,
        )

        if ret != RET_OK:
            logger.error(f"分足取得失敗: {code} - {data}")
            return pd.DataFrame()

        return data

    def get_realtime_klines(
        self,
        code: str,
        ktype: KLType = KLType.K_DAY,
        num: int = 10,
    ) -> pd.DataFrame:
        """
        リアルタイムローソク足（最新）を取得する

        Args:
            code: 銘柄コード
            ktype: 足種別
            num: 取得本数

        Returns:
            pd.DataFrame: ローソク足データ
        """
        # 配信登録
        ret, _ = self.ctx.subscribe(
            code,
            [SubType.K_DAY, SubType.K_1M, SubType.K_5M],
            subscribe_push=False,
        )
        if ret != RET_OK:
            logger.warning(f"配信登録失敗: {code}")

        ret, data = self.ctx.get_cur_kline(
            code,
            num=num,
            ktype=ktype,
        )

        if ret != RET_OK:
            logger.error(f"リアルタイム足取得失敗: {code} - {data}")
            return pd.DataFrame()

        return data

    def get_stock_basicinfo(
        self,
        market: Market = Market.JP,
        stock_type: SecurityType = SecurityType.STOCK,
    ) -> pd.DataFrame:
        """
        銘柄情報を取得する

        Args:
            market: 市場
            stock_type: 証券種別

        Returns:
            pd.DataFrame: 銘柄情報
        """
        logger.info(f"銘柄情報取得: market={market}")

        ret, data = self.ctx.get_stock_basicinfo(
            market,
            stock_type,
        )

        if ret != RET_OK:
            logger.error(f"銘柄情報取得失敗: {data}")
            return pd.DataFrame()

        return data

    def parse_snapshot_to_quote(self, row: pd.Series) -> Quote:
        """
        スナップショットデータをQuoteモデルに変換する

        Args:
            row: 1行分のスナップショットデータ

        Returns:
            Quote: 株価データ
        """
        return Quote(
            code=row.get("code", ""),
            timestamp=row.get("update_time", datetime.now().isoformat()),
            price=row.get("last_price"),
            open=row.get("open_price"),
            high=row.get("high_price"),
            low=row.get("low_price"),
            volume=row.get("volume"),
            turnover=row.get("turnover"),
        )

    def parse_kline_to_dailybar(
        self,
        row: pd.Series,
        code: str,
    ) -> DailyBar:
        """
        ローソク足データをDailyBarモデルに変換する

        Args:
            row: 1行分のローソク足データ
            code: 銘柄コード

        Returns:
            DailyBar: 日足データ
        """
        return DailyBar(
            code=code,
            date=row.get("time_key", "")[:10],  # YYYY-MM-DD部分のみ
            open=row.get("open"),
            high=row.get("high"),
            low=row.get("low"),
            close=row.get("close"),
            volume=row.get("volume"),
            turnover=row.get("turnover"),
        )
