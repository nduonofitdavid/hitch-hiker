"""Establish the initial migration revision without changing the schema.

Revision ID: 7907c7f8a5f8
Revises: 
Create Date: 2026-09-24 14:19:46.473451

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7907c7f8a5f8'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
  """Record the initial empty migration without changing the schema."""
  pass


def downgrade() -> None:
  """Reverse the initial empty migration without changing the schema."""
  pass
