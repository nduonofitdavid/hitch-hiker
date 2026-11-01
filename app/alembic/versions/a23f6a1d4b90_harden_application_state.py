"""Persist authentication, moderation, trip snapshots, and graph revisions.

Revision ID: a23f6a1d4b90
Revises: 38ee8cc5ca4b
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'a23f6a1d4b90'
down_revision = '38ee8cc5ca4b'
branch_labels = None
depends_on = None


def _reconcile_check_constraint(name: str, table: str, condition: str, *, drop_matching: bool = False, legacy_condition: str | None = None) -> None:
  """Reconcile matching checks, optionally removing them before a column type change."""
  expected_name = f'{name}_a23f6a1d4b90'
  legacy_check = ''
  if drop_matching and legacy_condition is not None:
    legacy_check = f'''
      IF existing.expression IS DISTINCT FROM
        (SELECT pg_get_expr(conbin, conrelid) FROM pg_constraint
          WHERE conrelid = '{table}'::regclass AND conname = '{expected_name}') THEN
        ALTER TABLE "{table}" DROP CONSTRAINT "{expected_name}";
        ALTER TABLE "{table}" ADD CONSTRAINT "{expected_name}" CHECK ({legacy_condition}) NOT VALID;
      END IF;'''
  # Let PostgreSQL normalize both expressions using the actual column types.
  # NOT VALID avoids scanning rows twice; a reused check is validated below.
  op.execute(f'''DO $$ DECLARE existing record; BEGIN
    SELECT contype, convalidated, connoinherit,
      pg_get_expr(conbin, conrelid) AS expression INTO existing
      FROM pg_constraint WHERE conrelid = '{table}'::regclass AND conname = '{name}';
    IF NOT FOUND THEN
      IF NOT {drop_matching} THEN
        ALTER TABLE "{table}" ADD CONSTRAINT "{name}" CHECK ({condition});
      END IF;
    ELSE
      IF existing.contype <> 'c' THEN
        RAISE EXCEPTION 'Constraint {name} on {table} has a conflicting type; reconcile it before migrating';
      END IF;
      ALTER TABLE "{table}" ADD CONSTRAINT "{expected_name}" CHECK ({condition}) NOT VALID;
      {legacy_check}
      IF existing.connoinherit OR existing.expression IS DISTINCT FROM
        (SELECT pg_get_expr(conbin, conrelid) FROM pg_constraint
          WHERE conrelid = '{table}'::regclass AND conname = '{expected_name}') THEN
        RAISE EXCEPTION 'Constraint {name} on {table} has a conflicting definition; reconcile it before migrating';
      END IF;
      ALTER TABLE "{table}" DROP CONSTRAINT "{expected_name}";
      IF NOT {drop_matching} AND NOT existing.convalidated THEN
        ALTER TABLE "{table}" VALIDATE CONSTRAINT "{name}";
      END IF;
      IF {drop_matching} THEN
        ALTER TABLE "{table}" DROP CONSTRAINT "{name}";
      END IF;
    END IF;
  END $$''')


def upgrade() -> None:
  """Add token revocation, moderation provenance, trip snapshots, and data constraints."""
  # Report ambiguous identities before adding constraints; never merge accounts
  # or contributor audit histories as an automatic migration side effect.
  op.execute('''DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM "user" GROUP BY lower(trim(email)) HAVING count(*) > 1) THEN
      RAISE EXCEPTION 'Duplicate normalized email addresses must be resolved before migrating';
    END IF;
    IF EXISTS (SELECT 1 FROM contributor GROUP BY user_id HAVING count(*) > 1) THEN
      RAISE EXCEPTION 'Duplicate contributor profiles must be resolved before migrating';
    END IF;
    IF EXISTS (SELECT 1 FROM city WHERE length(name) > 128) THEN
      RAISE EXCEPTION 'City names longer than 128 characters must be shortened before migrating';
    END IF;
  END $$''')
  op.execute('UPDATE "user" SET email = lower(trim(email))')
  op.create_index('ix_user_email_normalized', 'user', [sa.text('lower(email)')], unique=True)
  op.add_column('user', sa.Column('token_version', sa.Integer(), nullable=False, server_default='0'))
  op.alter_column('user', 'token_version', server_default=None)
  op.alter_column('city', 'name', type_=sa.String(128), existing_nullable=False)
  op.drop_index('ix_city_name', table_name='city')
  op.create_index('ix_city_name', 'city', ['name'], unique=False)
  op.create_unique_constraint('uq_city_name_country', 'city', ['name', 'country'])
  # Some installations ran the old revision 96, adding a second name constraint.
  op.execute('''DO $$ DECLARE item record; BEGIN
    FOR item IN SELECT conname FROM pg_constraint
      WHERE conrelid = 'stops'::regclass AND contype = 'u'
      AND conkey = ARRAY[(SELECT attnum FROM pg_attribute
        WHERE attrelid = 'stops'::regclass AND attname = 'name')]::smallint[]
    LOOP EXECUTE format('ALTER TABLE stops DROP CONSTRAINT %I', item.conname); END LOOP;
  END $$''')
  op.drop_index('ix_contributor_user_id', table_name='contributor')
  op.create_index('ix_contributor_user_id', 'contributor', ['user_id'], unique=True)
  op.alter_column('records', 'message', type_=sa.Text(), existing_nullable=False)
  op.alter_column('contributorrecord', 'description', type_=sa.Text(), existing_nullable=False)
  for column in (
    sa.Column('target_type', sa.String(16), nullable=True),
    sa.Column('target_id', sa.Uuid(), nullable=True),
    sa.Column('proposed_changes', sa.JSON(), nullable=True),
    sa.Column('original_data', sa.JSON(), nullable=True),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
  ):
    op.add_column('contributorrecord', column)
  op.alter_column('contributorrecord', 'submitted_at', server_default=None)
  op.create_index('ix_contributorrecord_target_id', 'contributorrecord', ['target_id'])
  for column in (
    sa.Column('record_id', sa.Uuid(), nullable=True),
    sa.Column('submitted_by', sa.Uuid(), nullable=True),
    sa.Column('status', postgresql.ENUM('ACCEPTED', 'PENDING', 'DENIED', name='contributionstatus', create_type=False), nullable=False, server_default='PENDING'),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
  ):
    op.add_column('pendingplace', column)
  op.alter_column('pendingplace', 'status', server_default=None)
  op.alter_column('pendingplace', 'submitted_at', server_default=None)
  op.create_foreign_key('pendingplace_record_id_fkey', 'pendingplace', 'contributorrecord', ['record_id'], ['id'])
  op.create_foreign_key('pendingplace_submitted_by_fkey', 'pendingplace', 'user', ['submitted_by'], ['id'])
  op.create_unique_constraint('pendingplace_record_id_key', 'pendingplace', ['record_id'])
  op.create_index('ix_pendingplace_submitted_by', 'pendingplace', ['submitted_by'])
  op.create_table('graphrevision',
    sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
  )
  op.execute('INSERT INTO graphrevision (id, version) VALUES (1, 0)')
  op.execute('ALTER TABLE tripstore DROP CONSTRAINT IF EXISTS "enforce trip uniqueness"')
  op.add_column('tripstore', sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True))
  op.add_column('tripstore', sa.Column('itinerary', sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")))
  op.alter_column('tripstore', 'itinerary', server_default=None)
  optimizer = postgresql.ENUM('PRICE', 'TIME', name='optimizerweight')
  optimizer.create(op.get_bind(), checkfirst=False)
  op.add_column('tripstore', sa.Column('optimize_by', postgresql.ENUM('PRICE', 'TIME', name='optimizerweight', create_type=False), nullable=True))
  op.add_column('tripstore', sa.Column('distance_estimated', sa.Boolean(), nullable=False, server_default=sa.false()))
  # Legacy trips did not record whether geometry was estimated. Treat their
  # distances as estimates rather than claiming an accuracy we cannot recover.
  op.execute('UPDATE tripstore SET distance_estimated = true')
  op.alter_column('tripstore', 'distance_estimated', server_default=None)
  checks = (
    ('route', 'distinct_stops', 'start <> "end"'),
    ('route', 'time', 'time > 0 AND time < 1e308'),
    ('route', 'price', 'price >= 0 AND price < 1e12'),
    ('tripstore', 'distance', 'distance >= 0 AND distance < 1e308'),
    ('tripstore', 'price', 'price >= 0 AND price < 1e12'),
    ('tripstore', 'time', 'time >= 0 AND time < 1e308'),
    ('userstats', 'nonnegative', 'no_of_trips >= 0 AND distance_covered >= 0 AND distance_covered < 1e308 AND total_spent >= 0 AND total_spent < 1e12 AND total_time >= 0 AND total_time < 1e308'),
  )
  # Type conversion leaves old float casts in check expressions. Compare money
  # rules first, including the legacy 1e308 bound, then validate rounded money
  # with rebuilt numeric checks below.
  for table, name, condition in checks:
    if (table, name) in (('route', 'price'), ('tripstore', 'price'), ('userstats', 'nonnegative')):
      _reconcile_check_constraint(f'ck_{table}_{name}', table, condition,
        drop_matching=True, legacy_condition=condition.replace('1e12', '1e308'))
  for table, column in (('route', 'price'), ('tripstore', 'price'), ('userstats', 'total_spent')):
    op.alter_column(table, column, type_=sa.Numeric(14, 2), existing_nullable=False,
      postgresql_using=f'round({column}::numeric, 2)')
  for table in ('stops', 'place', 'pendingplace'):
    _reconcile_check_constraint(f'ck_{table}_latitude', table, 'latitude BETWEEN -90 AND 90')
    _reconcile_check_constraint(f'ck_{table}_longitude', table, 'longitude BETWEEN -180 AND 180')
  for table, name, condition in checks:
    _reconcile_check_constraint(f'ck_{table}_{name}', table, condition)


def downgrade() -> None:
  """Restore the prior schema, rejecting data incompatible with its tighter limits."""
  # Restoring historical narrower columns/global uniqueness can be lossy.
  # PostgreSQL checks these casts and constraints, so incompatible data aborts
  # the transaction instead of being truncated or discarded.
  for table, names in (
    ('stops', ('latitude', 'longitude')),
    ('place', ('latitude', 'longitude')),
    ('pendingplace', ('latitude', 'longitude')),
    ('route', ('distinct_stops', 'time', 'price')),
    ('tripstore', ('distance', 'price', 'time')),
    ('userstats', ('nonnegative',)),
  ):
    for name in names:
      op.drop_constraint(f'ck_{table}_{name}', table, type_='check')
  for table, column in (('route', 'price'), ('tripstore', 'price'), ('userstats', 'total_spent')):
    op.alter_column(table, column, type_=sa.Float(), existing_nullable=False, postgresql_using=f'{column}::double precision')
  for column in ('distance_estimated', 'optimize_by', 'itinerary', 'completed_at'):
    op.drop_column('tripstore', column)
  postgresql.ENUM(name='optimizerweight').drop(op.get_bind(), checkfirst=False)
  op.drop_table('graphrevision')
  op.drop_index('ix_pendingplace_submitted_by', table_name='pendingplace')
  op.drop_constraint('pendingplace_record_id_key', 'pendingplace', type_='unique')
  op.drop_constraint('pendingplace_submitted_by_fkey', 'pendingplace', type_='foreignkey')
  op.drop_constraint('pendingplace_record_id_fkey', 'pendingplace', type_='foreignkey')
  for column in ('record_id', 'submitted_by', 'status', 'submitted_at', 'reviewed_at'):
    op.drop_column('pendingplace', column)
  op.drop_index('ix_contributorrecord_target_id', table_name='contributorrecord')
  for column in ('target_type', 'target_id', 'proposed_changes', 'original_data', 'submitted_at', 'reviewed_at'):
    op.drop_column('contributorrecord', column)
  op.execute('''DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM contributorrecord WHERE length(description) > 128)
      OR EXISTS (SELECT 1 FROM records WHERE length(message) > 256) THEN
      RAISE EXCEPTION 'Long audit descriptions must be exported or shortened before downgrading';
    END IF;
  END $$''')
  op.alter_column('contributorrecord', 'description', type_=sa.String(128), existing_nullable=False)
  op.alter_column('records', 'message', type_=sa.String(256), existing_nullable=False)
  op.drop_index('ix_contributor_user_id', table_name='contributor')
  op.create_index('ix_contributor_user_id', 'contributor', ['user_id'], unique=False)
  op.create_unique_constraint('stops_name_key', 'stops', ['name'])
  op.drop_constraint('uq_city_name_country', 'city', type_='unique')
  op.drop_index('ix_city_name', table_name='city')
  op.create_index('ix_city_name', 'city', ['name'], unique=True)
  op.alter_column('city', 'name', type_=sa.String(), existing_nullable=False)
  op.drop_column('user', 'token_version')
  op.drop_index('ix_user_email_normalized', table_name='user')
