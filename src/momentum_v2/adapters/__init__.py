"""Optional read-only data adapters for V2 research runs."""

from .sqlite_readonly import SQLiteReadOnlyBarSource

__all__ = ["SQLiteReadOnlyBarSource"]
