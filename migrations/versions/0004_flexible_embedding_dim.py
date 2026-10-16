"""embedding dimension no longer fixed at 1024

Revision ID: 0004_flexible_embedding_dim
Revises: 0003_chunk_contiguity
"""

from migrations.sql_revision import run_sql

revision = "0004_flexible_embedding_dim"
down_revision = "0003_chunk_contiguity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("0004_flexible_embedding_dim.up.sql")


def downgrade() -> None:
    run_sql("0004_flexible_embedding_dim.down.sql")
