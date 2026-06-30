"""
Moomoo Strategy Monitor - Streamlit Web UI

ファイルパス: app.py
何をするか: Streamlitで候補一覧・詳細・売買ログ・パフォーマンスを表示する
なぜ存在するか: CLIではなくブラウザで使いやすくするため
関連ファイル: src/screener.py, src/performance.py, src/trade_log.py

使い方:
    streamlit run app.py
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import sqlite3
import streamlit as st

from src.config import load_config
from src.data_freshness import DataFreshnessGuard
from src.performance import PerformanceEvaluator
from src.trade_log import TradeLog

st.set_page_config(
    page_title="Moomoo Strategy Monitor",
    page_icon="📈",
    layout="wide",
)


@st.cache_resource
def load_config_cached():
    """設定をキャッシュして読み込む"""
    return load_config("config.yaml")


def _read_sql(query: str, params: list | tuple | None = None) -> pd.DataFrame:
    """DBが未作成でも画面を落とさないSQL読み込みヘルパー"""
    config = load_config_cached()
    db_path = Path(config.database_path)

    if not db_path.exists():
        return pd.DataFrame()

    try:
        with sqlite3.connect(db_path) as conn:
            return pd.read_sql_query(query, conn, params=params or [])
    except Exception as e:
        st.warning(f"データ取得に失敗しました: {e}")
        return pd.DataFrame()


def check_data_freshness():
    """データ鮮度をチェック"""
    config = load_config_cached()
    guard = DataFreshnessGuard(config)
    return guard.check_freshness()


def get_signals_df(latest_only: bool = True) -> pd.DataFrame:
    """シグナルデータを取得"""
    where_latest = "WHERE s.date = (SELECT MAX(date) FROM signals)" if latest_only else ""

    query = f"""
        SELECT s.*, i.close, i.daily_return, i.ma5, i.ma25,
               i.volume_ratio, i.return_5d, i.turnover, i.history_days,
               sym.name
        FROM signals s
        LEFT JOIN indicators i ON s.code = i.code AND s.date = i.date
        LEFT JOIN symbols sym ON s.code = sym.code
        {where_latest}
        ORDER BY s.score DESC
    """
    return _read_sql(query)


def get_daily_bars_df(code: str, days: int = 60) -> pd.DataFrame:
    """日足データを取得"""
    return _read_sql(
        """
        SELECT * FROM daily_bars
        WHERE code = ?
        ORDER BY date DESC
        LIMIT ?
        """,
        [code, days],
    )


def get_trades_df() -> pd.DataFrame:
    """売買記録を取得"""
    return _read_sql(
        """
        SELECT t.*, s.name
        FROM trades_manual t
        LEFT JOIN symbols s ON t.code = s.code
        ORDER BY t.executed_at DESC
        """
    )


def get_positions_df() -> pd.DataFrame:
    """保有ポジションを取得"""
    config = load_config_cached()
    evaluator = PerformanceEvaluator(config)
    positions = evaluator.get_positions()

    if not positions:
        return pd.DataFrame()

    return pd.DataFrame([
        {
            "code": p.code,
            "name": p.name,
            "quantity": p.quantity,
            "avg_price": p.avg_price,
            "current_price": p.current_price,
            "unrealized_pnl": p.unrealized_pnl,
            "unrealized_return": p.unrealized_return,
        }
        for p in positions
    ])


def get_backtest_df() -> pd.DataFrame:
    """事後検証データを取得"""
    return _read_sql(
        """
        SELECT sb.*, s.signal_type, s.score, s.reason
        FROM signal_backtests sb
        LEFT JOIN signals s ON sb.signal_id = s.id
        ORDER BY sb.signal_date DESC
        """
    )


def _format_dataframe(df: pd.DataFrame, format_map: dict):
    """存在するカラムだけstyle.formatを適用する"""
    if df.empty:
        return df
    available_format = {k: v for k, v in format_map.items() if k in df.columns}
    return df.style.format(available_format) if available_format else df


def tab_dashboard():
    """ダッシュボードタブ"""
    st.header("ダッシュボード")

    status = check_data_freshness()
    if status.level == "ok":
        st.success(f"データは最新です: {status.latest_date}")
    elif status.level == "warning":
        st.warning(f"データが古いです: {status.message}")
    else:
        st.error(f"データが著しく古いです: {status.message}")

    signals_df = get_signals_df(latest_only=True)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        buy_count = len(signals_df[signals_df["signal_type"] == "BUY_CANDIDATE"]) if not signals_df.empty else 0
        st.metric("買い候補", f"{buy_count}件")
    with col2:
        watch_count = len(signals_df[signals_df["signal_type"] == "WATCH"]) if not signals_df.empty else 0
        st.metric("監視候補", f"{watch_count}件")
    with col3:
        exclude_count = len(signals_df[signals_df["signal_type"] == "EXCLUDE"]) if not signals_df.empty else 0
        st.metric("除外", f"{exclude_count}件")
    with col4:
        st.metric("監視銘柄数", f"{len(signals_df)}件")

    st.subheader("ポートフォリオ")
    config = load_config_cached()
    evaluator = PerformanceEvaluator(config)
    summary = evaluator.get_summary()
    positions_df = get_positions_df()

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("未実現損益", f"{summary.unrealized_pnl:,.0f}円")
    with col2:
        st.metric("実現損益", f"{summary.realized_pnl:,.0f}円")
    with col3:
        st.metric("保有中銘柄", f"{len(positions_df)}件")

    st.caption(f"最終更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


def tab_candidates():
    """候補一覧タブ"""
    st.header("候補一覧")

    latest_only = st.checkbox("最新日だけ表示", value=True)
    signals_df = get_signals_df(latest_only=latest_only)

    if signals_df.empty:
        st.info("データがありません。まず daily_update.py と screen_candidates.py --save を実行してください。")
        return

    col1, col2, col3 = st.columns(3)
    with col1:
        signal_filter = st.multiselect(
            "シグナル種別",
            options=["BUY_CANDIDATE", "WATCH", "EXCLUDE"],
            default=["BUY_CANDIDATE", "WATCH"],
        )
    with col2:
        min_score = st.slider("最小スコア", 0, 100, 50)
    with col3:
        etf_filter = st.radio("銘柄種別", options=["すべて", "個別株のみ", "ETFのみ"], horizontal=True)

    filtered = signals_df[
        signals_df["signal_type"].isin(signal_filter)
        & (signals_df["score"].fillna(0) >= min_score)
    ]

    if etf_filter == "個別株のみ":
        filtered = filtered[~filtered["code"].astype(str).str.contains(r"JP\.13|JP\.25", regex=True)]
    elif etf_filter == "ETFのみ":
        filtered = filtered[filtered["code"].astype(str).str.contains(r"JP\.13|JP\.25", regex=True)]

    columns = [
        "date", "code", "name", "close", "daily_return", "ma5", "ma25",
        "volume_ratio", "return_5d", "turnover", "score", "signal_type", "reason",
    ]
    columns = [c for c in columns if c in filtered.columns]
    st.dataframe(
        _format_dataframe(
            filtered[columns],
            {
                "close": "{:,.0f}",
                "daily_return": "{:.1f}%",
                "return_5d": "{:.1f}%",
                "turnover": "{:,.0f}",
                "score": "{:.0f}",
            },
        ),
        use_container_width=True,
    )

    if not filtered.empty:
        csv = filtered.to_csv(index=False, encoding="utf-8-sig")
        st.download_button(
            label="CSVダウンロード",
            data=csv,
            file_name=f"signals_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
        )


def tab_stock_detail():
    """銘柄詳細タブ"""
    st.header("銘柄詳細")

    signals_df = get_signals_df(latest_only=False)
    if signals_df.empty:
        st.info("データがありません。")
        return

    codes = sorted(signals_df["code"].dropna().unique())
    selected_code = st.selectbox("銘柄を選択", codes)

    if not selected_code:
        return

    stock_info = signals_df[signals_df["code"] == selected_code].iloc[0]

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("銘柄名", str(stock_info.get("name", "")))
    with col2:
        st.metric("現在値", f"{stock_info.get('close', 0):,.0f}")
    with col3:
        st.metric("スコア", f"{stock_info.get('score', 0):.0f}")
    with col4:
        st.metric("シグナル", str(stock_info.get("signal_type", "")))

    st.subheader("日足データ")
    daily_df = get_daily_bars_df(selected_code, 60)

    if not daily_df.empty:
        chart_df = daily_df.sort_values("date")
        st.line_chart(chart_df.set_index("date")["close"])
        st.bar_chart(chart_df.set_index("date")["volume"])
        st.dataframe(
            daily_df[["date", "open", "high", "low", "close", "volume"]].head(20),
            use_container_width=True,
        )
    else:
        st.info("日足データがありません")

    st.subheader("シグナル履歴")
    signal_history = signals_df[signals_df["code"] == selected_code]
    if not signal_history.empty:
        st.dataframe(
            signal_history[["date", "signal_type", "score", "reason"]],
            use_container_width=True,
        )

    st.subheader("手動売買ログ")
    trades_df = get_trades_df()
    stock_trades = trades_df[trades_df["code"] == selected_code] if not trades_df.empty else pd.DataFrame()
    if not stock_trades.empty:
        st.dataframe(stock_trades, use_container_width=True)
    else:
        st.info("売買記録がありません")


def tab_trade_log():
    """手動売買ログタブ"""
    st.header("手動売買ログ")

    st.warning(
        "これは手動売買ログであり、moomooへの発注ではありません。"
        "売買記録を手動で入力してください。"
    )

    with st.form("trade_form"):
        col1, col2 = st.columns(2)

        with col1:
            code = st.text_input("銘柄コード（例: JP.7203）")
            side = st.selectbox("売買方向", ["BUY", "SELL"])
            quantity = st.number_input("数量", min_value=1, value=1)

        with col2:
            price = st.number_input("価格", min_value=0.0, value=0.0)
            executed_at = st.date_input("実行日")
            reason = st.text_input("理由")

        exit_rule = st.text_input("売りルール（任意）")
        memo = st.text_input("メモ（任意）")

        submitted = st.form_submit_button("登録")

        if submitted:
            if code and price > 0:
                config = load_config_cached()
                trade_log = TradeLog(config)
                trade_id = trade_log.record_trade(
                    code=code.strip(),
                    side=side,
                    quantity=int(quantity),
                    price=float(price),
                    reason=reason,
                    exit_rule=exit_rule,
                    memo=memo,
                    executed_at=executed_at.strftime("%Y-%m-%d"),
                )
                st.success(f"登録完了 (ID: {trade_id})")
                st.rerun()
            else:
                st.error("銘柄コードと価格を入力してください")

    st.subheader("売買記録一覧")
    trades_df = get_trades_df()

    if trades_df.empty:
        st.info("売買記録がありません")
        return

    col1, col2 = st.columns(2)
    with col1:
        code_filter = st.text_input("銘柄コードでフィルタ")
    with col2:
        side_filter = st.radio("方向", ["すべて", "BUY", "SELL"], horizontal=True)

    filtered = trades_df
    if code_filter:
        filtered = filtered[filtered["code"].astype(str).str.contains(code_filter, case=False, na=False)]
    if side_filter != "すべて":
        filtered = filtered[filtered["side"] == side_filter]

    st.dataframe(filtered, use_container_width=True)

    if not filtered.empty:
        csv = filtered.to_csv(index=False, encoding="utf-8-sig")
        st.download_button(
            label="CSVダウンロード",
            data=csv,
            file_name=f"trades_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
        )


def tab_performance():
    """パフォーマンスタブ"""
    st.header("パフォーマンス")

    config = load_config_cached()
    evaluator = PerformanceEvaluator(config)
    summary = evaluator.get_summary()

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("投資総額", f"{summary.total_invested:,.0f}円")
    with col2:
        st.metric("総損益", f"{summary.total_pnl:,.0f}円")
    with col3:
        st.metric("勝率", f"{summary.win_rate:.1f}%" if summary.win_rate is not None else "N/A")

    st.subheader("保有中一覧")
    positions_df = get_positions_df()
    if not positions_df.empty:
        st.dataframe(
            _format_dataframe(
                positions_df,
                {
                    "avg_price": "{:,.0f}",
                    "current_price": "{:,.0f}",
                    "unrealized_pnl": "{:,.0f}",
                    "unrealized_return": "{:.1f}%",
                },
            ),
            use_container_width=True,
        )
    else:
        st.info("保有ポジションはありません")

    st.subheader("売買履歴")
    history = evaluator.get_trade_history()
    if history:
        history_df = pd.DataFrame([
            {
                "code": h.code,
                "name": h.name,
                "quantity": h.quantity,
                "entry_price": h.entry_price,
                "exit_price": h.exit_price,
                "pnl": h.pnl,
                "return_pct": h.return_pct,
                "holding_days": h.holding_days,
            }
            for h in history
        ])
        st.dataframe(
            _format_dataframe(
                history_df,
                {
                    "entry_price": "{:,.0f}",
                    "exit_price": "{:,.0f}",
                    "pnl": "{:,.0f}",
                    "return_pct": "{:.1f}%",
                },
            ),
            use_container_width=True,
        )
    else:
        st.info("売買履歴がありません")


def tab_backtest():
    """シグナル事後検証タブ"""
    st.header("シグナル事後検証")

    backtest_df = get_backtest_df()

    if backtest_df.empty:
        st.info("事後検証データがありません。performance_report.py --backtest を実行してください。")
        return

    col1, col2 = st.columns(2)
    with col1:
        horizon_filter = st.multiselect(
            "検証期間",
            options=sorted(backtest_df["horizon_days"].dropna().unique()),
            default=sorted(backtest_df["horizon_days"].dropna().unique()),
        )
    with col2:
        benchmark_filter = st.multiselect(
            "ベンチマーク",
            options=sorted(backtest_df["benchmark_code"].dropna().unique()),
            default=sorted(backtest_df["benchmark_code"].dropna().unique()),
        )

    filtered = backtest_df[
        backtest_df["horizon_days"].isin(horizon_filter)
        & backtest_df["benchmark_code"].isin(benchmark_filter)
    ]

    columns = [
        "signal_date", "code", "horizon_days", "signal_price",
        "future_price", "stock_return", "benchmark_return",
        "excess_return", "max_drawdown", "max_runup",
    ]
    columns = [c for c in columns if c in filtered.columns]

    st.dataframe(
        _format_dataframe(
            filtered[columns],
            {
                "signal_price": "{:,.0f}",
                "future_price": "{:,.0f}",
                "stock_return": "{:.1f}%",
                "benchmark_return": "{:.1f}%",
                "excess_return": "{:.1f}%",
                "max_drawdown": "{:.1f}%",
                "max_runup": "{:.1f}%",
            },
        ),
        use_container_width=True,
    )

    if not filtered.empty:
        csv = filtered.to_csv(index=False, encoding="utf-8-sig")
        st.download_button(
            label="CSVダウンロード",
            data=csv,
            file_name=f"backtest_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
        )


def tab_order_info():
    """注文についてタブ"""
    st.header("注文について")

    st.info("このアプリは売買候補の抽出と検証を行う補助ツールです。")

    st.markdown("""
    ### 取引方法

    moomoo JPの日本株注文は、**APIではなくmoomooアプリで手動実行**してください。

    実行した売買は「手動売買ログ」に記録してください。

    ### API注文について

    - 本アプリは**REAL注文APIを呼び出しません**
    - moomoo JP / FUTUJP では、OpenAPI経由の日本株SIMULATE注文が利用できないため、JP向けペーパートレード機能は無効です
    - アプリ内デモ取引とAPI SIMULATEは別物として扱います

    ### 検証方針

    ```text
    データ取得：moomoo API
    候補抽出：本アプリ
    検証：signals / signal_backtests
    実取引：moomooアプリで手動
    売買記録：trades_manual に手動入力
    API注文：未対応・使用禁止
    ```
    """)


def tab_virtual_trade():
    """仮想トレードタブ"""
    import pandas as pd
    from src.virtual_trade import VirtualTradeManager

    st.header("仮想トレード")
    st.warning("これはアプリ内の仮想注文です。moomooには注文を送信しません。")

    config = load_config_cached()
    manager = VirtualTradeManager(config)

    tab1, tab2, tab3, tab4 = st.tabs(["ポジション", "注文一覧", "約定一覧", "パフォーマンス"])

    with tab1:
        st.subheader("仮想ポジション")
        positions = manager.get_positions()
        if positions:
            records = []
            for p in positions:
                records.append({
                    "code": p.code, "quantity": p.quantity, "avg_cost": p.avg_cost,
                    "market_price": p.market_price, "unrealized_pl": p.unrealized_pl,
                })
            df = pd.DataFrame(records)
            st.dataframe(df.style.format({"avg_cost": "{:,.0f}", "market_price": "{:,.0f}", "unrealized_pl": "{:,.0f}"}), use_container_width=True)
        else:
            st.info("保有ポジションはありません")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("売却候補生成"):
                orders = manager.generate_exits("default")
                st.success(f"{len(orders)}件の売却注文を生成しました")
        with col2:
            if st.button("現在値更新"):
                updated = manager.update_market_prices("default")
                st.success(f"{updated}件のポジションを更新しました")
    with tab2:
        st.subheader("未約定注文")
        orders = manager.get_pending_orders()
        if orders:
            records = [{"id": o.id, "code": o.code, "side": o.side,"quantity": o.quantity, "order_type": o.order_type, "limit_price": o.limit_price, "status": o.status, "submitted_at": o.submitted_at} for o in orders]
            st.dataframe(pd.DataFrame(records), use_container_width=True)
        else:
            st.info("未約定注文はありません")
    with tab3:
        st.subheader("約定一覧")
        fills = manager.get_fills()
        if fills:
            records = [{"code": f.code, "side": f.side, "quantity": f.quantity, "price": f.price, "filled_at": f.filled_at, "fill_mode": f.fill_mode} for f in fills]
            st.dataframe(pd.DataFrame(records), use_container_width=True)
        else:
            st.info("約定はありません")
    with tab4:
        st.subheader("戦略パフォーマンス")
        perf = manager.get_strategy_performance()
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("現金", f"{perf['cash']:,.0f}円")
        with col2:
            st.metric("総資産", f"{perf['total_equity']:,.0f}円")
        with col3:
            st.metric("リターン", f"{perf['return_pct']:.2f}%")
        col1, col2 = st.columns(2)
        with col1:
            st.metric("実現損益", f"{perf['realized_pl']:,.0f}円")
        with col2:
            st.metric("未実現損益", f"{perf['unrealized_pl']:,.0f}円")
        curve = manager.get_equity_curve()
        if curve:
            curve_df = pd.DataFrame(curve)
            if not curve_df.empty:
                st.subheader("エクイティカーブ")
                st.line_chart(curve_df.set_index("date")["total_equity"])


def tab_daily_ops():
    """日次運用タブ"""
    import sqlite3
    from datetime import datetime
    from src.data_freshness import DataFreshnessGuard

    st.header("日次運用")

    # データ鮮度
    config = load_config_cached()
    guard = DataFreshnessGuard(config)
    status = guard.check_freshness()
    if status.level == "ok":
        st.success(f"  データ: {status.latest_date}")
    elif status.level == "warning":
        st.warning(f"⚠ データ: {status.latest_date}（{status.days_stale}日分古い）")
    else:
        st.error(f"  データ: {status.latest_date}（古すぎます）")

    # 本日の候補数
    db_path = config.database_path
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE date = ? AND signal_type = 'BUY_CANDIDATE'",
            (datetime.now().strftime("%Y-%m-%d"),),
        )
        buy_count = cursor.fetchone()[0]
        cursor = conn.execute(
            "SELECT COUNT(*) FROM virtual_orders WHERE submitted_at LIKE ?",
            (f"{datetime.now().strftime('%Y-%m-%d')}%",),
        )
        orders_today = cursor.fetchone()[0]
        cursor = conn.execute(
            "SELECT COUNT(*) FROM virtual_fills WHERE filled_at LIKE ?",
            (f"{datetime.now().strftime('%Y-%m-%d')}%",),
        )
        fills_today = cursor.fetchone()[0]

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("本日の候補", f"{buy_count}件")
    with col2:
        st.metric("本日の仮想注文", f"{orders_today}件")
    with col3:
        st.metric("本日の約定", f"{fills_today}件")

    # アクション
    st.subheader("アクション")
    if st.button("日次サイクル Dry-Run"):
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "run_daily_cycle.py", "--dry-run"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            st.success("Dry-run 成功")
        else:
            st.error(f"Dry-run 失敗: {result.stderr[:200]}")

    if st.button("仮想トレードレポート出力"):
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "virtual_order.py", "--report"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            st.success("レポート出力完了")
        else:
            st.error(f"レポート出力失敗: {result.stderr[:200]}")


def main() -> None:
    """メイン関数"""
    st.title("Moomoo Strategy Monitor")
    st.caption("日本株モメンタム検証ツール（手動売買ログ・検証用）")

    try:
        status = check_data_freshness()
        if status.level != "ok":
            st.error(
                f"データが古いです: {status.message}\n\n"
                f"最新日付: {status.latest_date}\n\n"
                "`python daily_update.py --force` を実行してください。"
            )
    except Exception as e:
        st.warning(f"データ鮮度チェックを実行できませんでした: {e}")

    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs([
        "ダッシュボード",
        "候補一覧",
        "銘柄詳細",
        "手動売買ログ",
        "パフォーマンス",
        "事後検証",
        "注文について",
        "仮想トレード",
        "日次運用",
    ])

    with tab1:
        tab_dashboard()
    with tab2:
        tab_candidates()
    with tab3:
        tab_stock_detail()
    with tab4:
        tab_trade_log()
    with tab5:
        tab_performance()
    with tab6:
        tab_backtest()
    with tab7:
        tab_order_info()
    with tab8:
        tab_virtual_trade()
    with tab9:
        tab_daily_ops()


if __name__ == "__main__":
    main()
