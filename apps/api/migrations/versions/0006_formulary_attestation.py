"""Require a signed screening envelope for active formulary candidates."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006_formulary_attestation"
down_revision = "0005_red_flag_attestation"
branch_labels = None
depends_on = None

NAME = "ck_formulary_active_attestation"
CHECK = """NOT active OR (
    min_age IS NOT NULL AND max_age IS NOT NULL
    AND jsonb_typeof(reviewed_payload) = 'object'
    AND reviewed_payload->'schema_version' = '1'::jsonb
    AND jsonb_typeof(reviewed_payload->'screening') = 'object'
    AND jsonb_typeof(reviewed_payload->'key_id') = 'string'
    AND reviewed_payload->>'signature' ~ '^[0-9a-f]{128}$'
    AND octet_length(reviewed_payload::text) <= 16384
) IS TRUE"""


def upgrade():
    op.add_column("formulary", sa.Column("reviewed_payload", JSONB), schema="roghyar")
    op.create_check_constraint(NAME, "formulary", CHECK, schema="roghyar")


def downgrade():
    # Refuse to discard a stored reviewed payload or bypass forced RLS.
    op.execute("SET LOCAL row_security = off")
    op.execute("""
        DO $rollback$
        BEGIN
          LOCK TABLE roghyar.formulary IN ACCESS EXCLUSIVE MODE;
          IF EXISTS (SELECT FROM roghyar.formulary WHERE reviewed_payload IS NOT NULL) THEN
            RAISE EXCEPTION 'Archive and migrate formulary attestations before downgrading';
          END IF;
        END $rollback$
    """)
    op.drop_constraint(NAME, "formulary", schema="roghyar", type_="check")
    op.drop_column("formulary", "reviewed_payload", schema="roghyar")
