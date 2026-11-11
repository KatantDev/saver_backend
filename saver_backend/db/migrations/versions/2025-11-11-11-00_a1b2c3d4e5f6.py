"""Add request_log table.

Revision ID: a1b2c3d4e5f6
Revises: 5b6bf21e7dc6
Create Date: 2025-11-11 11:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "5b6bf21e7dc6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Run the migration."""
    op.create_table(
        "request_log",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_request_log_created_at",
        "request_log",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Undo the migration."""
    op.drop_index("ix_request_log_created_at", table_name="request_log")
    op.drop_table("request_log")
