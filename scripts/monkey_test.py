import subprocess, sys

scripts = [
    ("test_connection.py", []),
    ("test_quote.py", []),
    ("screen_candidates.py", ["--help"]),
    ("screen_candidates.py", ["--date", "2099-01-01"]),
    ("screen_candidates.py", ["--date", "invalid-date"]),
    ("virtual_order.py", ["--help"]),
    ("virtual_order.py", ["--list"]),
    ("virtual_order.py", ["--list-fills"]),
    ("virtual_order.py", ["--performance"]),
    ("virtual_order.py", ["--code", "INVALID", "--side", "BUY", "--quantity", "1"]),
    ("virtual_order.py", ["--from-signals", "--date", "2099-01-01"]),
    ("virtual_report.py", ["--help"]),
    ("virtual_report.py", []),
    ("record_trade.py", ["--help"]),
    ("record_trade.py", ["--list"]),
    ("performance_report.py", ["--help"]),
    ("send_alerts.py", ["--help"]),
    ("run_daily_cycle.py", ["--help"]),
    ("run_daily_cycle.py", ["--dry-run"]),
    ("generate_reports.py", ["--help"]),
    ("generate_reports.py", []),
]

passed = 0
failed = 0
for script, args in scripts:
    cmd = [sys.executable, script] + args
    label = " ".join(cmd[1:])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode in (0, 1):
            passed += 1
            print("[OK] " + label)
        else:
            failed += 1
            print("[FAIL] " + label + " rc=" + str(result.returncode))
            err = result.stderr[:200].replace("\n", " ")
            print("  " + err)
    except subprocess.TimeoutExpired:
        failed += 1
        print("[TIMEOUT] " + label)
    except Exception as e:
        failed += 1
        print("[ERROR] " + label + " " + str(e))

print()
print(str(passed) + " passed, " + str(failed) + " failed")
