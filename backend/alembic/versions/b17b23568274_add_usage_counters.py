"""add usage counters

Revision ID: b17b23568274
Revises: b541ae4186b5
Create Date: 2026-09-25 00:00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b17b23568274"
down_revision: str | None = "b541ae4186b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_counters",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("day", "name"),
    )


def downgrade() -> None:
    op.drop_table("usage_counters")
