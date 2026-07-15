# moomoo-jp-momentum 現状メモ

## 現在の到達点

- ユニバースを 30銘柄から 366銘柄へ拡張済み
- 31セクターに分散
- `data/symbols.json` は366銘柄版を本採用済み
- `--max-trade-price 20000` 対応済み
- stale signals 問題は修正済み
- `volume_ratio >= 1.5` の固定ハードゲートが BUY_CANDIDATE 0件の主因と判明
- volume条件はハードゲートから外し、percentile / rank / relative volume によるスコアリング方式へ変更済み
- **daily_bars coverage: 337/366 (92.1%)** — moomoo(127) + yfinance(210)
- **daily_bars に source/turnover_source カラム追加** — moomoo/actual と yfinance/estimated を区別
- **indicators: 337 codes, 最新日付=2026-07-01**
- **BUY_CANDIDATE: 21件** (337 indicators, 2026-07-01)
- **yfinance validation: PASS** (close_corr=0.9999, daily_return_corr=0.9999, MA agree=99.8%)
- 既存テストは 100件パス (2026-07-15確認)
- lint / pyright はクリーン (P0核心ファイル)

## P0完了状況 (2026-07-15)

### P0-1: Backtest accounting audit ✅
- DD計算は正しい (stored_dd == calc_dd)
- `peak_equity` によるproper tracking確認

### P0-2: Missing symbol classification ✅
- 20銘柄すべてdelisted（永続的利用不可）
- 対象: JP.1884, JP.1890, JP.2651, JP.3250, JP.3938, JP.4185, JP.4541, JP.4551, JP.4581, JP.4921, JP.6355, JP.6641, JP.7518, JP.8270, JP.8355, JP.8527, JP.8905, JP.9062, JP.9437, JP.9719

### P0-3: Quota-aware fetch ✅
- `src/quote_service.py`: `get_history_kl_quota()`, `is_code_fetchable()`, quota-aware batch fetch
- `daily_update.py`: `--quota-check`, `--no-quota-aware` オプション追加

### P0-4: Price consistency ✅
- JP 127銘柄すべて遷移点10%以内（正常な日次変動）
- moomoo: 2025-01-06~2026-07-02, yfinance: 2025-01-06~2026-07-09
- 日付範囲が重複していないため、遷移点比較のみ実施

### 検証結果
- Tests: 100 passed, 3 skipped
- Ruff: P0核心ファイル クリーン
- Pyright: 0 errors

### 次のステップ
1. P0変更をcommit/push（チェックポイント）
2. 20 delisted銘柄を無効化 (enabled=0)
3. P1検証作業開始

## 主な変更ファイル

- `src/indicators.py`
  - `add_cross_sectional_stats()` 追加
  - `volume_ratio_percentile`, `volume_ratio_rank`, `relative_volume_ratio`, `market_median_volume_ratio`
- `src/signals.py`
  - `volume_ratio` の固定ハードゲートを廃止
  - `config.signals.volume.hard_gate: true` の場合は旧挙動を維持
- `src/scoring.py`
  - 出来高スコアを絶対値 + percentile のハイブリッド方式へ変更
- `src/screener.py`
  - シグナル判定前にメモリ内でクロスセクション統計を計算
- `src/backtest_runner.py`
  - バックテスト時も日付Dごとに全候補指標を集める
  - `add_cross_sectional_stats()` で日付D内の volume percentile / rank / relative を計算
  - enriched indicators で `strategy.evaluate()` と注文生成を実行
  - 未来データは使わない
- `universe_diagnostics.py`
  - 3ブランチのファネル分析を追加（旧 volume hard gate / 新 volume percentile / volume条件なし）
- `config.yaml`
  - `signals.volume` セクション追加
- `data/moomoo.db` schema migration
  - `daily_bars` に `source` / `turnover_source` カラム追加
  - moomoo行: source='moomoo', turnover_source='actual'
  - yfinance補完行: source='yfinance', turnover_source='estimated'
- `scripts/yf_validate.py`
  - yfinance vs moomoo validation (127 codes)
- `scripts/yf_supplement.py`
  - 239銘柄のyfinance補完（22銘柄はdelisted/not found）
