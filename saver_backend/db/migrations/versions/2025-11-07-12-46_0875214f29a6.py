"""Refactor video_cache to a generic cache table.

Revision ID: 0875214f29a6
Revises: 19b445ddc1a7
Create Date: 2025-11-07 12:46:48.802677

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0875214f29a6"
down_revision = "19b445ddc1a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Run the migration."""
    op.rename_table("video_cache", "cache")

    op.add_column(
        "cache",
        sa.Column(
            "content_type",
            sa.String(length=50),
            server_default="video",
            nullable=False,
        ),
    )

    op.alter_column("cache", "content_type", server_default=None)


def downgrade() -> None:
    """Undo the migration."""
    op.drop_column("cache", "content_type")
    op.rename_table("cache", "video_cache")
