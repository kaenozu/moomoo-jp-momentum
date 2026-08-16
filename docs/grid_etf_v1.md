# `grid_etf_v1`

`src/grid_etf.py` は既存の `momentum` と資金・注文・ポジション状態を共有しない、
日本ETF向けロング専用の検証用グリッドエンジンです。既存の日本株日次運用や
`src/virtual_trade.py` の動作を変更しません。

## 仕様

- グリッド間隔は直近 `atr_period` 本のTrue Range平均 × `atr_multiplier`
- 下側に最大 `levels` 段のBUYを置く
- BUY約定後の利確SELLは、約定したバーの次バーから有効
- 最大資金拘束率 `max_capital_pct` と1段資金 `level_capital` を適用
- 評価額の最大ドローダウンが `max_drawdown_pct` 以上になると新規注文を停止
- 1銘柄・ロングのみ。空売り、REAL注文、Moomoo注文APIは存在しない

`GridEtfV1.backtest()` に日付順の `GridBar` 列を渡すと、戦略専用の資金曲線と約定結果を返します。
日足OHLCの同一バー内で、BUY直後のSELLを楽観的に成立させない保守的モデルです。

SQLite上の日足を読み取って試す場合は、DBを書き換えずに次を実行します。

```bash
python grid_etf_backtest.py --code JP.1306 --from 2026-01-01 --to 2026-06-30
```

既存の履歴バックテスト入口から実行する場合は、次の専用分岐を使います。

```bash
python historical_backtest.py --strategy grid_etf_v1 --grid-code JP.1306 \
  --from 2026-01-01 --to 2026-06-30 --csv
```

`--csv` を指定すると `reports/grid_etf_v1_summary_*.csv` と資産曲線CSVを出力します。

再起動後も専用状態から続ける場合だけ、`--persist` を明示します。既存の `virtual_*` 表や
momentumの状態には書き込みません。

```bash
python grid_etf_backtest.py --code JP.1306 --from 2026-01-01 --to 2026-06-30 --persist
```

## 運用境界

これは検証用の独立エンジンであり、既存の `historical_backtest.py` や scheduler へ自動接続していません。
`--persist` は専用ledgerへの再開可能な状態保存だけを行います。ペーパートレード、Production、外部注文は実施しません。
