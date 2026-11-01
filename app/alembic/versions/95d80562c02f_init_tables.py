"""Preserve a historical migration revision without changing the schema.

Revision ID: 95d80562c02f
Revises: 2742e184adde
Create Date: 2026-09-28 10:38:26.101527

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '95d80562c02f'
down_revision: Union[str, Sequence[str], None] = '2742e184adde'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
  """Advance through this historical revision without changing the schema."""
  pass


def downgrade() -> None:
  """Reverse this historical revision without changing the schema."""
  pass
