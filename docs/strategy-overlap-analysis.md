# 戦略重複度分析

## 目的

`momentum` と `quality_low_risk` など、2つのバックテストrunが実際にどの程度同じリスクを取っているかを確認します。単に最終リターンを比較するのではなく、次の4層で重複を測定します。

1. 期間中に買った銘柄集合
2. 同一銘柄・同一日のentry event
3. 各日の日末保有銘柄集合
4. 日次equity returnの相関

この分析により、名称が異なる戦略でも実質的に同じ銘柄を同じ時期に保有しているのか、または異なる候補選択・タイミングを持つのかを判定できます。

## 実行方法

各strategyの最新runを比較:

```bash
python scripts/strategy_overlap_analysis.py
```

strategy名を明示:

```bash
python scripts/strategy_overlap_analysis.py \
  --strategy-a momentum \
  --strategy-b quality_low_risk
```

run IDを固定して再現可能に比較:

```bash
python scripts/strategy_overlap_analysis.py \
  --run-a 101 \
  --run-b 102
```

別DB・出力先を指定:

```bash
python scripts/strategy_overlap_analysis.py \
  --db data/moomoo.db \
  --run-a 101 \
  --run-b 102 \
  --output-dir reports/strategy-overlap
```

## 出力

- `*_summary.csv`: 全体指標
- `*_daily.csv`: 日別の保有銘柄数・共通銘柄・Jaccard係数
- `*_symbols.csv`: 売買銘柄単位の共通性と初回entry日
- `*_entries.csv`: 銘柄×entry日単位の完全一致

## 主要指標

| 指標 | 意味 |
| --- | --- |
| `traded_symbol_jaccard_pct` | 期間中に買った銘柄集合のJaccard係数 |
| `exact_entry_jaccard_pct` | 同一銘柄・同一entry日のevent集合Jaccard係数 |
| `avg_daily_holdings_jaccard_pct` | 日末保有銘柄集合の平均Jaccard係数 |
| `avg_overlap_coefficient_pct` | 両戦略が保有中の日に、小さい側の何割が共通か |
| `daily_return_correlation` | 共通期間の日次equity return相関 |

Jaccard係数は `共通 / 和集合` です。銘柄数が大きく異なる場合は、`avg_overlap_coefficient_pct` も併用してください。

## 計算上の注意

- 保有数量は `backtest_fills` のBUY/SELLを日付順に累積して復元
- 当日のfillは当日の日末保有へ反映
- 共通する `backtest_equity_curve.date` のみ比較
- SELLがBUYを上回り負の保有数量になるrunはデータ不整合として停止
- 同じ銘柄でもentry日が異なる場合、銘柄重複には含むが完全entry重複には含めない
- 両戦略ともポジション0の日は、Jaccard平均の対象外

## 判定の使い方

- 保有Jaccardとreturn相関がともに高い: 戦略分散効果が小さい可能性
- 銘柄Jaccardは高いがentry Jaccardが低い: 銘柄選択は近いがタイミングが異なる
- 銘柄Jaccardは低いがreturn相関が高い: 共通の市場・sector betaを取っている可能性
- すべて低い: 組み合わせによる分散余地があるが、単体品質は別途確認が必要
