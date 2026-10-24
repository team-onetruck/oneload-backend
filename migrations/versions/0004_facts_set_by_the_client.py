"""facts set by the client

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-25 01:14:02.297458

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Names of travel facts the client set with PATCH /sessions, protected from conversation updates.
    op.add_column("sessions", sa.Column("client_facts", sa.JSON(), server_default=sa.text("'[]'"), nullable=False))


def downgrade() -> None:
    op.drop_column("sessions", "client_facts")
