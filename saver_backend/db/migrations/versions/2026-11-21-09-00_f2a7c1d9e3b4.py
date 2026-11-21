"""Add failed_request table.

Revision ID: f2a7c1d9e3b4
Revises: a1b2c3d4e5f6
Create Date: 2026-11-21 09:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f2a7c1d9e3b4"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Run the migration."""
    op.create_table(
        "failed_request",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
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
    )
    op.create_index(
        "ix_failed_request_created_at",
        "failed_request",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Undo the migration."""
    op.drop_index("ix_failed_request_created_at", table_name="failed_request")
    op.drop_table("failed_request")