- `scripts/recalc_indicators.py`
  - indicators再計算 + relative strength + DB保存

## API制限

moomoo OpenD / K-line 取得で以下の制限あり。

- 購読枠 100銘柄 → historyモードでは回避済み
- レート制限 60 req / 30 sec → 1秒delayで対策済み
- 履歴K-line枠 **100 stocks/week** → 未解決。366銘柄全取得には複数週かかる可能性あり
- **現状**: moomoo(127) + yfinance(210) = 337/366 (92.1%) まで補完済み

## 7/8以降の手順

履歴K-line枠が回復したら、不足29銘柄をmoomooで補完する。

```bash
uv run pytest

# 不足29銘柄のみmoomooで再取得（上書きしない）
python daily_update.py --mode history --start 2025-01-01 --batch-size 80

# indicators再計算 + relative strength + 診断 + バックテスト
python scripts/recalc_indicators.py
python screen_candidates.py --date 2026-07-08 --save --csv --html
python universe_diagnostics.py --date 2026-07-08 --csv
python historical_backtest.py --from 2026-05-21 --to 2026-06-30 --strategy all --csv
```

## 段階的比較計画

履歴K-line枠の制限があるため、以下の段階で比較する。

- 100銘柄版
- 200銘柄版
- 300銘柄版
- 366銘柄完全版

各段階で以下を記録する。

- `coverage_code_count`
- `indicator_code_count`
- `BUY_CANDIDATE count`
- `WATCH count`
- `EXCLUDE count`
- `trade_count`
- `total_return_pct`
- `excess_return_vs_2559`
- `excess_return_vs_1306`
- `stop_loss count`
- `profit_factor`
- `max_drawdown_pct`

## 判定基準

BUY候補数が増えること自体は目的ではない。最重要指標は、2559 / 1306 に対する超過リターンが改善するか。

- **改善**: 2559 / 1306 との差が縮む
- **合格候補**: `quality_low_risk` または `momentum` が 2559 / 1306 比で -1%以内
- **有望**: どちらかが 2559 / 1306 を上回る
- **失敗**: BUY候補は増えたが、stop_loss と drawdown も増える

## バックテスト結果（暫定: 337 codes, yfinance補完版）

| Strategy | Return | Excess vs 2559 | Excess vs 1306 | Trades | Stop Loss |
|---|---|---|---|---|---|
| momentum | +1.57% | -1.15% | -2.05% | 5 | 3 (60%) |
| quality_low_risk | +0.74% | -1.98% | -2.88% | 3 | 1 (33%) |
| etf_rotation | -6.31% | -9.02% | -9.93% | 8 | 2 (25%) |

※ yfinance補完データによる暫定結果。moomoo実データで再検証推奨。

## momentum診断結果（2026-07-01分析）

### 5トレード明細

| Code | Entry | Exit | Ret | PnL | Days | Reason |
|---|---|---|---|---|---|---|
| JP.7186 | 5/21 | 5/28 | -6.45% | -109 | 7 | stop_loss |
| JP.2874 | 5/21 | 6/02 | -5.81% | -153 | 12 | stop_loss |
| JP.4543 | 5/21 | 6/12 | -3.93% | -92 | 22 | stop_loss |
| JP.6178 | 5/21 | 6/26 | +2.51% | +53 | 36 | ma25_cross |
| JP.4631 | 5/21 | 6/29 | +6.39% | +296 | 39 | ma25_cross |

### exit_reason別

| Reason | Trades | Win | Loss | WinRate | AvgRet | TotalPnl |
|---|---|---|---|---|---|---|
| stop_loss | 3 | 0 | 3 | 0% | -5.40% | -354 |
| ma25_cross | 2 | 2 | 0 | 100% | +4.45% | +349 |

### 負け要因分解

1. **cash drag（最大要因）**: 平均現金比率86.8%。29日間すべてでcash>80%。2559上昇日に pos=0 が複数日。見積もり損失: 2.36%
2. **entry selection**: 3/5敗北。ただしJP.4631(DIC)が+6.39%と大きく挽回。総PnLは-5円とほぼトントン
3. **exit timing**: stop_loss後のリバウンドあり。+0.8%~+3.4%を5日以内に取り戻している
4. **position sizing**: 全銘柄均等保有（size=1）。勝敗による配分差なし。問題なし

