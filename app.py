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

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import sqlite3
import streamlit as st

from src.config import load_config
from src.data_freshness import DataFreshnessGuard
from src.screener import Screener
from src.performance import PerformanceEvaluator
from src.trade_log import TradeLog

# ページ設定
st.set_page_config(
    page_title="Moomoo Strategy Monitor",
    page_icon=" ",
    layout="wide",
)


@st.cache_resource
def load_config_cached():
    """設定をキャッシュして読み込む"""
    return load_config("config.yaml")


@st.cache_resource
def get_data_store():
    """データストアを取得"""
    from src.data_store import DataStore
    config = load_config_cached()
    return DataStore(config)


def check_data_freshness():
    """データ鮮度をチェック"""
    config = load_config_cached()
    guard = DataFreshnessGuard(config)
    status = guard.check_freshness()
    return status


def get_signals_df():
    """シグナルデータを取得"""
    config = load_config_cached()
    db_path = config.database_path

    with sqlite3.connect(db_path) as conn:
        query = """
            SELECT s.*, i.close, i.daily_return, i.ma5, i.ma25,
                   i.volume_ratio, i.return_5d, i.turnover, i.history_days,
                   sym.name
            FROM signals s
            LEFT JOIN indicators i ON s.code = i.code AND s.date = i.date
            LEFT JOIN symbols sym ON s.code = sym.code
            ORDER BY s.score DESC
        """
        df = pd.read_sql_query(query, conn)

    return df


def get_daily_bars_df(code: str, days: int = 60):
    """日足データを取得"""
    config = load_config_cached()
    db_path = config.database_path

    with sqlite3.connect(db_path) as conn:
        query = """
            SELECT * FROM daily_bars
            WHERE code = ?
            ORDER BY date DESC
            LIMIT ?
        """
        df = pd.read_sql_query(query, conn, params=[code, days])

    return df


def get_trades_df():
    """売買記録を取得"""
    config = load_config_cached()
    db_path = config.database_path

    with sqlite3.connect(db_path) as conn:
        query = """
            SELECT t.*, s.name
            FROM trades_manual t
            LEFT JOIN symbols s ON t.code = s.code
            ORDER BY t.executed_at DESC
        """
        df = pd.read_sql_query(query, conn)

    return df


def get_positions_df():
    """保有ポジションを取得"""
    config = load_config_cached()
    evaluator = PerformanceEvaluator(config)
    positions = evaluator.get_positions()

    if not positions:
        return pd.DataFrame()

    records = []
    for p in positions:
        records.append({
            "code": p.code,
            "name": p.name,
            "quantity": p.quantity,
            "avg_price": p.avg_price,
            "current_price": p.current_price,
            "unrealized_pnl": p.unrealized_pnl,
            "unrealized_return": p.unrealized_return,
        })

    return pd.DataFrame(records)


def get_backtest_df():
    """事後検証データを取得"""
    config = load_config_cached()
    db_path = config.database_path

    with sqlite3.connect(db_path) as conn:
        query = """
            SELECT sb.*, s.signal_type, s.score, s.reason
            FROM signal_backtests sb
            LEFT JOIN signals s ON sb.signal_id = s.id
            ORDER BY sb.signal_date DESC
        """
        df = pd.read_sql_query(query, conn)

    return df


