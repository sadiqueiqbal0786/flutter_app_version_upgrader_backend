"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "apps",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("app_id", sa.String(200), nullable=False, unique=True, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("android_package_name", sa.String(200)),
        sa.Column("ios_bundle_id", sa.String(200)),
        sa.Column("apple_app_id", sa.String(50)),
        sa.Column("sync_enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_sync_error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "releases",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("app_pk", sa.Integer, sa.ForeignKey("apps.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("build", sa.Integer),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("store_url", sa.String(500)),
        sa.Column("release_notes", sa.Text),
        sa.UniqueConstraint("app_pk", "platform", "channel", "version", "build", name="uq_release"),
    )
    op.create_table(
        "policies",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("app_pk", sa.Integer, sa.ForeignKey("apps.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("mode", sa.String(20), nullable=False, server_default="auto_latest"),
        sa.Column("custom_minimum_version", sa.String(50)),
        sa.Column("custom_minimum_build", sa.Integer),
        sa.Column("max_age_days", sa.Integer),
        sa.Column("force_update", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("update_message", sa.Text),
        sa.Column("update_url", sa.String(500)),
        sa.Column("recommended_version", sa.String(50)),
        sa.Column("recommended_build", sa.Integer),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("app_pk", "platform", "channel", name="uq_policy"),
    )


def downgrade() -> None:
    op.drop_table("policies")
    op.drop_table("releases")
    op.drop_table("apps")
