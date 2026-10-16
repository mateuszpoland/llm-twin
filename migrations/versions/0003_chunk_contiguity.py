"""chunk ord must be contiguous 0..n-1, checked at commit

Revision ID: 0003_chunk_contiguity
Revises: 0002_holdout_and_ordering
"""

from migrations.sql_revision import run_sql

revision = "0003_chunk_contiguity"
down_revision = "0002_holdout_and_ordering"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("0003_chunk_contiguity.up.sql")


def downgrade() -> None:
    run_sql("0003_chunk_contiguity.down.sql")
