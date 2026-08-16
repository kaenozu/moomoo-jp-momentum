# Python依存とCIの再現性ポリシー

## 対象

この文書は、CI、検証用ローカル環境、Release Candidate生成で使用するPythonと依存関係の固定方法を定めます。

- 標準minor: **CPython 3.11のみ**
- Linux受入runtime: **CPython 3.11.15**
- Windows互換runtime: **CPython 3.11.9**
- CI installer: **pip 26.1.2**
- 固定ファイル: `constraints/py311.txt`
- production入力: `requirements.txt`
- development入力: `requirements-dev.txt`

Python 3.11.15は`actions/python-versions`にLinux/RHEL成果物だけがあり、Windows x64成果物はありません。Windows対応の最新3.11 exact patchは3.11.9です。このため、同一platform上の再実行ではexact patchを固定しつつ、Linuxの主品質ゲートは最新の3.11 security patch、Windowsは利用可能な最新3.11 installerによる互換ゲートとして分離します。

Python minor、platform別patch、pip、依存バージョンの更新は、機能変更と混在させません。Release Candidateの生成はLinux受入runtimeを使用します。

## clean checkoutからの構築

### Windows PowerShell

CPython 3.11.9を明示的にインストールした環境で実行します。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python --version
python -m pip install --disable-pip-version-check pip==26.1.2
python -m pip install --constraint constraints/py311.txt --requirement requirements.txt --requirement requirements-dev.txt
python -m pip check
python scripts/verify_locked_requirements.py
```

`python --version`が3.11.9でない場合、その結果をWindows受入証跡として使用しません。

### Linux

CPython 3.11.15を明示的に使用します。

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python --version
python -m pip install --disable-pip-version-check pip==26.1.2
python -m pip install --constraint constraints/py311.txt --requirement requirements.txt --requirement requirements-dev.txt
python -m pip check
python scripts/verify_locked_requirements.py
```

`python --version`が3.11.15でない場合、その結果をLinux受入またはRelease Candidate再現性の証跡として使用しません。

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

WindowsゲートはCPython 3.11.9、Linuxゲートは3.11.15ですが、`constraints/py311.txt`の共通依存versionは同一です。どちらか一方でのみ解決できるpackageを無条件pinとして追加しません。

## dependency update手順

依存更新は専用Issueと専用Draft PRで行い、自動mergeしません。

1. current `master` exact SHAから専用branchを作成する
2. UbuntuはCPython 3.11.15、WindowsはCPython 3.11.9、両方でpip 26.1.2のclean venvを作成する
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

7. Python exact patch、test件数、skip件数、warning、解決version差分、Windows/Linux結果をPR本文へ記録する
8. 人間のレビュー後にのみmergeする

## Python patch更新

`actions/python-versions`の公式manifestで対象OS・architectureの成果物を確認してから更新します。LinuxとWindowsで同一patchが提供されない場合は、次の優先順位で選定します。

1. 両方とも同一minorに維持する
2. Linuxは最新のsecurity patchを使用する
3. Windowsは公式x64成果物がある最新patchを使用する
4. platform別exact patchと理由をworkflow・文書・PR本文へ記録する

manifest確認なしで`3.11`のようなfloating指定へ戻してはいけません。

## GitHub Actions更新

workflow内の外部Actionは、major tagではなく公式releaseのfull commit SHAで固定します。SHAの横にrelease versionをコメントします。

Action更新時は公式releaseとcommit署名を確認し、専用PRでSHAとversionコメントを同時に変更します。floating tagへ戻してはいけません。

## rollback

依存更新に問題がある場合は、直前に成功したcommitの次をセットで復元します。

- `requirements.txt`
- `requirements-dev.txt`
- `constraints/py311.txt`
- `.github/workflows/tests.yml`のplatform別Python、pip、Action SHA

constraintsだけ、またはrequirementsだけを部分的に巻き戻しません。rollback後はclean checkoutから全品質ゲートを再実行します。

## 禁止事項

- dependency update PRの自動merge
- 範囲指定だけでCI依存を解決すること
- test skip、lint/typecheck無効化、timeout延長による成立
- REAL注文、OpenD trade context、live DB、Secretsを依存検証へ接続すること
