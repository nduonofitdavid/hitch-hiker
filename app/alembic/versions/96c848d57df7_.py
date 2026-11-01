"""Preserve historical trip duration while storing totals instead of averages.

Revision ID: 96c848d57df7
Revises: f53fb25c4188
Create Date: 2026-10-30 16:29:34.911289

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '96c848d57df7'
down_revision: Union[str, Sequence[str], None] = 'f53fb25c4188'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
  """Replace average trip duration with a backfilled accumulated duration."""
  op.add_column('userstats', sa.Column('total_time', sa.Float(), nullable=False, server_default=sa.text("0.0")))
  op.execute('UPDATE userstats SET total_time = average_time * no_of_trips')
  op.drop_column('userstats', 'average_time')


def downgrade() -> None:
  """Restore average trip duration from accumulated duration and trip counts."""
  op.add_column('userstats', sa.Column('average_time', sa.Float(), nullable=False, server_default=sa.text('0.0')))
  op.execute('UPDATE userstats SET average_time = CASE WHEN no_of_trips > 0 THEN total_time / no_of_trips ELSE 0 END')
  op.drop_column('userstats', 'total_time')
