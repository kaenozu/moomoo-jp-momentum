# Moomoo日本株モメンタム検証ツール

オルカン等のインデックス投資より上振れを狙える短中期戦術を、少額・ミニ株・1株単位で**検証**するためのツールです。

## 概要

- **目的**: データ取得 → 売買候補抽出 → アプリ内仮想検証 → 人間が確認して手動注文
- **対象**: 日本株、ETF、REIT（東証プライム中心）
- **初期資金想定**: 5万円〜20万円
- **主な比較対象**: primary=1306（TOPIX）、secondary=1321（日経225）、reference=2559（全世界株式）

## 重要な制約

- **自動売買はしません**
- **REAL注文APIは呼びません**
- **moomoo APIはデータ取得専用です**
- 実取引はmoomooアプリで手動実行してください
- 売買記録は `record_trade.py` またはStreamlitの手動売買ログに入力してください
- アプリ内仮想トレードは実注文ではありません

## moomoo API注文について

実機検証の結果、moomoo JP / FUTUJP ではOpenAPI経由の日本株SIMULATE注文が利用できない可能性が高いため、本プロジェクトではJP向けAPI注文機能を使いません。

```text
データ取得：moomoo API
候補抽出：本アプリ
仮想検証：アプリ内仮想トレード
実取引：moomooアプリで手動
手動記録：trades_manual
API注文：未対応・使用禁止
```

`paper_order.py` はUS市場向けexperimentalとしてのみ残っています。日本株では使いません。

## 銘柄ユニバース

`data/symbols.json` では銘柄に以下の属性を持たせます。

- `benchmark`: 比較専用。買い候補・仮想注文対象外
- `trade_candidate`: 実売買候補・仮想注文対象
- `watch_only`: 監視専用。スコアが高くても買い候補にしない
- `excluded`: 対象外

`role=trade_candidate` かつ `tradable=true` かつ価格レンジ内の銘柄だけが買い候補・仮想注文対象です。

初期価格レンジは `config.yaml` の `universe` で設定します。

```yaml
universe:
  min_trade_price: 500
  max_trade_price: 20000
```

## セットアップ

```bash
git clone <リポジトリURL>
cd moomoo-jp-momentum
python -m venv venv
venv\Scripts\activate  # Windows
pip install -r requirements.txt
copy config.example.yaml config.yaml
```

moomoo OpenDを起動し、ポート11111でログイン済みの状態にしてください。

## 実行方法

### 接続テスト

```bash
python test_connection.py
python test_quote.py
```

### 日次更新

benchmark銘柄も取得し、TOPIX等との相対強度を計算します。

```bash
python daily_update.py
python daily_update.py --force
python daily_update.py --dry-run
```

### 候補抽出

```bash
python screen_candidates.py --save --csv --html
```

### Streamlit UI

```bash
streamlit run app.py
```

タブ構成：

- ダッシュボード
- 候補一覧
- 銘柄詳細
- 手動売買ログ
- パフォーマンス
- 事後検証
- 注文について
- 仮想トレード
- 日次運用

### アプリ内仮想トレード

moomooには注文を送信しません。SQLite上で仮想注文・仮想約定・仮想ポジション・仮想cashを管理します。`signals.strategy_name`はシグナル生成アルゴリズム、`virtual_trade.portfolio_name`は仮想取引台帳を表し、別の識別子として扱います。

```bash
python virtual_order.py --from-signals --date 2026-06-30
python virtual_order.py --code JP.7203 --side BUY --quantity 1 --order-type MARKET_SIM --date 2026-06-30
python process_virtual_fills.py --date 2026-07-01
python virtual_order.py --generate-exits --date 2026-07-01
python virtual_order.py --list
python virtual_order.py --positions
python virtual_order.py --list-fills
python virtual_order.py --performance
```

### 日次運用サイクル

```bash
python run_daily_cycle.py --dry-run
python run_daily_cycle.py --date 2026-07-01
```

## 仮想約定仕様

### MARKET_SIM

初期設定では `next_day_open` です。

