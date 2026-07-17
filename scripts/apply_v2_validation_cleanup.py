"""Temporary, deterministic cleanup for V2 validation implementation."""

from __future__ import annotations

from pathlib import Path

PATH = Path("src/v2_validation.py")
text = PATH.read_text(encoding="utf-8")

old_import = "import math\nimport shutil\nimport sqlite3\n"
new_import = "import math\nimport sqlite3\n"
if text.count(old_import) != 1:
    raise RuntimeError("unexpected import block in src/v2_validation.py")
text = text.replace(old_import, new_import, 1)

old_digest = '''def logical_database_digest(path: str | Path) -> str:
    digest = hashlib.sha256()
    with sqlite3.connect(path) as conn:
        for table in list_user_tables(conn):
            projection = project_table(conn, table)
            digest.update(table.encode("utf-8"))
            digest.update(b"\\0")
            digest.update("\\0".join(projection.columns).encode("utf-8"))
            digest.update(b"\\0")
            digest.update(projection.rows_digest.encode("ascii"))
            digest.update(b"\\n")
    return digest.hexdigest()
'''
new_digest = '''def logical_database_digest(path: str | Path) -> str:
    digest = hashlib.sha256()
    with sqlite3.connect(path) as conn:
        schema_rows = conn.execute(
            """
            SELECT type, name, tbl_name, COALESCE(sql, '')
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            ORDER BY type, name
            """
        ).fetchall()
        for schema_row in schema_rows:
            digest.update(_canonical_row(tuple(schema_row)).encode("utf-8"))
            digest.update(b"\\n")
        for table in list_user_tables(conn):
            projection = project_table(conn, table)
            digest.update(table.encode("utf-8"))
            digest.update(b"\\0")
            digest.update("\\0".join(projection.columns).encode("utf-8"))
            digest.update(b"\\0")
            digest.update(projection.rows_digest.encode("ascii"))
            digest.update(b"\\n")
    return digest.hexdigest()
'''
if text.count(old_digest) != 1:
    raise RuntimeError("unexpected logical_database_digest implementation")
text = text.replace(old_digest, new_digest, 1)

old_uri = 'with sqlite3.connect(f"file:{source_path}?mode=ro", uri=True) as source_conn:'
new_uri = 'with sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True) as source_conn:'
if text.count(old_uri) != 1:
    raise RuntimeError("unexpected online backup URI implementation")
text = text.replace(old_uri, new_uri, 1)

PATH.write_text(text, encoding="utf-8")
print("Applied V2 validation cleanup.")
