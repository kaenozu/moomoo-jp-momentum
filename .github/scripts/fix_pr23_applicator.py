from pathlib import Path

path = Path(__file__).with_name("apply_pr23.py")
text = path.read_text(encoding="utf-8")
old = '''text = replace_once(
    text,
    '        today = datetime.now().strftime("%Y-%m-%d")\\n\\n        with self._get_connection() as conn:\\n',
    '        today = _resolve_target_date(target_date)\\n\\n        with self._get_connection() as conn:\\n',
    "new candidate date",
)
'''
new = '''old = '        today = datetime.now().strftime("%Y-%m-%d")\\n\\n        with self._get_connection() as conn:\\n'
if text.count(old) != 2:
    raise RuntimeError(f"alert target dates: expected two matches, found {text.count(old)}")
text = text.replace(
    old,
    '        today = _resolve_target_date(target_date)\\n\\n        with self._get_connection() as conn:\\n',
    1,
)
'''
if text.count(old) != 1:
    raise RuntimeError(f"applicator patch expected one match, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
Path(__file__).unlink()
