"""
相場データ取得モジュール

ファイルパス: src/quote_service.py
何をするか: moomoo OpenDから日本株の相場データを取得する
なぜ存在するか: 行情データの取得ロジックを一元管理するため
関連ファイル: connection.py, models.py, data_store.py

MVPでは行情カード不要で確認できたAPIを中心に使用する。
"""

import logging
import time
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
BATCH_SLEEP_SECONDS = 1.0


class QuoteService:
    """相場データ取得サービス"""

    def __init__(self, config: Config, quote_context: OpenQuoteContext):
        self.config = config
        self.ctx = quote_context


    def _request_history_kline_pages(
        self,
        code: str,
        num: int,
        start: Optional[str],
        end: Optional[str],
        log_label: str,
    ) -> pd.DataFrame:
        """Fetch all requested history pages using Futu continuation keys."""
        if num <= 0:
            return pd.DataFrame()

        pages: list[pd.DataFrame] = []
        remaining = num
        page_req_key = None
        seen_page_keys: set[str] = set()
        page_number = 1

        while remaining > 0:
            batch_size = min(remaining, MAX_KLINE_PER_REQUEST)
            ret, data, next_page_req_key = self.ctx.request_history_kline(
                code,
                ktype=KLType.K_DAY,
                max_count=batch_size,
                start=start,
                end=end,
                page_req_key=page_req_key,
            )

            if ret != RET_OK:
                logger.error(
                    "日足取得失敗%s: %s - page=%s - %s",
                    log_label,
                    code,
                    page_number,
                    data,
                )
                return pd.DataFrame()

            if not isinstance(data, pd.DataFrame):
                logger.error(
                    "日足取得失敗%s: %s - page=%s - DataFrameではありません",
                    log_label,
                    code,
                    page_number,
                )
                return pd.DataFrame()

            if data.empty:
                if next_page_req_key is not None:
                    logger.error(
                        "日足取得失敗%s: %s - page=%s - "
                        "空ページに継続キーが返されました",
                        log_label,
                        code,
                        page_number,
                    )
                    return pd.DataFrame()
                break

            pages.append(data)
            remaining -= len(data)

            if remaining <= 0 or next_page_req_key is None:
                break

            key_marker = repr(next_page_req_key)
            if key_marker in seen_page_keys:
                logger.error(
                    "日足取得失敗%s: %s - page=%s - 継続キーが循環しました",
                    log_label,
                    code,
                    page_number,
                )
                return pd.DataFrame()

            seen_page_keys.add(key_marker)
            page_req_key = next_page_req_key
            page_number += 1

        if not pages:
            return pd.DataFrame()

        combined = pd.concat(pages, ignore_index=True)
        if "time_key" in combined.columns:
            combined = combined.drop_duplicates(subset=["time_key"], keep="first")
        else:
            combined = combined.drop_duplicates(keep="first")

        return combined.iloc[:num].reset_index(drop=True)

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
        """日足ローソク足をFutuの継続キーを辿って取得する。"""
        logger.info("日足取得: %s (num=%s)", code, num)
        data = self._request_history_kline_pages(
            code,
            num,
            start,
            end,
            "",
        )
        logger.info("日足取得完了: %s - %s件", code, len(data))
        return data

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
                if "time_key" in data.columns and not data["time_key"].empty:
                    tk = pd.to_datetime(data["time_key"])
                    data = data[tk.dt.strftime("%Y-%m-%d") != today]
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

    def unsubscribe_symbols(
        self, codes: list[str], subtypes: Optional[list] = None
    ) -> None:
        """購読を解除して購読枠を解放する。"""
        if subtypes is None:
            subtypes = [SubType.K_DAY]
        for code in codes:
            try:
                ret, data = self.ctx.unsubscribe(code, subtypes)
                if ret != RET_OK:
                    logger.debug("unsubscribe失敗: %s - %s", code, data)
            except Exception as e:
                logger.debug("unsubscribe例外: %s - %s", code, e)

    def get_daily_klines_history_only(
        self,
        code: str,
        num: int = 120,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        """購読枠を消費せず、Futuの継続キーを辿って日足を取得する。"""
        logger.info(
            "日足取得(history): %s (num=%s, start=%s, end=%s)",
            code,
            num,
            start,
            end,
        )
        data = self._request_history_kline_pages(
            code,
            num,
            start,
            end,
            "(history)",
        )
        logger.info("日足取得完了(history): %s - %s件", code, len(data))
        return data

    def get_daily_klines_latest_only(self, code: str, num: int = 30) -> pd.DataFrame:
        """get_cur_klineで直近日足を取得する（subscribe→取得→unsubscribe）。

        取得後に必ずunsubscribeすることで購読枠を消費しっぱなしにしない。
        日次更新・当日データ取得向け。大量銘柄には不向き。
        """
        logger.info("日足取得(latest): %s (num=%s)", code, num)

        ret, data = self.ctx.subscribe(code, [SubType.K_DAY], subscribe_push=False)
        if ret != RET_OK:
            logger.warning("サブスクライブ失敗: %s - %s", code, data)

        try:
            ret, data = self.ctx.get_cur_kline(code, num=num, ktype=KLType.K_DAY)
            if ret != RET_OK:
                logger.error("直近日足取得失敗(latest): %s - %s", code, data)
                return pd.DataFrame()
        finally:
            self.unsubscribe_symbols([code], [SubType.K_DAY])

        if not data.empty:
            now_jst = datetime.now().astimezone()
            hour = now_jst.hour
            is_trading_hours = (9 <= hour < 15) or (hour == 15 and now_jst.minute < 30)
            if is_trading_hours:
                today = now_jst.strftime("%Y-%m-%d")
                before = len(data)
                if "time_key" in data.columns and not data["time_key"].empty:
                    tk = pd.to_datetime(data["time_key"])
                    data = data[tk.dt.strftime("%Y-%m-%d") != today]
                if len(data) < before:
                    logger.info("取引時間中のため当日足を除外: %s (%d→%d件)", code, before, len(data))

        logger.info("日足取得完了(latest): %s - %s件", code, len(data))
        return data

    def batch_fetch_daily_klines(
        self,
        codes: list[str],
        mode: str = "history",
        num: int = 120,
        start: Optional[str] = None,
        end: Optional[str] = None,
        batch_size: int = 80,
        retry_count: int = 2,
    ) -> dict[str, pd.DataFrame]:
        """複数銘柄の日足をバッチ処理で安定取得する。

        Args:
            codes: 取得対象銘柄コード一覧
            mode: "history"(request_history_kline) / "latest"(get_cur_kline) / "auto"
            num: 取得日数
            start: 取得開始日
            end: 取得終了日（None=今日まで）
            batch_size: 1バッチあたりの銘柄数
            retry_count: 失敗時のリトライ回数

        Returns:
            成功した銘柄の {code: DataFrame} 辞書
        """
        if not codes:
            return {}

        if mode == "auto":
            mode = "history" if len(codes) > 100 else "latest"

        total = len(codes)
        n_batches = (total + batch_size - 1) // batch_size
        logger.info("バッチ日足取得: %s銘柄, mode=%s, batch_size=%s, %sバッチ", total, mode, batch_size, n_batches)

        result: dict[str, pd.DataFrame] = {}
        failed: list[str] = []

        for batch_idx in range(0, total, batch_size):
            batch = codes[batch_idx : batch_idx + batch_size]
            batch_num = batch_idx // batch_size + 1
            logger.info("  バッチ %d/%d: %s銘柄 処理開始", batch_num, n_batches, len(batch))

            for idx_in_batch, code in enumerate(batch):
                # Rate limit: 60 req/30sec → 0.5s最小間隔、リトライ時も同じ
                if idx_in_batch > 0 or batch_num > 1:
                    time.sleep(BATCH_SLEEP_SECONDS)

                success = False
                for attempt in range(1, retry_count + 2):
                    if attempt > 1:
                        time.sleep(BATCH_SLEEP_SECONDS * 2)
                    try:
                        if mode == "latest":
                            df = self.get_daily_klines_latest_only(code, num=min(num, 30))
                        else:
                            df = self.get_daily_klines_history_only(code, num=num, start=start, end=end)

                        if not df.empty:
                            result[code] = df
                            success = True
                            break
                        else:
                            logger.warning("    [%s] データ空(attempt %d/%d)", code, attempt, retry_count + 1)
                    except Exception as e:
                        logger.error("    [%s] 例外: %s (attempt %d/%d)", code, e, attempt, retry_count + 1)

                if not success:
                    failed.append(code)

            if batch_idx + batch_size < total:
                logger.info("  バッチ間待機: %s秒", BATCH_SLEEP_SECONDS)
                time.sleep(BATCH_SLEEP_SECONDS)

        logger.info("バッチ日足取得完了: 成功=%d, 失敗=%d", len(result), len(failed))
        if failed:
            logger.warning("  失敗銘柄一覧: %s", failed)

        return result

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
            date=str(row.get("time_key", ""))[:10],
            open=row.get("open"),
            high=row.get("high"),
            low=row.get("low"),
            close=row.get("close"),
            volume=row.get("volume"),
            turnover=row.get("turnover"),
            source=str(row.get("source") or "moomoo"),
            turnover_source=str(row.get("turnover_source") or "actual"),
        )
