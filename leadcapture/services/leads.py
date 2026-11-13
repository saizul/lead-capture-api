from __future__ import annotations

import csv
import io
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from leadcapture.models import Form, Lead

CSV_BASE_COLUMNS = [
    "id",
    "created_at",
    "form",
    "status",
    "tags",
    "name",
    "email",
    "consent",
    "spam_score",
    "is_spam",
    "source_url",
    "note",
]


def merge_tags(
    current: Iterable[str],
    *,
    replace: list[str] | None = None,
    add: Iterable[str] = (),
    remove: Iterable[str] = (),
) -> list[str]:
    """Return the new, sorted tag list for a lead.

    ``replace`` wins over the lead's current tags; ``add`` and ``remove`` are applied on top.
    Inputs are expected to be normalised already (see ``schemas.normalize_tags``).
    """
    tags = set(current if replace is None else replace)
    tags.update(add)
    tags.difference_update(remove)
    return sorted(tags)


def _tag_condition(tag: str) -> Any:
    # Tags are stored as a JSON array of normalised strings, so the quoted value can only
    # match a whole element. This works the same on SQLite and PostgreSQL.
    return cast(Lead.tags, String).contains(f'"{tag}"', autoescape=True)


@dataclass
class LeadFilters:
    form: str | None = None
    status: str | None = None
    q: str | None = None
    spam: str = "exclude"  # exclude | include | only
    created_from: datetime | None = None
    created_to: datetime | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)  # every tag must be present

    def conditions(self) -> list[Any]:
        conds: list[Any] = []
        if self.form:
            conds.append(Form.slug == self.form)
        if self.status:
            conds.append(Lead.status == self.status)
        if self.spam == "exclude":
            conds.append(Lead.is_spam.is_(False))
        elif self.spam == "only":
            conds.append(Lead.is_spam.is_(True))
        if self.created_from:
            conds.append(Lead.created_at >= self.created_from)
        if self.created_to:
            conds.append(Lead.created_at <= self.created_to)
        conds.extend(_tag_condition(tag) for tag in self.tags)
        if self.q:
            needle = self.q.lower()
            conds.append(
                or_(
                    func.lower(Lead.email).contains(needle, autoescape=True),
                    func.lower(Lead.name).contains(needle, autoescape=True),
                    func.lower(cast(Lead.data, String)).contains(needle, autoescape=True),
                )
            )
        return conds


async def query_leads(
    session: AsyncSession, filters: LeadFilters, *, limit: int, offset: int = 0
) -> tuple[list[tuple[Lead, str]], int]:
    conds = filters.conditions()
    base = select(Lead, Form.slug).join(Form, Form.id == Lead.form_id).where(*conds)
    total = (
        await session.execute(
            select(func.count(Lead.id))
            .select_from(Lead)
            .join(Form, Form.id == Lead.form_id)
            .where(*conds)
        )
    ).scalar_one()
    rows = await session.execute(
        base.order_by(Lead.created_at.desc(), Lead.id).limit(limit).offset(offset)
    )
    return [(lead, slug) for lead, slug in rows.all()], int(total)


def _safe_cell(value: Any) -> Any:
    """Neutralise spreadsheet formula injection (=, +, -, @, tab, CR prefixes)."""
    if isinstance(value, str) and value and value[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def leads_to_csv(rows: list[tuple[Lead, str]]) -> str:
    data_keys = sorted({key for lead, _ in rows for key in lead.data})
    columns = CSV_BASE_COLUMNS + [f"data.{k}" for k in data_keys if k not in ("name", "email")]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(columns)
    for lead, slug in rows:
        base = [
            lead.id,
            lead.created_at.isoformat(),
            slug,
            lead.status,
            ";".join(lead.tags or []),
            lead.name,
            lead.email,
            lead.consent,
            lead.spam_score,
            lead.is_spam,
            lead.source_url,
            lead.note,
        ]
        extra = [lead.data.get(k) for k in data_keys if k not in ("name", "email")]
        writer.writerow([_safe_cell(v) for v in [*base, *extra]])
    return buffer.getvalue()


async def tag_counts(session: AsyncSession, filters: LeadFilters) -> list[tuple[str, int]]:
    """Count leads per tag for the leads matching ``filters``, most used first."""
    stmt = select(Lead.tags).join(Form, Form.id == Lead.form_id).where(*filters.conditions())
    counter: Counter[str] = Counter()
    for tags in (await session.execute(stmt)).scalars():
        counter.update(set(tags or []))
    return sorted(counter.items(), key=lambda item: (-item[1], item[0]))
