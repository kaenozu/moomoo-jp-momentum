# mp20 仮想ペーパートレード受入手順

## 採用する改善候補

複数期間検証で `max_positions=20` は `max_positions=5` を全期間で上回った。一方、rank-weighted sizing は期間依存が強く、stop lossの拡大も改善しなかったため、初回の仮想運用では次だけを変更する。

- `max_positions`: 20
- `stop_loss_pct`: 5.0を維持
- position sizing: equal weightを維持
- idle cash ETF overlay: 仮想運用初期段階では追加しない

## 安全境界

本手順が実行するのはSQLite上の `virtual_*` テーブルを使う仮想売買だけである。

- `paper_trade.enabled=false`
- `paper_trade.allow_market_order=false`
- `paper_trade.jp_api_simulate_supported=false`
- schedulerは生成時に無効化
- 受入ゲートに失敗した場合、日次サイクルは実行しない

## 1. 専用設定を生成

既存のローカル設定を直接上書きせず、専用ファイルを生成する。

```bash
python scripts/create_mp20_paper_config.py \
  --base config.yaml \
  --output config.paper-mp20.yaml
```

既存ファイルを意図的に再生成する場合だけ `--force` を付ける。

## 2. 受入ゲートを確認

```bash
python scripts/paper_trade_readiness.py \
  --config config.paper-mp20.yaml
```

`ready=true` かつ全項目が `PASS` になるまで実行へ進まない。

## 3. 初回はyfinanceで明示実行

```bash
python scripts/paper_trade_readiness.py \
  --config config.paper-mp20.yaml \
  --date YYYY-MM-DD \
  --provider yfinance \
  --execute
```

OpenDとmoomooデータ取得を確認済みの場合は `--provider auto` を使用できる。`--allow-stale` はデータ日付を確認し、古いデータで続行する根拠がある場合だけ付ける。

## 4. 毎回確認する項目

- `virtual_orders`, `fills`, `exits` が候補数・保有上限と整合する
- `virtual_positions` に同一銘柄の重複や数量超過がない
- `virtual_equity_curve` のcashが負になっていない
- `virtual_trade.max_total_positions=20` を超えていない
- 実注文系の `paper_orders` / `paper_fills` に新規行がない

## 未実施の検証

実データDBとOpenDはリポジトリに含まれないため、GitHub Actionsでは本番相当の日次データ取得・仮想約定までは実行しない。初回実行結果を保存してから、月次寄与度と戦略重複分析の数値に基づいて次の改善を判断する。
