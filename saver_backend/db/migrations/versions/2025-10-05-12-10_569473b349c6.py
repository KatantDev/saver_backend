"""init.

Revision ID: 569473b349c6
Revises:
Create Date: 2025-10-05 12:10:59.276042

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "569473b349c6"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Run the migration."""
    op.create_table(
        "users",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=32), nullable=True),
        sa.Column("first_name", sa.String(length=64), nullable=False),
        sa.Column("last_name", sa.String(length=64), nullable=True),
        sa.Column(
            "lang",
            sa.String(length=10),
            server_default=sa.text("'en'"),
            nullable=False,
        ),
        sa.Column(
            "is_admin",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
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
    op.create_index(op.f("ix_users_telegram_id"), "users", ["telegram_id"], unique=True)


def downgrade() -> None:
    """Undo the migration."""
    op.drop_index(op.f("ix_users_telegram_id"), table_name="users")
    op.drop_table("users")
