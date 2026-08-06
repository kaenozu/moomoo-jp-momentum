# Contributing

## 基本方針

- REAL注文、trade unlock、live DB、Secretsへ接続しないでください。
- 機能変更、依存更新、CI基盤変更は別PRに分けてください。
- test skip、lint/typecheck無効化、timeout延長で問題を隠さないでください。
- dependency update PRはDraftで開始し、自動mergeしません。

## 標準検証環境

- Linux full quality gate / Release Candidate: CPython 3.11.15
- Windows dependency compatibility gate: CPython 3.11.9
- pip 26.1.2
- `constraints/py311.txt`による完全一致pin

Windowsで3.11.9を使う理由は、CPython 3.11.15に公式の`actions/python-versions` Windows x64成果物がなく、3.11.9がWindows対応の最新3.11 exact patchだからです。platformごとのexact patchはworkflowと再現性文書に固定します。

clean checkoutでは次の手順でdevelopment環境を構築します。

```bash
python -m venv .venv
python --version
python -m pip install --disable-pip-version-check pip==26.1.2
python -m pip install --constraint constraints/py311.txt --requirement requirements.txt --requirement requirements-dev.txt
python -m pip check
python scripts/verify_locked_requirements.py
```

仮想環境の有効化方法はOSに合わせてください。Linuxでは3.11.15、Windowsでは3.11.9であることを`python --version`で確認します。詳細な手順、依存更新、Action SHA更新、rollback方法は[`docs/dependency-reproducibility.md`](docs/dependency-reproducibility.md)を参照してください。

## 品質ゲート

```bash
python -m pytest tests/ -m "not slow" -q
ruff check src/ tests/ scripts/verify_locked_requirements.py run_daily_cycle.py
pyright
python -m compileall -q src scripts tests
python run_daily_cycle.py --dry-run --config tests/fixtures/config.test.yaml
```

`git diff --check`と差分目視確認も実施してください。実行件数、skip件数、warning、未実行項目をPR本文に記録します。

## 依存更新

依存更新は専用Issue・専用branch・専用Draft PRで行います。

1. `requirements.txt`または`requirements-dev.txt`を更新する
2. Ubuntu CPython 3.11.15とWindows CPython 3.11.9のclean venvで解決結果を取得する
3. 共通依存は完全一致pin、platform固有依存はPEP 508 marker付きpinとして`constraints/py311.txt`へ反映する
4. locked install、`pip check`、lock検証、全品質ゲートを実行する
5. Python exact patch、解決version差分、warning、rollback先commitをPR本文へ記録する

依存更新と同時に無関係なリファクタを行わないでください。
