"""
仮想注文CLI

アプリ内ペーパートレードの仮想注文を管理する。
moomooには一切注文を送信しない。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.config import load_config
from src.screener import Screener
from src.virtual_trade import VirtualTradeManager
from src.trading_identity import signal_strategy_name, virtual_portfolio_name


def print_warning():
    print()
    print("=" * 60)
    print("  これはアプリ内の仮想注文です。moomooには注文を送信しません。")
    print("  実注文ではありません。")
    print("=" * 60)
    print()


def list_orders(manager: VirtualTradeManager, strategy: str):
    orders = manager.get_pending_orders(strategy)
    if not orders:
        print("未約定の仮想注文はありません")
        return
    print("\n仮想注文一覧（未約定）:")
    print(f"{'ID':>4} {'コード':<12} {'方向':<5} {'数量':>4} {'タイプ':<12} {'指値':>8} {'提出日':<19}")
    print("-" * 80)
    for o in orders:
        limit = f"{o.limit_price:,.0f}" if o.limit_price else "N/A"
        print(f"{o.id:>4} {o.code:<12} {o.side:<5} {o.quantity:>4} {o.order_type:<12} {limit:>8} {o.submitted_at:<19}")


def list_positions(manager: VirtualTradeManager, strategy: str):
    positions = manager.get_positions(strategy)
    if not positions:
        print("保有ポジションはありません")
        return
    print("\n仮想ポジション一覧:")
    print(f"{'コード':<12} {'数量':>4} {'平均取得':>10} {'現在値':>10} {'評価損益':>10}")
    print("-" * 60)
    for p in positions:
        mkt = f"{p.market_price:,.0f}" if p.market_price is not None else "N/A"
        pl = f"{p.unrealized_pl:,.0f}" if p.unrealized_pl is not None else "N/A"
        print(f"{p.code:<12} {p.quantity:>4} {p.avg_cost:>10,.0f} {mkt:>10} {pl:>10}")


def list_fills(manager: VirtualTradeManager, strategy: str):
    fills = manager.get_fills(strategy)
    if not fills:
        print("約定はありません")
        return
    print("\n仮想約定一覧:")
    print(f"{'ID':>4} {'コード':<12} {'方向':<5} {'数量':>4} {'価格':>10} {'約定日':<12} {'モード':<20}")
    print("-" * 80)
    for f in fills:
        print(f"{f.id:>4} {f.code:<12} {f.side:<5} {f.quantity:>4} {f.price:>10,.0f} {f.filled_at[:10]:<12} {f.fill_mode:<20}")


def show_performance(manager: VirtualTradeManager, strategy: str):
    perf = manager.get_strategy_performance(strategy)
    print("\n戦略パフォーマンス:")
    print(f"  戦術: {perf['strategy_name']}")
    print(f"  初期資金: {perf['initial_cash']:,.0f}円")
    print(f"  現金: {perf['cash']:,.0f}円")
    print(f"  ポジション評価額: {perf['position_value']:,.0f}円")
    print(f"  総資産: {perf['total_equity']:,.0f}円")
    print(f"  実現損益: {perf['realized_pl']:,.0f}円")
    print(f"  未実現損益: {perf['unrealized_pl']:,.0f}円")
    print(f"  総損益: {perf['total_pl']:,.0f}円")
    print(f"  リターン: {perf['return_pct']:.2f}%")
    print(f"  保有銘柄数: {perf['position_count']}")


def from_signals(
    manager: VirtualTradeManager,
    config,
    date: str,
    portfolio: str,
    signal_strategy: str,
):
    from src.data_store import DataStore
    DataStore(config).sync_symbols_from_json(config.watchlist_file)

    screener = Screener(config)
    candidates = screener.screen_candidates(date=None if date == "latest" else date)
    vt_config = config.get("virtual_trade", {})
    score_threshold = vt_config.get("score_threshold_for_order", 70)
    cash = manager.get_cash(portfolio)
    created = 0

    for c in candidates:
        if c.signal_type != "BUY_CANDIDATE":
            continue
        if c.strategy_name != signal_strategy:
            continue
        if c.role != "trade_candidate" or not c.tradable:
            continue
        if c.score < score_threshold:
            continue
        if c.close and c.close > cash:
            continue
        order = manager.place_order(
            strategy_name=portfolio,
            code=c.code,
            side="BUY",
            quantity=1,
            order_type="MARKET_SIM",
            submitted_at=c.date,
        )
        if order:
            created += 1
            cash -= c.close or 0

    print(
        f"シグナル戦略 {signal_strategy} → 仮想portfolio {portfolio}: "
        f"{created} 件の仮想注文を作成しました"
    )


def main():
    parser = argparse.ArgumentParser(description="Moomoo 仮想注文CLI")
    parser.add_argument("--code", help="銘柄コード")
    parser.add_argument("--side", choices=["BUY", "SELL"])
    parser.add_argument("--quantity", type=int, help="数量")
    parser.add_argument("--order-type", choices=["MARKET_SIM", "LIMIT_SIM"], default="MARKET_SIM")
    parser.add_argument("--limit-price", type=float, help="指値価格")
    parser.add_argument("--from-signals", action="store_true", help="シグナルから仮想注文を作成")
    parser.add_argument("--generate-exits", action="store_true", help="売却注文を生成")
    parser.add_argument("--date", help="基準日")
    parser.add_argument("--list", action="store_true", help="注文一覧")
    parser.add_argument("--positions", action="store_true", help="ポジション一覧")
    parser.add_argument("--list-fills", action="store_true", help="約定一覧")
    parser.add_argument("--cancel", action="store_true", help="注文キャンセル")
    parser.add_argument("--order-id", type=int, help="注文ID")
    parser.add_argument("--performance", action="store_true", help="パフォーマンス表示")
    parser.add_argument("--report", action="store_true", help="レポート出力（CSV/HTML）")
    parser.add_argument(
        "--portfolio",
        "--strategy",
        dest="portfolio",
        default=None,
        help="仮想取引portfolio名（--strategyは互換alias）",
    )
    parser.add_argument(
        "--signal-strategy",
        default=None,
        help="シグナル生成戦略名",
    )
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    print_warning()
    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1

    portfolio = args.portfolio or virtual_portfolio_name(config)
    signal_strategy = args.signal_strategy or signal_strategy_name(config)
    manager = VirtualTradeManager(config)

    if args.list:
        list_orders(manager, portfolio)
        return 0
    if args.positions:
        list_positions(manager, portfolio)
        return 0
    if args.list_fills:
        list_fills(manager, portfolio)
        return 0
    if args.performance:
        show_performance(manager, portfolio)
        return 0
    if args.report:
        from datetime import datetime
        import pandas as pd
        out_dir = Path("reports")
        out_dir.mkdir(parents=True, exist_ok=True)
        report = manager.generate_report(portfolio)
        date_str = datetime.now().strftime("%Y%m%d")
        csv_path = out_dir / f"virtual_trade_{date_str}.csv"
        pd.DataFrame([report]).to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"[OK] CSV: {csv_path}")
        return 0
    if args.from_signals:
        from_signals(
            manager,
            config,
            args.date or "latest",
            portfolio,
            signal_strategy,
        )
        return 0
    if args.generate_exits:
        orders = manager.generate_exits(portfolio, args.date)
        print(f"{len(orders)} 件の売却注文を生成しました")
        return 0
    if args.cancel:
        if not args.order_id:
            print("[ERROR] --order-id が必要です")
            return 1
        if manager.cancel_order(args.order_id):
            print(f"[OK] 注文 {args.order_id} をキャンセルしました")
        else:
            print(f"[ERROR] 注文 {args.order_id} のキャンセルに失敗しました")
        return 0

    if not args.code or not args.side or args.quantity is None:
        print("[ERROR] --code, --side, --quantity が必要です")
        parser.print_help()
        return 1

    order = manager.place_order(
        strategy_name=portfolio,
        code=args.code,
        side=args.side,
        quantity=args.quantity,
        order_type=args.order_type,
        limit_price=args.limit_price,
        submitted_at=args.date,
    )
    if order:
        print(f"[OK] 仮想注文作成 (ID: {order.id}) {order.code} {order.side} {order.quantity}株")
        return 0
    print("[ERROR] 仮想注文作成失敗")
    return 1


if __name__ == "__main__":
    sys.exit(main())
