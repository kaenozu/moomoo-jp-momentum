"""US adaptive-grid research package.

The package is isolated from the JP daily pipeline and contains no broker
order path. Import-time aliases route all research scripts through the
canonical data and accounting safety policy so legacy caches or the legacy
backtester cannot be used accidentally.
"""

from __future__ import annotations

from . import backtest as _backtest
from . import benchmark as _benchmark
from . import data as _data
from .data_v2 import attach_corporate_actions, load_or_fetch
from .research_safety import ResearchGridBacktester, buy_and_hold_with_dividends

setattr(_backtest, "GridBacktester", ResearchGridBacktester)
setattr(_benchmark, "buy_and_hold", buy_and_hold_with_dividends)
setattr(_data, "load_or_fetch", load_or_fetch)
setattr(_data, "attach_corporate_actions", attach_corporate_actions)

__all__ = [
    "ResearchGridBacktester",
    "attach_corporate_actions",
    "buy_and_hold_with_dividends",
    "load_or_fetch",
]
