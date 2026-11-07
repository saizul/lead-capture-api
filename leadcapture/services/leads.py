from __future__ import annotations

import csv
import io
from dataclasses import dataclass
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
    "name",
    "email",
    "consent",
    "spam_score",
    "is_spam",
    "source_url",
]


@dataclass
class LeadFilters:
    form: str | None = None
    status: str | None = None
    q: str | None = None
    spam: str = "exclude"  # exclude | include | only
    created_from: datetime | None = None
    created_to: datetime | None = None

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
            lead.name,
            lead.email,
            lead.consent,
            lead.spam_score,
            lead.is_spam,
            lead.source_url,
        ]
        extra = [lead.data.get(k) for k in data_keys if k not in ("name", "email")]
        writer.writerow([_safe_cell(v) for v in [*base, *extra]])
    return buffer.getvalue()
