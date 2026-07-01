"""
相場データ取得モジュール

ファイルパス: src/quote_service.py
何をするか: moomoo OpenDから日本株の相場データを取得する
なぜ存在するか: 行情データの取得ロジックを一元管理するため
関連ファイル: connection.py, models.py, data_store.py

MVPでは行情カード不要で確認できたAPIを中心に使用する。
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

MAX_KLINE_PER_REQUEST = 1000


class QuoteService:
    """相場データ取得サービス"""

    def __init__(self, config: Config, quote_context: OpenQuoteContext):
        self.config = config
        self.ctx = quote_context

    def get_stock_snapshot(self, codes: list[str]) -> pd.DataFrame:
        """複数銘柄のマーケットスナップショットを取得する"""
        if not codes:
            return pd.DataFrame()

        logger.info("スナップショット取得: %s銘柄", len(codes))
        ret, data = self.ctx.get_market_snapshot(codes)

        if ret != RET_OK:
            logger.error("スナップショット取得失敗: %s", data)
            return pd.DataFrame()

        return data

    def get_stock_quote(self, codes: list[str]) -> pd.DataFrame:
        """複数銘柄のリアルタイム株価を取得する"""
        if not codes:
            return pd.DataFrame()

        for code in codes:
            ret, data = self.ctx.subscribe(
                code,
                [SubType.QUOTE],
                subscribe_push=False,
            )
            if ret != RET_OK:
                logger.warning("配信登録失敗: %s - %s", code, data)

        ret, data = self.ctx.get_stock_quote(codes)

        if ret != RET_OK:
            logger.error("株価取得失敗: %s", data)
            return pd.DataFrame()

        return data

    def get_daily_klines(
        self,
        code: str,
        num: int = 120,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        """日足ローソク足を取得する"""
        logger.info("日足取得: %s (num=%s)", code, num)

        if num <= MAX_KLINE_PER_REQUEST:
            ret, data, _ = self.ctx.request_history_kline(
                code,
                ktype=KLType.K_DAY,
                max_count=num,
                start=start,
                end=end,
            )

            if ret != RET_OK:
                logger.error("日足取得失敗: %s - %s", code, data)
                return pd.DataFrame()

            return data

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
                logger.error("日足取得失敗: %s - %s", code, data)
                break

            if data.empty:
                break

            all_data = pd.concat([all_data, data], ignore_index=True)
            remaining -= len(data)

            if page_req_key is None or len(data) < batch_size:
                break

            oldest_date = data["time_key"].min()[:10]
            current_end = oldest_date

        logger.info("日足取得完了: %s - %s件", code, len(all_data))
        return all_data

    def get_cur_daily_klines(self, code: str, num: int = 30) -> pd.DataFrame:
        """get_cur_klineで直近の日足を取得する（取引時間中の当日不完全足は除外）"""
        logger.info("直近日足取得(get_cur_kline): %s (num=%s)", code, num)

        ret, data = self.ctx.subscribe(
            code,
            [SubType.K_DAY],
            subscribe_push=False,
        )
        if ret != RET_OK:
            logger.warning("サブスクライブ失敗: %s - %s", code, data)

        ret, data = self.ctx.get_cur_kline(
            code,
            num=num,
            ktype=KLType.K_DAY,
        )

        if ret != RET_OK:
            logger.error("直近日足取得失敗: %s - %s", code, data)
            return pd.DataFrame()

        # 取引時間中（市場が開いている時間帯）の場合は、当日の不完全足を除外
        if not data.empty:
            from datetime import datetime, timezone
            now_jst = datetime.now(timezone.utc).astimezone()
            hour = now_jst.hour
            # 日本時間 9:00〜15:29 は取引時間中
            is_trading_hours = (9 <= hour < 15) or (hour == 15 and now_jst.minute < 30)
            if is_trading_hours:
                today = now_jst.strftime("%Y-%m-%d")
                before = len(data)
                data = data[data["time_key"].dt.strftime("%Y-%m-%d") != today]
                if len(data) < before:
                    logger.info("取引時間中のため当日足を除外: %s (%d→%d件)", code, before, len(data))

        logger.info("直近日足取得完了: %s - %s件", code, len(data))
        return data

    def get_daily_klines_with_fallback(
        self,
        code: str,
        num: int = 120,
        start: Optional[str] = None,
    ) -> pd.DataFrame:
        """日足を取得する（get_cur_kline + request_history_kline のフォールバック付き）"""
        cur_df = self.get_cur_daily_klines(code, num=min(num, 30))

        start_date = start or (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
        hist_df = self.get_daily_klines(code, num=num, start=start_date)

        if cur_df.empty and hist_df.empty:
            return pd.DataFrame()
        if cur_df.empty:
            return hist_df
        if hist_df.empty:
            return cur_df

        combined = pd.concat([cur_df, hist_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["time_key"], keep="first")
        combined = combined.sort_values("time_key", ascending=False).reset_index(drop=True)

        logger.info("日足取得完了(フォールバック): %s - %s件", code, len(combined))
        return combined

    def get_intraday_klines(
        self,
        code: str,
        ktype: KLType = KLType.K_1M,
        num: int = 100,
    ) -> pd.DataFrame:
        """分足ローソク足を取得する"""
        logger.info("分足取得: %s (ktype=%s, num=%s)", code, ktype, num)

        ret, data, _ = self.ctx.request_history_kline(
            code,
            ktype=ktype,
            max_count=num,
        )

        if ret != RET_OK:
            logger.error("分足取得失敗: %s - %s", code, data)
            return pd.DataFrame()

        return data

    def get_realtime_klines(
        self,
        code: str,
        ktype: KLType = KLType.K_DAY,
        num: int = 10,
    ) -> pd.DataFrame:
        """リアルタイムローソク足（最新）を取得する"""
        ret, data = self.ctx.subscribe(
            code,
            [SubType.K_DAY, SubType.K_1M, SubType.K_5M],
            subscribe_push=False,
        )
        if ret != RET_OK:
            logger.warning("配信登録失敗: %s - %s", code, data)

        ret, data = self.ctx.get_cur_kline(
            code,
            num=num,
            ktype=ktype,
        )

        if ret != RET_OK:
            logger.error("リアルタイム足取得失敗: %s - %s", code, data)
            return pd.DataFrame()

        return data

    def get_stock_basicinfo(
        self,
        market: Market = Market.JP,
        stock_type: SecurityType = SecurityType.STOCK,
    ) -> pd.DataFrame:
        """銘柄情報を取得する"""
        logger.info("銘柄情報取得: market=%s", market)

        ret, data = self.ctx.get_stock_basicinfo(
            market,
            stock_type,
        )

        if ret != RET_OK:
            logger.error("銘柄情報取得失敗: %s", data)
            return pd.DataFrame()

        return data

    def parse_snapshot_to_quote(self, row: pd.Series) -> Quote:
        """スナップショットデータをQuoteモデルに変換する"""
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

    def parse_kline_to_dailybar(self, row: pd.Series, code: str) -> DailyBar:
        """ローソク足データをDailyBarモデルに変換する"""
        return DailyBar(
            code=code,
            date=row.get("time_key", "")[:10],
            open=row.get("open"),
            high=row.get("high"),
            low=row.get("low"),
            close=row.get("close"),
            volume=row.get("volume"),
            turnover=row.get("turnover"),
        )
