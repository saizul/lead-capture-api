"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-01-05
"""
import sqlalchemy as sa
from alembic import op

from leadcapture.db import UTCDateTime

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "forms",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column("allowed_origins", sa.JSON(), nullable=False),
        sa.Column("notifiers", sa.JSON(), nullable=False),
        sa.Column("honeypot_field", sa.String(64), nullable=False),
        sa.Column("min_submit_seconds", sa.Integer(), nullable=False),
        sa.Column("require_consent", sa.Boolean(), nullable=False),
        sa.Column("spam_threshold", sa.Integer(), nullable=False),
        sa.Column("blocked_words", sa.JSON(), nullable=False),
        sa.Column("rate_limit_per_ip", sa.Integer(), nullable=False),
        sa.Column("rate_limit_per_form", sa.Integer(), nullable=False),
        sa.Column("success_redirect_url", sa.String(2048), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
    )
    op.create_index("ix_forms_slug", "forms", ["slug"], unique=True)

    op.create_table(
        "leads",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("form_id", sa.String(36), sa.ForeignKey("forms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("email", sa.String(254), nullable=True),
        sa.Column("consent", sa.Boolean(), nullable=False),
        sa.Column("consent_at", UTCDateTime(), nullable=True),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("source_url", sa.String(500), nullable=True),
        sa.Column("spam_score", sa.Integer(), nullable=False),
        sa.Column("is_spam", sa.Boolean(), nullable=False),
        sa.Column("spam_reasons", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
    )
    op.create_index("ix_leads_form_id", "leads", ["form_id"])
    op.create_index("ix_leads_email", "leads", ["email"])
    op.create_index("ix_leads_is_spam", "leads", ["is_spam"])
    op.create_index("ix_leads_status", "leads", ["status"])
    op.create_index("ix_leads_created_at", "leads", ["created_at"])

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("lead_id", sa.String(36), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("notifier_type", sa.String(32), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", UTCDateTime(), nullable=False),
        sa.Column("locked_at", UTCDateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.Column("delivered_at", UTCDateTime(), nullable=True),
    )
    op.create_index("ix_outbox_events_lead_id", "outbox_events", ["lead_id"])
    op.create_index("ix_outbox_due", "outbox_events", ["status", "next_attempt_at"])

    op.create_table(
        "delivery_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "outbox_id", sa.String(36), sa.ForeignKey("outbox_events.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("lead_id", sa.String(36), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("notifier_type", sa.String(32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
    )
    op.create_index("ix_delivery_logs_outbox_id", "delivery_logs", ["outbox_id"])
    op.create_index("ix_delivery_logs_lead_id", "delivery_logs", ["lead_id"])
    op.create_index("ix_delivery_logs_created_at", "delivery_logs", ["created_at"])

    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("prefix", sa.String(12), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("last_used_at", UTCDateTime(), nullable=True),
    )
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)


def downgrade() -> None:
    op.drop_table("api_keys")
    op.drop_table("delivery_logs")
    op.drop_table("outbox_events")
    op.drop_table("leads")
    op.drop_table("forms")
