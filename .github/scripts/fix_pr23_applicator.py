from pathlib import Path

path = Path(__file__).with_name("apply_pr23.py")
text = path.read_text(encoding="utf-8")

candidate_old = '''text = replace_once(
    text,
    '        today = datetime.now().strftime("%Y-%m-%d")\\n\\n        with self._get_connection() as conn:\\n',
    '        today = _resolve_target_date(target_date)\\n\\n        with self._get_connection() as conn:\\n',
    "new candidate date",
)
'''
candidate_new = '''old = '        today = datetime.now().strftime("%Y-%m-%d")\\n\\n        with self._get_connection() as conn:\\n'
if text.count(old) != 2:
    raise RuntimeError(f"alert target dates: expected two matches, found {text.count(old)}")
text = text.replace(
    old,
    '        today = _resolve_target_date(target_date)\\n\\n        with self._get_connection() as conn:\\n',
    1,
)
'''
if text.count(candidate_old) != 1:
    raise RuntimeError(
        f"candidate applicator patch expected one match, found {text.count(candidate_old)}"
    )
text = text.replace(candidate_old, candidate_new, 1)

config_old = '''for config_path in ("config.example.yaml", "tests/fixtures/config.test.yaml"):
    text = read(config_path)
    text = replace_once(
        text,
        '''  webhook:
    enabled: false
    url: ''
''',
        '''  webhook:
    enabled: false
    url: ''
  operational:
    enabled: false
    timeout_seconds: 10
''',
        f"{config_path} operational config",
    )
    write(config_path, text)
'''
config_new = '''config_webhook_blocks = {
    "config.example.yaml": "  webhook:\\n    enabled: false\\n    url: ''\\n",
    "tests/fixtures/config.test.yaml": '  webhook:\\n    enabled: false\\n    url: ""\\n',
}
for config_path, webhook_block in config_webhook_blocks.items():
    text = read(config_path)
    replacement = webhook_block + (
        "  operational:\\n"
        "    enabled: false\\n"
        "    timeout_seconds: 10\\n"
    )
    text = replace_once(
        text,
        webhook_block,
        replacement,
        f"{config_path} operational config",
    )
    write(config_path, text)
'''
if text.count(config_old) != 1:
    raise RuntimeError(
        f"config applicator patch expected one match, found {text.count(config_old)}"
    )
text = text.replace(config_old, config_new, 1)

path.write_text(text, encoding="utf-8")
Path(__file__).unlink()
