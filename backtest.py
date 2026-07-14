"""Compatibility entrypoint for the safe historical backtest.

The former implementation modified virtual-trading tables in the source database.
This wrapper delegates to ``validated_backtest.py``, which runs only against a
SQLite Online Backup copy and never calls an order API.
"""

from validated_backtest import main


if __name__ == "__main__":
    raise SystemExit(main())
