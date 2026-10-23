"""session travel facts and plan

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-23 16:36:06.962192

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_FACTS = ("ticket_type", "refund_status", "area_state")


def upgrade() -> None:
    # Travel facts for the task planner; existing sessions start with everything unknown.
    for name in _FACTS:
        op.add_column("sessions", sa.Column(name, sa.String(length=16), server_default="UNKNOWN", nullable=False))
    op.add_column("sessions", sa.Column("location_method", sa.String(length=24), nullable=True))
    op.add_column("sessions", sa.Column("plan", sa.JSON(), nullable=True))


def downgrade() -> None:
    for name in ("plan", "location_method", *_FACTS):
        op.drop_column("sessions", name)
