"""Preserve a historical migration revision without changing the schema.

Revision ID: 2f39e40ae739
Revises: 88923ac109d9
Create Date: 2026-09-30 23:24:20.997539

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2f39e40ae739'
down_revision: Union[str, Sequence[str], None] = '88923ac109d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
  """Advance through this historical revision without changing the schema."""
  pass


def downgrade() -> None:
  """Reverse this historical revision without changing the schema."""
  pass
