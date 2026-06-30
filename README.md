# Moomoo日本株モメンタム検証ツール

オルカン等のインデックス投資より上振れを狙える短中期戦術を、少額・ミニ株・1株単位で検証するためのツールです。

## 概要

- **目的**: リアルタイム監視 → 売買候補の抽出 → 通知/画面表示 → 人間が確認して手動注文
- **対象**: 日本株、ETF、REIT（東証プライム中心）
- **取引単位**: 1株単位（単元未満株）
- **初期資金想定**: 5万円〜20万円
- **ベンチマーク**: MAXIS全世界株式（オール・カントリー）2559

## 重要な制約

- **自動売買は禁止**。あくまで監視・候補抽出・通知のみ
- **実注文機能はMVPに含まれません**
- **投資判断はユーザーが行う前提**です
- 「必ず儲かる」「買うべき」などの断定表現は一切使用しません

## 前提条件

### 1. moomoo OpenD

[moomoo OpenD](https://www.moomoo.com/download/opend)をインストールし、起動してください。

```
1. moomoo OpenDをダウンロード
2. インストール
3. moomooアカウントでログイン
4. ポート11111でリッスンしていることを確認
```

### 2. Python環境

- Python 3.11以上
- venvの作成を推奨

### 3. moomoo API SDK（futu-api）

moomoo公式ドキュメントでは `moomoo` パッケージと `futu-api` パッケージが混在していますが、本ツールでは **`futu-api`** を採用しています。

- **公式リポジトリ**: [FutunnOpen/py-futu-api](https://github.com/FutunnOpen/py-futu-api)
- **インストール**: `pip install futu-api`
- **import方法**: `from futu import *`

moomooパッケージ（`pip install moomoo`）は互換性がある場合もありますが、本ツールでは `futu-api` に統一しています。公式ドキュメントのサンプルで `from moomoo import *` を見かけますが、本ツールのコードでは使用しません。

### 4. 行情カード（MVPでは不要）

**現時点では有料行情カードは不要です。**

テスト結果、行情カードなしでも以下が取得できています：
- スナップショット（現在値・出来高・売買代金）
- 日足データ（過去120営業日）

ただし、以下は未検証です：
- リアルタイム性（遅延の有無）
- 板情報（LV2/LV3）
- ティック情報
- プッシュ配信

将来、板読み・ティック分析・高速監視を行う場合のみ、有料行情カードを再検討してください。

[相場ストア](https://qtcard.moomoo.com/index/cards-mall?variety=1&marketId=15&clientlang=0)

## セットアップ

### 1. リポジトリのクローン

```bash
git clone <リポジトリURL>
cd moomoo
```

### 2. 仮想環境の作成

```bash
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # macOS/Linux
```

### 3. 依存関係のインストール

```bash
pip install -r requirements.txt
```

### 4. 設定ファイルの作成

```bash
copy config.example.yaml config.yaml
```

`config.yaml`を編集してください。特にOpenDの接続先を確認してください。

### 5. 銘柄リストの確認

`data/symbols.json`に初期銘柄リストが含まれています。必要に応じて編集してください。

## 実行方法

### 1. OpenD接続テスト

```bash
python test_connection.py
```

接続に成功すると、JP.7203（トヨタ自動車）のスナップショットが表示されます。

### 2. 相場データ取得テスト

```bash
python test_quote.py
```

以下のテストが実行されます：
- 1銘柄のスナップショット取得
- 複数銘柄のスナップショット取得
- 日足データ取得
- SQLiteへの保存
- 保存データの検証

### 3. 日次更新（指標計算）

```bash
python daily_update.py              # 全銘柄を更新
python daily_update.py --force      # 強制再取得（スキップしない）
python daily_update.py --dry-run    # テスト実行（API呼び出しなし）
```

以下の処理が実行されます：
- 全銘柄の日足データ取得（直近120営業日）
- 5日/25日移動平均線の計算
- 20営業日高値・平均出来高の計算
- 前日比・5日リターンの計算
- SQLiteへの保存
- CSV出力（`reports/indicators_YYYYMMDD.csv`）

### 4. 候補一覧表示（フェーズ3）

```bash
python screen_candidates.py                    # 最新データで実行
python screen_candidates.py --date 2026-06-30  # 指定日で実行
python screen_candidates.py --csv              # CSV出力
python screen_candidates.py --html             # HTML出力
python screen_candidates.py --top 10           # 上位10件のみ表示
python screen_candidates.py --save             # signalsテーブルに保存
```

### 5. 銘柄リストのDB読み込み（オプション）

```python
from src.config import load_config
from src.data_store import DataStore

config = load_config("config.yaml")
data_store = DataStore(config)
data_store.load_symbols_from_json("data/symbols.json")
```

### 6. Streamlit画面（フェーズ5）

```bash
streamlit run app.py
```

ブラウザで http://localhost:8501 が開きます。

タブ構成：
- ダッシュボード: サマリー・データ鮮度・ポートフォリオ
- 候補一覧: シグナル判定結果・フィルタ・CSV出力
- 銘柄詳細: 日足チャート・シグナル履歴・売買ログ
- 手動売買ログ: 売買記録の入力・一覧表示
- パフォーマンス: 損益・勝率・ベンチマーク比較
- 事後検証: シグナルの検証結果
- **ペーパートレード**: SIMULATE環境での仮想取引（実注文ではありません）

### 7. ペーパートレード（フェーズ5.5）

moomoo OpenAPIのSIMULATE環境を使った仮想取引です。
**実資金は使用しません。TrdEnv.REAL は使用しません。**

```bash
# テスト実行
python test_paper_trade.py

# CLIでペーパートレード
python paper_order.py --code JP.7203 --side BUY --quantity 1 --price 2700 --order-type LIMIT
python paper_order.py --list-orders
python paper_order.py --positions
python paper_order.py --cancel --order-id XXXXX
```

注意：
- `paper_trade.enabled` はデフォルトで `false` です
- **moomoo JP / FUTUJP では、OpenAPI経由の日本株SIMULATE注文が利用できません**
- アプリ内デモ取引とAPI SIMULATEは別物です
- 取引はmoomooアプリで手動実行してください

### 8. アラート送信

```bash
python send_alerts.py
```

### 8. 定期実行（フェーズ5）

```bash
python scheduler.py              # 起動
python scheduler.py --dry-run    # テスト実行
python scheduler.py --list       # ジョブ一覧表示
```

注意: `scheduler.enabled` はデフォルトで `false` です。
設定ファイルで `scheduler.enabled: true` にしてください。

### 9. レポート一括生成

```bash
python generate_reports.py              # 日次レポート
python generate_reports.py --weekly     # 週次レポート
```

## ディレクトリ構成

```
moomoo/
├── README.md
├── requirements.txt
├── config.example.yaml
├── config.yaml              # ユーザー設定（git対象外）
├── app.py                   # Streamlit Web UI
├── test_connection.py       # OpenD接続テスト
├── test_quote.py            # 相場取得テスト
├── daily_update.py          # 日次更新（指標計算）
├── screen_candidates.py     # 候補一覧表示
├── record_trade.py          # 手動売買記録
├── performance_report.py    # パフォーマンスレポート
├── send_alerts.py           # アラート送信
├── scheduler.py             # 定期実行スケジューラ
├── generate_reports.py      # レポート一括生成
├── diagnose_data_freshness.py  # データ鮮度診断
├── src/
│   ├── __init__.py
│   ├── config.py            # 設定管理
│   ├── models.py            # データモデル・DBスキーマ
│   ├── connection.py        # OpenD接続管理
│   ├── quote_service.py     # 相場データ取得
│   ├── data_store.py        # SQLite保存
│   ├── indicators.py        # テクニカル指標計算
│   ├── signals.py           # シグナル判定
│   ├── scoring.py           # スコアリング
│   ├── screener.py          # 候補抽出
│   ├── trade_log.py         # 手動売買ログ管理
│   ├── benchmark.py         # ベンチマーク管理
│   ├── performance.py       # パフォーマンス評価
│   ├── data_freshness.py    # データ鮮度ガード
│   └── alerts.py            # アラート管理
├── data/
│   ├── symbols.json         # 銘柄リスト
│   └── moomoo.db            # SQLite（実行時に作成）
└── reports/                 # レポート出力先
    ├── indicators_YYYYMMDD.csv
    ├── signals_YYYYMMDD.csv
    ├── signals_YYYYMMDD.html
    ├── performance_YYYYMMDD.csv
    ├── performance_YYYYMMDD.html
    └── alerts_YYYYMMDD.txt
```

## 指標計算結果

`daily_update.py`で計算される指標は以下の通りです：

| 指標 | 説明 | 計算方法 |
|------|------|----------|
| close | 終値 | 直近の終値 |
| ma5 | 5日移動平均線 | 直近5営業日の終値平均 |
| ma25 | 25日移動平均線 | 直近25営業日の終値平均 |
| ma5_deviation | MA5乖離 | 現在値 - MA5 |
| ma25_deviation | MA25乖離 | 現在値 - MA25 |
| volume | 出来高 | 当日の出来高 |
| volume_ma20 | 20日平均出来高 | 直近20営業日の出来高平均 |
| volume_ratio | 出来高比率 | 当日出来高 / 20日平均出来高 |
| turnover | 売買代金 | 当日の売買代金 |
| high_20d | 20日高値 | 直近20営業日の最高値 |
| high_20d_distance | 高値距離 | (現在値 - 20日高値) / 20日高値 × 100 |
| prev_close | 前日終値 | 前営業日の終値 |
| daily_return | 前日比 | (現在値 - 前日終値) / 前日終値 × 100 |
| return_5d | 5日リターン | (現在値 - 5日前終値) / 5日前終値 × 100 |

## 銘柄コード形式

本ツールでは、moomoo APIの銘柄コード形式を統一しています：

- **日本株**: `JP.7203`（トヨタ自動車）
- **日本ETF**: `JP.1306`（TOPIX連動ETF）

`data/symbols.json` もこの形式で記述してください。

## 取引時間（東証現物市場）

- **前場**: 9:00〜11:30
- **後場**: 12:30〜15:30（2024年11月5日以降）
- クロージング・オークションは15:30に実施

### MVPの制限

- 祝日・休場日・臨時休場には完全対応しません
- 平日のみ、指定時間帯のみの簡易判定です
- 取引時間外のデータ取得はスキップされます

## データベーステーブル

| テーブル | 用途 |
|---------|------|
| `symbols` | 銘柄リスト |
| `quotes` | リアルタイム株価 |
| `daily_bars` | 日足 |
| `intraday_bars` | 分足 |
| `signals` | シグナル |
| `trades_manual` | 手動売買ログ |
| `benchmark_prices` | ベンチマーク価格 |
| `performance_snapshots` | ポートフォリオスナップショット |

## ベンチマーク

### 第一ベンチマーク
- **2559** MAXIS全世界株式（オール・カントリー）上場投信

### 補助ベンチマーク
- **1306** TOPIX連動ETF
- **1300** 日経平均連動ETF
- **2558** MAXIS米国株式（S&P500）上場投信

## API発注可否について

現時点では、moomoo OpenD API経由での日本株・単元未満株注文の可否が未確認です。

### 確認すべき事項

1. 日本株で数量1株の注文がAPIから通るか
2. 100株未満の注文がAPIから通るか
3. 成行のみか、指値も可能か
4. 特定口座/NISA口座の指定可否
5. 本番口座とデモ口座で仕様差があるか
6. API上の最小注文数量が銘柄ごとにどう扱われるか
7. APIレスポンス上で、単元未満株注文として識別できるか

### 確認方法

1. moomoo証券サポートへの確認
2. デモ口座またはSIMULATE環境での実機テスト

**確認が完了するまで、API発注機能は実装しません。**

## 既知の制限

1. **祝日・休場日対応**: 祝日・臨時休場には完全対応していません。平日のみの簡易判定です。
2. **取引時間外**: 日足更新は取引時間外でも実行可能です。リアルタイム監視のみ取引時間内です。
3. **データ遅延**: LV2行情の場合、多少の遅延が発生する可能性があります。
4. **購読枠**: 資産額に応じて購読枠が異なります（100〜2000枠）。
5. **過去データ**: 過去ローソク足は7日間で100〜2000銘柄まで取得可能です。
6. **為替**: MVPでは日本株のみを対象とするため、為替データは扱いません。
7. **データ不足**: 25営業日未満のデータしかない銘柄はシグナル判定対象から除外されます。
8. **自動発注**: API発注機能は未実装です。手動発注を前提としています。

## シグナル判定ルール

### 買い候補（BUY_CANDIDATE）

以下をすべて満たす場合：

- close > MA5
- close > MA25
- MA5 > MA25（上昇トレンド）
- 20日高値から5%以内
- 5日リターン > 0%
- 出来高比率 >= 1.2倍
- 売買代金 >= 10億円

### 監視候補（WATCH）

以下のような場合：

- close > MA25 だが MA5 <= MA25
- リターンは良いが出来高不足
- 20日高値圏だが出来高不足
- トレンド良好だが売買代金やや不足

### 除外（EXCLUDE）

以下の場合：

- close < MA25
- 25営業日未満
- 出来高が0またはNULL
- 売買代金が著しく不足
- 5日リターンが大きくマイナス

### リスク警告

買い候補であっても警告を付ける場合：

- 当日リターン >= 8%（急騰警告）
- 5日リターン >= 15%（過熱警告）
- 出来高比率 >= 5倍（出来高急増）
- 20日高値を10%以上更新

## スコアリングルール

100点満点で計算します：

| 項目 | 配点 | 内容 |
|------|------|------|
| トレンド | 30点 | MA5/MA25との位置関係 |
| 出来高 | 20点 | 20日平均に対する倍率 |
| 相対強度 | 25点 | 5日リターン（暫定） |
| 流動性 | 15点 | 売買代金 |
| 20日高値圏 | 10点 | 高値からの距離 |
| リスク減点 | -30点 | 急騰・過熱・出来高急増 |

### スコア閾値

- **70点以上**: 買い候補
- **50〜69点**: 監視候補
- **50点未満**: 除外

## 注意事項

- このツールは投資助言を提供するものではありません
- 売買候補はあくまで「候補」として表示します
- 「買うべき」「推奨買い」「必ず上がる」などの断定は禁止しています
- 最終的な投資判断はユーザーご自身が行ってください
- 過去のリターンは将来の保証ではありません
- 投資にはリスクが伴います

## 仮想トレード（アプリ内ペーパートレード）

### LIMIT_SIM の制限

- LIMIT_SIMの約定判定は、**日足のhigh/lowのみ**で行っています
- 実際の板情報・約定順序・スプレッドとは**異なります**
- 同日に複数の約定条件が成立した場合の優先順位は、現状の実装に依存します
- これは**戦略検証用の近似シミュレーション**であり、厳密な約定再現ではありません
- 実際の取引ではmoomooアプリで手動注文してください

### 注文タイプ

| タイプ | 約定価格 | 設定 |
|--------|---------|------|
| MARKET_SIM | 翌営業日始値（デフォルト） | configの `market_fill_mode` で変更可 |
| LIMIT_SIM | BUY: low <= limit_price, SELL: high >= limit_price | 約定順序は日足データ依存 |

## API注文方針（重要）

### 実機検証結果

moomoo JP / FUTUJP では、**OpenAPI経由の日本株SIMULATE注文が利用できません**。

- アプリ内デモ取引（JP Stock Paper Trading）は利用可能
- しかし、APIからは`TrdEnv.SIMULATE`で注文ができない
- エラーメッセージ: "ERROR. the type of environment param is wrong"
- APIから見えるのはREAL口座のみ

### 本アプリの注文方針

```
データ取得：moomoo API
候補抽出：本アプリ
検証：signals / signal_backtests
実取引：moomooアプリで手動
売買記録：trades_manual に手動入力
API注文：未対応・使用禁止
```

- 本アプリは**REAL注文APIを呼び出しません**
- 取引は**moomooアプリで手動実行**してください
- 実行した売買は**手動売買ログに記録**してください
- `record_trade.py` またはStreamlitの手動売買ログから登録できます

### ペーパートレードについて

- moomoo JP / FUTUJP では、API経由のペーパートレードは利用できません
- アプリ内デモ取引とAPI SIMULATEは別物として扱います
- 本プロジェクトでは`paper_trade.enabled`を`false`のままにしています

## ライセンス

MIT License
