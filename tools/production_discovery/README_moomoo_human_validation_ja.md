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

## 入力

1. `human-validation.template.json`をコピーし、各checkへ事実と証拠参照を記入する
2. operator実行結果の以下を用意する
   - `05-operator-result.json`
   - `03-discovery-redacted.json`
3. このrelease packageの`release-manifest.json`を使用する

`CONFIRMED`には空でない`value`と1件以上の`evidence_refs`が必要です。推測は`CONFIRMED`にしないでください。

## Release packageの確認

実行前に外側ZIPのSHA-256、`SHA256SUMS.txt`、`release-manifest.json`を確認してください。`release_candidate=true`、`source_ref=refs/heads/master`、`source_event=push`の組み合わせだけがmaster由来候補です。nested operator ZIPのSHA-256もrelease manifestと一致する必要があります。

## 実行

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

既存ファイルは上書きしません。

## 終了コード

- `0`: `ELIGIBLE_FOR_SEPARATE_PREFLIGHT_APPROVAL`
- `2`: `BLOCKED`または`INCONCLUSIVE`
- `1`: `CORRECTION_REQUIRED`または入力／出力エラー

masterのpushから作られたrelease packageだけが`release_candidate=true`です。PRや手動の検証buildは`VALIDATION_ONLY`となり、preflight候補には進めません。
