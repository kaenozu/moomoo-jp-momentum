# moomoo 本番環境 read-only 棚卸し v4

## 対象

- Issue: `kaenozu/moomoo-jp-momentum#27`
- PowerShell棚卸し本体: v4.0.0
- Pythonオペレーターツール: v1.2.0

v4は、検証済みclean checkoutと本番runtime directoryが別である場合を明示的に扱います。`database.path: data/moomoo.db`のような相対パスをcheckout基準で決めつけず、Scheduled Taskの`WorkingDirectory`またはオペレーターが直接確認したruntime directoryごとに解決候補を記録します。

## 安全境界

このパッケージは次を行いません。

- SQLite接続
- リポジトリPythonモジュールのimport
- writer、Scheduled Task、サービスの停止・変更
- Git変更
- `-PreflightOnly`
- 本番バックアップ／復元
- `-ConfirmProductionExecution`
- cutover
- OpenD trade context／実注文API

終了コード0でも、本番ドリル許可ではありません。結果は常に次を維持します。

```text
production_readiness       = BLOCKED
preflight_authorized       = false
production_drill_authorized = false
cutover_authorized         = false
```

## v1.2.0の主な改善

- `--expected-head`を必須化し、古いmaster SHAの暗黙利用を廃止
- `--production-working-directory`を複数指定可能
- Scheduled Taskの`WorkingDirectory`をauthoritative runtime evidenceとして保持
- configとruntime directoryの全組合せについて、相対`database.path`と`database_backup.directory`を解決
- 既存DBへ解決するauthoritative mappingが0件なら機械検証失敗
- 複数の既存DBへ解決する場合は`MULTIPLE_LIVE_DB_PATHS`でfalse-successを拒否
- Windows Serviceと実行中プロセスはworking directoryを直接証明しないため、人手確認として残す
- WSL、Docker、別ユーザーセッションをread-onlyで棚卸し
- 別PC writer不存在はローカル実行では証明不能と明記
- JSON配列をPowerShellへ渡す際はJSON文字列1引数を使用し、Windows PowerShell 5.1の配列引数差を回避
- 証跡、マスキング、終了コード、許可境界を`05-operator-result.json`へ固定

## 前提

- 本番候補Windows PC
- Python 3.11以降
- PyYAML
- Git
- Windows PowerShell 5.1またはPowerShell 7
- 検証用checkout、保護対象checkout、配布bundle、証跡出力先が区別されている
- 証跡出力ルートは事前作成済み

## 実行前に確認する値

推測せず、GitHubと実機から直接確認します。

```text
verified checkout
protected checkout
approved 40-character Git SHA
origin URL
config search root
production process working directory
```

runtime directoryは、Scheduled Taskの`Start in`／`WorkingDirectory`、サービス運用手順、起動バッチ、実際の起動コマンドなどから確認します。

## 実行例

```powershell
python .\moomoo_discovery_operator.py run `
  --bundle-dir . `
  --output-root "D:\moomoo-discovery-evidence" `
  --repo-path "C:\moomoo-verified" `
  --protected-checkout-path "C:\moomoo-runtime-checkout" `
  --expected-head "<approved-40-character-sha>" `
  --expected-remote "https://github.com/kaenozu/moomoo-jp-momentum.git" `
  --config-search-root "C:\moomoo-runtime" `
  --production-working-directory "C:\moomoo-runtime"

$operatorExitCode = $LASTEXITCODE
Write-Host "operator exit code=$operatorExitCode"
```

複数のruntime候補が直接証拠として残る場合は、オプションを繰り返します。

```powershell
--production-working-directory "C:\runtime-a" `
--production-working-directory "D:\runtime-b"
```

複数候補が別々の既存DBへ解決した場合、ツールは成功扱いにしません。

## 終了コード

| code | status | 意味 |
|---:|---|---|
| 0 | `completed_readonly_discovery` | 機械検証PASS。人手確認と全本番ゲートは残る |
| 2 | `completed_with_corrections_required` | runtime/config/DBの証拠不足、競合、環境不備を修正して再実行 |
| 1 | `blocked` | hash/parser/PowerShell/JSON/出力先等の実行ゲート失敗 |

## 最初に確認する証跡

```text
05-operator-result.json
03-discovery-redacted.json
04-discovery-summary.md
```

`05-operator-result.json`の期待値は次です。

```text
status                       = completed_readonly_discovery
validation_status            = PASS
production_readiness         = BLOCKED
preflight_authorized         = false
production_drill_authorized  = false
cutover_authorized           = false
operator_exit_code           = 0
```

## 外部共有

共有候補:

- `03-discovery-redacted.json`
- `04-discovery-summary.md`
- 内容を目視確認した`05-operator-result.json`

共有禁止:

- `*.bin`
- `00-manifest.json`
- `01-gated-discovery.json`
- `02-discovery-review.json`

未マスキング証跡にはマシン名、ユーザー、絶対パス、プロセスコマンド、Scheduled Task、サービス、SMB情報が含まれる可能性があります。

## 人が確定する項目

機械検証PASS後も次を直接確認します。

- このPCが本番ホストか
- 本番アプリの起動元
- production working directory
- active `config.yaml`
- `database.path`の生値
- resolved live DB絶対パス
- WAL／SHM
- 全writer
- Scheduled Task、Windows Service、Startup、手動ジョブ
- 別ユーザーセッション、WSL、Docker、別PC
- writer停止・再開手順
- single-host条件
- no-write window
- secondary storageの障害ドメインと空き容量
- 未使用のevidence／secondary／restoreパス

1点でも不明なら`-PreflightOnly`へ進みません。

## 開発者向け検証

```powershell
.\run_moomoo_discovery_operator_tests.ps1 `
  -PythonExecutable python `
  -PowerShellExecutable powershell.exe
```

PowerShell 7:

```powershell
pwsh -NoProfile -File .\run_moomoo_discovery_operator_tests.ps1 `
  -PythonExecutable python `
  -PowerShellExecutable pwsh.exe
```

静的検査:

```powershell
python .\validate_moomoo_discovery_operator.py
```
