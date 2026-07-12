from pathlib import Path

path = Path("src/relative_strength.py")
text = path.read_text(encoding="utf-8")
replacements = [
    (
        "import sqlite3\nfrom dataclasses import dataclass\n",
        "import sqlite3\nfrom contextlib import closing\nfrom dataclasses import dataclass\n",
    ),
    (
        "        with self._get_connection() as conn:\n            # 基準日より前のデータを取得\n",
        "        with closing(self._get_connection()) as conn:\n            # 基準日より前のデータを取得\n",
    ),
    (
        "        with self._get_connection() as conn:\n            for code, rs in rs_data.items():\n",
        "        with closing(self._get_connection()) as conn, conn:\n            for code, rs in rs_data.items():\n",
    ),
]
for old, new in replacements:
    if text.count(old) != 1:
        raise RuntimeError(f"expected one match, found {text.count(old)}: {old!r}")
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
