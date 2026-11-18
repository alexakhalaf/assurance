"""Helpers shared by every CSV and Excel export."""
from __future__ import annotations

_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(v):
    """Neutralize spreadsheet formula injection (OWASP CSV injection): device-controlled text such as an
    interface description '=HYPERLINK(...)' must open as text, not run as a formula. Numbers are left alone."""
    if isinstance(v, str) and v.startswith(_FORMULA):
        return "'" + v
    return v
