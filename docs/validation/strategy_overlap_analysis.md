# 戦略重複分析

## 目的

`momentum` と `quality_low_risk` が実質的に同じリスクを取っているか、組み合わせで分散効果が得られるかを、永続化済みバックテストから確認する。

月別寄与度分析で劣後月を特定した後、本分析で次を比較する。

- 日次リターン相関と下落日の一致
- 同一銘柄・同一シグナル日のエントリー重複
- 同一銘柄・同一月のエントリー重複
- 採用銘柄集合と日次保有銘柄の重複
- 日次50/50リバランス時のリターンと最大ドローダウン

## 実行方法

既定では各戦略の最新 `momentum` / `quality_low_risk` runを比較する。

```bash
python scripts/strategy_overlap_analysis.py
```

戦略名を明示する場合:

```bash
python scripts/strategy_overlap_analysis.py \
  --strategy-a momentum \
  --strategy-b quality_low_risk
```

特定のrun同士を比較する。

```bash
python scripts/strategy_overlap_analysis.py \
  --run-a-id 123 \
  --run-b-id 124 \
  --output-dir reports/strategy-overlap
```

標準以外のSQLite DBを使う場合は `--db` を指定する。

## 出力

出力ファイル名には比較したrun IDが入る。

- `summary_run_<A>_vs_<B>.csv`
- `daily_overlap_run_<A>_vs_<B>.csv`
- `symbol_overlap_run_<A>_vs_<B>.csv`
- `entry_overlap_run_<A>_vs_<B>.csv`

### サマリー指標

| 指標 | 意味 |
| --- | --- |
| `daily_return_correlation` | 共通観測日間で計算した日次リターン相関 |
| `same_direction_days_pct` | 両戦略の騰落方向が一致した日数の比率。0%リターンは非下落として扱う |
| `negative_day_jaccard_pct` | 下落日集合のJaccard係数 |
| `exact_entry_jaccard_pct` | 同一銘柄・同一シグナル日のBUY集合のJaccard係数 |
| `code_month_entry_jaccard_pct` | 同一銘柄・同一シグナル月のBUY集合のJaccard係数 |
| `symbol_jaccard_pct` | 比較期間内にBUYした銘柄集合のJaccard係数 |
| `avg_holdings_jaccard_pct` | 少なくとも一方が保有していた日の保有銘柄Jaccard平均 |
| `avg_holdings_overlap_coefficient_pct` | 両方が保有していた日に、小さい側の保有集合の何割が共通か |
| `combined_50_50_return_pct` | 各共通観測日で50/50へ戻す仮想ポートフォリオの複利リターン |
| `combined_50_50_max_drawdown_pct` | 上記50/50ポートフォリオの最大ドローダウン |

## 計算上の注意

- リターンは両runに存在する共通観測日を先に揃え、その共通観測日間で計算する。片方だけ営業日が欠けても異なる期間のリターン同士を比較しない。
- エントリー重複は共通比較期間内に約定したBUYだけを対象にする。保有重複の再構築には比較開始前の約定も反映する。
- 50/50結果は日次リバランスの仮想値であり、手数料・税金・追加スリッページは含まない。
- Jaccard係数は両集合が空の場合に未定義となるため、CSVでは空欄になる。
- 未対応side、0以下の数量、SELL超過による負の復元在庫はデータ不整合として停止する。

## 判断の目安

以下は採否を自動決定する閾値ではなく、追加調査を始めるための目安とする。

- リターン相関・保有重複・エントリー重複がすべて高い場合、2戦略を別枠運用しても分散効果は小さい。
- エントリー重複は高いが保有重複が低い場合、主な差は退出タイミングにある可能性が高い。
- 銘柄重複は低いが下落日重複が高い場合、セクターや市場βなど共通因子を追加分析する。
- 50/50で単独戦略より最大ドローダウンが下がらない場合、併用より戦略ルールの改善を優先する。
