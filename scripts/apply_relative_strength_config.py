from pathlib import Path

path = Path("src/relative_strength.py")
text = path.read_text(encoding="utf-8")
old = '''        # ベンチマークコード
        rs_config = config.get("relative_strength", {})
        self.default_benchmark = rs_config.get(
            "default_benchmark_for_screening", "JP.1306"
        )
        self.periods = rs_config.get("periods", [5, 20, 60])
'''
new = '''        # 現行設定を優先し、旧トップレベル設定も後方互換で受け入れる
        legacy_config = config.get("relative_strength", {})
        rs_config = config.get("signals.relative_strength", legacy_config)
        self.default_benchmark = rs_config.get(
            "benchmark_code",
            rs_config.get("default_benchmark_for_screening", "JP.1306"),
        )
        self.periods = rs_config.get(
            "periods",
            legacy_config.get("periods", [5, 20, 60]),
        )
'''
if text.count(old) != 1:
    raise RuntimeError(f"expected one config block, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
