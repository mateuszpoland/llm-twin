"""Execute a reviewed .sql file as a migration step.

Keeps DDL readable as SQL rather than buried in Python op.* calls, while
Alembic still owns ordering, the transaction and revision bookkeeping.
"""

from pathlib import Path

from alembic import op

SQL_DIR = Path(__file__).parent / "sql"


def run_sql(name: str) -> None:
    path = SQL_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"migration SQL not found: {path}")
    op.execute(path.read_text(encoding="utf-8"))
