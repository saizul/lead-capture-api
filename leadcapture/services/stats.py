"""Pure helpers for the admin statistics endpoints."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def spam_rate(total: int, spam: int) -> float | None:
    """Share of submissions flagged as spam, 0-1 rounded to 4 places; None when there were none."""
    if total <= 0:
        return None
    return round(spam / total, 4)


def spam_rate_series(
    rows: Iterable[tuple[str, str, bool, int]],
    dates: list[str],
) -> dict[str, Any]:
    """Zero-filled daily spam counts and rates per form.

    ``rows`` are ``(form_slug, iso_day, is_spam, count)``. Days outside ``dates`` are ignored.
    Each series holds ``total``, ``spam`` and ``rate`` lists aligned with ``dates``; ``rate`` is
    ``None`` for days without submissions so charts can show a gap instead of 0%.
    """
    index = {d: i for i, d in enumerate(dates)}
    size = len(dates)
    per_form: dict[str, dict[str, list[int]]] = {}
    overall = {"total": [0] * size, "spam": [0] * size}

    for slug, day, is_spam, count in rows:
        i = index.get(str(day))
        if i is None:
            continue
        series = per_form.setdefault(slug, {"total": [0] * size, "spam": [0] * size})
        series["total"][i] += count
        overall["total"][i] += count
        if is_spam:
            series["spam"][i] += count
            overall["spam"][i] += count

    def finish(series: dict[str, list[int]]) -> dict[str, Any]:
        total, spam = series["total"], series["spam"]
        return {
            "total": total,
            "spam": spam,
            "rate": [spam_rate(t, s) for t, s in zip(total, spam, strict=True)],
            "overall_rate": spam_rate(sum(total), sum(spam)),
        }

    return {
        "forms": {slug: finish(s) for slug, s in sorted(per_form.items())},
        "overall": finish(overall),
    }
