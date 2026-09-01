"""Initial schema: runs, run_events, comparisons, comparison_runs.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels = None
depends_on = None

RUN_STATUS = ("queued", "running", "succeeded", "failed")

# create_type=False: the type is created once, explicitly, below. Left to
# itself SQLAlchemy emits a CREATE TYPE for every table that references the
# enum, and the second one fails.
status = postgresql.ENUM(*RUN_STATUS, name="job_status", create_type=False)


def upgrade() -> None:
    postgresql.ENUM(*RUN_STATUS, name="job_status").create(op.get_bind(), checkfirst=True)

    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("status", status, nullable=False, server_default="queued"),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("minutes", sa.Float(), nullable=False),
        sa.Column("warmup_minutes", sa.Float(), nullable=False, server_default="0"),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("summary", postgresql.JSONB(), nullable=True),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stores_events", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_runs_status_created", "runs", ["status", sa.text("created_at DESC")])
    op.create_index("ix_runs_created", "runs", [sa.text("created_at DESC")])

    op.create_table(
        "run_events",
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("t", sa.Float(), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("board", sa.Integer(), nullable=True),
        # `json`, not `jsonb`, and deliberately. jsonb is a parsed
        # representation: it sorts object keys and drops the original text, so
        # a detail object does not come back out the way it went in. This
        # column's whole job is to reproduce a saved log exactly, and nothing
        # ever queries inside it -- the queryable copy of the configuration
        # lives on runs.config, which is jsonb.
        sa.Column("station", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("run_id", "seq"),
    )

    op.create_table(
        "comparisons",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("status", status, nullable=False, server_default="queued"),
        sa.Column("baseline_config", postgresql.JSONB(), nullable=False),
        sa.Column("variant_config", postgresql.JSONB(), nullable=False),
        sa.Column("seeds", sa.Integer(), nullable=False),
        sa.Column("minutes", sa.Float(), nullable=False),
        sa.Column("warmup_minutes", sa.Float(), nullable=False, server_default="0"),
        sa.Column("summary", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_comparisons_created", "comparisons", [sa.text("created_at DESC")])

    op.create_table(
        "comparison_runs",
        sa.Column(
            "comparison_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("comparisons.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("comparison_id", "run_id"),
        sa.CheckConstraint("role in ('baseline','variant')", name="ck_comparison_runs_role"),
    )
    op.create_index("ix_comparison_runs_run", "comparison_runs", ["run_id"])


def downgrade() -> None:
    op.drop_table("comparison_runs")
    op.drop_table("comparisons")
    op.drop_table("run_events")
    op.drop_table("runs")
    postgresql.ENUM(name="job_status").drop(op.get_bind(), checkfirst=True)
