"""Keep pending place edits and review metadata in their contribution record.

Revision ID: d6e4b8a29f10
Revises: a23f6a1d4b90
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'd6e4b8a29f10'
down_revision = 'a23f6a1d4b90'
branch_labels = None
depends_on = None


def upgrade() -> None:
  """Validate linked legacy edits, preserve their identities, and remove duplicate storage."""
  # Legacy rows without trustworthy provenance require explicit reconciliation.
  # Never infer a contributor or silently choose between conflicting proposals.
  op.execute('LOCK TABLE pendingplace, contributorrecord, contributor, place IN SHARE ROW EXCLUSIVE MODE')
  op.execute('''DO $$ DECLARE edit record; proposed jsonb; BEGIN
    IF EXISTS (SELECT 1 FROM pendingplace WHERE record_id IS NOT NULL
      GROUP BY record_id HAVING count(*) > 1) THEN
      RAISE EXCEPTION 'Multiple pending place edits reference one contribution; reconcile their record_id values before migrating';
    END IF;
    FOR edit IN SELECT p.*, r.id AS linked_record, r.action_type,
      r.target_type, r.target_id, r.proposed_changes, r.contribution_status,
      c.user_id AS contributor_user, target.created_by AS target_owner
      FROM pendingplace p
      LEFT JOIN contributorrecord r ON r.id = p.record_id
      LEFT JOIN contributor c ON c.id = r.contributor_id
      LEFT JOIN place target ON target.id = p.place_id
    LOOP
      IF edit.linked_record IS NULL THEN
        RAISE EXCEPTION 'Pending place edit % has no linked contribution; reconcile its record_id before migrating', edit.id;
      END IF;
      IF edit.action_type <> 'UPDATE' OR edit.target_type IS DISTINCT FROM 'place'
        OR edit.target_id IS DISTINCT FROM edit.place_id THEN
        RAISE EXCEPTION 'Pending place edit % conflicts with contribution target or action; reconcile its provenance before migrating', edit.id;
      END IF;
      IF edit.contributor_user IS NULL OR edit.target_owner IS DISTINCT FROM edit.contributor_user
        OR (edit.submitted_by IS NOT NULL AND edit.submitted_by <> edit.contributor_user) THEN
        RAISE EXCEPTION 'Pending place edit % has conflicting or missing ownership; reconcile its contributor and submitted_by before migrating', edit.id;
      END IF;
      IF edit.status IS DISTINCT FROM edit.contribution_status THEN
        RAISE EXCEPTION 'Pending place edit % has conflicting review status; reconcile its contribution before migrating', edit.id;
      END IF;
      proposed := jsonb_build_object('name', edit.name, 'latitude', edit.latitude,
        'longitude', edit.longitude, 'description', edit.description,
        'city', edit.city, 'country', edit.country, 'place_type', edit.place_type);
      IF edit.proposed_changes IS NOT NULL AND edit.proposed_changes::jsonb <> proposed THEN
        RAISE EXCEPTION 'Pending place edit % differs from its recorded proposal; reconcile both payloads before migrating', edit.id;
      END IF;
    END LOOP;
  END $$''')
  op.add_column('contributorrecord', sa.Column('pending_place_id', sa.Uuid(), nullable=True))
  op.create_index('ix_contributorrecord_pending_place_id', 'contributorrecord', ['pending_place_id'], unique=True)
  # Keep the earliest known submission time and the canonical review timestamp;
  # a missing canonical review time can safely use the linked legacy value.
  op.execute('''UPDATE contributorrecord r SET
    pending_place_id = p.id,
    proposed_changes = COALESCE(r.proposed_changes, json_build_object(
      'name', p.name, 'latitude', p.latitude, 'longitude', p.longitude,
      'description', p.description, 'city', p.city, 'country', p.country,
      'place_type', p.place_type)),
    submitted_at = LEAST(r.submitted_at, p.submitted_at),
    reviewed_at = COALESCE(r.reviewed_at, p.reviewed_at)
    FROM pendingplace p WHERE p.record_id = r.id''')
  op.drop_table('pendingplace')


def downgrade() -> None:
  """Recreate the legacy pending table from canonical proposals without changing IDs."""
  op.execute('LOCK TABLE contributorrecord, contributor, place IN SHARE ROW EXCLUSIVE MODE')
  op.execute('''DO $$ DECLARE edit record; proposal jsonb; BEGIN
    FOR edit IN SELECT r.*, c.user_id AS contributor_user,
      p.created_by AS target_owner FROM contributorrecord r
      LEFT JOIN contributor c ON c.id = r.contributor_id
      LEFT JOIN place p ON p.id = r.target_id
      WHERE r.pending_place_id IS NOT NULL
    LOOP
      IF edit.action_type <> 'UPDATE' OR edit.target_type IS DISTINCT FROM 'place'
        OR edit.target_id IS NULL OR edit.contributor_user IS NULL
        OR edit.target_owner IS DISTINCT FROM edit.contributor_user THEN
        RAISE EXCEPTION 'Contribution % has invalid place-edit provenance; reconcile it before downgrading', edit.id;
      END IF;
      proposal := edit.proposed_changes::jsonb;
      IF proposal IS NULL OR jsonb_typeof(proposal) <> 'object'
        OR NOT proposal ?& ARRAY['name', 'latitude', 'longitude', 'description', 'city', 'country', 'place_type']
        OR jsonb_typeof(proposal->'name') <> 'string'
        OR jsonb_typeof(proposal->'description') <> 'string'
        OR jsonb_typeof(proposal->'latitude') <> 'number'
        OR jsonb_typeof(proposal->'longitude') <> 'number'
        OR jsonb_typeof(proposal->'city') <> 'string'
        OR jsonb_typeof(proposal->'country') <> 'string'
        OR jsonb_typeof(proposal->'place_type') NOT IN ('string', 'null') THEN
        RAISE EXCEPTION 'Contribution % has an invalid place proposal; reconcile it before downgrading', edit.id;
      END IF;
    END LOOP;
  END $$''')
  op.create_table('pendingplace',
    sa.Column('name', sa.String(128), nullable=False),
    sa.Column('latitude', sa.Float(), nullable=False),
    sa.Column('longitude', sa.Float(), nullable=False),
    sa.Column('description', sa.String(256), nullable=False),
    sa.Column('city', sa.Uuid(), nullable=False),
    sa.Column('country', sa.Uuid(), nullable=False),
    sa.Column('place_type', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), primary_key=True, nullable=False),
    sa.Column('place_id', sa.Uuid(), nullable=False),
    sa.Column('record_id', sa.Uuid(), nullable=True),
    sa.Column('submitted_by', sa.Uuid(), nullable=True),
    sa.Column('status', postgresql.ENUM('ACCEPTED', 'PENDING', 'DENIED', name='contributionstatus', create_type=False), nullable=False),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['city'], ['city.id'], name='pendingplace_city_fkey'),
    sa.ForeignKeyConstraint(['country'], ['country.id'], name='pendingplace_country_fkey'),
    sa.ForeignKeyConstraint(['place_id'], ['place.id'], name='pendingplace_place_id_fkey'),
    sa.ForeignKeyConstraint(['place_type'], ['placetype.id'], name='pendingplace_place_type_fkey'),
    sa.ForeignKeyConstraint(['record_id'], ['contributorrecord.id'], name='pendingplace_record_id_fkey'),
    sa.ForeignKeyConstraint(['submitted_by'], ['user.id'], name='pendingplace_submitted_by_fkey'),
    sa.UniqueConstraint('record_id', name='pendingplace_record_id_key'),
    sa.CheckConstraint('latitude BETWEEN -90 AND 90', name='ck_pendingplace_latitude'),
    sa.CheckConstraint('longitude BETWEEN -180 AND 180', name='ck_pendingplace_longitude'),
  )
  for column in ('city', 'country', 'place_id', 'place_type', 'submitted_by'):
    op.create_index(f'ix_pendingplace_{column}', 'pendingplace', [column])
  op.execute('''INSERT INTO pendingplace (id, place_id, record_id, submitted_by,
    name, latitude, longitude, description, city, country, place_type,
    status, submitted_at, reviewed_at)
    SELECT r.pending_place_id, r.target_id, r.id, c.user_id,
      r.proposed_changes->>'name', (r.proposed_changes->>'latitude')::double precision,
      (r.proposed_changes->>'longitude')::double precision,
      r.proposed_changes->>'description', (r.proposed_changes->>'city')::uuid,
      (r.proposed_changes->>'country')::uuid, (r.proposed_changes->>'place_type')::uuid,
      r.contribution_status, r.submitted_at, r.reviewed_at
    FROM contributorrecord r JOIN contributor c ON c.id = r.contributor_id
    WHERE r.pending_place_id IS NOT NULL''')
  op.drop_index('ix_contributorrecord_pending_place_id', table_name='contributorrecord')
  op.drop_column('contributorrecord', 'pending_place_id')