def tab_dashboard():
    """ダッシュボードタブ"""
    st.header("ダッシュボード")

    # データ鮮度チェック
    status = check_data_freshness()

    if status.level == "ok":
        st.success(f"  データは最新です: {status.latest_date}")
    elif status.level == "warning":
        st.warning(f"⚠ データが古いです: {status.message}")
    else:
        st.error(f"  データが著しく古いです: {status.message}")

    # サマリー表示
    col1, col2, col3, col4 = st.columns(4)

    signals_df = get_signals_df()

    with col1:
        buy_count = len(signals_df[signals_df["signal_type"] == "BUY_CANDIDATE"])
        st.metric("買い候補", f"{buy_count}件")

    with col2:
        watch_count = len(signals_df[signals_df["signal_type"] == "WATCH"])
        st.metric("監視候補", f"{watch_count}件")

    with col3:
        exclude_count = len(signals_df[signals_df["signal_type"] == "EXCLUDE"])
        st.metric("除外", f"{exclude_count}件")

    with col4:
        st.metric("監視銘柄数", f"{len(signals_df)}件")

    # ポートフォリオサマリー
    st.subheader("ポートフォリオ")

    col1, col2, col3 = st.columns(3)

    with col1:
        positions_df = get_positions_df()
        if not positions_df.empty:
            total_pnl = positions_df["unrealized_pnl"].sum()
            st.metric("未実現損益", f"{total_pnl:,.0f}円")
        else:
            st.metric("未実現損益", "0円")

    with col2:
        config = load_config_cached()
        trade_log = TradeLog(config)
        trades = trade_log.get_all_trades()
        realized_pnl = sum(
            t.price * t.quantity * (1 if t.side == "SELL" else -1)
            for t in trades
        )
        st.metric("実現損益", f"{realized_pnl:,.0f}円")

    with col3:
        st.metric("保有中銘柄", f"{len(positions_df)}件")

    # 最終更新時刻
    st.caption(f"最終更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


def tab_candidates():
    """候補一覧タブ"""
    st.header("候補一覧")

    signals_df = get_signals_df()

    if signals_df.empty:
        st.info("データがありません。まず daily_update.py を実行してください。")
        return

    # フィルタ
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
        etf_filter = st.radio(
            "銘柄種別",
            options=["すべて", "個別株のみ", "ETFのみ"],
            horizontal=True,
        )

    # フィルタ適用
    filtered = signals_df[
        signals_df["signal_type"].isin(signal_filter)
        & (signals_df["score"] >= min_score)
    ]

    if etf_filter == "個別株のみ":
        filtered = filtered[~filtered["code"].str.contains("JP.13|JP.25")]
    elif etf_filter == "ETFのみ":
        filtered = filtered[filtered["code"].str.contains("JP.13|JP.25")]

    # 表示
    st.dataframe(
        filtered[[
            "code", "name", "close", "daily_return", "ma5", "ma25",
            "volume_ratio", "return_5d", "score", "signal_type", "reason",
        ]].style.format({
            "close": "{:,.0f}",
            "daily_return": "{:.1f}%",
            "return_5d": "{:.1f}%",
            "score": "{:.0f}",
        }),
        use_container_width=True,
    )

    # CSVダウンロード
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

    # 銘柄選択
    signals_df = get_signals_df()
    if signals_df.empty:
        st.info("データがありません。")
        return

    codes = signals_df["code"].unique()
    selected_code = st.selectbox("銘柄を選択", codes)

    if not selected_code:
        return

    # 銘柄情報
    stock_info = signals_df[signals_df["code"] == selected_code].iloc[0]

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("銘柄名", stock_info.get("name", ""))
    with col2:
        st.metric("現在値", f"{stock_info.get('close', 0):,.0f}")
    with col3:
        st.metric("スコア", f"{stock_info.get('score', 0):.0f}")
    with col4:
        st.metric("シグナル", stock_info.get("signal_type", ""))

    # 日足データ
    st.subheader("日足データ")
    daily_df = get_daily_bars_df(selected_code, 60)

    if not daily_df.empty:
        # 終値推移チャート
        st.line_chart(daily_df.set_index("date")["close"])

        # 出来高チャート
        st.bar_chart(daily_df.set_index("date")["volume"])

        # 日足テーブル
        st.dataframe(
            daily_df[["date", "open", "high", "low", "close", "volume"]].head(20),
            use_container_width=True,
        )

    # シグナル履歴
    st.subheader("シグナル履歴")
    signal_history = signals_df[signals_df["code"] == selected_code]
    if not signal_history.empty:
        st.dataframe(
            signal_history[["date", "signal_type", "score", "reason"]],
            use_container_width=True,
        )

    # 手動売買ログ
    st.subheader("手動売買ログ")
    trades_df = get_trades_df()
    stock_trades = trades_df[trades_df["code"] == selected_code]
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

    # 入力フォーム
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
                    code=code,
                    side=side,
                    quantity=quantity,
                    price=price,
                    reason=reason,
                    exit_rule=exit_rule,
                    memo=memo,
                    executed_at=executed_at.strftime("%Y-%m-%d"),
                )
                st.success(f"登録完了 (ID: {trade_id})")
                st.rerun()
            else:
                st.error("銘柄コードと価格を入力してください")

    # 一覧表示
    st.subheader("売買記録一覧")

    trades_df = get_trades_df()

    if trades_df.empty:
        st.info("売買記録がありません")
        return

    # フィルタ
    col1, col2 = st.columns(2)
    with col1:
        code_filter = st.text_input("銘柄コードでフィルタ")
    with col2:
        side_filter = st.radio("方向", ["すべて", "BUY", "SELL"], horizontal=True)

    filtered = trades_df
    if code_filter:
        filtered = filtered[filtered["code"].str.contains(code_filter)]
    if side_filter != "すべて":
        filtered = filtered[filtered["side"] == side_filter]

    st.dataframe(filtered, use_container_width=True)

    # CSVダウンロード
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

    # サマリー
    summary = evaluator.get_summary()

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("投資総額", f"{summary.total_invested:,.0f}円")
    with col2:
        st.metric("総損益", f"{summary.total_pnl:,.0f}円")
    with col3:
        if summary.win_rate is not None:
            st.metric("勝率", f"{summary.win_rate:.1f}%")

    # 保有中一覧
    st.subheader("保有中一覧")
    positions_df = get_positions_df()
    if not positions_df.empty:
        st.dataframe(
            positions_df.style.format({
                "avg_price": "{:,.0f}",
                "current_price": "{:,.0f}",
                "unrealized_pnl": "{:,.0f}",
                "unrealized_return": "{:.1f}%",
            }),
            use_container_width=True,
        )
    else:
        st.info("保有ポジションはありません")

    # 売買履歴
    st.subheader("売買履歴")
    history = evaluator.get_trade_history()
    if history:
        records = []
        for h in history:
            records.append({
                "code": h.code,
                "name": h.name,
                "quantity": h.quantity,
                "entry_price": h.entry_price,
                "exit_price": h.exit_price,
                "pnl": h.pnl,
                "return_pct": h.return_pct,
                "holding_days": h.holding_days,
            })
        history_df = pd.DataFrame(records)
        st.dataframe(
            history_df.style.format({
                "entry_price": "{:,.0f}",
                "exit_price": "{:,.0f}",
                "pnl": "{:,.0f}",
                "return_pct": "{:.1f}%",
            }),
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

    # フィルタ
    col1, col2 = st.columns(2)
    with col1:
        horizon_filter = st.multiselect(
            "検証期間",
            options=backtest_df["horizon_days"].unique(),
            default=backtest_df["horizon_days"].unique(),
        )
    with col2:
        benchmark_filter = st.multiselect(
            "ベンチマーク",
            options=backtest_df["benchmark_code"].unique(),
            default=backtest_df["benchmark_code"].unique(),
        )

    filtered = backtest_df[
        backtest_df["horizon_days"].isin(horizon_filter)
        & backtest_df["benchmark_code"].isin(benchmark_filter)
    ]

    st.dataframe(
        filtered[[
            "signal_date", "code", "horizon_days", "signal_price",
            "future_price", "stock_return", "benchmark_return",
            "excess_return", "max_drawdown", "max_runup",
        ]].style.format({
            "signal_price": "{:,.0f}",
            "future_price": "{:,.0f}",
            "stock_return": "{:.1f}%",
            "benchmark_return": "{:.1f}%",
            "excess_return": "{:.1f}%",
            "max_drawdown": "{:.1f}%",
            "max_runup": "{:.1f}%",
        }),
        use_container_width=True,
    )

    # CSVダウンロード
    if not filtered.empty:
        csv = filtered.to_csv(index=False, encoding="utf-8-sig")
        st.download_button(
            label="CSVダウンロード",
            data=csv,
            file_name=f"backtest_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
        )


def tab_paper_trade():
    """注文についてタブ（ペーパートレード情報）"""
    st.header("注文について")

    st.info(
        "**このアプリは売買候補の抽出と検証を行う補助ツールです。**"
    )

    st.markdown("""
    ### 取引方法

    moomoo JPの日本株注文は、**APIではなくmoomooアプリで手動実行**してください。

    実行した売買は「手動売買ログ」に記録してください。

    ### API注文について

    - 本アプリは**REAL注文APIを呼び出しません**
    - moomoo JP / FUTUJP では、OpenAPI経由の日本株SIMULATE注文が利用できないため、ペーパートレード機能は無効です
    - アプリ内デモ取引とAPI SIMULATEは別物として扱います

    ### 検証方針

    ```
    データ取得：moomoo API
    候補抽出：本アプリ
    検証：signals / signal_backtests
    実取引：moomooアプリで手動
    売買記録：trades_manual に手動入力
    API注文：未対応・使用禁止
    ```
    """)
    """メイン関数"""
    st.title("  Moomoo Strategy Monitor")
    st.caption("日本株モメンタム検証ツール（手動売買ログ・検証用）")

    # データ鮮度チェック
    status = check_data_freshness()
    if status.level != "ok":
        st.error(
            f"  データが古いです: {status.message}\n"
            f"最新日付: {status.latest_date}\n"
            f"`python daily_update.py --force` を実行してください。"
        )

    # タブ
    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        "ダッシュボード",
        "候補一覧",
        "銘柄詳細",
        "手動売買ログ",
        "パフォーマンス",
        "事後検証",
        "注文について",
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
        tab_paper_trade()


if __name__ == "__main__":
    main()
