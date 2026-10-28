"""installed-version stats

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "install_stats",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("app_pk", sa.Integer, sa.ForeignKey("apps.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("build", sa.Integer),
        sa.Column("checks", sa.Integer, nullable=False, server_default="0"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("app_pk", "platform", "channel", "version", "build", name="uq_install_stat"),
    )


def downgrade() -> None:
    op.drop_table("install_stats")
