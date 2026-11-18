"""trigram index for text search over state rows

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-18 20:40:00

Text search (global search, state table search) is an ILIKE over the row JSON. Without an index that is
a sequential scan of every row in the snapshot (4-12 s at 2M rows). pg_trgm is a trusted extension
(PostgreSQL 13+), so the database owner can create it.
"""
from typing import Sequence, Union

from alembic import op


revision: str = '0008'
down_revision: Union[str, Sequence[str], None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    with op.get_context().autocommit_block():
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_state_data_trgm "
                   "ON state_rows USING gin ((data::text) gin_trgm_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_state_data_trgm")
