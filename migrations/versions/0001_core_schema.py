"""core schema: documents, chunks, embeddings, samples, pseudonyms

Revision ID: 0001_core_schema
Revises:
"""

from migrations.sql_revision import run_sql

revision = "0001_core_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql("0001_core_schema.up.sql")


def downgrade() -> None:
    run_sql("0001_core_schema.down.sql")
