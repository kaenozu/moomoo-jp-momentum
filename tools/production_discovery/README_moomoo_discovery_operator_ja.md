# moomoo 本番環境 read-only 棚卸し v4

## 対象

- Issue: `kaenozu/moomoo-jp-momentum#27`
- PowerShell棚卸し本体: v4.0.0
- Pythonオペレーターツール: v1.2.2

> **v1.2.0は使用禁止です。** Windows検証で失敗した候補版であり、`WINDOWS_VALIDATION_FAILED_CANDIDATE / DO_NOT_USE_FOR_PRODUCTION`として扱います。

> **v1.2.1も本番利用禁止です。** gateの成功行に含まれる`error: null`をreview側がエラー行として誤除外し、正常なfrozen file-setを不一致と判定する欠陥がWindows end-to-end handoff検証で確認されました。v1.2.2で修正済みです。

v4は、検証済みclean checkoutと本番runtime directoryが別である場合を明示的に扱います。`database.path: data/moomoo.db`のような相対パスをcheckout基準で決めつけず、runtime evidenceごとに解決候補を記録します。

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
production_readiness        = BLOCKED
preflight_authorized        = false
production_drill_authorized = false
cutover_authorized          = false
```

## v1.2.2の改善

- gateの成功行に`error: null`が存在しても、非エラー行として保持
- 非空の`error`、`error_type`、`invocation_error`だけをエラー行として除外
- null／空errorと実エラーを分離する回帰テストを追加
- Scheduled Task actionに`Execute`、`Arguments`、`WorkingDirectory`等のoptional propertyがない場合も、StrictMode下で棚卸し全体を失敗させず、存在する値だけを記録
- operator bundle、Windows CI、handoff内包versionをv1.2.2へ固定

## v1.2.1から継承する改善

### runtime evidenceを3分類

```text
machine_observed
  Scheduled TaskのWorkingDirectoryなど、実機から直接取得した値

human_asserted
  オペレーターが明示入力し、根拠参照を添付した値

derived_candidate
  checkout、config directory、実行directoryなどから導出した候補
```

`--production-working-directory`は`human_asserted`です。入力しただけでmachine-observed evidenceには昇格しません。

### 判定を3層へ分離

```text
machine_validation_status
human_validation_status
operational_validation_status
```

代表的な正常終了は次です。

```text
validation_status             = MACHINE_PASS_HUMAN_REVIEW_REQUIRED
machine_validation_status     = PASS
human_validation_status       = PENDING
operational_validation_status = INCONCLUSIVE
production_readiness          = BLOCKED
```

単純な`PASS`を本番同定完了として表示しません。

### Windows／配布物ハードニング

- Windows PowerShell 5.1とPowerShell 7のstderrを診断ログへ分離
- 失敗時も診断artifactを保持
- Git blobの正確なbytesからbundleを生成
- checkoutのCRLF／LF差でfrozen SHAが変わらない
- 各シェルで2回ビルドし同一SHAを確認
- PowerShell 5.1とPowerShell 7のZIPをbyte-for-byte比較
- ZIP重複entry、圧縮破損、内部`SHA256SUMS.txt`、manifest、安全境界を検証
- manifestへsource commit／refを記録

## 前提

- 本番候補Windows PC
- Python 3.11以降
- PyYAML
- Git
- Windows PowerShell 5.1またはPowerShell 7
- 検証用checkout、保護対象checkout、bundle、証跡出力先が分離されている
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
working directoryを裏付ける証拠
```

## 実行例

明示的にruntime directoryを入力する場合は、その根拠も必須です。

```powershell
python .\moomoo_discovery_operator.py run `
  --bundle-dir . `
  --output-root "D:\moomoo-discovery-evidence" `
  --repo-path "C:\moomoo-verified" `
  --protected-checkout-path "C:\moomoo-runtime-checkout" `
  --expected-head "<approved-40-character-sha>" `
  --expected-remote "https://github.com/kaenozu/moomoo-jp-momentum.git" `
  --config-search-root "C:\moomoo-runtime" `
  --production-working-directory "C:\moomoo-runtime" `
  --production-working-directory-source "manual-command" `
  --production-working-directory-evidence "<redacted launch-command reference>"

$operatorExitCode = $LASTEXITCODE
Write-Host "operator exit code=$operatorExitCode"
```

使用可能なsource:

```text
manual-command
startup-script
service-runbook
scheduled-task-review
other-direct-evidence
```

Scheduled Taskの`WorkingDirectory`が自動取得される場合は`machine_observed`として記録されます。ただし、そのTaskが本番のactive launch sourceかは人が確認します。

## 終了コード

| code | status | 意味 |
|---:|---|---|
| 0 | `completed_readonly_discovery` | 機械検証はPASS。人手確認と全本番ゲートは未完了 |
| 2 | `completed_with_corrections_required` | 機械検証失敗またはruntime／DB競合を修正して再実行 |
| 1 | `blocked` | hash、parser、PowerShell、JSON、出力先など実行ゲート失敗 |

## 最初に確認する証跡

```text
05-operator-result.json
03-discovery-redacted.json
04-discovery-summary.md
```

期待される安全側の値:

```text
status                        = completed_readonly_discovery
validation_status             = MACHINE_PASS_HUMAN_REVIEW_REQUIRED
machine_validation_status     = PASS
human_validation_status       = PENDING
operational_validation_status = INCONCLUSIVE
production_readiness          = BLOCKED
preflight_authorized          = false
production_drill_authorized   = false
cutover_authorized            = false
operator_exit_code            = 0
```

## false-success防止

次の場合は本番同定完了ではありません。

- human-asserted directoryだけが既存DBへ解決した
- config候補が複数ある
- active launch sourceが未確認
- Service／Processのworking directoryが不明
- 別ユーザー、WSL、Docker、別PC writerを除外できない
- 単一DB候補が存在するだけで、それが本番DBか確認していない

複数のsupported runtime候補が別々の既存DBへ解決した場合は`MULTIPLE_LIVE_DB_PATHS`で拒否します。

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

Windows PowerShell 5.1:

```powershell
.\run_moomoo_discovery_operator_tests.ps1 `
  -PythonExecutable python `
  -PowerShellExecutable powershell.exe `
  -DiagnosticsDir .\ci-diagnostics
```

PowerShell 7:

```powershell
pwsh -NoProfile -File .\run_moomoo_discovery_operator_tests.ps1 `
  -PythonExecutable python `
  -PowerShellExecutable pwsh.exe `
  -DiagnosticsDir .\ci-diagnostics
```

静的検査:

```powershell
python .\validate_moomoo_discovery_operator.py
```
