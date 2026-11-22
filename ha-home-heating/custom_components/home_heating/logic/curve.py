"""Lookup tables: linear interpolation between points, the end value beyond the table."""

from __future__ import annotations

from itertools import pairwise


def interpolate(table: list[tuple[float, float]], x: float) -> float:
    """Value at x from a table of (x, y) points.

    Between two points the value is linear, below the first and above the last point
    the end value is used.
    """
    if not table:
        raise ValueError("empty table")
    points = sorted(table)
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in pairwise(points):
        if x0 <= x <= x1:
            if x1 == x0:
                return y1
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]  # not reached


def crossing(table: list[tuple[float, float]], y: float) -> float | None:
    """First x at which the table reaches y, or None when it never does."""
    points = sorted(table)
    for (x0, y0), (x1, y1) in pairwise(points):
        if (y0 - y) * (y1 - y) <= 0 and y0 != y1:
            return x0 + (y - y0) / (y1 - y0) * (x1 - x0)
    return None


def table_from_rows(rows: list) -> list[tuple[float, float]]:
    """Table from the settings rows: [{"outside": .., "water": ..}] or [[x, y]]."""
    table = []
    for row in rows or []:
        if isinstance(row, dict):
            x, y = row.get("outside"), row.get("water")
        else:
            x, y = row[0], row[1]
        if x is None or y is None:
            continue
        table.append((float(x), float(y)))
    return sorted(table)
