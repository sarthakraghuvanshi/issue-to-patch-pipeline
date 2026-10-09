"""Persist read-only issue resolution plans.

Revision ID: f184b917c021
Revises: e76ae6980d6e
"""
from alembic import op
import sqlalchemy as sa

revision = "f184b917c021"
down_revision = "e76ae6980d6e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "issue_plans",
        sa.Column("plan_id", sa.String(64), primary_key=True),
        sa.Column("owner", sa.String(255), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False, unique=True),
        sa.Column("issue_url", sa.String(512), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("issue_plans")
