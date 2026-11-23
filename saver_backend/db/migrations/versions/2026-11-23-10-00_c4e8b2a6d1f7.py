"""Add error_event table and failed_request.traceback.

Revision ID: c4e8b2a6d1f7
Revises: f2a7c1d9e3b4
Create Date: 2026-11-23 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c4e8b2a6d1f7"
down_revision = "f2a7c1d9e3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Run the migration."""
    op.add_column("failed_request", sa.Column("traceback", sa.Text(), nullable=True))
    op.create_table(
        "error_event",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(length=40), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("process", sa.String(length=32), nullable=False),
        sa.Column("logger", sa.String(length=255), nullable=False),
        sa.Column("error_type", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("traceback", sa.Text(), nullable=True),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "fingerprint",
            "day",
            name="uq_error_event_fingerprint_day",
        ),
    )
    op.create_index("ix_error_event_day", "error_event", ["day"], unique=False)


def downgrade() -> None:
    """Undo the migration."""
    op.drop_index("ix_error_event_day", table_name="error_event")
    op.drop_table("error_event")
    op.drop_column("failed_request", "traceback")
