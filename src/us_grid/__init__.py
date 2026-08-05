"""
US adaptive grid research package.

US ETF / US equity adaptive grid strategy backtesting, SQLite virtual trading,
and SIMULATE-only execution for the moomoo-jp-momentum project.

This package is deliberately isolated from the existing JP daily pipeline:
it does not touch run_daily_cycle.py, the JP strategy registry, or the JP
config sections.
"""
