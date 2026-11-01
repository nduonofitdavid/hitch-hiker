"""Preserve a historical migration revision without changing the schema.

Revision ID: 2742e184adde
Revises: 7907c7f8a5f8
Create Date: 2026-09-24 14:21:35.144934

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2742e184adde'
down_revision: Union[str, Sequence[str], None] = '7907c7f8a5f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
  """Advance through this historical revision without changing the schema."""
  pass


def downgrade() -> None:
  """Reverse this historical revision without changing the schema."""
  pass
