"""US adaptive-grid research package.

The package is isolated from the JP daily pipeline and contains no broker
order path. Import-time aliases route all research scripts through the
canonical data, accounting, and benchmark policy so legacy caches or the
legacy backtester cannot be used accidentally.
"""

from __future__ import annotations

from . import backtest as _backtest
from . import benchmark as _benchmark
from . import data as _data
from .research_context import attach_corporate_actions, load_or_fetch
from .research_runtime import CanonicalGridBacktester, canonical_buy_and_hold

setattr(_backtest, "GridBacktester", CanonicalGridBacktester)
setattr(_benchmark, "buy_and_hold", canonical_buy_and_hold)
setattr(_data, "load_or_fetch", load_or_fetch)
setattr(_data, "attach_corporate_actions", attach_corporate_actions)

__all__ = [
    "CanonicalGridBacktester",
    "attach_corporate_actions",
    "canonical_buy_and_hold",
    "load_or_fetch",
]
