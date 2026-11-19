"""per-device failed tables

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-19 00:30:00

A command that failed on a device (timeout, refused by command authorization, parser error) leaves the
tables it feeds unknown for that device. Recording them lets diffs and checks skip those tables instead
of reporting every row as removed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0010'
down_revision: Union[str, Sequence[str], None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('device_collections', sa.Column(
        'failed_tables', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'),
        server_default='[]', nullable=False))


def downgrade() -> None:
    op.drop_column('device_collections', 'failed_tables')
