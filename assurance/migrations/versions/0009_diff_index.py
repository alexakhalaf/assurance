"""covering index for snapshot diffs

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-18 21:00:00

A diff is a full outer join of two snapshots on (device, table, key). Without an index in that order
PostgreSQL sorts both sides on disk (about 4 s at 1,000 devices). This index makes it a merge join of
two index-only scans (about 0.7 s).
"""
from typing import Sequence, Union

from alembic import op


revision: str = '0009'
down_revision: Union[str, Sequence[str], None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_state_snap_identity "
                   "ON state_rows (snapshot_id, device_name, table_name, key) INCLUDE (row_hash, id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_state_snap_identity")