### 結論
- **主因は cash drag（86.8%現金）**: 5ポジション上限 × 平均3,000円 = 15,000円 / 100,000円
- 選択した銘柄自体は（entry selection不良にもかかわらず）総PnLほぼ±0
- **2559 benchmarkデータに異常値**: 6/9が+878.97%（ETF価格調整によるものと推測）
- **改善方向**: max_positions増加, min_trade_price引下げ, or 背景資金を2559待機枠に配分

## 資金効率比較結果（2026-07-01）

### max_positions比較

| max_pos | Return | vs2559 | vs1306 | Cash% | Trades | StopLoss | DD% |
|---|---|---|---|---|---|---|---|
| **5** | +1.57% | -1.15% | -2.05% | 87% | 5 | 3 | 0.2% |
| **10** | +2.38% | -0.33% | -1.24% | 64% | 6 | 3 | 0.8% |
| **15** | +2.69% | -0.03% | -0.93% | 46% | 14 | 6 | 0.6% |
| **20** | **+6.51%** | **+3.80%** | **+2.89%** | 26% | 19 | 10 | 0.3% |

### idle cash allocation比較（mp=5）

| Idle Mode | Return | vs2559 | vs1306 |
|---|---|---|---|
| cash | +1.57% | -1.15% | -2.05% |
| **2559** | **+3.92%** | **+1.21%** | **+0.30%** |
| **1306** | **+4.71%** | **+2.00%** | **+1.09%** |

### 最良シナリオ: mp20 + idle in 1306
- Return: **+7.43%**
- vs2559: **+4.72%**
- vs1306: **+3.81%**
- DD: 0.3%

### benchmark異常診断結果（2026-07-01）

**検出された異常値:**
- JP.2559: 2026-06-05 (-89.97%), 2026-06-09 (+878.97%) — ETF価格調整
- JP.2558: 2026-06-05 (-90.04%), 2026-06-09 (+905.88%) — 同上
- JP.1306: 異常なし
- JP.1365: 異常なし

**補正結果（異常日を除去）:**
- 2559 raw return: +2.71% → clean: +1.61% (-1.11%)
- 1306 raw return: +3.62% → clean: +2.72% (-0.90%)
- momentum excess vs 2559: raw -1.15% → clean -0.04%
- momentum excess vs 1306: raw -2.05% → clean -1.15%

**判定: 2559比 -1.15% は benchmark 異常値で歪んでいた**
- 補正後の excess vs 2559 は -0.04%（ほぼトントン）
- benchmark異常値の excess への影響: -1.11%
- cash drag が主因であることは変わらないが、その影響幅は当初推定より小さい
- 今後の benchmark 比較では異常日を除外した evaluate を推奨

## 現時点の評価

- 実弾投入: まだ不可
- ペーパートレード: 継続
- アプリ評価: お蔵入りではない
- 現在の主目的: 戦略検証基盤
- 次の勝負: 366銘柄フル取得後のバックテストと指数比較

## 資金効率比較結果（2026-07-01）

### max_positions比較

| max_pos | Return | vs2559 | vs1306 | Cash% | Trades | StopLoss | DD% |
|---|---|---|---|---|---|---|---|
| **5** | +1.57% | -1.15% | -2.05% | 87% | 5 | 3 | 0.2% |
| **10** | +2.38% | -0.33% | -1.24% | 64% | 6 | 3 | 0.8% |
| **15** | +2.69% | -0.03% | -0.93% | 46% | 14 | 6 | 0.6% |
| **20** | **+6.51%** | **+3.80%** | **+2.89%** | 26% | 19 | 10 | 0.3% |

### idle cash allocation比較（mp=5）

| Idle Mode | Return | vs2559 | vs1306 |
|---|---|---|---|
| cash | +1.57% | -1.15% | -2.05% |
| **2559** | **+3.92%** | **+1.21%** | **+0.30%** |
| **1306** | **+4.71%** | **+2.00%** | **+1.09%** |

### 最良シナリオ: mp20 + idle in 1306
- Return: **+7.43%**
- vs2559: **+4.72%**
- vs1306: **+3.81%**
- DD: 0.3%

