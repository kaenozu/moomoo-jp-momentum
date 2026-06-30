"""
Moomoo Strategy Monitor - Streamlit Web UI
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

st.set_page_config(page_title="Moomoo Strategy Monitor", page_icon="📈", layout="wide")


@st.cache_resource
def load_config_cached():
    return load_config("config.yaml")


def _read_sql(query: str, params: list | tuple | None = None) -> pd.DataFrame:
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
    config = load_config_cached()
    return DataFreshnessGuard(config).check_freshness()


def get_signals_df(latest_only: bool = True) -> pd.DataFrame:
    where_latest = "WHERE s.date = (SELECT MAX(date) FROM signals)" if latest_only else ""
    query = f"""
        SELECT s.*, i.close, i.daily_return, i.ma5, i.ma25,
               i.volume_ratio, i.return_5d, i.return_5d_vs_benchmark,
               i.turnover, i.history_days,
               sym.name, sym.type, sym.role, sym.tradable
        FROM signals s
        LEFT JOIN indicators i ON s.code = i.code AND s.date = i.date
        LEFT JOIN symbols sym ON s.code = sym.code
        {where_latest}
        ORDER BY s.score DESC
    """
    return _read_sql(query)


def get_daily_bars_df(code: str, days: int = 60) -> pd.DataFrame:
    return _read_sql("SELECT * FROM daily_bars WHERE code = ? ORDER BY date DESC LIMIT ?", [code, days])


def get_trades_df() -> pd.DataFrame:
    return _read_sql(
        """
        SELECT t.*, s.name
        FROM trades_manual t
        LEFT JOIN symbols s ON t.code = s.code
        ORDER BY t.executed_at DESC
        """
    )


def get_positions_df() -> pd.DataFrame:
    config = load_config_cached()
    positions = PerformanceEvaluator(config).get_positions()
    if not positions:
        return pd.DataFrame()
    return pd.DataFrame([{
        "code": p.code,
        "name": p.name,
        "quantity": p.quantity,
        "avg_price": p.avg_price,
        "current_price": p.current_price,
        "unrealized_pnl": p.unrealized_pnl,
        "unrealized_return": p.unrealized_return,
    } for p in positions])


def get_backtest_df() -> pd.DataFrame:
    return _read_sql(
        """
        SELECT sb.*, s.signal_type, s.score, s.reason
        FROM signal_backtests sb
        LEFT JOIN signals s ON sb.signal_id = s.id
        ORDER BY sb.signal_date DESC
        """
    )


def _format_dataframe(df: pd.DataFrame, format_map: dict):
    if df.empty:
        return df
    available_format = {k: v for k, v in format_map.items() if k in df.columns}
    return df.style.format(available_format) if available_format else df


def tab_dashboard():
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
        st.metric("買い候補", f"{len(signals_df[signals_df['signal_type'] == 'BUY_CANDIDATE']) if not signals_df.empty else 0}件")
    with col2:
        st.metric("監視候補", f"{len(signals_df[signals_df['signal_type'] == 'WATCH']) if not signals_df.empty else 0}件")
    with col3:
        st.metric("除外", f"{len(signals_df[signals_df['signal_type'] == 'EXCLUDE']) if not signals_df.empty else 0}件")
    with col4:
        st.metric("監視銘柄数", f"{len(signals_df)}件")

    st.subheader("ポートフォリオ")
    config = load_config_cached()
    summary = PerformanceEvaluator(config).get_summary()
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
    st.header("候補一覧")
    latest_only = st.checkbox("最新日だけ表示", value=True)
    signals_df = get_signals_df(latest_only=latest_only)
    if signals_df.empty:
        st.info("データがありません。まず daily_update.py と screen_candidates.py --save を実行してください。")
        return

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        signal_filter = st.multiselect("シグナル種別", options=["BUY_CANDIDATE", "WATCH", "EXCLUDE"], default=["BUY_CANDIDATE", "WATCH"])
    with col2:
        min_score = st.slider("最小スコア", 0, 100, 50)
    with col3:
        role_values = sorted([x for x in signals_df.get("role", pd.Series(dtype=str)).dropna().unique()])
        role_filter = st.multiselect("role", options=role_values, default=role_values)
    with col4:
        tradable_only = st.checkbox("tradableのみ", value=False)

    filtered = signals_df[
        signals_df["signal_type"].isin(signal_filter)
        & (signals_df["score"].fillna(0) >= min_score)
    ]
    if role_filter and "role" in filtered.columns:
        filtered = filtered[filtered["role"].isin(role_filter)]
    if tradable_only and "tradable" in filtered.columns:
        filtered = filtered[filtered["tradable"].fillna(0).astype(bool)]

    columns = [
        "date", "code", "name", "type", "role", "tradable", "close", "daily_return",
        "ma5", "ma25", "volume_ratio", "return_5d", "return_5d_vs_benchmark",
        "turnover", "score", "signal_type", "reason",
    ]
    columns = [c for c in columns if c in filtered.columns]
    st.dataframe(
        _format_dataframe(filtered[columns], {
            "close": "{:,.0f}",
            "daily_return": "{:.1f}%",
            "return_5d": "{:.1f}%",
            "return_5d_vs_benchmark": "{:.1f}%",
            "turnover": "{:,.0f}",
            "score": "{:.0f}",
        }),
        use_container_width=True,
    )
    if not filtered.empty:
        csv = filtered.to_csv(index=False, encoding="utf-8-sig")
        st.download_button("CSVダウンロード", data=csv, file_name=f"signals_{datetime.now().strftime('%Y%m%d')}.csv", mime="text/csv")


def tab_stock_detail():
    st.header("銘柄詳細")
    signals_df = get_signals_df(latest_only=False)
    if signals_df.empty:
        st.info("シグナルデータがありません")
        return
    code = st.selectbox("銘柄", options=sorted(signals_df["code"].unique()))
    bars = get_daily_bars_df(code, 90)
    if not bars.empty:
        st.subheader("終値推移")
        chart_df = bars.sort_values("date")[["date", "close"]].set_index("date")
        st.line_chart(chart_df)
        st.dataframe(bars, use_container_width=True)
    st.subheader("シグナル履歴")
    st.dataframe(signals_df[signals_df["code"] == code].sort_values("date", ascending=False), use_container_width=True)


def tab_trade_log():
    st.header("手動売買ログ")
    st.info("これは手動売買ログです。実注文ではありません。")
    df = get_trades_df()
    if not df.empty:
        st.dataframe(df, use_container_width=True)

    with st.form("trade_form"):
        code = st.text_input("銘柄コード", value="JP.7203")
        side = st.selectbox("売買", ["BUY", "SELL"])
        quantity = st.number_input("数量", min_value=1, value=1)
        price = st.number_input("価格", min_value=0.0, value=0.0)
        reason = st.text_input("理由")
        submitted = st.form_submit_button("ログ登録")
        if submitted:
            config = load_config_cached()
            log = TradeLog(config)
            log.record_trade(code=code, side=side, quantity=int(quantity), price=float(price), reason=reason)
            st.success("登録しました。画面を再読み込みしてください。")


def tab_performance():
    st.header("パフォーマンス")
    config = load_config_cached()
    evaluator = PerformanceEvaluator(config)
    summary = evaluator.get_summary()
    st.json(summary.__dict__ if hasattr(summary, "__dict__") else {})
    positions_df = get_positions_df()
    if not positions_df.empty:
        st.dataframe(positions_df, use_container_width=True)


def tab_backtest():
    st.header("事後検証")
    df = get_backtest_df()
    if df.empty:
        st.info("事後検証データがありません")
        return
    st.dataframe(df, use_container_width=True)
    csv = df.to_csv(index=False, encoding="utf-8-sig")
    st.download_button("CSVダウンロード", data=csv, file_name=f"backtest_{datetime.now().strftime('%Y%m%d')}.csv", mime="text/csv")


def tab_order_info():
    st.header("注文について")
    st.info("このアプリは売買候補の抽出と検証を行う補助ツールです。")
    st.markdown("""
    ### 取引方法
    moomoo JPの日本株注文は、**APIではなくmoomooアプリで手動実行**してください。

    ### API注文について
    - 本アプリは**REAL注文APIを呼び出しません**
    - moomoo JP / FUTUJP では、OpenAPI経由の日本株SIMULATE注文が利用できないため、JP向けペーパートレード機能は無効です
    - アプリ内デモ取引とAPI SIMULATEは別物として扱います

    ### 検証方針
    ```text
    データ取得：moomoo API
    候補抽出：本アプリ
    仮想検証：アプリ内仮想トレード
    実取引：moomooアプリで手動
    API注文：未対応・使用禁止
    ```
    """)


def tab_virtual_trade():
    from src.virtual_trade import VirtualTradeManager
    st.header("仮想トレード")
    st.warning("これはアプリ内の仮想注文です。moomooには注文を送信しません。")
    manager = VirtualTradeManager(load_config_cached())
    sub1, sub2, sub3, sub4 = st.tabs(["ポジション", "注文一覧", "約定一覧", "パフォーマンス"])
    with sub1:
        positions = manager.get_positions()
        if positions:
            st.dataframe(pd.DataFrame([p.__dict__ for p in positions]), use_container_width=True)
        else:
            st.info("保有ポジションはありません")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("売却候補生成"):
                st.success(f"{len(manager.generate_exits('default'))}件の売却注文を生成しました")
        with col2:
            if st.button("現在値更新"):
                st.success(f"{manager.update_market_prices('default')}件のポジションを更新しました")
    with sub2:
        orders = manager.get_pending_orders()
        st.dataframe(pd.DataFrame([o.__dict__ for o in orders]) if orders else pd.DataFrame(), use_container_width=True)
    with sub3:
        fills = manager.get_fills()
        st.dataframe(pd.DataFrame([f.__dict__ for f in fills]) if fills else pd.DataFrame(), use_container_width=True)
    with sub4:
        perf = manager.get_strategy_performance()
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("現金", f"{perf['cash']:,.0f}円")
        with col2:
            st.metric("総資産", f"{perf['total_equity']:,.0f}円")
        with col3:
            st.metric("リターン", f"{perf['return_pct']:.2f}%")
        st.json(perf)
        curve = manager.get_equity_curve()
        if curve:
            curve_df = pd.DataFrame(curve)
            st.line_chart(curve_df.sort_values("date").set_index("date")["total_equity"])


def tab_daily_ops():
    st.header("日次運用")
    config = load_config_cached()
    status = DataFreshnessGuard(config).check_freshness()
    if status.level == "ok":
        st.success(f"データ: {status.latest_date}")
    elif status.level == "warning":
        st.warning(f"データ: {status.latest_date}（{status.days_stale}日分古い）")
    else:
        st.error(f"データ: {status.latest_date}（古すぎます）")

    if st.button("日次サイクル Dry-Run"):
        from run_daily_cycle import run_cycle
        from datetime import datetime
        try:
            results = run_cycle(datetime.now().strftime("%Y-%m-%d"), dry_run=True)
            st.success(f"Dry-run 成功: {results}")
        except Exception as e:
            st.error(f"Dry-run 失敗: {e}")

    if st.button("仮想トレードレポート出力"):
        from virtual_order import show_performance
        from src.virtual_trade import VirtualTradeManager
        try:
            manager = VirtualTradeManager(load_config_cached())
            show_performance(manager, "default")
            st.success("レポート出力完了")
        except Exception as e:
            st.error(f"レポート出力失敗: {e}")


def main() -> None:
    st.title("Moomoo Strategy Monitor")
    st.caption("日本株モメンタム検証ツール（手動売買ログ・仮想検証用）")
    try:
        status = check_data_freshness()
        if status.level != "ok":
            st.error(f"データが古いです: {status.message}\n\n最新日付: {status.latest_date}\n\n`python daily_update.py --force` を実行してください。")
    except Exception as e:
        st.warning(f"データ鮮度チェックを実行できませんでした: {e}")

    tabs = st.tabs(["ダッシュボード", "候補一覧", "銘柄詳細", "手動売買ログ", "パフォーマンス", "事後検証", "注文について", "仮想トレード", "日次運用"])
    funcs = [tab_dashboard, tab_candidates, tab_stock_detail, tab_trade_log, tab_performance, tab_backtest, tab_order_info, tab_virtual_trade, tab_daily_ops]
    for tab, func in zip(tabs, funcs):
        with tab:
            func()


if __name__ == "__main__":
    main()
