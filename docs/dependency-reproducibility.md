# Python依存とCIの再現性ポリシー

## 対象

この文書は、CI、検証用ローカル環境、Release Candidate生成で使用するPythonと依存関係の固定方法を定めます。

- 標準runtime: **CPython 3.11.15**
- サポートminor: **CPython 3.11のみ**
- CI installer: **pip 26.1.2**
- 固定ファイル: `constraints/py311.txt`
- production入力: `requirements.txt`
- development入力: `requirements-dev.txt`

ローカルで別の3.11 patchを使用することはできますが、受入判定とRelease Candidateの再現性確認は3.11.15で行います。Python minor、patch、pip、依存バージョンの更新は、機能変更と混在させません。

## clean checkoutからの構築

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --disable-pip-version-check pip==26.1.2
python -m pip install --constraint constraints/py311.txt --requirement requirements.txt --requirement requirements-dev.txt
python -m pip check
python scripts/verify_locked_requirements.py
```

### Linux

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install --disable-pip-version-check pip==26.1.2
python -m pip install --constraint constraints/py311.txt --requirement requirements.txt --requirement requirements-dev.txt
python -m pip check
python scripts/verify_locked_requirements.py
```

production依存だけが必要な場合も、同じconstraintsを使用します。

```bash
python -m pip install --constraint constraints/py311.txt --requirement requirements.txt
```

constraintsとrequirementsが矛盾する場合、pipの解決処理または`verify_locked_requirements.py`が失敗します。検証スクリプトは、次を確認します。

1. 現在のplatformで有効なconstraintがすべて単一の完全一致`==`であること
2. production/developmentの直接依存すべてに有効なpinがあること
3. pinがrequirementsの許容範囲内であること
4. インストール済みversionがすべてpinと一致すること

## platform差異

基準constraintsはUbuntu 24.04とCPython 3.11.15で成功したCI環境から取得しています。Windowsだけで必要になる依存はPEP 508 markerを付けます。

- `colorama`: Windowsのみ
- `tzdata`: pandasのtimezone挙動をplatform間で揃えるため両方で固定
- `futu-api`, `pandas`, `yfinance`, `streamlit`: WindowsとLinuxの両方で同じversionを使用

依存更新時は、UbuntuとWindowsのclean venvでlocked install、`pip check`、lock検証を実行します。片方でのみ解決されるpackageを無条件pinとして追加しません。

## dependency update手順

依存更新は専用Issueと専用Draft PRで行い、自動mergeしません。

1. current `master` exact SHAから専用branchを作成する
2. CPython 3.11.15とpip 26.1.2のclean venvをUbuntuとWindowsに作成する
3. 更新対象を`requirements.txt`または`requirements-dev.txt`で変更する
4. clean環境で依存を解決し、`python -m pip freeze --exclude pip --exclude setuptools --exclude wheel`を取得する
5. 両platformの結果を比較し、共通packageを完全一致pin、platform固有packageをmarker付きpinとして`constraints/py311.txt`へ反映する
6. 次の品質ゲートを実行する

```bash
python -m pip check
python scripts/verify_locked_requirements.py
python -m pytest tests/ -m "not slow" -q
ruff check src/ tests/ scripts/verify_locked_requirements.py run_daily_cycle.py
pyright
python -m compileall -q src scripts tests
python run_daily_cycle.py --dry-run --config tests/fixtures/config.test.yaml
```

7. skip件数、warning、解決version差分、Windows/Linux結果をPR本文へ記録する
8. 人間のレビュー後にのみmergeする

## GitHub Actions更新

workflow内の外部Actionは、major tagではなく公式releaseのfull commit SHAで固定します。SHAの横にrelease versionをコメントします。

Action更新時は公式releaseとcommit署名を確認し、専用PRでSHAとversionコメントを同時に変更します。floating tagへ戻してはいけません。

## rollback

依存更新に問題がある場合は、直前に成功したcommitの次をセットで復元します。

- `requirements.txt`
- `requirements-dev.txt`
- `constraints/py311.txt`
- `.github/workflows/tests.yml`のPython、pip、Action SHA

constraintsだけ、またはrequirementsだけを部分的に巻き戻しません。rollback後はclean checkoutから全品質ゲートを再実行します。

## 禁止事項

- dependency update PRの自動merge
- 範囲指定だけでCI依存を解決すること
- test skip、lint/typecheck無効化、timeout延長による成立
- REAL注文、OpenD trade context、live DB、Secretsを依存検証へ接続すること
