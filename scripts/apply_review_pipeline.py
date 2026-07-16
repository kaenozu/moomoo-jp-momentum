"""One-time migration for data pipeline and backtest fixes."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_function(path: str, name: str, source: str) -> None:
    text = read(path)
    lines = text.splitlines(keepends=True)
    pattern = re.compile(rf"^(\s*)def {re.escape(name)}\s*\(")
    start = indent = None
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if match:
            start = index
            indent = len(match.group(1))
            break
    if start is None or indent is None:
        raise RuntimeError(f"{path}: {name} not found")
    end = len(lines)
    next_def = re.compile(r"^" + (" " * indent) + r"(?:def|class)\s+")
    for index in range(start + 1, len(lines)):
        if lines[index].strip() and next_def.match(lines[index]):
            end = index
            break
    write(path, "".join(lines[:start]) + source.rstrip() + "\n\n" + "".join(lines[end:]))


replace_function(
    "src/quote_service.py",
    "get_daily_klines",
    '''    def get_daily_klines(
        self,
        code: str,
        num: int = 120,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        """page_req_keyを引き継いで履歴日足を取得する。"""
        logger.info("日足取得: %s (num=%s)", code, num)
        if num <= 0:
            return pd.DataFrame()
        frames: list[pd.DataFrame] = []
        remaining = num
        page_req_key = None
        while remaining > 0:
            batch_size = min(remaining, MAX_KLINE_PER_REQUEST)
            ret, data, next_page_key = self.ctx.request_history_kline(
                code,
                ktype=KLType.K_DAY,
                max_count=batch_size,
                start=start,
                end=end,
                page_req_key=page_req_key,
            )
            if ret != RET_OK:
                logger.error("日足取得失敗: %s - %s", code, data)
                break
            if not isinstance(data, pd.DataFrame) or data.empty:
                break
            frames.append(data)
            remaining -= len(data)
            if next_page_key is None or len(data) < batch_size:
                break
            page_req_key = next_page_key
        if not frames:
            return pd.DataFrame()
        result = pd.concat(frames, ignore_index=True)
        if "time_key" in result.columns:
            result = (
                result.drop_duplicates(subset=["time_key"], keep="last")
                .sort_values("time_key")
                .reset_index(drop=True)
            )
        if len(result) > num:
            result = result.tail(num).reset_index(drop=True)
        return result
''',
)

cycle = read("run_daily_cycle.py")
if "import json\n" not in cycle:
    cycle = cycle.replace("import argparse\n", "import argparse\nimport json\n", 1)
    write("run_daily_cycle.py", cycle)

replace_function(
    "run_daily_cycle.py",
    "run_cycle",
    '''def run_cycle(
    target_date: str,
    dry_run: bool = False,
    config_path: str = "config.yaml",
    allow_stale: bool = False,
) -> dict:
    config = load_config(config_path)
    configured_markets, latest_bar_count = get_daily_cycle_settings(config)
    normalized_markets = {str(item).upper() for item in configured_markets}

    if dry_run:
        watchlist_path = Path(config.watchlist_file)
        try:
            raw_symbols = json.loads(watchlist_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"watchlistを読み込めません: {exc}") from exc
        if not isinstance(raw_symbols, list):
            raise RuntimeError("watchlistのトップレベルがlistではありません")
        selected = [
            item for item in raw_symbols
            if isinstance(item, dict)
            and item.get("enabled", True)
            and str(item.get("market", "JP")).upper() in normalized_markets
        ]
        benchmark_count = sum(
            1 for item in selected if item.get("role") == "benchmark"
        )
        if benchmark_count == 0:
            raise RuntimeError("日次対象にbenchmark が0件です")
        return {
            "connection_attempted": False,
            "database_write_attempted": False,
            "virtual_trade_enabled": bool(config.get("virtual_trade.enabled", True)),
            "symbols": len(selected),
            "benchmarks": benchmark_count,
        }

    results: dict[str, int | bool] = {}
    connection = OpenDConnection(config)
    try:
        status = connection.connect()
        if not status.connected:
            raise RuntimeError(f"OpenD接続失敗: {status.message}")
        results["connection"] = True
        store = DataStore(config)
        store.sync_symbols_from_json(config.watchlist_file)
        symbols = get_daily_cycle_symbols(store, configured_markets)
        codes = [symbol.code for symbol in symbols]
        names = {symbol.code: symbol.name for symbol in symbols}
        benchmarks = {symbol.code for symbol in symbols if symbol.role == "benchmark"}
        results["symbols"] = len(codes)

        quote_service = QuoteService(config, status.quote_context)
        updated = 0
        for index, code in enumerate(codes, 1):
            logger.info("[%d/%d] %s の日足を取得中...", index, len(codes), code)
            frame = quote_service.get_daily_klines_latest_only(
                code, num=latest_bar_count
            )
            if not frame.empty:
                store.save_dataframe_to_daily_bars(frame, code)
                updated += 1
        results["daily_bars"] = updated

        history_limit = max(120, latest_bar_count)
        data_dict = {}
        for code in codes:
            history = store.get_daily_bars(
                code, end_date=target_date, limit=history_limit
            )
            if not history.empty:
                history = history.sort_values("date").rename(
                    columns={"date": "time_key"}
                )
                data_dict[code] = history
        if data_dict:
            indicators = calculate_indicators_batch(data_dict, names)
            indicators_df = indicators_to_dataframe(indicators)
            benchmark_code = config.get(
                "signals.relative_strength.benchmark_code", "JP.1306"
            )
            indicators_df = add_relative_strength(indicators_df, benchmark_code)
            results["indicators"] = save_indicators_to_db(store, indicators_df)
            results["benchmark_prices"] = save_benchmark_prices_from_indicators(
                store, indicators_df, benchmarks
            )
        else:
            results["indicators"] = 0
            results["benchmark_prices"] = 0

        freshness = DataFreshnessGuard(config).check_freshness()
        if freshness.level == "error":
            raise SystemError(f"データ鮮度エラー: {freshness.message}")
        if freshness.level == "warning" and not allow_stale:
            raise SystemError(
                f"データが古いため処理を停止します: {freshness.message}"
            )
        if freshness.level == "warning":
            logger.warning("古いデータで明示続行します: %s", freshness.message)

        screener = Screener(config)
        candidates = screener.screen_candidates(date=target_date)
        results["signals"] = screener.save_signals_to_db(candidates)

        manager = VirtualTradeManager(config)
        threshold = config.get("virtual_trade.score_threshold_for_order", 70)
        available_cash = manager.get_available_cash("default", target_date)
        created = 0
        for candidate in candidates:
            if candidate.signal_type != "BUY_CANDIDATE":
                continue
            if candidate.role != "trade_candidate" or not candidate.tradable:
                continue
            if candidate.score < threshold:
                continue
            if candidate.close and candidate.close > available_cash:
                continue
            order = manager.place_order(
                strategy_name="default",
                code=candidate.code,
                side="BUY",
                quantity=1,
                order_type="MARKET_SIM",
                submitted_at=candidate.date,
            )
            if order:
                created += 1
                available_cash = manager.get_available_cash("default", target_date)
        results["virtual_orders"] = created
        results["fills"] = len(manager.process_fills("default", target_date))
        results["exits"] = len(manager.generate_exits("default", target_date))
        results["price_updates"] = manager.update_market_prices(
            "default", target_date
        )
        manager.save_equity_curve("default", target_date)
        results["alerts"] = len(AlertManager(config).run_all_checks())
        return results
    finally:
        connection.disconnect()
''',
)

cycle = read("run_daily_cycle.py")
if 'parser.add_argument("--allow-stale"' not in cycle:
    cycle = cycle.replace(
        '    parser.add_argument("--config", default="config.yaml", help="設定ファイルパス")\n',
        '    parser.add_argument("--config", default="config.yaml", help="設定ファイルパス")\n'
        '    parser.add_argument("--allow-stale", action="store_true", help="古いデータでの続行を明示許可")\n',
        1,
    )
cycle = cycle.replace(
    "        results = run_cycle(args.date, dry_run=args.dry_run, config_path=args.config)\n",
    "        results = run_cycle(args.date, dry_run=args.dry_run, config_path=args.config, allow_stale=args.allow_stale)\n",
)
write("run_daily_cycle.py", cycle)

# Backtest fixes are targeted to keep the existing engine structure.
backtest = read("src/backtest_runner.py")
backtest = backtest.replace(
    '        return code.startswith("JP.13") or code.startswith("JP.25")',
    '        configured = self.config.get("strategies.etf_rotation.codes", ["JP.2559", "JP.1306", "JP.1320", "JP.2558", "JP.2563"])\n'
    '        return code in {str(item) for item in configured}',
)
backtest = backtest.replace(
    '                "SELECT code, name, role, tradable FROM symbols WHERE enabled=1"',
    '                "SELECT code, name, market, role, tradable FROM symbols WHERE enabled=1"',
)
old_candidates = '''        trade_candidates = [
            r for r in rows
            if r["role"] == "trade_candidate" and r["tradable"]
        ]
'''
new_candidates = '''        backtest_market = str(self.config.get("backtest.market", "JP")).upper()
        rows = [
            row for row in rows
            if str(row["market"] or "JP").upper() == backtest_market
        ]
        trade_candidates = [
            row for row in rows
            if row["role"] == "trade_candidate" and row["tradable"]
        ]
'''
backtest = backtest.replace(old_candidates, new_candidates)
backtest = backtest.replace(
    '''                    daily_bm_ret = (bm_today - idle_bench_prev) / idle_bench_prev
                    self.cash = self.cash * (1 + daily_bm_ret)
''',
    '''                    daily_bm_ret = (bm_today - idle_bench_prev) / idle_bench_prev
                    investable_idle_cash = max(0.0, self.cash - self.reserved_cash)
                    self.cash += investable_idle_cash * daily_bm_ret
''',
)
fill_anchor = '''                if order.side == "BUY":
                    cost = order.fill_price * order.quantity + self.commission
                    with self._conn() as conn:
'''
if "BUY fill skipped for insufficient cash" not in backtest:
    backtest = backtest.replace(
        fill_anchor,
        '''                if order.side == "BUY":
                    cost = order.fill_price * order.quantity + self.commission
                    if cost > self.cash + 1e-9:
                        logger.warning(
                            "BUY fill skipped for insufficient cash: %s cost=%.2f cash=%.2f",
                            order.code, cost, self.cash,
                        )
                        self.reserved_cash = max(0.0, self.reserved_cash - cost)
                        continue
                    with self._conn() as conn:
''',
    )
ranking_anchor = "            for sym, ind in valid_pairs:\n"
if "valid_pairs.sort" not in backtest:
    backtest = backtest.replace(
        ranking_anchor,
        '''            valid_pairs.sort(
                key=lambda pair: (
                    pair[1].return_20d_vs_benchmark
                    if pair[1].return_20d_vs_benchmark is not None
                    else float("-inf"),
                    pair[1].return_5d_vs_benchmark
                    if pair[1].return_5d_vs_benchmark is not None
                    else float("-inf"),
                    pair[1].return_5d
                    if pair[1].return_5d is not None
                    else float("-inf"),
                ),
                reverse=True,
            )

            for sym, ind in valid_pairs:
''',
        1,
    )
write("src/backtest_runner.py", backtest)

for path in ("scripts/yf_supplement.py", "scripts/yf_validation.py"):
    if (ROOT / path).exists():
        write(path, read(path).replace("auto_adjust=False", "auto_adjust=True"))

print("pipeline review fixes applied")
