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
