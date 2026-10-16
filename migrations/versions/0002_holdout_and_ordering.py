"""held_out flag, deferrable chunk ordering, chunks_evaluable view

Revision ID: 0002_holdout_and_ordering
Revises: 0001_core_schema
"""

from migrations.sql_revision import run_sql

revision = "0002_holdout_and_ordering"
down_revision = "0001_core_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("0002_holdout_and_ordering.up.sql")


def downgrade() -> None:
    run_sql("0002_holdout_and_ordering.down.sql")