- BUY: 翌営業日始値 × `(1 + slippage_bps / 10000)`
- SELL: 翌営業日始値 × `(1 - slippage_bps / 10000)`

### LIMIT_SIM

日足ベースの簡易判定です。

- BUY: `low <= limit_price` で約定
- SELL: `high >= limit_price` で約定

日足high/lowのみで判定するため、実際の板・約定順序・スプレッドとは異なります。

## 重要な検証上の注意

- 仮想トレードは戦略検証用の近似です
- 実際の約定価格、板、スプレッド、約定順序、税金、手数料とは異なる可能性があります
- 同日に複数の価格条件が成立した場合の厳密な順序再現はしません
- 実運用ではmoomooアプリで手動注文してください

## 相対強度

`daily_update.py` はベンチマーク銘柄も取得し、`return_5d_vs_benchmark` を計算します。初期ベンチマークは `JP.1306` です。

```yaml
signals:
  relative_strength:
    benchmark_code: "JP.1306"
```

スコアリングでは `return_5d_vs_benchmark` を優先し、欠損時のみ `return_5d` にフォールバックします。

## 主要コマンドまとめ

```bash
python daily_update.py --force
python screen_candidates.py --save --csv --html
python virtual_order.py --from-signals --date YYYY-MM-DD
python process_virtual_fills.py --date YYYY-MM-DD
python virtual_order.py --generate-exits --date YYYY-MM-DD
python virtual_order.py --performance
streamlit run app.py
```

## 運用上の安全策

- `run_daily_cycle.py` はJPX休場日にはOpenDやSQLiteへ接続せず正常スキップします。
- 通常アラートは `--date` で指定した対象日を使用します。
- 運用異常Webhookは既定で無効です。利用時は次を設定します。

```yaml
alerts:
  webhook:
    enabled: true
    url: "https://example.invalid/webhook"
  operational:
    enabled: true
    timeout_seconds: 10
```

運用異常通知はSQLiteへ依存せず、OpenD接続失敗、データ鮮度停止、仮想取引整合性停止、想定外例外、scheduler timeoutを通知対象にします。

## SQLiteバックアップと復元検証

バックアップはDBファイルの単純コピーではなく、SQLite Online Backup APIで一貫したスナップショットを作成します。作成前の元DBと作成後のスナップショットに `PRAGMA quick_check` を実行し、SHA-256、スキーマバージョン、最新仮想約定日、最新equity日をJSONメタデータへ保存します。

```yaml
database_backup:
  enabled: false
  directory: backups
  retain_daily: 7
  retain_weekly: 4
  verify_after_backup: true
```

明示的なバックアップ、検証、世代整理は次のCLIで実行します。

```bash
python database_backup.py --config config.yaml backup --kind daily
python database_backup.py --config config.yaml backup --kind weekly
python database_backup.py --config config.yaml verify backups/<backup>.sqlite3
python database_backup.py --config config.yaml prune --dry-run
python database_backup.py --config config.yaml prune
```

復元は自動実行しません。稼働中DBと異なる未使用パスを指定し、`quick_check`と仮想取引integrity checkerに成功した場合だけ復元ファイルを公開します。検証後の本番DB切り替えは人間が行います。

```bash
python database_backup.py --config config.yaml restore \
  backups/<backup>.sqlite3 data/recovery/moomoo-restored.db \
  --portfolio default --dry-run

python database_backup.py --config config.yaml restore \
  backups/<backup>.sqlite3 data/recovery/moomoo-restored.db \
  --portfolio default
```


## ベンチマークと価格調整

比較役割は `benchmark.primary` / `benchmark.secondary` / `benchmark.reference` から解決します。
初期設定は 1306（TOPIX）/ 1321（日経225）/ 2559（全世界株式）です。
`corporate_actions` に登録した分割係数は、バックテストのベンチマーク価格系列に自動適用されます。
生の `daily_bars` は監査可能性のため書き換えません。異常値は `data_quality_flags` に記録します。

```bash
python scripts/rerun_p2_benchmarks.py --config config.yaml
```
