"""message payloads and idempotency claims

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-23 11:22:47.398759

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Keys are claimed before the request runs, so status and body are empty until it succeeds.
    op.alter_column("idempotency_keys", "status_code", existing_type=sa.INTEGER(), nullable=True)
    op.alter_column("idempotency_keys", "body", existing_type=postgresql.BYTEA(), nullable=True)
    # Full reply per message, so a reconnecting client can redraw the conversation.
    op.add_column("messages", sa.Column("payload", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "payload")
    op.execute("DELETE FROM idempotency_keys WHERE status_code IS NULL")  # in-flight claims cannot be kept
    op.alter_column("idempotency_keys", "body", existing_type=postgresql.BYTEA(), nullable=False)
    op.alter_column("idempotency_keys", "status_code", existing_type=sa.INTEGER(), nullable=False)