## mp20複数期間検証結果（2026-07-01）

### 再現性（Period A: 2026-05-21〜06-30）
- mp5: 前回+1.57% → 今回+1.59%（一致）
- mp20: 前回+6.51% → 今回+6.62%（一致）

### コスト感応度（mp20, Period A）
- none: +6.72%, コスト0
- conservative(5bps): +6.19%, コスト0.43%
- severe(10bps): +5.66%, コスト0.85%
→ コストの影響は軽微（最大0.85%）

### 複数期間（mp20, conservative cost）

| Period | Return | vs2559 | Cash% | Trades | Stops | DD% |
|--------|--------|--------|-------|--------|-------|-----|
| A(5/21-6/30) | +6.19% | +3.48% | 26% | 19 | 10 | 0.3% |
| B(1/1-3/31) | +10.63% | +14.85% | 14% | 58 | 14 | 0.5% |
| C(4/1-6/30) | +2.25% | -11.71% | 28% | 77 | 37 | 6.0% |
| D(1/1-6/30) | +7.21% | -5.24% | 23% | 146 | 57 | 0.5% |

### 判定
- **mp20はmp5より全期間で優位**: 再現性あり、コスト影響軽微
- **mp20が2559を上回るのは2/4期間**: 強気相場（特に全体的な上昇相場）では2559に劣後する
- **Period Cは要注意**: 37stops/77trades、DD6.0%。過剰売買気味
- **現時点の結論: mp20を次期標準設定候補としてよいが、相場環境依存が残る**
- idle cash ETF allocationはoptional overlayとして別枠扱い

## Period C 負け要因診断（2026-07-01）

### mp20のPeriod C（2026-04-01〜06-30）結果
- net return: +3.08%（2559 clean: +13.96%）
- excess vs 2559: -10.89%
- trades: 77, stop_loss: 37（48.1%）, ma25_cross: 40
- win rate: 14.3%, avg holding: 14.5日
- max drawdown: 6.0%, avg cash: 28%

### stop_loss分析
- 37件中19件（51%）が5日以内にリバウンド → やや早すぎる可能性
- 損切り集中sector: 食品(5), 電気機器(5), サービス(4)
- stop_loss総損: -8,578円、ma25_cross損: -2,320円（合算: -6,258円ネット）

### mp15 vs mp20（Period C比較）
- mp15: +0.91%, trades=52, stops=23, cash=46%
- mp20: +3.08%, trades=77, stops=37, cash=28%
- mp20がmp15を上回った（+3.08% vs +0.91%）→ 買いすぎではない
- ただしstop_loss率は48.1%と高く、改善余地あり

### 主因
1. **2559自体が+13.96%と非常に強い相場** — 個別株モメンタム戦略が相対的に負ける環境
2. **stop_loss率48.1%** — 取引の半数近くが損切り、平均リターンを押し下げ
3. **cash drag（+3.91%の機会損失）** — 28%現金はまだ大きい
4. **低ランク候補（rank 16-20）の品質低下** — fwd5d=-2.56%

### 結論
- Period Cの主因はentry/exitルールの問題ではなく、**相場環境要因（2559急騰）**
- mp20はmp15より優位（+3.08% vs +0.91%）
- stop_lossがやや早いが、37件中19件リバウンド程度なら許容範囲
- 次の改善候補: stop_loss幅拡大、rank-weighted sizing、idle cash ETF配分

## Rank-weighted sizing 比較結果（2026-07-01）

### Period C での改善効果
| Pattern | Return | vs2559 | R16-20貢献 | 備考 |
|---------|--------|--------|------------|------|
| equal_weight | +3.51% | -10.45% | -0.34% | baseline |
| mild_rank_weight | +4.02% | -9.94% | -0.14% | △+0.51% |
| **strong_rank_weight** | **+4.59%** | **-9.37%** | -0.06% | **△+1.08%** |
| rank16_20_reduced | +3.41% | -10.55% | -0.17% | △-0.10% |
| rank16_20_cutoff | +3.31% | -10.65% | +0.00% | △-0.20% |

