# 月別超過リターン寄与度分析

## 目的

バックテストの月別リターンを `JP.1306` または `JP.2559` と比較し、超過リターンを次の2要因へ分解します。

- `cash_drag_pct`: 前日キャッシュ比率により取り逃したベンチマークリターン
- `residual_effect_pct`: 銘柄選択、売買タイミング、執行コスト、スリッページ、およびcash残高へ組み込まれたidle-cash overlayを含む残差

`residual_effect_pct` は純粋な銘柄選択効果ではありません。現在の `backtest_equity_curve` はidle-cash overlayやコストを独立系列として保存していないため、それらを分離できないことが理由です。

## 実行方法

run IDを直接指定する場合:

```bash
python scripts/monthly_return_attribution.py \
  --run-id 123 \
  --benchmark 1306
```

strategyの最新runを使用する場合:

```bash
python scripts/monthly_return_attribution.py \
  --strategy momentum \
  --benchmark 1306
```

別DB・出力先を指定する場合:

```bash
python scripts/monthly_return_attribution.py \
  --db data/moomoo.db \
  --strategy momentum \
  --benchmark 2559 \
  --output-dir reports/monthly-attribution
```

## 出力

- `daily_attribution_run_<run_id>_<benchmark>.csv`
- `monthly_attribution_run_<run_id>_<benchmark>.csv`

月次CSVの主要列:

| 列 | 意味 |
| --- | --- |
| `strategy_return_pct` | 戦略の月次複利リターン |
| `benchmark_return_pct` | ベンチマークの月次複利リターン |
| `active_return_pct` | 戦略 - ベンチマーク |
| `cash_drag_pct` | キャッシュ保有による寄与 |
| `residual_effect_pct` | 選択・タイミング・コスト・overlay等の残差 |
| `avg_cash_weight_pct` | 前日基準の平均キャッシュ比率 |
| `max_drawdown_pct` | 入力equity curveの月内最大ドローダウン |
| `reconciliation_error_bps` | 寄与合計と超過リターンの差。通常は0付近 |

## 計算仕様

日次では前日キャッシュ比率を使い、先読みを避けます。

```text
active_return = strategy_return - benchmark_return
cash_drag = -lagged_cash_weight * benchmark_return
residual_effect = strategy_return - (1 - lagged_cash_weight) * benchmark_return
active_return = cash_drag + residual_effect
```

日次寄与を単純加算すると複利リターンと一致しないため、Carinoの対数リンク係数で各月の算術超過リターンへ再調整します。`reconciliation_error_bps` により再調整誤差を確認できます。

## 入力制約

- `total_equity` と選択ベンチマーク値は正数
- `cash` は0以上かつ `total_equity` 以下
- 日付重複なし
- 先頭観測日は比較元がないためリターン0のベースライン
- リターンは終了観測日の月へ帰属
