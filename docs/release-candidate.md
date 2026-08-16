# Current master Source Release Candidate

## 目的

この経路は、現在のdefault branchに含まれるソースをexact commitへ結び付け、検証専用の決定的ZIP artifactとして保存するためのものです。

このartifactはデプロイ、Production切替、REAL注文、OpenD trade context、Scheduled Task、live DB、backup/restoreを実行または許可しません。Release Candidateという名称は「検証対象となるexact source snapshot」を示すだけで、Production authorityを意味しません。

## 過去のrelease経路を復元しない理由

過去のPR #32、#34、#35、#39では、production discovery operator、human-evidence gate、master release status observerを含むv1.2.2向け経路が実装されていました。

しかし、旧release merge commit `1a12bc935939d663a5541cdb6a496e9673cc905f`と、Issue #79調査時点のcurrent `master` `e15458ec58091bdc69b44f9a8eb95749571d5b4a`は、merge-base `31f1dc4bc363f4144d3ce333879ce8742f2ca848`から別系統へ分岐しています。旧release commitはcurrent masterの祖先ではありません。また、current treeには旧production discovery bundleや`moomoo/master-release`のconsumer参照がありません。

したがって、旧branchをmerge、rebase、cherry-pick、release baseとして再利用しません。current codebaseに必要な機能だけを新規実装します。旧branch削除はこのIssueの範囲外で、別途明示許可が必要です。

## 依存関係

この実装は、PR #86で導入された`src/source_manifest.py`のcanonical source boundary（`is_manifest_path`）を再利用します。boundary規則は`src/source_manifest.py`に一元化され、本経路は独自の除外リストを持ちません。

- base: `master`
- Linux full validation: CPython 3.11.15
- Windows compatibility validation: CPython 3.11.9
- immutable GitHub Action SHAs

## Artifact分類

`release-manifest.json`の分類はfail-closedです。

### `MASTER_RELEASE_CANDIDATE`

次のすべてを満たす場合だけ設定します。

- eventが`push`
- refが`refs/heads/master`
- source commitがcheckout済みHEADと一致
- tracked worktreeがclean

### `VALIDATION_ONLY`

以下を含む、それ以外のすべてです。

- pull request
- workflow dispatch
- local build
- dirty validation build
- master以外のbranch

どちらの分類でも次は常に`false`です。

```json
{
  "production_authority": false,
  "real_order_authorized": false,
  "cutover_authorized": false
}
```

`MASTER_RELEASE_CANDIDATE`であってもProduction操作の権限はありません。

## Source snapshot

builderはworktreeのファイルを直接読みません。指定commitのGit treeとblobを読み、Gitに記録されたbyte列とmodeからZIPを生成します。これにより、checkout時の改行変換やWindows/Linux filesystem差異をartifactへ持ち込みません。

次をartifactから除外します（canonical boundary = `src/source_manifest.is_manifest_path`）。

- `config.yaml` / `config.yml`
- `data/`配下。ただし公開watchlistの`data/symbols.json`は含める（case-insensitive）
- `.env`系（任意階層の`.env`/`.env.*`/`*.env`）
- SQLite / DB / key / certificate / keystore候補（`.cer`, `.crt`, `.db`, `.der`, `.jks`, `.key`, `.keystore`, `.p12`, `.pem`, `.pfx`, `.pkcs12`, `.pyc`, `.pyo`, `.sqlite`, `.sqlite3`）
- `backups/`, `dist/`, `logs/`, `reports/`, `.venv/`, `__pycache__/`等のcache
- symlink、submodule、通常blob以外

各source fileは10 MiB以下、全source合計は100 MiB以下に制限します。上限超過時はfailします。

## 決定性

ZIPは次の規則で生成します。

- member pathを昇順
- timestampを1980-01-01 00:00:00へ固定
- Git modeを固定
- `ZIP_STORED`で保存
- canonical JSON
- source member、manifestのSHA-256を`SHA256SUMS.txt`へ完全収録

同じGit commitとmetadataから2回生成したartifactはbyte一致しなければなりません。WindowsとLinuxのartifact SHA-256も一致しなければworkflowは失敗します。

## 検証

CLIはrepository rootからmoduleとして実行します。ファイルパス実行ではなく`python -m`を使用することで、WindowsとLinuxで同じimport契約になります。

ローカルのvalidation-only build例です。

```bash
python -m scripts.build_release_candidate \
  --output dist/moomoo-source-release-candidate.zip \
  --repository kaenozu/moomoo-jp-momentum \
  --source-commit HEAD \
  --source-ref refs/heads/local \
  --source-event local

python -m scripts.verify_release_candidate \
  dist/moomoo-source-release-candidate.zip \
  --expected-repository kaenozu/moomoo-jp-momentum \
  --expected-commit "$(git rev-parse HEAD)" \
  --expected-ref refs/heads/local \
  --expected-event local \
  --expected-status VALIDATION_ONLY
```

verifierは次を拒否します。

- corrupt ZIP
- absolute path、path traversal、backslash、非portable path
- duplicate / case-colliding member
- missing / unexpected member
- manifestのunknown / missing key
- member size / SHA-256不一致
- `SHA256SUMS.txt`の不足、余分、重複、形式不正
- source repository / commit / ref / event不一致
- classification不整合
- Production、REAL注文、cutover authorityが`true`

## GitHub Actions

`.github/workflows/release-candidate.yml`は次で実行します。

- pull request
- workflow dispatch
- `master` push

PRとworkflow dispatchのartifactは必ず`VALIDATION_ONLY`です。`master` pushだけが候補分類になり得ます。

Artifact retentionは14日です。期限切れartifactをrelease、rollback、Production判断の根拠として使用しません。必要な証跡はPR本文、Issue、または承認済みの別保管先へSHA-256とsource metadataを記録します。

## Rollback

この経路に問題がある場合は、最後に成功したcommitへ次をセットで戻します。

- `.github/workflows/release-candidate.yml`
- `src/release_candidate.py`
- `src/release_candidate_security.py`
- `src/source_manifest.py`（canonical boundary）
- builder / verifier CLI
- tests
- この文書

workflowだけ、manifest契約だけ、verifierだけを部分的に巻き戻しません。rollback後は、同一入力2回build、Windows/Linux SHA一致、negative tests、locked dependency gateを再実行します。

生成済みartifactはコードのrollbackを実行せず、Production rollback権限も持ちません。

## 禁止事項

- stale release branchのmerge / rebase / cherry-pick
- validation artifactをProduction candidateとして扱うこと
- artifactからREAL注文、OpenD、live DB、scheduler、restore、cutoverを起動すること
- Secrets、認証情報、private dataをartifactへ含めること
- test skip、検証弱体化、権限フラグ変更で成立させること
- Issue #79の完了だけを理由にReleaseまたはProduction操作を行うこと
