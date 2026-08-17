# Moomoo日本株モメンタム検証ツール

日本株・ETF・REITを対象に、データ取得、候補抽出、SQLite上の仮想トレード、運用検証を行うための研究・運用支援ツールです。

## 重要な安全境界

- **REAL注文APIは使用しません。**
- 日本株の実取引は moomoo アプリで人間が手動実行します。
- moomoo OpenAPI は主にデータ取得・接続確認用途です。
- アプリ内の仮想注文・約定・cash・position は実注文ではありません。
- `paper_order.py` などUS向けexperimental経路は、JP日次運用と分離して扱います。
- SIMULATE / REAL注文、trade unlock、Production scheduler、live DB cutover は別の明示承認が必要です。

```text
データ取得   : moomoo API / 対応データソース
候補抽出     : 本リポジトリ
仮想検証     : SQLite virtual trade
実取引       : moomooアプリで手動
API注文      : JP運用では使用禁止
```

## 主な機能

- 日次データ更新とベンチマーク比較
- モメンタム候補抽出
- Streamlit UI
- SQLite 仮想注文 / 約定 / position / cash
- 日次運用サイクル
- scheduler / alert / backup / recovery の運用検証
- 戦略研究用 backtest

## 銘柄ユニバース

`data/symbols.json` の主な role:

- `benchmark`: 比較専用
- `trade_candidate`: 候補・仮想注文対象
- `watch_only`: 監視専用
- `excluded`: 対象外

`role=trade_candidate`、`tradable=true`、価格レンジ内を満たす銘柄だけを候補対象にします。

## セットアップ

```bash
git clone <repository-url>
cd moomoo-jp-momentum
python -m venv venv
venv\Scripts\activate  # Windows
pip install -r requirements.txt
copy config.example.yaml config.yaml
```

OpenDを使用する操作では、別途OpenDを起動し必要なログイン状態を確認してください。

## 基本コマンド

```bash
python test_connection.py
python daily_update.py --dry-run
python daily_update.py --force
python screen_candidates.py --save --csv --html
streamlit run app.py
```

### 仮想トレード

```bash
python virtual_order.py --from-signals --date YYYY-MM-DD
python process_virtual_fills.py --date YYYY-MM-DD
python virtual_order.py --generate-exits --date YYYY-MM-DD
python virtual_order.py --positions
python virtual_order.py --performance
```

### 日次サイクル

```bash
python run_daily_cycle.py --dry-run
python run_daily_cycle.py --date YYYY-MM-DD
```

## 仮想約定の前提

`MARKET_SIM` / `LIMIT_SIM` は日足ベースの近似です。板、厳密な約定順序、スプレッド、税金、手数料、実際の流動性は完全には再現しません。

バックテストや仮想成績は、実運用の利益保証として扱わないでください。

## Coordinator Issue 同期

`#77` のような手書き Coordinator Issue の PR/merge 状態は時間とともに必ず stale になります。
`scripts/sync_coordinator_issue.py` は gh CLI から実 PR 状態を取得して、`<!-- GENERATED -->` セクションを機械生成・更新します（policy 部と生成部の分離）。

```bash
python scripts/sync_coordinator_issue.py --issue 77 --repo kaenozu/moomoo-jp-momentum --dry-run
```

- `--dry-run`: 本文を更新せずに生成結果のみ表示
- `--write`: Issue 本文の生成セクションを置換（workflow はこちらを使用）
- 未コミット変更は workflow 側の `gh pr list` / `gh api` が正となるため、README に PR 番号を固定しません

## 検証

変更範囲に応じて、current CI の品質ゲートを基準に確認します。

```bash
python -m pytest tests/ -m "not slow" -q
ruff check --output-format=github src/ tests/ scripts/validate_source_manifest.py scripts/sync_coordinator_issue.py run_daily_cycle.py
pyright
python -m compileall -q src scripts run_daily_cycle.py
git diff --check
```

加えて、dry-run、skip-fetch、SQLite整合性、scheduler / calendar、artifact hygiene 等の関連ゲートを実行します。

## 運用受入

CIで再現できない環境依存の受入を、コード品質と分離して管理します。

例:

- production相当SQLiteを使うvirtual-only受入
- backup / restore drill
- Windows reboot後のscheduler / OpenD回復
- real webhook failure drill
- 実schedulerによるJPX休場日no-op確認

最新の依存関係とrelease判定は GitHub Issues / Pull Requests を正としてください。READMEには変動しやすいPR番号・SHA・研究結果を固定しません。
