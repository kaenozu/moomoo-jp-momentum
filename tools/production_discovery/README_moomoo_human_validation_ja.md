# moomoo production discovery human-validation gate

このキットは、read-only discoveryの機械結果と本番運用者の確認結果を分離して記録します。

## 安全境界

このvalidatorは次を実行しません。

- SQLite接続
- writer停止・再開
- Scheduled Task／Service変更
- `-PreflightOnly`
- 本番バックアップ／復元
- cutover
- OpenD trade contextまたは実注文API

validatorが到達できる最上位状態は次です。

```text
ELIGIBLE_FOR_SEPARATE_PREFLIGHT_APPROVAL
```

これはpreflightの承認ではありません。別の明示的承認が必要です。出力では常に次を維持します。

```text
production_readiness        = BLOCKED
preflight_authorized        = false
production_drill_authorized = false
cutover_authorized          = false
```

## Release packageの確認

release ZIPを展開した後も元のZIPを保持し、同梱verifierで検証します。

```powershell
python .\compare_moomoo_discovery_releases.py `
  --left .\moomoo_production_discovery_release_v1.2.2.zip `
  --output .\release-verification.json
```

検証は次を拒否します。

- ZIP破損または重複entry
- 想定外／不足member
- `SHA256SUMS.txt`未列挙または不一致
- release manifestのsource／authorization不整合
- nested operator ZIPのmember、内部SHA、manifest、source不整合

`release_candidate=true`、`source_ref=refs/heads/master`、`source_event=push`の組み合わせだけがmaster由来候補です。PRや手動buildは`VALIDATION_ONLY`です。

### Master release runの特定

masterへのpushでrelease workflowが完了すると、対象commitへ`moomoo/master-release`というcommit statusが付与されます。statusのtarget URLは、そのrelease packageを生成した正確なGitHub Actions runを指します。

`success`はWindows PowerShell 5.1／PowerShell 7のbuildとcross-shell比較が成功したことだけを示します。本番環境の同定、human validation、preflight、backup／restore、cutoverの承認を意味しません。

正式候補として使用するartifactは、statusが指すrun内の次の名前です。

```text
moomoo-discovery-release-canonical-<master commit SHA>
```

## 入力

1. `human-validation.template.json`をコピーし、各checkへ事実と証拠参照を記入する
2. operator実行結果の以下を用意する
   - `05-operator-result.json`
   - `03-discovery-redacted.json`
3. 検証済みrelease packageの`release-manifest.json`を使用する

`CONFIRMED`には空でない`value`と1件以上の`evidence_refs`が必要です。推測は`CONFIRMED`にしないでください。

`production_working_directory`、`active_config_path`、`resolved_live_database`をすべて`CONFIRMED`にする場合、3項目は`03-discovery-redacted.json`内の同一existing runtime mappingと一致しなければなりません。

## Human validationの実行

```powershell
python .\validate_moomoo_human_validation.py `
  --human-validation .\human-validation.json `
  --operator-result .\05-operator-result.json `
  --discovery-redacted .\03-discovery-redacted.json `
  --release-manifest .\release-manifest.json `
  --output-dir .\evidence
```

出力:

```text
06-human-validation.json
07-preflight-eligibility.json
```

既存ファイルは上書きしません。`06-human-validation.json`には本番パス等が含まれ得るため、そのまま公開しないでください。

## 終了コード

- `0`: `ELIGIBLE_FOR_SEPARATE_PREFLIGHT_APPROVAL`
- `2`: `BLOCKED`または`INCONCLUSIVE`
- `1`: `CORRECTION_REQUIRED`または入力／出力エラー

master由来packageで全項目が確認済みでも、preflightは自動承認されません。別の明示的承認が必要です。
