"""
pytest共有フィクスチャ

ファイルパス: tests/conftest.py
何をするか: 全テストがsrc/をインポートできるようパスを通す
なぜ存在するか: 個別テストファイルにsys.path.insertを書かずに済ませるため
関連ファイル: pytest.ini
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