### 重要発見
- **rank 16-20 の品質は期間によって異なる**: Period Bでは好成績(+3.75%), Period Cでは悪化(-0.34%)
- **strong_rank_weight が Period C で最良**だが、改善幅は+1.08%に留まる
- **Period D では rank 16-20 が最も好成績**だったため、weightingが逆効果
- **rank cutoff は逆効果** (候補を減らすことでcash drag増加)
- **結論: 現時点では equal_weight 維持が安全。rank-weighted は period 依存性が強い**

### 診断で見る詳細項目

**per-trade breakdown:**
code, name, entry_date, entry_price, exit_date, exit_price, holding_days, position_size, return_pct, realized_pnl, exit_reason, entry_score, volume_ratio_percentile, return_5d, return_20d, close_vs_ma25, ma5_vs_ma25

**exit_reason別:**
exit_reason, trades, win_rate, avg_return, total_pnl

**負け要因分解:**
- entry selection: 買った5銘柄の選択が悪かったか
- exit timing: 売るタイミングが悪かったか
- position sizing: 勝ち銘柄に小さく負けに大きく張っていないか
- cash drag: 現金比率が高く指数上昇についていけなかったか

**executed vs non-executed比較:**
平均score, 平均return_5d, 平均return_20d, 平均volume_percentile, その後の平均リターン

**equity curve:**
date, strategy_equity, benchmark_2559, benchmark_1306, daily_excess, cash_pct, positions_count, drawdown_pct

## Stop_loss幅診断結果（2026-07-01）

### Period C比較

| 設定 | Return | vs2559 | Trades | Stops | SL% | DD% |
|------|--------|--------|--------|-------|-----|-----|
| -5% (current) | +3.51% | -10.46% | 77 | 37 | 48% | 6.0% |
| -8% | +1.85% | -12.12% | 65 | 13 | 20% | 6.6% |
| -10% | +2.09% | -11.87% | 65 | 6 | 9% | 6.8% |
| no_stop_loss | +3.81% | -10.15% | 59 | 0 | 0% | 6.5% |

### リバウンド再検証（重要）
- 5日以内にentry_priceまで回復: 14%（← 以前「51%リバウンド」は誤り）
- 10日以内にentry_priceまで回復: 16%
- Median rebound%: -6.79%（大部分はリバウンドしても損失）
- **stop_lossは正当**（84%は止めて正解）

### 結論
- stop_loss幅拡大はPeriod Cで改善せず（むしろ悪化）
- -5% currentがbest balance
- **stop_lossルール変更は不要**
- Period Cの劣後主因は相場環境とcash drag、stop_loss幅の問題ではない

## moomoo実データ再取得準備状況（2026-07-01）

### DBカバレッジ
| 項目 | 値 |
|---|---|
| total symbols | 366 |
| daily_bars codes | 337 (92.1%) |
| moomoo | 127 codes, 23,612 rows |
| yfinance | 210 codes, 76,029 rows |
| missing | 29 (20 delisted tc + 6 wo + 3 ex) |

### moomoo quota状況
- `get_history_kl_quota()` returns (100, 0, [])
- 実際のfetch: "Insufficient historical K-line quota (stock: 100/100)" — リトライ3回後も失敗
- 結論: quotaは既に消費済み。消費分は7日後に自動解放
- 前回fetchから約7日経過しているが、quota解放タイミングがまだ来ていない可能性

### 再取得優先リスト
- `reports/moomoo_refetch_priority_codes_20260701.csv` に210銘柄の優先順位付きリスト作成済み
- Priority 1 (traded): 85 codes
- Priority 2 (buy_candidate): 5 codes
- Priority 4 (other): 120 codes
- 次回quota reset後に最初の80銘柄から取得

### 次回quota reset後の手順
```bash
# 80銘柄再取得（moomoo優先、INSERT OR IGNOREで既存は上書きしない）
python daily_update.py --mode history --start 2025-01-01 --batch-size 80

# indicators再計算
python scripts/recalc_indicators.py

# 診断
python screen_candidates.py --date <today> --save --csv
python universe_diagnostics.py --date <today> --csv
python historical_backtest.py --from 2026-05-21 --to 2026-06-30 --strategy all --csv
```
