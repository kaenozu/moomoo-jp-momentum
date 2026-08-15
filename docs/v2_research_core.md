# V2 research core

V2は、V1のSQLite・moomoo・Streamlit・BrokerExecutionとは独立した、外部I/Oなしの戦略研究基盤です。目的は、バックテスト結果をそのままBOT採用とみなすことではなく、同一条件で候補戦略を比較し、後続のOOS・robustness・Paper検証へ進めることです。

## 実装範囲

```text
CanonicalBar / MarketSnapshot
        ↓
Strategy scores
        ↓
Allocator / Exposure / RiskPolicy
        ↓
MemoryPortfolio / SimulationEngine
        ↓
Metrics / Experiment / Tournament
```

`SimulationEngine`はnext-day-openの決定的な約定モデルを使います。売り注文を先に処理し、翌日ギャップに備えた固定cash bufferを持つため、注文順序と現金不足時の挙動が再現可能です。

## Tournament

`StrategyTournament`は同じsnapshot列、初期資金、最大ポジション数、約定モデルで次を比較します。

- `buy_hold`
- `equal_weight`
- `momentum`
- `volatility_adjusted_momentum`
- `trend_momentum`
- `benchmark_alpha`

各結果には次の指標を必ず含めます。

`CAGR`、`excess CAGR`、`Sharpe`、`Sortino`、`MaxDD`、`Calmar`、`turnover`、`exposure`

## 安全境界

- REAL注文、SIMULATE注文、BrokerExecutionは実装しない
- SQLite、moomoo API、yfinance、ネットワーク、Streamlitは実装しない
- V1の既存コード・保存形式・注文経路は変更しない
- Tournamentの結果だけで `VALIDATED` や `BOT_CANDIDATE` に昇格させない

## 次の昇格ゲート

このPRで未実施の項目は、実データを接続する後続作業として分離します。

1. データprovenanceと異常値を含むOOS fixture
2. parameter / universe / cost robustness
3. benchmark比較とwalk-forward
4. backtest / paper decision parity
5. Paper forwardの再起動・欠損・二重注文・損失上限検証

## 実データのread-only実行

既存DBを変更せず、`daily_bars`だけを読み取ってTournamentを実行できます。

```powershell
uv run --with-requirements requirements.txt --with-requirements requirements-dev.txt `
  python scripts/run_v2_sqlite_tournament.py `
  --db data/moomoo.db `
  --from 2025-01-01 `
  --to 2026-07-31 `
  --benchmark JP.1306 `
  --max-positions 20
```

このコマンドはSQLiteを`mode=ro`で開きます。DBがない場合や日足が存在しない場合は、推測データで代用せず停止します。
